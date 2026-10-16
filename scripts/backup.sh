#!/usr/bin/env bash
# Дамп PostgreSQL + архив media.
# Использование: ./scripts/backup.sh [output_dir]
set -e

OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"

TS=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="$OUT_DIR/cerber_${TS}.dump"
MEDIA_FILE="$OUT_DIR/media_${TS}.tar.gz"

echo "🛠  Дамп PostgreSQL → $DUMP_FILE"
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T postgres pg_dump -U "${POSTGRES_USER:-cerber}" \
    -d "${POSTGRES_DB:-cerber}" -F c > "$DUMP_FILE"

echo "📦 Архив media → $MEDIA_FILE"
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T web tar czf - -C /app/media . > "$MEDIA_FILE" 2>/dev/null || true

echo ""
echo "✅ Готово:"
ls -lh "$OUT_DIR"/*"${TS}"*

# Ротация: удаляем дампы старше 30 дней
find "$OUT_DIR" -name "cerber_*.dump" -mtime +30 -delete
find "$OUT_DIR" -name "media_*.tar.gz" -mtime +30 -delete
echo "🧹 Старые дампы (>30 дней) удалены"
