#!/usr/bin/env python3
"""
Агент для DC (Samba/Linux).
Собирает метрики и отправляет в Cerberus.
"""
import requests
import socket
import subprocess
import time
import json
import logging
import os

# Настройки
CERBER_URL = "http://192.168.7.160:8888"  # Адрес Cerberus
INTERVAL = 30  # Секунд между отправками

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def get_hostname():
    return socket.gethostname()

def get_services():
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend", "--plain"],
            capture_output=True, text=True, timeout=10
        )
        services = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 4:
                name = parts[0].replace('.service', '')
                if '@' in name:
                    continue
                state = parts[2]
                services.append({
                    'name': name,
                    'state': 'Running' if state == 'active' else 'Stopped'
                })
        return services
    except Exception as e:
        logging.error(f"Ошибка получения служб: {e}")
        return []

def get_system_metrics():
    metrics = []
    try:
        with open('/proc/loadavg', 'r') as f:
            load = f.read().split()[0]
            metrics.append({'key': 'cpu_load', 'value': float(load), 'unit': ''})
    except: pass
    try:
        with open('/proc/meminfo', 'r') as f:
            mem = f.read()
            total = int([l for l in mem.split('\n') if 'MemTotal' in l][0].split()[1]) / 1024
            free = int([l for l in mem.split('\n') if 'MemFree' in l][0].split()[1]) / 1024
            used_pct = (total - free) / total * 100
            metrics.append({'key': 'ram_used_pct', 'value': round(used_pct, 1), 'unit': '%'})
    except: pass
    try:
        stat = os.statvfs('/')
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        used_pct = (total - free) / total * 100
        metrics.append({'key': 'disk_used_pct', 'value': round(used_pct, 1), 'unit': '%'})
    except: pass
    return metrics

def send_report():
    hostname = get_hostname()
    metrics = get_system_metrics()
    services = get_services()
    all_metrics = metrics[:]
    for s in services:
        all_metrics.append({
            'key': f'svc_{s["name"]}',
            'value': 1 if s['state'] == 'Running' else 0,
            'unit': ''
        })
    payload = {
        'hostname': hostname,
        'metrics': all_metrics,
        'services': services,
    }
    try:
        resp = requests.post(f"{CERBER_URL}/servers/api/agent/report/", json=payload, timeout=10)
        if resp.status_code == 200:
            logging.info(f"Данные отправлены ({len(metrics)} метрик, {len(services)} служб)")
        else:
            logging.error(f"Ошибка: {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"Ошибка соединения: {e}")

if __name__ == '__main__':
    logging.info(f"Агент запущен. Отправка на {CERBER_URL} каждые {INTERVAL} сек.")
    while True:
        send_report()
        time.sleep(INTERVAL)
