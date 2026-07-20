#!/usr/bin/env bash
# tup-cloud.sh — local development helper for the tup-cloud compose stack.
#
#   ./tup-cloud.sh start              start (or create) all containers
#   ./tup-cloud.sh stop               stop containers (data is kept)
#   ./tup-cloud.sh rebuild [service]  rebuild images and restart (all or one service)
#   ./tup-cloud.sh purge              stop and DELETE everything: containers,
#                                     volumes (Postgres data, Telegram session,
#                                     cache, spool) and local images
#   ./tup-cloud.sh status             container status + health
#   ./tup-cloud.sh logs [service]     follow logs (default: backend)

set -euo pipefail
cd "$(dirname "$0")"

compose() { docker compose "$@"; }

require_docker() {
    if ! docker info >/dev/null 2>&1; then
        echo "Docker daemon is not running." >&2
        if [ -d "/Applications/Rancher Desktop.app" ]; then
            echo "Starting Rancher Desktop…" >&2
            open -a "Rancher Desktop"
            for _ in $(seq 1 60); do
                docker info >/dev/null 2>&1 && return 0
                sleep 3
            done
        fi
        echo "Could not reach Docker — start it manually and retry." >&2
        exit 1
    fi
}

require_env() {
    if [ ! -f .env ]; then
        echo "No .env found. Copy .env.example and fill in your Telegram credentials:" >&2
        echo "  cp .env.example .env" >&2
        exit 1
    fi
}

wait_healthy() {
    echo -n "Waiting for backend"
    for _ in $(seq 1 30); do
        if curl -fs http://localhost:8080/api/health >/dev/null 2>&1; then
            echo " — up: http://localhost:8080"
            return 0
        fi
        echo -n "."
        sleep 2
    done
    echo
    echo "Backend did not become healthy; check: ./tup-cloud.sh logs backend" >&2
    return 1
}

cmd="${1:-}"
shift || true

case "$cmd" in
    start)
        require_docker
        require_env
        compose up -d
        wait_healthy
        ;;
    stop)
        require_docker
        compose stop
        echo "Stopped. Data volumes are kept — './tup-cloud.sh start' resumes."
        ;;
    rebuild)
        require_docker
        require_env
        compose up -d --build "$@"
        wait_healthy
        ;;
    purge)
        require_docker
        echo "This DELETES all containers, images, and volumes:"
        echo "  - Postgres data (users, drives, VFS index, logs)"
        echo "  - Telegram MTProto session (bot re-logs-in on next start)"
        echo "  - file cache and upload spool"
        read -r -p "Type 'purge' to confirm: " answer
        if [ "$answer" != "purge" ]; then
            echo "Aborted."
            exit 1
        fi
        compose down --volumes --rmi local --remove-orphans
        echo "Purged. './tup-cloud.sh start' rebuilds from scratch."
        ;;
    status)
        require_docker
        compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
        ;;
    logs)
        require_docker
        compose logs -f "${1:-backend}"
        ;;
    *)
        sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
