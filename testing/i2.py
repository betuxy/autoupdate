#!/usr/bin/env python3
"""
i2 — Icinga2 API command-line interface.

Usage:
  i2 [global options] <command> [command options]
  i2 --help | i2 <command> --help

Environment variables (override the corresponding flag):
  ICINGA_URL       API base URL
  ICINGA_USER      API username
  ICINGA_PASSWORD  API password
"""

import argparse
import base64
import getpass
import json
import logging
import logging.handlers
import os
import shutil
import ssl
import stat
import sys
import textwrap
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


# ── State mappings ────────────────────────────────────────────────────────────

HOST_STATES    = {0: "UP", 1: "DOWN", 2: "UNREACHABLE"}
SERVICE_STATES = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}


# ── JSON structured logger ────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts":     datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":  record.levelname,
            "msg":    record.getMessage(),
            "logger": record.name,
            "file":   record.pathname,
            "func":   record.funcName,
            "line":   record.lineno,
        }
        if record.exc_info:
            entry["stacktrace"] = self.formatException(record.exc_info)
        return json.dumps(entry)


_DEFAULT_LOG   = os.path.expanduser("~/.local/share/i2/i2.log")
_LOG_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


def _setup_logging(debug: bool, log_file: str, console: bool) -> logging.Logger:
    log = logging.getLogger("i2")
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = _JsonFormatter()

    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=_LOG_MAX_BYTES, backupCount=1, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    log.addHandler(fh)

    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        log.addHandler(ch)

    return log


# ── Icinga2 API client ────────────────────────────────────────────────────────

class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


class Icinga2Client:
    def __init__(self, url: str, user: str, password: str, verify: bool = True):
        self.base = url.rstrip("/")
        self._auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        if verify:
            self._ctx = ssl.create_default_context()
        else:
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def _req(self, method: str, path: str, body: dict = None, extra_headers: dict = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": f"Basic {self._auth}",
            "Accept":        "application/json",
        }
        if data:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)

        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                results = json.loads(raw).get("results", [])
                msg = results[0].get("status", raw.decode()) if results else raw.decode()
            except Exception:
                msg = raw.decode()
            raise ApiError(e.code, f"HTTP {e.code}: {msg}") from None

    def get_objects(self, obj_type: str, filters: str = None, attrs: list = None) -> list:
        body = {}
        if filters:
            body["filter"] = filters
        if attrs:
            body["attrs"] = attrs
        if body:
            r = self._req("POST", f"/v1/objects/{obj_type}", body=body,
                          extra_headers={"X-HTTP-Method-Override": "GET"})
        else:
            r = self._req("GET", f"/v1/objects/{obj_type}")
        return r.get("results", [])

    def action(self, name: str, body: dict) -> list:
        return self._req("POST", f"/v1/actions/{name}", body=body).get("results", [])


# ── Output helpers ────────────────────────────────────────────────────────────

def _state(val: int, mapping: dict) -> str:
    return mapping.get(val, f"STATE({val})")


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 3] + "..."


def _table(rows: list[dict], columns: list[tuple]):
    if not rows:
        print("  (no results)")
        return
    headers = [c[0] for c in columns]
    keys    = [c[1] for c in columns]
    widths  = [len(h) for h in headers]
    for row in rows:
        for i, k in enumerate(keys):
            widths[i] = max(widths[i], len(str(row.get(k, ""))))
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    sep = "  " + "  ".join("─" * w for w in widths)
    print(fmt.format(*headers))
    print(sep)
    for row in rows:
        print(fmt.format(*[str(row.get(k, "")) for k in keys]))


def _out(data, as_json: bool):
    if as_json:
        print(json.dumps(data, indent=2, default=str))


def _err(msg: str, as_json: bool, code: int = 1, **extra):
    if as_json:
        print(json.dumps({"error": msg, **extra}, indent=2), file=sys.stderr)
    else:
        print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


# ── Command implementations ───────────────────────────────────────────────────

def cmd_hosts(client: Icinga2Client, args, log: logging.Logger):
    expr = args.filter or ""
    if args.state:
        rev = {v: k for k, v in HOST_STATES.items()}
        num = rev.get(args.state.upper())
        if num is None:
            log.error("Unknown host state '%s'. Valid: %s", args.state, ", ".join(HOST_STATES.values()))
            _err(f"Unknown state '{args.state}'. Valid: {', '.join(HOST_STATES.values())}", args.json)
        sf = f"host.state == {num}"
        expr = f"({expr}) && {sf}" if expr else sf

    log.info("Querying hosts filter=%s", expr or "(none)")
    results = client.get_objects("hosts", filters=expr or None, attrs=[
        "name", "address", "state", "last_check_result",
        "last_state_change", "acknowledgement", "downtime_depth",
    ])
    log.info("Received %d host(s)", len(results))

    rows = []
    for r in results:
        a = r["attrs"]
        rows.append({
            "host":    a.get("name", r.get("name", "")),
            "state":   _state(a.get("state", -1), HOST_STATES),
            "address": a.get("address") or "-",
            "checked": _fmt_ts((a.get("last_check_result") or {}).get("execution_end")),
            "changed": _fmt_ts(a.get("last_state_change")),
            "ack":     "✓" if a.get("acknowledgement") else "",
            "dt":      f"({a['downtime_depth']})" if a.get("downtime_depth") else "",
        })

    if args.json:
        _out(rows, True)
    else:
        _table(rows, [
            ("HOST",        "host"),
            ("STATE",       "state"),
            ("ADDRESS",     "address"),
            ("LAST CHECK",  "checked"),
            ("LAST CHANGE", "changed"),
            ("ACK",         "ack"),
            ("DT",          "dt"),
        ])
    return rows


