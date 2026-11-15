#!/bin/bash
# Синхронизация ARP с хоста в Cerberus
# Запускать через cron (каждые 5 минут)

CERBER_URL="http://localhost:8888"
CERBER_USER="1"
CERBER_PASS="1"

# Логин (получаем cookie)
COOKIE=$(mktemp)
CSRF=$(curl -s -c "$COOKIE" "$CERBER_URL/accounts/login/" | grep -o 'name="csrfmiddlewaretoken" value="[^"]*"' | cut -d'"' -f4)
curl -s -b "$COOKIE" -c "$COOKIE" -X POST "$CERBER_URL/accounts/login/" \
  -H "Referer: $CERBER_URL/accounts/login/" \
  --data-urlencode "csrfmiddlewaretoken=$CSRF" \
  --data-urlencode "username=$CERBER_USER" \
  --data-urlencode "password=$CERBER_PASS" \
  -o /dev/null

# Получаем список подсетей из API
SUBNETS=$(curl -s -b "$COOKIE" "$CERBER_URL/api/v1/subnets/" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for s in data.get('results', []):
    if s.get('is_active') and s.get('cidr'):
        print(s['cidr'])
")

# Для каждой подсети
for SUBNET in $SUBNETS; do
    echo "[$(date)] Обработка подсети: $SUBNET"
    
    # Пингуем (чтобы заполнить ARP)
    fping -a -q -g "$SUBNET" 2>/dev/null >/dev/null
    
    # Читаем ARP
    ip neigh show | grep -E "$(echo $SUBNET | cut -d'/' -f1 | cut -d'.' -f1-3)\." | while read -r line; do
        IP=$(echo "$line" | awk '{print $1}')
        MAC=$(echo "$line" | grep -o 'lladdr [^ ]*' | awk '{print $2}')
        if [ -n "$MAC" ]; then
            # Отправляем в Cerberus (через API)
            curl -s -b "$COOKIE" -X POST "$CERBER_URL/api/v1/ip-addresses/update-mac/" \
              -H "Content-Type: application/json" \
              -H "X-CSRFToken: $CSRF" \
              -d "{\"ip\": \"$IP\", \"mac\": \"$MAC\"}" >/dev/null
        fi
    done
done

rm -f "$COOKIE"
