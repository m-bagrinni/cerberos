#!/usr/bin/env bash
set -e

wait_for() {
    local host="$1" port="$2" name="$3"
    echo "⏳ Ожидаем ${name} на ${host}:${port}..."
    until python -c "
import socket, sys
s = socket.socket(); s.settimeout(1)
try:
    s.connect(('${host}', ${port})); s.close()
except Exception:
    sys.exit(1)
" 2>/dev/null; do
        sleep 1
    done
    echo "✅ ${name} доступен"
}

if [ -n "$POSTGRES_HOST" ]; then
    wait_for "$POSTGRES_HOST" "${POSTGRES_PORT:-5432}" "PostgreSQL"
fi

if [ -n "$REDIS_URL" ]; then
    RHOST=$(python -c "import urllib.parse as u,os; p=u.urlparse(os.getenv('REDIS_URL','redis://redis:6379/0')); print(p.hostname)")
    RPORT=$(python -c "import urllib.parse as u,os; p=u.urlparse(os.getenv('REDIS_URL','redis://redis:6379/0')); print(p.port or 6379)")
    wait_for "$RHOST" "$RPORT" "Redis"
fi

if [ "$DJANGO_ENV" = "dev" ] && [ -f manage.py ]; then
    echo "🛠  Применяем миграции..."
    python manage.py migrate --noinput || echo "⚠️  migrate пропущен (вероятно, ещё нет Django-проекта)"
fi

exec "$@"
