from ldap3 import Server, Connection, ALL, SUBTREE, Tls, MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE
import ssl
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

def get_ad_connection():
    """Создает и возвращает подключение к AD."""
    server_uri = settings.AD_SERVER
    use_ssl = settings.AD_USE_SSL
    
    if use_ssl:
        tls_config = Tls(validate=ssl.CERT_NONE)
        server = Server(server_uri, use_ssl=True, tls=tls_config, get_info=ALL)
    else:
        server = Server(server_uri, get_info=ALL)

    conn = Connection(
        server,
        user=settings.AD_USER,
        password=settings.AD_PASSWORD,
        auto_bind=True,
        receive_timeout=10
    )
    return conn

def get_ad_users(search_filter="(objectClass=user)"):
    """Получает список пользователей."""
    try:
        conn = get_ad_connection()
        attributes = ['sAMAccountName', 'displayName', 'mail', 'userAccountControl', 'lastLogonTimestamp']
        
        conn.search(
            search_base=settings.AD_BASE_DN,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=attributes,
            size_limit=500
        )
        
        users = []
        for entry in conn.entries:
            uac = int(entry.userAccountControl.value) if entry.userAccountControl.value else 512
            is_active = not (uac & 2)
            
            users.append({
                'login': entry.sAMAccountName.value or '',
                'name': entry.displayName.value or '',
                'email': entry.mail.value or '',
                'is_active': is_active,
                'dn': entry.entry_dn,
                'last_logon': entry.lastLogonTimestamp.value if entry.lastLogonTimestamp.value else None,
            })
            
        conn.unbind()
        return {'ok': True, 'users': users}
    except Exception as e:
        logger.error(f"AD Connection Error: {e}")
        return {'ok': False, 'error': str(e)}

def toggle_user_block(login, block=True):
    """Блокирует или разблокирует пользователя."""
    try:
        conn = get_ad_connection()
        
        # Ищем пользователя по логину
        conn.search(
            search_base=settings.AD_BASE_DN,
            search_filter=f"(sAMAccountName={login})",
            search_scope=SUBTREE,
            attributes=['userAccountControl']
        )
        
        if not conn.entries:
            return {'ok': False, 'error': 'Пользователь не найден'}
        
        user_dn = conn.entries[0].entry_dn
        uac = int(conn.entries[0].userAccountControl.value) if conn.entries[0].userAccountControl.value else 512
        
        # Бит 2 (значение 2) — Account Disabled
        if block:
            new_uac = uac | 2  # Включаем бит блокировки
        else:
            new_uac = uac & ~2 # Выключаем бит блокировки
            
        conn.modify(user_dn, {'userAccountControl': [(MODIFY_REPLACE, [str(new_uac)])]})
        conn.unbind()
        return {'ok': True}
    except Exception as e:
        logger.error(f"AD Toggle Block Error: {e}")
        return {'ok': False, 'error': str(e)}

def update_user_attributes(login, name=None, email=None):
    """Обновляет имя и email пользователя."""
    try:
        conn = get_ad_connection()
        conn.search(settings.AD_BASE_DN, f"(sAMAccountName={login})", SUBTREE, attributes=['*'])
        if not conn.entries:
            return {'ok': False, 'error': 'Пользователь не найден'}
        
        dn = conn.entries[0].entry_dn
        changes = {}
        
        if name is not None:
            changes['displayName'] = [(MODIFY_REPLACE, [name])]
        if email is not None:
            changes['mail'] = [(MODIFY_REPLACE, [email])]
            
        if changes:
            conn.modify(dn, changes)
        conn.unbind()
        return {'ok': True}
    except Exception as e:
        logger.error(f"AD Update Error: {e}")
        return {'ok': False, 'error': str(e)}

