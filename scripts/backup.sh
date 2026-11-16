#!/usr/bin/env bash
# Дамп PostgreSQL + архив media, с проверкой и ротацией.
# Использование: scripts/backup.sh [output_dir]
#   COMPOSE_FILES — какие compose-файлы (по умолчанию dev-стек, на котором работает сайт)
#   KEEP_DAYS     — сколько дней хранить (по умолчанию 14)
# По cron: 30 3 * * * /opt/cerber/scripts/backup.sh >> /opt/cerber/backups/backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."
OUT_DIR="${1:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f docker-compose.dev.yml}"
DC="docker compose $COMPOSE_FILES"
mkdir -p "$OUT_DIR"

TS=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="$OUT_DIR/cerber_${TS}.dump"
MEDIA_FILE="$OUT_DIR/media_${TS}.tar.gz"
echo "[$(date '+%F %T')] бэкап $TS"

# Имя БД/пользователя берём из контейнера — не зависим от окружения cron
$DC exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c' > "$DUMP_FILE.part"
# Проверка: архив читается pg_restore и в нём есть таблицы пользователей
TOC=$($DC exec -T postgres pg_restore --list < "$DUMP_FILE.part" || true)
if ! grep -q "TABLE DATA public accounts_user" <<<"$TOC"; then
    echo "ОШИБКА: дамп не прошёл проверку, оставлен как $DUMP_FILE.part" >&2
    exit 1
fi
mv "$DUMP_FILE.part" "$DUMP_FILE"

$DC exec -T web tar czf - -C /app/media . > "$MEDIA_FILE" 2>/dev/null || echo "media: архив не создан (не критично)"

ls -lh "$DUMP_FILE" "$MEDIA_FILE" 2>/dev/null || true

# Ротация
find "$OUT_DIR" -maxdepth 1 \( -name "cerber_*.dump" -o -name "media_*.tar.gz" -o -name "*.part" \) -mtime +"$KEEP_DAYS" -delete
echo "готово, хранится дней: $KEEP_DAYS"
