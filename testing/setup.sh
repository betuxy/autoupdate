#!/bin/bash
# Bootstrap the test environment.
# Run from the project root:  bash testing/setup.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_DIR="$SCRIPT_DIR/keys"

echo "=== Autoupdate playbook — test environment setup ==="

# 1. Generate SSH key pair for Ansible → agent access
mkdir -p "$KEY_DIR"
if [ ! -f "$KEY_DIR/ansible_ed25519" ]; then
    echo "[1/3] Generating SSH key pair..."
    ssh-keygen -t ed25519 -f "$KEY_DIR/ansible_ed25519" -N "" -C "ansible-test-autoupdate"
else
    echo "[1/3] SSH key pair already exists, skipping."
fi

# 2. Build and start containers
echo "[2/3] Building and starting containers (this may take a few minutes)..."
cd "$SCRIPT_DIR"
podman compose up -d --build

# 3. Wait for Icinga2 API to become healthy
echo "[3/3] Waiting for Icinga2 API to be ready..."
MAX_WAIT=120
ELAPSED=0
until curl -sk -u "autoupdate:Pain-Frequently-Mother-Sun3-Instead" \
       "https://localhost:5665/v1/status" > /dev/null 2>&1; do
    if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
        echo "ERROR: Icinga2 API did not become ready within ${MAX_WAIT}s."
        podman compose logs icinga-master
        exit 1
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

echo ""
echo "=== Environment ready ==="
echo ""
echo "Next steps (run from project root):"
echo ""
echo "  # Simulate CRITICAL apt service on both agents:"
echo "  ansible-playbook -i testing/inventory.yml testing/simulate-critical.yml"
echo ""
echo "  # Run the autoupdate playbook against the test environment:"
echo "  source ansible-venv/bin/activate"
echo "  ansible-playbook -i testing/inventory.yml autoupdate.yml"
echo ""
echo "  # Dry run:"
echo "  ansible-playbook -i testing/inventory.yml --check autoupdate.yml"
echo ""
echo "  # Limit to one agent:"
echo "  ansible-playbook -i testing/inventory.yml -l agent1 autoupdate.yml"
echo ""
echo "  # Tear down:"
echo "  cd testing && podman compose down"