def reset_user_password(login, new_password):
    """Сбрасывает пароль пользователя (требует LDAPS)."""
    try:
        conn = get_ad_connection()
        conn.search(settings.AD_BASE_DN, f"(sAMAccountName={login})", SUBTREE, attributes=['distinguishedName'])
        if not conn.entries:
            return {'ok': False, 'error': 'Пользователь не найден'}
        
        dn = conn.entries[0].entry_dn
        
        # Пароль в AD передается в кодировке UTF-16LE в кавычках
        encoded_pwd = ('"' + new_password + '"').encode('utf-16-le')
        
        # Меняем пароль
        conn.modify(dn, {'unicodePwd': [(MODIFY_REPLACE, [encoded_pwd])]})
        
        # Снимаем флаг "Сменить пароль при следующем входе" (опционально)
        # conn.modify(dn, {'pwdLastSet': [(MODIFY_REPLACE, ['-1'])]})
        
        conn.unbind()
        return {'ok': True}
    except Exception as e:
        logger.error(f"AD Password Reset Error: {e}")
        return {'ok': False, 'error': str(e)}

def create_user(login, name, password, email=None):
    """Создает нового пользователя в AD."""
    try:
        conn = get_ad_connection()
        
        # Формируем DN для нового пользователя (кладем в CN=Users)
        user_dn = f"CN={name},CN=Users,{settings.AD_BASE_DN}"
        
        # Атрибуты для создания пользователя
        attributes = {
            'objectClass': ['top', 'person', 'organizationalPerson', 'user'],
            'sAMAccountName': login,
            'userPrincipalName': f"{login}@she.domain",
            'displayName': name,
            'givenName': name.split(' ')[0] if name else '',
            'sn': name.split(' ')[-1] if name and ' ' in name else '',
            'userAccountControl': '514',  # 514 = Аккаунт отключен (создаем выключенным)
        }
        
        if email:
            attributes['mail'] = email
            
        # Создаем пользователя
        success = conn.add(user_dn, attributes=attributes)
        
        if not success:
            return {'ok': False, 'error': f"Ошибка создания: {conn.result.get('description')}"}
        
        # Включаем аккаунт (512 = Активен)
        conn.modify(user_dn, {'userAccountControl': [(MODIFY_REPLACE, ['512'])]})
        
        # Устанавливаем пароль (только через LDAPS!)
        if password:
            encoded_pwd = ('"' + password + '"').encode('utf-16-le')
            conn.modify(user_dn, {'unicodePwd': [(MODIFY_REPLACE, [encoded_pwd])]})
        
        conn.unbind()
        return {'ok': True}
    except Exception as e:
        logger.error(f"AD Create User Error: {e}")
        return {'ok': False, 'error': str(e)}

def get_ad_groups():
    """Получает список групп из AD."""
    try:
        conn = get_ad_connection()
        conn.search(
            search_base=settings.AD_BASE_DN,
            search_filter="(&(objectClass=group)(!(distinguishedName=*,CN=Builtin,*)))",
            search_scope=SUBTREE,
            attributes=['sAMAccountName', 'description', 'member'],
            size_limit=500
        )
        
        groups = []
        for entry in conn.entries:
            groups.append({
                'name': entry.sAMAccountName.value or '',
                'description': entry.description.value or '',
                'members_count': len(entry.member) if entry.member else 0,
            })
        conn.unbind()
        
        # Сортируем: сначала группы с префиксом "гр_", потом остальные по алфавиту
        groups.sort(key=lambda g: (not g['name'].startswith('гр_'), g['name'].lower()))
        
        return {'ok': True, 'groups': groups}
    except Exception as e:
        logger.error(f"AD Groups Error: {e}")
        return {'ok': False, 'error': str(e)}

