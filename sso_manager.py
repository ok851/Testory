"""
SSO 单点登录管理模块 - 支持LDAP、企业微信、OAuth2.0等多种登录方式
"""
import json
import hashlib
import secrets
import requests
from urllib.parse import quote, urlencode
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from database import Database


class SSOProviderType(Enum):
    """SSO 提供商类型"""
    LDAP = "ldap"                   # LDAP/AD 目录服务
    WECOM = "wecom"                 # 企业微信
    DINGTALK = "dingtalk"           # 钉钉
    FEISHU = "feishu"               # 飞书
    OAUTH2 = "oauth2"               # 通用 OAuth2.0
    SAML = "saml"                   # SAML 2.0


@dataclass
class SSOConfig:
    """SSO 配置"""
    id: int
    tenant_id: Optional[int]
    provider_type: str
    name: str
    is_active: bool = True
    # OAuth2 配置
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    auth_url: Optional[str] = None
    token_url: Optional[str] = None
    userinfo_url: Optional[str] = None
    callback_url: Optional[str] = None
    # LDAP 配置
    ldap_host: Optional[str] = None
    ldap_port: int = 389
    ldap_base_dn: Optional[str] = None
    ldap_bind_dn: Optional[str] = None
    ldap_bind_password: Optional[str] = None
    ldap_user_filter: str = "(uid={username})"
    # 企业微信配置
    wecom_corp_id: Optional[str] = None
    wecom_agent_id: Optional[str] = None
    wecom_secret: Optional[str] = None


# 允许通过 API 更新的列（防止任意字段名注入 SQL）
_SSO_UPDATE_COLUMNS = frozenset({
    'tenant_id', 'provider_type', 'name', 'client_id', 'client_secret',
    'auth_url', 'token_url', 'userinfo_url', 'callback_url',
    'ldap_host', 'ldap_port', 'ldap_base_dn', 'ldap_bind_dn',
    'ldap_bind_password', 'ldap_user_filter',
    'wecom_corp_id', 'wecom_agent_id', 'wecom_secret', 'is_active',
})


def _ldap_attr_one(entry, attr_name: str):
    """从 ldap3 Entry 上安全读取单值属性。"""
    if not hasattr(entry, attr_name):
        return None
    val = getattr(entry, attr_name)
    if hasattr(val, 'value'):
        v = val.value
        if isinstance(v, (list, tuple)) and v:
            v = v[0]
        return str(v) if v is not None else None
    return str(val) if val is not None else None