def cmd_services(client: Icinga2Client, args, log: logging.Logger):
    expr = args.filter or ""
    if args.host:
        hf = f'host.name == "{args.host}"'
        expr = f"({expr}) && {hf}" if expr else hf
    if args.name:
        nf = f'match("{args.name}", service.name)'
        expr = f"({expr}) && {nf}" if expr else nf
    if args.state:
        rev = {v: k for k, v in SERVICE_STATES.items()}
        num = rev.get(args.state.upper())
        if num is None:
            log.error("Unknown service state '%s'. Valid: %s", args.state, ", ".join(SERVICE_STATES.values()))
            _err(f"Unknown state '{args.state}'. Valid: {', '.join(SERVICE_STATES.values())}", args.json)
        sf = f"service.state == {num}"
        expr = f"({expr}) && {sf}" if expr else sf

    log.info("Querying services filter=%s", expr or "(none)")
    results = client.get_objects("services", filters=expr or None, attrs=[
        "name", "host_name", "state", "last_check_result",
        "last_state_change", "acknowledgement", "downtime_depth",
    ])
    log.info("Received %d service(s)", len(results))

    rows = []
    for r in results:
        a = r["attrs"]
        lcr = a.get("last_check_result") or {}
        rows.append({
            "host":    a.get("host_name", "-"),
            "service": a.get("name", r.get("name", "")),
            "state":   _state(a.get("state", -1), SERVICE_STATES),
            "checked": _fmt_ts(lcr.get("execution_end")),
            "output":  lcr.get("output", "") if args.max_output == 0 else _trunc(lcr.get("output", ""), args.max_output),
            "ack":     "✓" if a.get("acknowledgement") else "",
            "dt":      f"({a['downtime_depth']})" if a.get("downtime_depth") else "",
        })

    if args.json:
        _out(rows, True)
    else:
        _table(rows, [
            ("HOST",       "host"),
            ("SERVICE",    "service"),
            ("STATE",      "state"),
            ("LAST CHECK", "checked"),
            ("OUTPUT",     "output"),
            ("ACK",        "ack"),
            ("DT",         "dt"),
        ])
    return rows


def cmd_downtime_schedule(client: Icinga2Client, args, log: logging.Logger):
    now = datetime.now(tz=timezone.utc)
    end = now + timedelta(seconds=args.duration)
    body = {
        "type":         "Service" if args.service else "Host",
        "filter":       f'host.name == "{args.host}"',
        "start_time":   now.timestamp(),
        "end_time":     end.timestamp(),
        "duration":     args.duration,
        "author":       args.author,
        "comment":      args.comment,
        "fixed":        True,
        "all_services": not bool(args.service),
    }
    if args.service:
        body["filter"] += f' && service.name == "{args.service}"'
        body["all_services"] = False

    log.info("Scheduling downtime host=%s service=%s duration=%ds", args.host, args.service, args.duration)
    results = client.action("schedule-downtime", body)
    count   = len(results)
    until   = _fmt_ts(end.timestamp())
    target  = f"{args.host}/{args.service}" if args.service else args.host

    if args.json:
        _out({"status": "ok", "host": args.host, "service": args.service, "objects": count, "until": until}, True)
    else:
        print(f"Downtime scheduled on {target}: {count} object(s) until {until}")
    log.info("Downtime scheduled objects=%d until=%s", count, until)


def cmd_downtime_remove(client: Icinga2Client, args, log: logging.Logger):
    expr = f'downtime.host_name == "{args.host}"'
    if args.author:
        expr += f' && downtime.author == "{args.author}"'

    log.info("Removing downtime host=%s", args.host)
    results = client.action("remove-downtime", {"type": "Downtime", "filter": expr})
    count   = len(results)

    if args.json:
        _out({"status": "ok", "host": args.host, "removed": count}, True)
    else:
        print(f"Removed {count} downtime(s) for {args.host}")
    log.info("Removed %d downtime(s)", count)


