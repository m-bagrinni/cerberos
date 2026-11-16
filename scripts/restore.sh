#!/usr/bin/env bash
# Восстановление PostgreSQL из дампа (scripts/backup.sh).
# Использование: scripts/restore.sh backups/cerber_YYYYMMDD_HHMMSS.dump
#   COMPOSE_FILES — по умолчанию dev-стек, на котором работает сайт
set -euo pipefail
cd "$(dirname "$0")/.."

DUMP_FILE="${1:-}"
if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
    echo "Использование: $0 <путь_к_dump>"; exit 1
fi
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f docker-compose.dev.yml}"
DC="docker compose $COMPOSE_FILES"

# Все сервисы, кроме БД и Redis (web, бот, воркеры, beat, ops…)
APP_SERVICES=$($DC config --services | grep -vxE 'postgres|redis' | tr '\n' ' ')

echo "⚠️  Текущие данные БД будут удалены и заменены из $DUMP_FILE"
echo "    Будут остановлены: $APP_SERVICES"
read -r -p "Продолжить? (yes/no): " ans
[ "$ans" = "yes" ] || { echo "Отменено"; exit 0; }

$DC stop $APP_SERVICES
$DC exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";" -c "CREATE DATABASE \"$POSTGRES_DB\";"'
$DC exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' < "$DUMP_FILE"
$DC start $APP_SERVICES
$DC exec -T web python manage.py migrate --noinput
echo "✅ Восстановление завершено"
