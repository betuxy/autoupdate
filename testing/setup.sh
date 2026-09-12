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

REQUIRED_PORTS=(5665 8080 2222 2223)
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

# 1. Generate SSH key pair for Ansible → agent access
mkdir -p "$KEY_DIR"
if [ ! -f "$KEY_DIR/ansible_ed25519" ]; then
    echo "[1/3] Generating SSH key pair..."
    ssh-keygen -t ed25519 -f "$KEY_DIR/ansible_ed25519" -N "" -C "ansible-test-autoupdate"
else
    echo "[1/3] SSH key pair already exists, skipping."
fi

# 2. Build and start containers
cd "$SCRIPT_DIR"
if $FORCE_BUILD; then
    echo "[2/3] Building and starting containers (forced rebuild)..."
    podman compose up -d --build
else
    echo "[2/3] Starting containers (building images only if not cached)..."
    podman compose up -d
fi

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