def cmd_downtime_list(client: Icinga2Client, args, log: logging.Logger):
    expr = f'downtime.host_name == "{args.host}"' if args.host else None

    log.info("Listing downtimes host=%s", args.host or "(all)")
    results = client.get_objects("downtimes", filters=expr, attrs=[
        "host_name", "service_name", "author", "comment",
        "start_time", "end_time", "in_effect",
    ])
    log.info("Received %d downtime(s)", len(results))

    rows = []
    for r in results:
        a = r["attrs"]
        rows.append({
            "host":    a.get("host_name", "-"),
            "service": a.get("service_name") or "(host)",
            "author":  a.get("author", "-"),
            "comment": _trunc(a.get("comment") or "", 40),
            "start":   _fmt_ts(a.get("start_time")),
            "end":     _fmt_ts(a.get("end_time")),
            "active":  "✓" if a.get("in_effect") else "",
        })

    if args.json:
        _out(rows, True)
    else:
        if not rows:
            print("  No downtimes found.")
        else:
            _table(rows, [
                ("HOST",    "host"),
                ("SERVICE", "service"),
                ("AUTHOR",  "author"),
                ("COMMENT", "comment"),
                ("START",   "start"),
                ("END",     "end"),
                ("ACTIVE",  "active"),
            ])
    return rows


def cmd_recheck(client: Icinga2Client, args, log: logging.Logger):
    expr   = f'host.name == "{args.host}"'
    target = args.host
    if args.service:
        expr  += f' && service.name == "{args.service}"'
        target = f"{args.host}/{args.service}"

    log.info("Triggering recheck target=%s", target)
    results = client.action("reschedule-check", {
        "type":        "Service" if args.service else "Host",
        "filter":      expr,
        "force_check": True,
    })
    count = len(results)

    if args.json:
        _out({"status": "ok", "target": target, "objects": count}, True)
    else:
        print(f"Recheck triggered for {target} ({count} object(s))")
    log.info("Recheck scheduled objects=%d", count)


def cmd_report(client: Icinga2Client, args, log: logging.Logger):
    log.info("Generating overview report")
    hosts    = client.get_objects("hosts",    attrs=["name", "state", "acknowledgement", "downtime_depth"])
    services = client.get_objects("services", attrs=["name", "host_name", "state", "acknowledgement", "downtime_depth"])

    hcounts = {s: 0 for s in HOST_STATES.values()}
    scounts = {s: 0 for s in SERVICE_STATES.values()}
    for h in hosts:
        hcounts[_state(h["attrs"].get("state", -1), HOST_STATES)] += 1
    for s in services:
        scounts[_state(s["attrs"].get("state", -1), SERVICE_STATES)] += 1

    unhandled_hosts = [
        h for h in hosts
        if h["attrs"].get("state", 0) != 0
        and not h["attrs"].get("acknowledgement")
        and not h["attrs"].get("downtime_depth")
    ]
    unhandled_svcs = [
        s for s in services
        if s["attrs"].get("state", 0) != 0
        and not s["attrs"].get("acknowledgement")
        and not s["attrs"].get("downtime_depth")
    ]

    if args.json:
        _out({
            "hosts":              hcounts,
            "services":           scounts,
            "unhandled_hosts":    len(unhandled_hosts),
            "unhandled_services": len(unhandled_svcs),
        }, True)
        return

    W = 52
    print("═" * W)
    print("  Icinga2 Overview")
    print("═" * W)
    print()
    print("  Hosts")
    for st, count in hcounts.items():
        print(f"    {st:<14} {count:>4}")
    print()
    print("  Services")
    for st, count in scounts.items():
        print(f"    {st:<14} {count:>4}")
    print()

    if unhandled_hosts:
        print(f"  Unhandled Host Problems ({len(unhandled_hosts)})")
        print("  " + "─" * (W - 2))
        for h in unhandled_hosts:
            a = h["attrs"]
            print(f"  {a['name']:<36} {_state(a['state'], HOST_STATES)}")
        print()

    if unhandled_svcs:
        print(f"  Unhandled Service Problems ({len(unhandled_svcs)})")
        print("  " + "─" * (W - 2))
        for s in unhandled_svcs:
            a = s["attrs"]
            print(f"  {a['host_name']}/{a['name']:<35} {_state(a['state'], SERVICE_STATES)}")
        print()

    if not unhandled_hosts and not unhandled_svcs:
        print("  ✓ All hosts and services are OK.")
        print()

    print("═" * W)
    log.info("Report done hosts=%d services=%d unhandled_h=%d unhandled_s=%d",
             len(hosts), len(services), len(unhandled_hosts), len(unhandled_svcs))


# ── Shell completions ─────────────────────────────────────────────────────────

