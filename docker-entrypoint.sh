#!/usr/bin/env bash
set -e

# Compile Cython extensions if not present (needed when source is volume-mounted)
if ! ls /app/qlib/data/_libs/rolling*.so &>/dev/null; then
    echo "[entrypoint] Compiling Cython extensions..."
    make -C /app prerequisite
fi

exec "$@"
