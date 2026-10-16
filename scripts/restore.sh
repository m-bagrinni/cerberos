#!/usr/bin/env bash
# Восстановление PostgreSQL из дампа.
# Использование: ./scripts/restore.sh backups/cerber_YYYYMMDD_HHMMSS.dump
set -e

DUMP_FILE="$1"
if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
    echo "Использование: $0 <путь_к_dump>"
    exit 1
fi

echo "⚠️  Это удалит текущие данные БД и заменит их из $DUMP_FILE"
read -p "Продолжить? (yes/no): " ans
if [ "$ans" != "yes" ]; then
    echo "Отменено"
    exit 0
fi

echo "🛑 Останавливаем web/worker/beat"
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop web worker beat

echo "🗑  Дропаем и создаём БД"
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T postgres psql -U "${POSTGRES_USER:-cerber}" -d postgres \
    -c "DROP DATABASE IF EXISTS ${POSTGRES_DB:-cerber};" \
    -c "CREATE DATABASE ${POSTGRES_DB:-cerber};"

echo "📥 Восстанавливаем из дампа"
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T postgres pg_restore -U "${POSTGRES_USER:-cerber}" \
    -d "${POSTGRES_DB:-cerber}" --clean --if-exists < "$DUMP_FILE"

echo "🛠  Применяем миграции"
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    run --rm web python manage.py migrate --noinput

echo "🚀 Запускаем сервисы"
docker compose -f docker-compose.yml -f docker-compose.prod.yml start web worker beat

echo "✅ Восстановление завершено"