_COMPLETION_BASH = r"""# i2 bash completion — generated by 'i2 completions bash'
# To activate for this session:
#   eval "$(i2 completions bash)"
# To activate permanently, add that line to ~/.bashrc

_i2_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    local cword=$COMP_CWORD

    # Walk tokens to find the subcommand (skip option values)
    local cmd="" dt_cmd="" skip_next=0
    local -a vflags=(--url --user --password --log-file)
    # --log-console is a boolean flag, not included in vflags
    local i w vo
    for ((i = 1; i < cword; i++)); do
        w="${COMP_WORDS[i]}"
        if ((skip_next)); then skip_next=0; continue; fi
        for vo in "${vflags[@]}"; do
            [[ "$w" == "$vo" ]] && { skip_next=1; break; }
        done
        ((skip_next)) && continue
        if [[ "$w" != -* ]]; then
            if   [[ -z "$cmd" ]];                              then cmd="$w"
            elif [[ "$cmd" == downtime && -z "$dt_cmd" ]];     then dt_cmd="$w"
            fi
        fi
    done

    case "$cmd" in
        "")
            if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W \
                    "--url --user --password --no-verify -k --json --debug --log-file --log-console --help" \
                    -- "$cur"))
            else
                COMPREPLY=($(compgen -W \
                    "hosts services downtime recheck report install completions" \
                    -- "$cur"))
            fi ;;
        hosts)
            case "$prev" in
                --state) COMPREPLY=($(compgen -W "UP DOWN UNREACHABLE" -- "$cur")) ;;
                *)       COMPREPLY=($(compgen -W "--state --filter --help" -- "$cur")) ;;
            esac ;;
        services)
            case "$prev" in
                --state) COMPREPLY=($(compgen -W "OK WARNING CRITICAL UNKNOWN" -- "$cur")) ;;
                *)       COMPREPLY=($(compgen -W "--host --name --state --filter --max-output --help" -- "$cur")) ;;
            esac ;;
        downtime)
            if [[ -z "$dt_cmd" ]]; then
                COMPREPLY=($(compgen -W "schedule remove list" -- "$cur"))
            else
                case "$dt_cmd" in
                    schedule) COMPREPLY=($(compgen -W \
                                  "--host --service --duration --comment --author --help" \
                                  -- "$cur")) ;;
                    remove)   COMPREPLY=($(compgen -W "--host --author --help" -- "$cur")) ;;
                    list)     COMPREPLY=($(compgen -W "--host --help" -- "$cur")) ;;
                esac
            fi ;;
        recheck)
            COMPREPLY=($(compgen -W "--host --service --help" -- "$cur")) ;;
        report|install)
            COMPREPLY=($(compgen -W "--help" -- "$cur")) ;;
        completions)
            COMPREPLY=($(compgen -W "bash zsh fish xonsh" -- "$cur")) ;;
    esac
}

complete -F _i2_complete i2
"""

_COMPLETION_ZSH = r"""#compdef i2
# i2 zsh completion — generated by 'i2 completions zsh'
# To activate for this session:
#   eval "$(i2 completions zsh)"
# To activate permanently, add that line to ~/.zshrc

_i2() {
    local context state state_descr line
    typeset -A opt_args

    local -a global_opts=(
        '--url=[API base URL]:url:'
        '--user=[API username]:user:'
        '--password=[API password]:password:'
        '(-k --no-verify)'{-k,--no-verify}'[Skip TLS certificate verification]'
        '--json[Emit JSON instead of a table]'
        '--debug[Enable debug logging]'
        '--log-file=[Write JSON logs to file]:log file:_files'
        '--log-console[Also print JSON logs to stderr]'
        '(- :)'{-h,--help}'[Show help and exit]'
    )

    _arguments -C \
        $global_opts \
        '1:command:->command' \
        '*::args:->args' && return

    case $state in
        command)
            local -a cmds=(
                'hosts:List hosts'
                'services:List services'
                'downtime:Schedule, remove, or list downtimes'
                'recheck:Trigger an immediate forced check'
                'report:Overview: state counts + unhandled problems'
                'install:Install i2 to a user-writable directory in PATH'
                'completions:Print shell completion script'
            )
            _describe 'command' cmds ;;
        args)
            case $line[1] in
                hosts)
                    _arguments \
                        '--state=[Host state]:state:(UP DOWN UNREACHABLE)' \
                        '--filter=[Raw Icinga2 filter expression]:expression:' \
                        '(- :)--help[Show help]' ;;
                services)
                    _arguments \
                        '--host=[Filter by host name]:hostname:' \
                        '--name=[Filter by service name]:name:' \
                        '--state=[Service state]:state:(OK WARNING CRITICAL UNKNOWN)' \
                        '--filter=[Raw Icinga2 filter expression]:expression:' \
                        '--max-output=[Truncate plugin output to N chars; 0=no limit]:chars:' \
                        '(- :)--help[Show help]' ;;
                downtime)
                    local context state line
                    _arguments -C \
                        '1:subcommand:->sub' \
                        '*::subargs:->subargs' && return
                    case $state in
                        sub)
                            local -a dt=(
                                'schedule:Schedule a downtime'
                                'remove:Remove downtimes for a host'
                                'list:List active downtimes'
                            )
                            _describe 'subcommand' dt ;;
                        subargs)
                            case $line[1] in
                                schedule)
                                    _arguments \
                                        '--host=[Host name]:hostname:' \
                                        '--service=[Service name]:service:' \
                                        '--duration=[Duration in seconds]:seconds:' \
                                        '--comment=[Comment]:comment:' \
                                        '--author=[Author]:author:' \
                                        '(- :)--help[Show help]' ;;
                                remove)
                                    _arguments \
                                        '--host=[Host name]:hostname:' \
                                        '--author=[Only remove by this author]:author:' \
                                        '(- :)--help[Show help]' ;;
                                list)
                                    _arguments \
                                        '--host=[Filter by host name]:hostname:' \
                                        '(- :)--help[Show help]' ;;
                            esac ;;
                    esac ;;
                recheck)
                    _arguments \
                        '--host=[Host name]:hostname:' \
                        '--service=[Service name]:service:' \
                        '(- :)--help[Show help]' ;;
                report|install)
                    _arguments '(- :)--help[Show help]' ;;
                completions)
                    _arguments '1:shell:(bash zsh fish xonsh)' ;;
            esac ;;
    esac
}

compdef _i2 i2
"""

