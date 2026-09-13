#!/bin/bash
# Runs once per container boot (before icinga2.service).
# On first boot: performs icinga2 node setup using the ticket bind-mounted at /tmp/icinga-ticket.
# On subsequent boots: skips setup (cert already present) so icinga2 starts normally.
set -e

CN="$(hostname)"
CERT="/var/lib/icinga2/certs/${CN}.crt"

if [ -f "$CERT" ]; then
    echo "setup-icinga-agent: already configured (${CERT} exists), skipping."
    exit 0
fi

if [ ! -f /tmp/icinga-ticket ]; then
    echo "setup-icinga-agent: no ticket at /tmp/icinga-ticket — icinga2 will not be configured."
    exit 0
fi

TICKET=$(cat /tmp/icinga-ticket)
TRUSTED_CERT=/var/lib/icinga2/certs/trusted-parent.crt

echo "setup-icinga-agent: fetching trusted cert from icinga-master ..."
# icinga2 drops privileges to the 'nagios' user before writing, and pki save-cert
# does not create its target directory itself — it must already exist and be
# writable by that user.
install -d -o nagios -g nagios -m 0700 "$(dirname "$TRUSTED_CERT")"
icinga2 pki save-cert --trustedcert "$TRUSTED_CERT" --host icinga-master --port 5665

echo "setup-icinga-agent: running node setup for CN=${CN} ..."

icinga2 node setup \
    --cn          "$CN" \
    --zone        "$CN" \
    --parent_host icinga-master \
    --endpoint    "icinga-master,icinga-master,5665" \
    --parent_zone master \
    --ticket      "$TICKET" \
    --trustedcert "$TRUSTED_CERT" \
    --accept-commands

echo "setup-icinga-agent: done."
