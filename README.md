# Autoupdate Playbook

Queries the Icinga2 API for hosts with `apt` or `needrestart` services in CRITICAL state,
verifies each host is actually reachable (guards against stale/satellite-down reports),
then updates and reboots each reachable host one at a time, waiting for full monitoring
recovery before moving to the next. All steps are appended to a persistent log file.

## Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ Play 1 — localhost                                              │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 01-query-icinga.yml                                      │   │
│  │                                                          │   │
│  │  Icinga2 API ──► filter: apt/needrestart CRITICAL        │   │
│  │                        │                                 │   │
│  │                        ▼                                 │   │
│  │               extract host_name (unique)                 │   │
│  │                        │                                 │   │
│  │                        ▼                                 │   │
│  │               add_host → group: needs_update             │   │
│  │               log: "Icinga CRITICAL hosts: ..."          │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                             │
                  -l limit applied here
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Play 2 — group: needs_update   ignore_unreachable: true         │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 02-check-reachability.yml          (per host, parallel)  │   │
│  │                                                          │   │
│  │  log: "Checking SSH reachability"                        │   │
│  │            │                                             │   │
│  │            ▼                                             │   │
│  │  wait_for_connection (timeout: 10 s)                     │   │
│  │            │                           │                 │   │
│  │         SUCCESS                    UNREACHABLE           │   │
│  │            │                           │                 │   │
│  │            ▼                           ▼                 │   │
│  │  add_host → reachable_for_update  host excluded          │   │
│  │  log: "REACHABLE - queued"        from play              │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Play 3 — localhost                                              │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 03-report-unreachable.yml                                │   │
│  │                                                          │   │
│  │  attempted − reachable = unreachable                     │   │
│  │  log + warn: "UNREACHABLE - skipped" (per host)          │   │
│  │  log + show: final update queue                          │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Play 4 — group: reachable_for_update   serial: 1               │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 04-set-downtime.yml                          [--check: SKIP] │
│  │  log + Icinga API ──► schedule-downtime (host+services)  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                             │                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 05-update-reboot.yml                                     │   │
│  │  apt cache refresh        [--check: RUN]                 │   │
│  │  apt-get --simulate       [--check: RUN → shows pkgs]    │   │
│  │  apt full-upgrade         [--check: SKIP]                │   │
│  │  reboot + wait for SSH    [--check: SKIP]                │   │
│  └──────────────────────────────────────────────────────────┘   │
│                             │                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 06-check-recovery.yml                        [--check: SKIP] │
│  │                                                          │   │
│  │  reschedule-check (Host)  ──► force fresh host check     │   │
│  │  reschedule-check (Service) ► force fresh service checks │   │
│  │  pause (autoupdate_recheck_delay) ► let checks run       │   │
│  │            │                                             │   │
│  │            ▼                                             │   │
│  │  poll Icinga2 API ──► no CRITICAL outside downtime?      │   │
│  │       ▲    │                    │                        │   │
│  │       └────┘ retry              ▼                        │   │
│  │    (every 60 s,          OK → continue                   │   │
│  │     up to 30 min)        FAIL → playbook stops,          │   │
│  │                                downtime kept active       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                             │                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 07-remove-downtime.yml                       [--check: SKIP] │
│  │  Icinga API ──► remove-downtime (host + services)        │   │
│  │  log: "Downtime removed - update complete"               │   │
│  └──────────────────────────────────────────────────────────┘   │
│                             │                                   │
│                   next host (serial: 1)                         │
└─────────────────────────────────────────────────────────────────┘
```

## Why the reachability check?

Icinga can show a host as UP with stale service states when the satellite monitoring it
has gone down and no host dependency is defined. The playbook would then attempt to update
a host that is actually unreachable. Play 2 catches this by probing SSH directly — if the
connection times out, the host is logged as unreachable and excluded from the update queue.

## Log file

Every significant event is appended to `autoupdate_log_path` (default:
`logs/autoupdate.log` relative to the playbook directory) in the format:

```
2026-09-10T12:00:00Z | localhost    | Autoupdate run started
2026-09-10T12:00:01Z | localhost    | Icinga CRITICAL hosts: host1, host2, host3
2026-09-10T12:00:02Z | host1        | Checking SSH reachability
2026-09-10T12:00:02Z | host2        | Checking SSH reachability
2026-09-10T12:00:02Z | host3        | Checking SSH reachability
2026-09-10T12:00:03Z | host1        | REACHABLE - queued for update
2026-09-10T12:00:12Z | host2        | REACHABLE - queued for update
2026-09-10T12:00:12Z | host3        | UNREACHABLE - skipped
2026-09-10T12:00:12Z | localhost    | Update queue: host1, host2
2026-09-10T12:00:13Z | host1        | Scheduling Icinga downtime
2026-09-10T12:00:13Z | host1        | Icinga downtime scheduled (8 objects)
2026-09-10T12:00:14Z | host1        | Starting apt full-upgrade
2026-09-10T12:00:14Z | host1        | PKG libssl3 3.0.2-0ubuntu1.12 -> 3.0.2-0ubuntu1.15
2026-09-10T12:00:14Z | host1        | PKG libssl-dev 3.0.2-0ubuntu1.12 -> 3.0.2-0ubuntu1.15
2026-09-10T12:00:14Z | host1        | PKG curl 7.81.0-1ubuntu1.15 -> 7.81.0-1ubuntu1.16
2026-09-10T12:00:14Z | host1        | PKG python3-minimal (new) -> 3.10.6-1
2026-09-10T12:01:30Z | host1        | Rebooting
2026-09-10T12:02:15Z | host1        | Back online after reboot
2026-09-10T12:02:15Z | host1        | Polling Icinga for service recovery
2026-09-10T12:03:20Z | host1        | All services recovered
2026-09-10T12:03:21Z | host1        | Downtime removed - update complete
...
```

## Usage

```bash
# Activate the venv first
source ansible-venv/bin/activate