_COMPLETION_FISH = """\
# i2 fish completion — generated by 'i2 completions fish'
# To install permanently:
#   i2 completions fish > ~/.config/fish/completions/i2.fish
# To activate for this session:
#   i2 completions fish | source

set -l i2_cmds hosts services downtime recheck report install completions
set -l dt_cmds schedule remove list

# Disable default file completion
complete -c i2 -f

# Global options (visible before any subcommand)
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -l url       -d 'API base URL'                       -r
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -l user      -d 'API username'                       -r
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -l password  -d 'API password'                       -r
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -s k -l no-verify -d 'Skip TLS certificate verification'
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -l json      -d 'Emit JSON instead of a table'
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -l debug     -d 'Enable debug logging'
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -l log-file    -d 'Write JSON logs to file'    -r
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -l log-console -d 'Also print JSON logs to stderr'

# Subcommands
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -a hosts       -d 'List hosts'
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -a services    -d 'List services'
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -a downtime    -d 'Schedule, remove, or list downtimes'
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -a recheck     -d 'Trigger an immediate forced check'
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -a report      -d 'Overview: state counts + unhandled problems'
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -a install     -d 'Install i2 to a user-writable directory in PATH'
complete -c i2 -n "not __fish_seen_subcommand_from $i2_cmds" -a completions -d 'Print shell completion script'

# hosts options
complete -c i2 -n "__fish_seen_subcommand_from hosts" -l state  -d 'Host state'                   -r -a 'UP DOWN UNREACHABLE'
complete -c i2 -n "__fish_seen_subcommand_from hosts" -l filter -d 'Raw Icinga2 filter expression' -r

# services options
complete -c i2 -n "__fish_seen_subcommand_from services" -l host       -d 'Filter by host name'                      -r
complete -c i2 -n "__fish_seen_subcommand_from services" -l name       -d 'Filter by service name'                    -r
complete -c i2 -n "__fish_seen_subcommand_from services" -l state      -d 'Service state'                             -r -a 'OK WARNING CRITICAL UNKNOWN'
complete -c i2 -n "__fish_seen_subcommand_from services" -l filter     -d 'Raw Icinga2 filter expression'              -r
complete -c i2 -n "__fish_seen_subcommand_from services" -l max-output -d 'Truncate plugin output to N chars; 0=no limit' -r

# downtime subcommands
complete -c i2 -n "__fish_seen_subcommand_from downtime; and not __fish_seen_subcommand_from $dt_cmds" -a schedule -d 'Schedule a downtime'
complete -c i2 -n "__fish_seen_subcommand_from downtime; and not __fish_seen_subcommand_from $dt_cmds" -a remove   -d 'Remove downtimes for a host'
complete -c i2 -n "__fish_seen_subcommand_from downtime; and not __fish_seen_subcommand_from $dt_cmds" -a list     -d 'List active downtimes'

# downtime schedule options
complete -c i2 -n "__fish_seen_subcommand_from downtime; and __fish_seen_subcommand_from schedule" -l host     -d 'Host name'           -r
complete -c i2 -n "__fish_seen_subcommand_from downtime; and __fish_seen_subcommand_from schedule" -l service  -d 'Service name'        -r
complete -c i2 -n "__fish_seen_subcommand_from downtime; and __fish_seen_subcommand_from schedule" -l duration -d 'Duration in seconds' -r
complete -c i2 -n "__fish_seen_subcommand_from downtime; and __fish_seen_subcommand_from schedule" -l comment  -d 'Comment'             -r
complete -c i2 -n "__fish_seen_subcommand_from downtime; and __fish_seen_subcommand_from schedule" -l author   -d 'Author'              -r

# downtime remove options
complete -c i2 -n "__fish_seen_subcommand_from downtime; and __fish_seen_subcommand_from remove" -l host   -d 'Host name'                 -r
complete -c i2 -n "__fish_seen_subcommand_from downtime; and __fish_seen_subcommand_from remove" -l author -d 'Only remove by this author' -r

# downtime list options
complete -c i2 -n "__fish_seen_subcommand_from downtime; and __fish_seen_subcommand_from list" -l host -d 'Filter by host name' -r

# recheck options
complete -c i2 -n "__fish_seen_subcommand_from recheck" -l host    -d 'Host name'    -r
complete -c i2 -n "__fish_seen_subcommand_from recheck" -l service -d 'Service name' -r

# completions shell argument
complete -c i2 -n "__fish_seen_subcommand_from completions" -a 'bash zsh fish xonsh'
"""

