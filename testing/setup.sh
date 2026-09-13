#!/bin/bash
# Bootstrap the test environment.
# Run from the project root:  bash testing/setup.sh [--build|-b] [--reset|-r]
#   --build|-b   force rebuild of container images even if already cached
#   --reset|-r   tear down existing environment first (passes --volumes to teardown)
set -e

FORCE_BUILD=false
RESET=false
for arg in "$@"; do
    case "$arg" in
        --build|-b) FORCE_BUILD=true ;;
        --reset|-r) RESET=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_DIR="$SCRIPT_DIR/keys"

echo "=== Autoupdate playbook — test environment setup ==="

# Preflight checks
if ! command -v podman &>/dev/null; then
    echo "ERROR: podman is not installed or not in PATH." >&2
    exit 1
fi
if ! podman compose version &>/dev/null; then
    echo "ERROR: podman-compose provider not found." >&2
    echo "Install it with:  pip install podman-compose" >&2
    echo "             or:  sudo apt install podman-compose" >&2
    exit 1
fi

if $RESET; then
    echo "Resetting existing environment..."
    bash "$SCRIPT_DIR/teardown.sh" --volumes
fi

REQUIRED_PORTS=(5665 8080 2222 2223 2224 2225)
PORT_ERRORS=0
for port in "${REQUIRED_PORTS[@]}"; do
    if ss -tlnH "sport = :$port" 2>/dev/null | grep -q .; then
        echo "ERROR: port $port is already in use." >&2
        PORT_ERRORS=$((PORT_ERRORS + 1))
    fi
done
if [ "$PORT_ERRORS" -gt 0 ]; then
    echo "Free the ports above and re-run, or stop existing containers with:" >&2
    echo "  cd testing && podman compose down" >&2
    exit 1
fi

mkdir -p "$KEY_DIR"
cd "$SCRIPT_DIR"

# 1. Generate SSH key pair for Ansible → agent access
if [ ! -f "$KEY_DIR/ansible_ed25519" ]; then
    echo "[1/4] Generating SSH key pair..."
    ssh-keygen -t ed25519 -f "$KEY_DIR/ansible_ed25519" -N "" -C "ansible-test-autoupdate"
else
    echo "[1/4] SSH key pair already exists, skipping."
fi

# 2. Start master + supporting services; agents come later (need PKI tickets first)
if $FORCE_BUILD; then
    echo "[2/4] Building and starting master + support services (forced rebuild)..."
    podman compose up -d --build icinga-master redis mariadb icingadb icingaweb2
else
    echo "[2/4] Starting master + support services..."
    podman compose up -d icinga-master redis mariadb icingadb icingaweb2
fi

# 3. Wait for Icinga2 API, then generate per-agent PKI tickets
echo "[3/4] Waiting for Icinga2 API to be ready..."
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

echo "      Generating Icinga2 PKI tickets for agents..."
for agent in agent1 agent2; do
    podman exec autoupdate_icinga-master \
        icinga2 pki ticket --cn "$agent" \
        > "$KEY_DIR/${agent}.ticket"
    echo "      Ticket for $agent written to keys/${agent}.ticket"
done

# 4. Start agents (they read their ticket on first boot via setup-icinga-agent.service)
if $FORCE_BUILD; then
    echo "[4/4] Starting agent containers (forced rebuild)..."
    podman compose up -d --build agent1 agent2
else
    echo "[4/4] Starting agent containers..."
    podman compose up -d agent1 agent2
fi

echo ""
echo "=== Environment ready ==="
echo ""
echo "Agents connect to the master and run icinga2 node setup on first boot."
echo "Allow ~30 s for agents to appear as UP in Icinga."
echo ""
echo "Next steps (run from project root):"
echo ""
echo "  # Check service states (agents run real check_apt + check_needrestart):"
echo "  i2 -k services"
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
echo "  bash testing/teardown.sh"
