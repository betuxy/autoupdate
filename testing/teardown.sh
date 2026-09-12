#!/bin/bash
# Tear down the test environment.
# Run from the project root:  bash testing/teardown.sh
#   --volumes   also remove named volumes (icinga-master PKI/config data)
#   --keys      also delete the generated SSH key pair in testing/keys/
#   --all       implies --volumes and --keys
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REMOVE_VOLUMES=false
REMOVE_KEYS=false
for arg in "$@"; do
    case "$arg" in
        --volumes|-v) REMOVE_VOLUMES=true ;;
        --keys|-k)    REMOVE_KEYS=true ;;
        --all|-a)     REMOVE_VOLUMES=true; REMOVE_KEYS=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

cd "$SCRIPT_DIR"

COMPOSE_ARGS=()
$REMOVE_VOLUMES && COMPOSE_ARGS+=(--volumes)

echo "Stopping and removing containers..."
podman compose down "${COMPOSE_ARGS[@]}"

if $REMOVE_KEYS; then
    echo "Removing SSH key pair..."
    rm -f keys/ansible_ed25519 keys/ansible_ed25519.pub
fi

echo "Done."
