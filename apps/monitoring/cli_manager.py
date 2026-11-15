"""
CLI-обёртка для управления сетевым оборудованием (D-Link, Eltex).
Использует netmiko (SSH/Telnet).
"""
import logging
from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException

logger = logging.getLogger(__name__)


def get_device_connection(device, username, password):
    device_type = "dlink_ds"
    if device.vendor and "eltex" in device.vendor.name.lower():
        device_type = "dlink_ds"
    
    params = {
        "device_type": device_type,
        "host": device.ip,
        "username": username,
        "password": password,
        "timeout": 15,
        "global_delay_factor": 4,
        "session_log": "/tmp/netmiko_session.log",
    }
    
    try:
        connection = ConnectHandler(**params)
        return connection
    except NetmikoTimeoutException:
        logger.error(f"Таймаут подключения к {device.ip}")
        raise
    except NetmikoAuthenticationException:
        logger.error(f"Ошибка аутентификации на {device.ip}")
        raise


def set_port_pvid(device, username, password, port_name, vlan_id):
    conn = get_device_connection(device, username, password)
    try:
        commands = [
            "configure terminal",
            f"vlan {vlan_id}",
            f"untagged {port_name}",
            "exit",
            f"interface {port_name}",
            f"switchport pvid {vlan_id}",
            "exit",
            "exit",
            "write memory",
        ]
        output = conn.send_config_set(commands)
        logger.info(f"PVID {port_name} -> {vlan_id} на {device.ip}")
        return True, output
    finally:
        conn.disconnect()