_COMPLETION_XONSH = """\
# i2 xonsh completion — generated by 'i2 completions xonsh'
# To activate for this session:
#   exec($(i2 completions xonsh))
# To activate permanently, add that line to ~/.xonshrc

def _i2_completer(prefix, line, begidx, endidx, ctx):
    import shlex
    try:
        tokens = shlex.split(line[:begidx])
    except ValueError:
        tokens = line[:begidx].split()

    value_flags = {'--url', '--user', '--password', '--log-file'}
    commands    = {'hosts', 'services', 'downtime', 'recheck', 'report', 'install', 'completions'}

    cmd, dt_cmd, skip_next = None, None, False
    for tok in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if tok in value_flags:
            skip_next = True
            continue
        if not tok.startswith('-'):
            if cmd is None:
                cmd = tok
            elif cmd == 'downtime' and dt_cmd is None:
                dt_cmd = tok

    last = tokens[-1] if len(tokens) > 1 else ''

    if cmd is None:
        if prefix.startswith('-'):
            opts = {'--url', '--user', '--password', '--no-verify', '-k',
                    '--json', '--debug', '--log-file', '--log-console', '--help'}
            return {o for o in opts if o.startswith(prefix)}
        return {c for c in commands if c.startswith(prefix)}

    if cmd == 'hosts':
        if last == '--state':
            return {s for s in ('UP', 'DOWN', 'UNREACHABLE') if s.startswith(prefix)}
        return {o for o in ('--state', '--filter', '--help') if o.startswith(prefix)}

    if cmd == 'services':
        if last == '--state':
            return {s for s in ('OK', 'WARNING', 'CRITICAL', 'UNKNOWN') if s.startswith(prefix)}
        return {o for o in ('--host', '--name', '--state', '--filter', '--max-output', '--help') if o.startswith(prefix)}

    if cmd == 'downtime':
        if dt_cmd is None:
            return {s for s in ('schedule', 'remove', 'list') if s.startswith(prefix)}
        if dt_cmd == 'schedule':
            opts = ('--host', '--service', '--duration', '--comment', '--author', '--help')
        elif dt_cmd == 'remove':
            opts = ('--host', '--author', '--help')
        elif dt_cmd == 'list':
            opts = ('--host', '--help')
        else:
            return set()
        return {o for o in opts if o.startswith(prefix)}

    if cmd == 'recheck':
        return {o for o in ('--host', '--service', '--help') if o.startswith(prefix)}

    if cmd == 'completions':
        return {s for s in ('bash', 'zsh', 'fish', 'xonsh') if s.startswith(prefix)}

    return set()

completer add i2 _i2_completer 0
"""

_COMPLETIONS = {
    "bash":  _COMPLETION_BASH,
    "zsh":   _COMPLETION_ZSH,
    "fish":  _COMPLETION_FISH,
    "xonsh": _COMPLETION_XONSH,
}


def cmd_completions(args) -> None:
    print(_COMPLETIONS[args.shell], end="")


# ── Install command ───────────────────────────────────────────────────────────

def _pick_install_dir() -> tuple[str, bool]:
    """Return (directory, already_in_PATH).

    Preference order:
      1. ~/.local/bin  — standard user bin, writable without sudo
      2. First writable directory already in PATH
      3. ~/.local/bin  — create it even if not yet in PATH
    """
    local_bin  = os.path.expanduser("~/.local/bin")
    path_dirs  = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]

    if local_bin in path_dirs and os.access(local_bin, os.W_OK):
        return local_bin, True

    for d in path_dirs:
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            return d, True

    return local_bin, local_bin in path_dirs


