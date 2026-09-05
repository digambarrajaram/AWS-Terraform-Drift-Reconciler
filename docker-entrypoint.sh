#!/bin/sh
set -eu

# Bind mounts (e.g. DRIFT_CLONE_BASE=/var/lib/drift-clones) arrive owned by the
# host user.  Fix ownership before dropping to the unprivileged runtime user.
clone_base="${DRIFT_CLONE_BASE:-/tmp/drift-clones}"

if [ "$(id -u)" -eq 0 ]; then
    mkdir -p /tmp/drift-clones /tmp/drift-logs "$clone_base"
    chown -R drift:drift /tmp/drift-clones /tmp/drift-logs
    if [ "$clone_base" != "/tmp/drift-clones" ]; then
        chown drift:drift "$clone_base"
    fi
    exec runuser -u drift -- "$@"
fi

exec "$@"
