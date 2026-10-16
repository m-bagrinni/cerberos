#!/usr/bin/env bash
set -e

# Ждём postgres и redis
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

wait_for "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "PostgreSQL"

if [ -n "$REDIS_URL" ]; then
    RHOST=$(python -c "import urllib.parse as u,os; p=u.urlparse(os.getenv('REDIS_URL','redis://redis:6379/0')); print(p.hostname)")
    RPORT=$(python -c "import urllib.parse as u,os; p=u.urlparse(os.getenv('REDIS_URL','redis://redis:6379/0')); print(p.port or 6379)")
    wait_for "$RHOST" "$RPORT" "Redis"
fi

# Миграции
echo "🛠  Применяем миграции..."
python manage.py migrate --noinput

# Статика
echo "📦 Собираем статику..."
python manage.py collectstatic --noinput --clear

# Проверка конфигурации
echo "🔍 Проверка Django..."
python manage.py check --deploy 2>&1 | head -20 || true

echo "🚀 Запуск: $@"
exec "$@"