def cmd_install(args) -> None:
    src = os.path.realpath(__file__)
    install_dir, in_path = _pick_install_dir()
    dest = os.path.join(install_dir, "i2")

    os.makedirs(install_dir, exist_ok=True)

    if os.path.exists(dest) and os.path.realpath(dest) == src:
        if args.json:
            print(json.dumps({"installed": dest, "in_path": in_path, "note": "already up to date"}, indent=2))
        else:
            print(f"Already installed: {dest}")
        return

    try:
        shutil.copy2(src, dest)
    except shutil.SameFileError:
        if args.json:
            print(json.dumps({"installed": dest, "in_path": in_path, "note": "already up to date"}, indent=2))
        else:
            print(f"Already installed: {dest}")
        return

    os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if args.json:
        print(json.dumps({"installed": dest, "in_path": in_path}, indent=2))
    else:
        print(f"Installed: {dest}")

    if not in_path:
        profile = _detect_shell_profile()
        export  = f'export PATH="$HOME/.local/bin:$PATH"'
        print(f"\n  {install_dir} is not in PATH.")
        print(f"  Add it by appending this line to {profile}:")
        print(f"\n    {export}\n")
        print( "  Then start a new shell or run:  source " + profile)


def _detect_shell_profile() -> str:
    shell = os.environ.get("SHELL", "")
    if "zsh"  in shell: return "~/.zshrc"
    if "fish" in shell: return "~/.config/fish/config.fish"
    if "bash" in shell:
        return "~/.bash_profile" if os.path.exists(os.path.expanduser("~/.bash_profile")) else "~/.bashrc"
    return "~/.profile"


# ── Argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="i2",
        description="Icinga2 API command-line interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            environment variables:
              ICINGA_URL       API base URL         (default: https://localhost:5665)
              ICINGA_USER      API username         (default: root)
              ICINGA_PASSWORD  API password

            examples:
              i2 report
              i2 hosts --state DOWN
              i2 services --host web01 --state CRITICAL
              i2 services --name "apt"
              i2 downtime schedule --host web01 --duration 3600 --comment "Maintenance"
              i2 downtime list
              i2 downtime remove --host web01
              i2 recheck --host web01 --service apt
              i2 services --json | jq '.[].state'
        """),
    )

    g = p.add_argument_group("connection")
    g.add_argument("--url",       default=os.getenv("ICINGA_URL",  "https://localhost:5665"),
                   metavar="URL", help="API base URL (default: https://localhost:5665)")
    g.add_argument("--user",      default=os.getenv("ICINGA_USER", "root"),
                   metavar="USER", help="API username (default: root)")
    g.add_argument("--password",  default=os.getenv("ICINGA_PASSWORD"),
                   metavar="PASS", help="API password (prompted if omitted)")
    g.add_argument("-k", "--no-verify", action="store_true",
                   help="Skip TLS certificate verification")

    o = p.add_argument_group("output")
    o.add_argument("--json",        action="store_true", help="Emit JSON instead of a table")
    o.add_argument("--debug",       action="store_true", help="Debug logging with stack traces")
    o.add_argument("--log-file",    metavar="FILE",      default=_DEFAULT_LOG,
                   help="Log file path (default: ~/.local/share/i2/i2.log)")
    o.add_argument("--log-console", action="store_true",
                   help="Also print JSON logs to stderr")

    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = False

    # hosts
    ph = sub.add_parser("hosts", help="List hosts",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        description=textwrap.dedent("""\
                            List hosts, optionally filtered by state or a custom expression.

                            Icinga2 filter expressions use attribute names:
                              host.name, host.address, host.state, host.vars.<custom>

                            Examples:
                              i2 hosts
                              i2 hosts --state DOWN
                              i2 hosts --filter 'host.address == "10.0.0.1"'
                        """))
    ph.add_argument("--state",  metavar="STATE", help="UP | DOWN | UNREACHABLE")
    ph.add_argument("--filter", metavar="EXPR",  help="Raw Icinga2 filter expression")

    # services
    ps = sub.add_parser("services", help="List services",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        description=textwrap.dedent("""\
                            List services, with optional filters.
                            --name supports Icinga2 wildcards: apt, check_*, *ssl*

                            Examples:
                              i2 services --state CRITICAL
                              i2 services --host web01
                              i2 services --host web01 --name apt
                              i2 services --filter 'service.state >= 1 && !service.acknowledgement'
                        """))
    ps.add_argument("--host",        help="Filter by host name")
    ps.add_argument("--name",        help="Filter by service name (wildcards supported)")
    ps.add_argument("--state",       metavar="STATE", help="OK | WARNING | CRITICAL | UNKNOWN")
    ps.add_argument("--filter",      metavar="EXPR",  help="Raw Icinga2 filter expression")
    ps.add_argument("--max-output",  metavar="N", type=int, default=60,
                    help="Truncate plugin output to N chars; 0 = no limit (default: 60)")

    # downtime
    pd = sub.add_parser("downtime", help="Schedule, remove, or list downtimes")
    dtsub = pd.add_subparsers(dest="dt_command", metavar="subcommand")
    dtsub.required = True

    pds = dtsub.add_parser("schedule", help="Schedule a downtime",
                           formatter_class=argparse.RawDescriptionHelpFormatter,
                           description=textwrap.dedent("""\
                               Schedule a downtime on a host and all its services.
                               Use --service to target a single service instead.

                               Examples:
                                 i2 downtime schedule --host web01 --duration 3600
                                 i2 downtime schedule --host web01 --service apt --duration 600
                           """))
    pds.add_argument("--host",     required=True)
    pds.add_argument("--service",  help="Service name (default: host + all services)")
    pds.add_argument("--duration", type=int, default=7200, metavar="SECS",
                     help="Duration in seconds (default: 7200 = 2 h)")
    pds.add_argument("--comment",  default="Scheduled via i2 CLI")
    pds.add_argument("--author",   default=os.getenv("USER", "i2-cli"),
                     help="Author name (default: $USER)")

    pdr = dtsub.add_parser("remove", help="Remove downtimes for a host")
    pdr.add_argument("--host",   required=True)
    pdr.add_argument("--author", help="Only remove downtimes by this author")

    pdl = dtsub.add_parser("list", help="List active downtimes")
    pdl.add_argument("--host", help="Filter by host name")

    # recheck
    pr = sub.add_parser("recheck", help="Trigger an immediate forced check",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        description=textwrap.dedent("""\
                            Force Icinga2 to run a check immediately, bypassing the normal
                            check interval. Useful after resolving an issue to confirm recovery.

                            Examples:
                              i2 recheck --host web01
                              i2 recheck --host web01 --service apt
                        """))
    pr.add_argument("--host",    required=True)
    pr.add_argument("--service", help="Service name (default: recheck the host)")

    # report
    sub.add_parser("report", help="Overview: state counts + unhandled problems",
                   formatter_class=argparse.RawDescriptionHelpFormatter,
                   description=textwrap.dedent("""\
                       Print host/service state counts and a list of unhandled problems
                       (not acknowledged, not in downtime).
                   """))

    # install
    sub.add_parser("install", help="Install i2 to a user-writable directory in PATH",
                   formatter_class=argparse.RawDescriptionHelpFormatter,
                   description=textwrap.dedent("""\
                       Copy this script to the best available user-writable directory in PATH.
                       Prefers ~/.local/bin; falls back to the first writable PATH entry.
                       If the target directory is not yet in PATH, prints the export line to add.
                   """))

    # completions
    pc = sub.add_parser("completions", help="Print shell completion script",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        description=textwrap.dedent("""\
                            Print a completion script for the given shell and exit.

                            Bash:   eval "$(i2 completions bash)"
                            Zsh:    eval "$(i2 completions zsh)"
                            Fish:   i2 completions fish > ~/.config/fish/completions/i2.fish
                            Xonsh:  exec($(i2 completions xonsh))

                            Add the appropriate line to your shell's startup file to make
                            completions permanent.
                        """))
    pc.add_argument("shell", choices=["bash", "zsh", "fish", "xonsh"],
                    help="Target shell")

    return p


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = _build_parser()
    args = parser.parse_args()

    if not getattr(args, "command", None):
        parser.print_help()
        sys.exit(0)

    if args.command == "install":
        cmd_install(args)
        return

    if args.command == "completions":
        cmd_completions(args)
        return

    log = _setup_logging(args.debug, args.log_file, args.log_console)

    password = args.password
    if not password:
        try:
            password = getpass.getpass(f"Password for {args.user}@{args.url}: ")
        except KeyboardInterrupt:
            print()
            sys.exit(1)

    try:
        client = Icinga2Client(args.url, args.user, password, verify=not args.no_verify)
        log.debug("Connected url=%s user=%s verify=%s", args.url, args.user, not args.no_verify)

        match args.command:
            case "hosts":
                cmd_hosts(client, args, log)
            case "services":
                cmd_services(client, args, log)
            case "downtime":
                match args.dt_command:
                    case "schedule": cmd_downtime_schedule(client, args, log)
                    case "remove":   cmd_downtime_remove(client, args, log)
                    case "list":     cmd_downtime_list(client, args, log)
            case "recheck":
                cmd_recheck(client, args, log)
            case "report":
                cmd_report(client, args, log)

    except ApiError as e:
        log.error("API error status=%d msg=%s", e.status_code, e, exc_info=args.debug)
        _err(str(e), args.json, status_code=e.status_code)
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if "certificate" in reason.lower() or "ssl" in reason.lower():
            log.error("TLS error: %s", reason, exc_info=args.debug)
            _err("TLS verification failed — use --no-verify to skip.", args.json)
        else:
            log.error("Connection error: %s", reason, exc_info=args.debug)
            _err(f"Could not connect to {args.url} — is Icinga2 running?", args.json, url=args.url)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except Exception as e:
        log.error("Unexpected error: %s", e, exc_info=True)
        _err(str(e), args.json)


if __name__ == "__main__":
    main()