class SSOManager:
    """SSO 单点登录管理器"""
    
    def __init__(self, db: Database = None):
        self.db = db or Database()
        # OAuth / 企业微信 state（内存）；多进程部署时需改为 Redis 等共享存储
        self._state_cache = {}
    
    # ==================== SSO 配置管理 ====================
    
    def get_sso_configs(self, tenant_id: int = None) -> list:
        """获取 SSO 配置列表"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        if tenant_id:
            cursor.execute(
                "SELECT * FROM sso_configs WHERE tenant_id = ? OR tenant_id IS NULL ORDER BY created_at DESC",
                (tenant_id,)
            )
        else:
            cursor.execute("SELECT * FROM sso_configs ORDER BY created_at DESC")
        
        rows = cursor.fetchall()
        conn.close()
        
        configs = []
        for row in rows:
            configs.append({
                'id': row[0],
                'tenant_id': row[1],
                'provider_type': row[2],
                'name': row[3],
                'client_id': row[4],
                'auth_url': row[6],
                'callback_url': row[9],
                'ldap_host': row[10],
                'ldap_port': row[11],
                'wecom_corp_id': row[16],
                'is_active': bool(row[19]),
                'created_at': row[20]
            })
        return configs
    
    def get_sso_config(self, config_id: int) -> Optional[SSOConfig]:
        """获取单个 SSO 配置"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sso_configs WHERE id = ?", (config_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return SSOConfig(
            id=row[0],
            tenant_id=row[1],
            provider_type=row[2],
            name=row[3],
            client_id=row[4],
            client_secret=row[5],
            auth_url=row[6],
            token_url=row[7],
            userinfo_url=row[8],
            callback_url=row[9],
            ldap_host=row[10],
            ldap_port=row[11] or 389,
            ldap_base_dn=row[12],
            ldap_bind_dn=row[13],
            ldap_bind_password=row[14],
            ldap_user_filter=row[15] or "(uid={username})",
            wecom_corp_id=row[16],
            wecom_agent_id=row[17],
            wecom_secret=row[18],
            is_active=bool(row[19])
        )
    
    def create_sso_config(self, config_data: Dict[str, Any]) -> int:
        """创建 SSO 配置"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO sso_configs (
                tenant_id, provider_type, name, client_id, client_secret,
                auth_url, token_url, userinfo_url, callback_url,
                ldap_host, ldap_port, ldap_base_dn, ldap_bind_dn, ldap_bind_password, ldap_user_filter,
                wecom_corp_id, wecom_agent_id, wecom_secret, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            config_data.get('tenant_id'),
            config_data.get('provider_type'),
            config_data.get('name'),
            config_data.get('client_id'),
            config_data.get('client_secret'),
            config_data.get('auth_url'),
            config_data.get('token_url'),
            config_data.get('userinfo_url'),
            config_data.get('callback_url'),
            config_data.get('ldap_host'),
            config_data.get('ldap_port', 389),
            config_data.get('ldap_base_dn'),
            config_data.get('ldap_bind_dn'),
            config_data.get('ldap_bind_password'),
            config_data.get('ldap_user_filter', '(uid={username})'),
            config_data.get('wecom_corp_id'),
            config_data.get('wecom_agent_id'),
            config_data.get('wecom_secret'),
            config_data.get('is_active', True)
        ))
        
        config_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return config_id
    
    def update_sso_config(self, config_id: int, config_data: Dict[str, Any]) -> bool:
        """更新 SSO 配置"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        fields = []
        values = []
        for key, value in config_data.items():
            if key in ('id', 'created_at') or key not in _SSO_UPDATE_COLUMNS:
                continue
            if key == 'is_active':
                value = 1 if value else 0
            fields.append(f"{key} = ?")
            values.append(value)
        
        if not fields:
            return False
        
        values.append(config_id)
        cursor.execute(f"UPDATE sso_configs SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    
    def delete_sso_config(self, config_id: int) -> bool:
        """删除 SSO 配置"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sso_configs WHERE id = ?", (config_id,))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return success
    
    # ==================== LDAP 登录 ====================
    
    def ldap_authenticate(self, config: SSOConfig, username: str, password: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        LDAP 认证
        
        Returns:
            (success, user_info, message)
        """
        try:
            import ldap3
            from ldap3 import Server, Connection, ALL, SUBTREE
        except ImportError:
            return False, None, "请安装 ldap3 库: pip install ldap3"
        
        try:
            # 连接 LDAP 服务器
            server = Server(config.ldap_host, port=config.ldap_port, get_info=ALL)
            
            # 使用管理员账号绑定（用于搜索用户）
            bind_conn = Connection(server, user=config.ldap_bind_dn, password=config.ldap_bind_password)
            if not bind_conn.bind():
                return False, None, "LDAP 管理员绑定失败"
            
            # 搜索用户
            search_filter = config.ldap_user_filter.replace('{username}', username)
            bind_conn.search(
                search_base=config.ldap_base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=['cn', 'mail', 'uid', 'displayName', 'telephoneNumber']
            )
            
            if not bind_conn.entries:
                return False, None, "用户不存在"
            
            user_entry = bind_conn.entries[0]
            user_dn = user_entry.entry_dn
            
            # 使用用户 DN 和密码验证
            user_conn = Connection(server, user=user_dn, password=password)
            if not user_conn.bind():
                return False, None, "密码错误"
            
            ext = _ldap_attr_one(user_entry, 'uid') or username
            disp = _ldap_attr_one(user_entry, 'displayName') or _ldap_attr_one(user_entry, 'cn') or username
            # 获取用户信息
            user_info = {
                'external_id': ext,
                'username': username,
                'display_name': disp,
                'email': _ldap_attr_one(user_entry, 'mail'),
                'phone': _ldap_attr_one(user_entry, 'telephoneNumber'),
            }
            
            return True, user_info, "认证成功"
            
        except Exception as e:
            return False, None, f"LDAP 认证异常: {str(e)}"
    
    # ==================== 企业微信登录 ====================
    
    def wecom_get_login_url(self, config: SSOConfig, redirect_uri: str) -> str:
        """获取企业微信扫码登录 URL"""
        state = secrets.token_urlsafe(16)
        self._state_cache[state] = {'config_id': config.id, 'expires': datetime.now() + timedelta(minutes=10)}
        
        # 企业微信扫码登录（query 必须 URL 编码）
        q = urlencode(
            {
                'appid': config.wecom_corp_id or '',
                'agentid': config.wecom_agent_id or '',
                'redirect_uri': redirect_uri,
                'state': state,
            },
            quote_via=quote,
        )
        return f"https://open.work.weixin.qq.com/wwopen/sso/qrConnect?{q}"
    
    def wecom_authenticate(self, config: SSOConfig, code: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        企业微信认证回调处理
        
        Args:
            config: SSO 配置
            code: 企业微信返回的授权码
            
        Returns:
            (success, user_info, message)
        """
        try:
            # 获取 access_token
            token_url = (
                f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?"
                f"corpid={config.wecom_corp_id}&corpsecret={config.wecom_secret}"
            )
            token_resp = requests.get(token_url, timeout=10)
            token_data = token_resp.json()
            
            if token_data.get('errcode', 0) != 0:
                return False, None, f"获取 access_token 失败: {token_data.get('errmsg')}"
            
            access_token = token_data['access_token']
            
            # 获取用户信息
            user_url = (
                f"https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo?"
                f"access_token={access_token}&code={code}"
            )
            user_resp = requests.get(user_url, timeout=10)
            user_data = user_resp.json()
            
            if user_data.get('errcode', 0) != 0:
                return False, None, f"获取用户信息失败: {user_data.get('errmsg')}"
            
            user_id = user_data.get('UserId') or user_data.get('userid')
            if not user_id:
                return False, None, "无法获取用户 ID"
            
            # 获取用户详细信息
            detail_url = (
                f"https://qyapi.weixin.qq.com/cgi-bin/user/get?"
                f"access_token={access_token}&userid={user_id}"
            )
            detail_resp = requests.get(detail_url, timeout=10)
            detail_data = detail_resp.json()
            
            user_info = {
                'external_id': user_id,
                'username': user_id,
                'display_name': detail_data.get('name', user_id),
                'email': detail_data.get('email'),
                'phone': detail_data.get('mobile'),
                'avatar_url': detail_data.get('avatar')
            }
            
            return True, user_info, "认证成功"
            
        except Exception as e:
            return False, None, f"企业微信认证异常: {str(e)}"
    
    # ==================== OAuth2.0 通用登录 ====================
    
    def oauth2_get_login_url(self, config: SSOConfig, scope: str = "openid profile email") -> str:
        """获取 OAuth2.0 授权 URL"""
        state = secrets.token_urlsafe(16)
        self._state_cache[state] = {'config_id': config.id, 'expires': datetime.now() + timedelta(minutes=10)}
        
        base = (config.auth_url or '').strip()
        if not base:
            return ''
        q = urlencode(
            {
                'client_id': config.client_id or '',
                'redirect_uri': config.callback_url or '',
                'response_type': 'code',
                'scope': scope,
                'state': state,
            },
            quote_via=quote,
        )
        sep = '&' if ('?' in base) else '?'
        return f"{base}{sep}{q}"
    
    def oauth2_authenticate(self, config: SSOConfig, code: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        OAuth2.0 认证回调处理
        """
        try:
            # 交换 access_token
            token_resp = requests.post(config.token_url, data={
                'grant_type': 'authorization_code',
                'code': code,
                'client_id': config.client_id,
                'client_secret': config.client_secret,
                'redirect_uri': config.callback_url
            }, timeout=10)
            
            token_data = token_resp.json()
            access_token = token_data.get('access_token')
            
            if not access_token:
                return False, None, f"获取 access_token 失败: {token_data}"
            
            # 获取用户信息
            headers = {'Authorization': f'Bearer {access_token}'}
            user_resp = requests.get(config.userinfo_url, headers=headers, timeout=10)
            user_data = user_resp.json()
            
            user_info = {
                'external_id': user_data.get('sub') or user_data.get('id'),
                'username': user_data.get('preferred_username') or user_data.get('login') or user_data.get('email', '').split('@')[0],
                'display_name': user_data.get('name') or user_data.get('nickname'),
                'email': user_data.get('email'),
                'avatar_url': user_data.get('picture') or user_data.get('avatar_url')
            }
            
            return True, user_info, "认证成功"
            
        except Exception as e:
            return False, None, f"OAuth2.0 认证异常: {str(e)}"
    
    # ==================== 通用方法 ====================
    
    def authenticate(self, config_id: int, **kwargs) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        统一认证入口
        
        Args:
            config_id: SSO 配置 ID
            **kwargs: 认证参数（username, password, code 等）
            
        Returns:
            (success, user_info, message)
        """
        config = self.get_sso_config(config_id)
        if not config:
            return False, None, "SSO 配置不存在"
        
        if not config.is_active:
            return False, None, "SSO 配置已禁用"
        
        provider_type = config.provider_type
        
        if provider_type == SSOProviderType.LDAP.value:
            username = kwargs.get('username')
            password = kwargs.get('password')
            if not username or not password:
                return False, None, "用户名和密码不能为空"
            return self.ldap_authenticate(config, username, password)
        
        elif provider_type == SSOProviderType.WECOM.value:
            code = kwargs.get('code')
            if not code:
                return False, None, "授权码不能为空"
            return self.wecom_authenticate(config, code)
        
        elif provider_type == SSOProviderType.OAUTH2.value:
            code = kwargs.get('code')
            if not code:
                return False, None, "授权码不能为空"
            return self.oauth2_authenticate(config, code)
        
        else:
            return False, None, f"不支持的 SSO 类型: {provider_type}"
    
    def get_or_create_user(self, provider_type: str, user_info: Dict[str, Any], tenant_id: int = None) -> Optional[int]:
        """
        根据 SSO 用户信息获取或创建本地用户
        
        Returns:
            用户 ID
        """
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        external_id = user_info.get('external_id')
        
        # 查找已绑定的用户
        cursor.execute(
            "SELECT user_id FROM user_sso_bindings WHERE provider_type = ? AND external_id = ?",
            (provider_type, external_id)
        )
        binding = cursor.fetchone()
        
        if binding:
            user_id = binding[0]
        else:
            # 创建新用户
            username = user_info.get('username', external_id)
            # 确保用户名唯一
            base_username = username
            counter = 1
            while True:
                cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
                if not cursor.fetchone():
                    break
                username = f"{base_username}_{counter}"
                counter += 1
            
            # 生成随机密码（SSO 用户不使用密码登录）
            from werkzeug.security import generate_password_hash
            random_password = secrets.token_urlsafe(32)
            password_hash = generate_password_hash(random_password)
            
            cursor.execute('''
                INSERT INTO users (username, password_hash, email, role, tenant_id, display_name, phone, avatar_url)
                VALUES (?, ?, ?, 'tester', ?, ?, ?, ?)
            ''', (
                username,
                password_hash,
                user_info.get('email'),
                tenant_id,
                user_info.get('display_name'),
                user_info.get('phone'),
                user_info.get('avatar_url')
            ))
            user_id = cursor.lastrowid
            
            # 创建绑定记录
            cursor.execute('''
                INSERT INTO user_sso_bindings (user_id, provider_type, external_id, external_username, external_email)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, provider_type, external_id, user_info.get('username'), user_info.get('email')))
        
        # 记录登录
        cursor.execute('''
            INSERT INTO sso_login_records (user_id, provider_type, external_id)
            VALUES (?, ?, ?)
        ''', (user_id, provider_type, external_id))
        
        conn.commit()
        conn.close()
        return user_id
    
    def verify_state(self, state: str) -> Optional[int]:
        """
        验证 OAuth state 参数
        
        Returns:
            SSO 配置 ID，验证失败返回 None
        """
        cache_item = self._state_cache.get(state)
        if not cache_item:
            return None
        
        if datetime.now() > cache_item['expires']:
            del self._state_cache[state]
            return None
        
        config_id = cache_item['config_id']
        del self._state_cache[state]
        return config_id


# 全局 SSO 管理器实例
sso_manager = SSOManager()