# Update all hosts Icinga flags as needing updates
ansible-playbook autoupdate.yml

# Limit to specific hosts (intersected with Icinga results)
ansible-playbook -l webserver1,webserver2 autoupdate.yml

# Limit to an inventory group
ansible-playbook -l webservers autoupdate.yml

# Dry run — shows which hosts would be updated and what packages are pending
ansible-playbook --check autoupdate.yml
ansible-playbook --check -l webserver1 autoupdate.yml
```

The `-l` flag is safe: the playbook only processes hosts that appear in **both** the Icinga
CRITICAL results and the `-l` pattern. Excluded hosts are never touched.

## Check mode (dry run)

Running with `--check` performs all read and probe operations but skips every action that
would change state on a host or in Icinga:

| Task | `--check` behaviour |
|---|---|
| Icinga API query | **runs** — live data needed to drive the report |
| SSH reachability probe | **runs** — non-destructive, gives accurate results |
| apt cache refresh | **runs** — needed so the simulate step is accurate |
| `apt-get --simulate dist-upgrade` | **runs** — shows exactly what would be installed |
| apt full-upgrade | skipped (native check mode) |
| reboot | skipped (native check mode) |
| Icinga schedule-downtime | skipped (`when: not ansible_check_mode`) |
| Icinga recovery poll | skipped (`when: not ansible_check_mode`) |
| Icinga remove-downtime | skipped (`when: not ansible_check_mode`) |
| Log file writes | skipped (`when: not ansible_check_mode`) |

In practice a `--check` run answers: *"Which hosts does Icinga think need updates, are they
actually reachable, and what packages would be installed?"*

## Failure behaviour

If `06-check-recovery.yml` exhausts all retries, the playbook fails for that host and
**stops**. The Icinga downtime is intentionally left active so the host stays silenced
during manual investigation. Subsequent hosts in the queue are not processed.

## Variables

All variables live in `group_vars/all/autoupdate.yml`.

### Icinga2 API

| Variable | Default | Description |
|---|---|---|
| `icinga_api_url` | `https://localhost:5665` | Base URL of the Icinga2 API |
| `icinga_api_user` | `root` | API user |
| `icinga_api_password` | `""` | API password — **store in vault** |
| `icinga_api_validate_certs` | `true` | Verify TLS certificate |
| `icinga_api_no_log` | `false` | Set to `true` to suppress credentials from verbose output |

### Downtime

| Variable | Default | Description |
|---|---|---|
| `icinga_downtime_duration` | `7200` | Downtime window in seconds (default 2 h) |
| `icinga_downtime_author` | `ansible` | Author field on the scheduled downtime |
| `icinga_downtime_comment` | `Automated update and reboot by autoupdate playbook` | Comment on the scheduled downtime |

### Reachability

| Variable | Default | Description |
|---|---|---|
| `autoupdate_reachability_timeout` | `10` | Seconds to wait for SSH before marking host unreachable |

### Update and reboot

| Variable | Default | Description |
|---|---|---|
| `autoupdate_pre_reboot_delay` | `5` | Seconds to wait before issuing the reboot |
| `autoupdate_reboot_timeout` | `600` | Seconds to wait for the host to come back after reboot |

### Recovery polling

| Variable | Default | Description |
|---|---|---|
| `autoupdate_recheck_delay` | `30` | Seconds to wait after forcing Icinga rechecks before starting the recovery poll — should cover the time the check engine needs to execute all service checks |
| `autoupdate_recovery_retries` | `30` | Number of Icinga poll attempts before giving up |
| `autoupdate_recovery_delay` | `60` | Seconds between each poll attempt |