def add_user_to_group(login, group_name):
    """Добавляет пользователя в группу."""
    try:
        conn = get_ad_connection()
        # Ищем группу
        conn.search(settings.AD_BASE_DN, f"(sAMAccountName={group_name})", SUBTREE)
        if not conn.entries:
            return {'ok': False, 'error': 'Группа не найдена'}
        group_dn = conn.entries[0].entry_dn

        # Ищем пользователя
        conn.search(settings.AD_BASE_DN, f"(sAMAccountName={login})", SUBTREE)
        if not conn.entries:
            return {'ok': False, 'error': 'Пользователь не найден'}
        user_dn = conn.entries[0].entry_dn

        # Добавляем в группу
        conn.modify(group_dn, {'member': [(MODIFY_ADD, [user_dn])]})
        conn.unbind()
        return {'ok': True}
    except Exception as e:
        logger.error(f"AD Add to Group Error: {e}")
        return {'ok': False, 'error': str(e)}

def remove_user_from_group(login, group_name):
    """Удаляет пользователя из группы."""
    try:
        conn = get_ad_connection()
        # Ищем группу
        conn.search(settings.AD_BASE_DN, f"(sAMAccountName={group_name})", SUBTREE)
        if not conn.entries:
            return {'ok': False, 'error': 'Группа не найдена'}
        group_dn = conn.entries[0].entry_dn

        # Ищем пользователя
        conn.search(settings.AD_BASE_DN, f"(sAMAccountName={login})", SUBTREE)
        if not conn.entries:
            return {'ok': False, 'error': 'Пользователь не найден'}
        user_dn = conn.entries[0].entry_dn

        # Удаляем из группы
        conn.modify(group_dn, {'member': [(MODIFY_DELETE, [user_dn])]})
        conn.unbind()
        return {'ok': True}
    except Exception as e:
        logger.error(f"AD Remove from Group Error: {e}")
        return {'ok': False, 'error': str(e)}

def get_group_members(group_name):
    """Возвращает список участников группы (логины)."""
    try:
        conn = get_ad_connection()
        conn.search(
            search_base=settings.AD_BASE_DN,
            search_filter=f"(sAMAccountName={group_name})",
            search_scope=SUBTREE,
            attributes=['member']
        )
        if not conn.entries:
            return {'ok': False, 'error': 'Группа не найдена'}
        
        members_dns = conn.entries[0].member.values if conn.entries[0].member else []
        
        # Получаем логины по DN
        members = []
        for dn in members_dns:
            conn.search(
                search_base=settings.AD_BASE_DN,
                search_filter=f"(distinguishedName={dn})",
                search_scope=SUBTREE,
                attributes=['sAMAccountName']
            )
            if conn.entries:
                members.append(conn.entries[0].sAMAccountName.value)
        
        conn.unbind()
        return {'ok': True, 'members': members}
    except Exception as e:
        logger.error(f"AD Get Group Members Error: {e}")
        return {'ok': False, 'error': str(e)}

def create_group(group_name, description='', group_type='global'):
    """Создает новую группу в AD.
    group_type: 'global' (глобальная), 'domainlocal' (локальная домена)
    """
    try:
        conn = get_ad_connection()
        
        # DN для новой группы (в CN=Users)
        group_dn = f"CN={group_name},CN=Users,{settings.AD_BASE_DN}"
        
        # groupType: -2147483646 (Global Security), -2147483644 (Domain Local Security)
        # 2 = Global, 4 = Domain Local, 8 = Universal (с добавлением Security bit -2147483648)
        if group_type == 'global':
            gtype = '-2147483646'
        elif group_type == 'domainlocal':
            gtype = '-2147483644'
        else:
            gtype = '-2147483640'  # Universal
            
        attributes = {
            'objectClass': ['top', 'group'],
            'sAMAccountName': group_name,
            'groupType': gtype,
        }
        if description:
            attributes['description'] = description
            
        success = conn.add(group_dn, attributes=attributes)
        if not success:
            return {'ok': False, 'error': f"Ошибка создания: {conn.result.get('description')}"}
            
        conn.unbind()
        return {'ok': True}
    except Exception as e:
        logger.error(f"AD Create Group Error: {e}")
        return {'ok': False, 'error': str(e)}
