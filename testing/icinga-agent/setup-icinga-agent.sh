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
echo "setup-icinga-agent: running node setup for CN=${CN} ..."

icinga2 node setup \
    --cn          "$CN" \
    --zone        "$CN" \
    --master_host icinga-master \
    --endpoint    "icinga-master,icinga-master,5665" \
    --parent_zone icinga-master \
    --ticket      "$TICKET" \
    --accept-commands

echo "setup-icinga-agent: done."