Maximum recovery wait = `autoupdate_recheck_delay + autoupdate_recovery_retries × autoupdate_recovery_delay` (default ~31 min).

### Logging

| Variable | Default | Description |
|---|---|---|
| `autoupdate_log_path` | `{{ playbook_dir }}/logs/autoupdate.log` | Path to the append-only log file |

---

## Test environment

A self-contained Podman environment for end-to-end testing without a real Icinga2 installation.

### Services

| Container | Image | Port | Purpose |
|---|---|---|---|
| `icinga-master` | `icinga/icinga2` | 5665 | Icinga2 API |
| `icingaweb2` | `icinga/icingaweb2` | 8080 | Web console |
| `redis` | `redis:7-alpine` | — | IcingaDB backend |
| `icingadb` | `icinga/icingadb` | — | IcingaDB bridge |
| `mariadb` | `mariadb:10.11` | — | Persistent storage |
| `agent1` | custom Ubuntu 24.04 | 2222 | SSH update target |
| `agent2` | custom Ubuntu 24.04 | 2223 | SSH update target |

Agent containers have an `ansible` user with passwordless sudo. `/sbin/reboot` is overridden to
`kill 1` so `ansible.builtin.reboot` works — the `restart: always` policy brings the container
back up automatically.

### Quick start

```bash
# Start (builds images only if not cached)
bash testing/setup.sh

# Force rebuild of agent image
bash testing/setup.sh --build

# Wipe everything and start fresh
bash testing/setup.sh --reset
```

IcingaWeb2 is at **http://localhost:8080** — log in with `icingaadmin` / `icinga`.

### Simulate CRITICAL states

```bash
# Push apt + needrestart to CRITICAL on both agents
ansible-playbook -i testing/inventory.yml testing/simulate-critical.yml

# Target a single host
ansible-playbook -i testing/inventory.yml -e 'sim_hosts=agent1' testing/simulate-critical.yml

# Clear back to OK
ansible-playbook -i testing/inventory.yml testing/clear-critical.yml
```

### Run the playbook against the test environment

```bash
source ansible-venv/bin/activate
ansible-playbook -i testing/inventory.yml autoupdate.yml
ansible-playbook -i testing/inventory.yml --check autoupdate.yml
ansible-playbook -i testing/inventory.yml -l agent1 autoupdate.yml
```

### Teardown

```bash
bash testing/teardown.sh              # stop and remove containers
bash testing/teardown.sh --volumes    # also remove named volumes (Icinga PKI / MariaDB data)
bash testing/teardown.sh --keys       # also delete the generated SSH key pair
bash testing/teardown.sh --all        # everything above
```

---

## i2 — Icinga2 CLI

`testing/i2.py` is a standalone Icinga2 API CLI with no external dependencies (pure stdlib).

### Install

```bash
python3 testing/i2.py install
```

Copies the script to the first writable directory in `PATH` (prefers `~/.local/bin`).
If the target directory is not in `PATH`, the command prints the export line to add.

### Commands

```
i2 hosts                                         # list all hosts
i2 hosts --state DOWN                            # filter by state
i2 hosts --filter 'host.vars.env == "prod"'      # raw Icinga2 filter

i2 services                                      # list all services
i2 services --host web01 --state CRITICAL
i2 services --name "apt"                         # wildcards supported

i2 downtime schedule --host web01 --duration 3600 --comment "Maintenance"
i2 downtime list
i2 downtime list --host web01
i2 downtime remove --host web01

i2 recheck --host web01
i2 recheck --host web01 --service apt

i2 report                                        # state counts + unhandled problems
```

### Global flags

| Flag | Default | Description |
|---|---|---|
| `--url URL` | `https://localhost:5665` | API base URL (`ICINGA_URL`) |
| `--user USER` | `root` | API username (`ICINGA_USER`) |
| `--password PASS` | prompted | API password (`ICINGA_PASSWORD`) |
| `--no-verify` | off | Skip TLS certificate verification |
| `--json` | off | Emit JSON instead of a table |
| `--debug` | off | Debug logging with stack traces |
| `--log-file FILE` | stderr | Write JSON logs to file |

Environment variables in parentheses override the corresponding flag.
Global flags must appear **before** the subcommand name.

### Shell completions

```bash
# Bash — add to ~/.bashrc
eval "$(i2 completions bash)"

# Zsh — add to ~/.zshrc
eval "$(i2 completions zsh)"

# Fish — install permanently
i2 completions fish > ~/.config/fish/completions/i2.fish

# Xonsh — add to ~/.xonshrc
exec($(i2 completions xonsh))
```
