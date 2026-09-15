#!/usr/bin/env bash
# Container entrypoint: wait for PostgreSQL, apply migrations, then start.
#
# Migrations run here rather than at application startup so that scaling the
# service to several replicas does not race: `alembic upgrade` takes a lock,
# and a replica that loses the race simply finds the schema already current.
set -euo pipefail

host="${POSTGRES_HOST:-db}"
port="${POSTGRES_PORT:-5432}"

echo "waiting for postgres at ${host}:${port}..."
for _ in $(seq 1 60); do
    if python -c "
import socket, sys
sock = socket.socket()
sock.settimeout(1)
try:
    sock.connect(('${host}', ${port}))
except OSError:
    sys.exit(1)
finally:
    sock.close()
" >/dev/null 2>&1; then
        echo "postgres is up"
        break
    fi
    sleep 1
done

echo "applying migrations..."
alembic upgrade head

if [ "${SEED_ON_START:-false}" = "true" ]; then
    echo "seeding demo data..."
    python -m scripts.seed
fi

exec "$@"
