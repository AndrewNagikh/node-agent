#!/bin/sh
# Container entrypoint: `orchestrator` or `node`.
#
# Mirrors run-agent.sh's contract -- the only address you have to supply is
# where the orchestrator is. This machine's own address is detected, because a
# machine knows its own IP and asking the operator for it is asking for
# something the program can find out.
set -eu

ROLE="${1:-node}"
shift 2>/dev/null || true

# This container's LAN address, as other machines will reach it.
#
# With `network_mode: host` this is the host's real address and everything
# works. Without it, this returns the container's private bridge IP, which no
# other machine can route to -- so we refuse rather than register an address
# that silently breaks the cluster later. Set ADVERTISE_HOST explicitly in
# that case (Docker Desktop on macOS/Windows has no host networking).
detect_lan_ip() {
    ip -4 route get 1.1.1.1 2>/dev/null \
        | awk '{ for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit } }'
}

is_container_private_ip() {
    # Docker's default bridge pools. Not exhaustive -- a custom pool is exactly
    # the case where the operator should be setting ADVERTISE_HOST anyway.
    case "$1" in
        172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 0 ;;
        *) return 1 ;;
    esac
}

case "$ROLE" in
orchestrator)
    PORT="${PORT:-9000}"
    echo "entrypoint: orchestrator on 0.0.0.0:${PORT}, models=${MODELS_DIR}"
    exec /app/orchestrator \
        --listen "0.0.0.0:${PORT}" \
        --models-dir "${MODELS_DIR}" \
        "$@"
    ;;

node)
    if [ -z "${ORCHESTRATOR:-}" ]; then
        echo "entrypoint: set ORCHESTRATOR=http://<orchestrator-host>:9000" >&2
        exit 1
    fi

    # A label, not an address. Only has to be unique in the cluster.
    NODE_ID="${NODE_ID:-$(hostname)}"
    PORT="${PORT:-9001}"

    if [ -z "${ADVERTISE_HOST:-}" ]; then
        ADVERTISE_HOST="$(detect_lan_ip || true)"
        if [ -z "$ADVERTISE_HOST" ]; then
            echo "entrypoint: could not detect this machine's IP -- set ADVERTISE_HOST" >&2
            exit 1
        fi
        if is_container_private_ip "$ADVERTISE_HOST"; then
            # One stream, or the lines interleave and print out of order.
            {
                echo "entrypoint: detected $ADVERTISE_HOST, a Docker bridge address."
                echo "entrypoint: other machines cannot reach it, so registering it would"
                echo "entrypoint: join a cluster that cannot talk back."
                echo "entrypoint: use network_mode: host, or set ADVERTISE_HOST to this"
                echo "entrypoint: machine's LAN IP."
            } >&2
            exit 1
        fi
    fi

    echo "entrypoint: node_id=${NODE_ID} advertise=${ADVERTISE_HOST}:${PORT}"
    echo "entrypoint: orchestrator=${ORCHESTRATOR} models=${MODELS_DIR}"
    exec /app/node_agent \
        --listen "0.0.0.0:${PORT}" \
        --advertise-host "${ADVERTISE_HOST}" \
        --orchestrator "${ORCHESTRATOR}" \
        --node-id "${NODE_ID}" \
        --models-dir "${MODELS_DIR}" \
        "$@"
    ;;

*)
    # Anything else runs verbatim, so the image doubles as a way to poke at the
    # binaries (`docker run ... node-agent /app/orchestrator --help`).
    exec "$ROLE" "$@"
    ;;
esac
