"""
License 管理模块 - 用于验证和管理个人版/企业版授权
"""
import json
import hashlib
import base64
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class LicenseType(Enum):
    FREE = "free"              # 免费版
    PROFESSIONAL = "professional"  # 个人专业版(团队版)
    ENTERPRISE = "enterprise"  # 企业版


@dataclass
class LicenseInfo:
    """License 信息"""
    license_type: str          # personal/enterprise/trial
    issued_to: str            # 授权对象（公司名/个人）
    issued_at: str            # 签发时间
    expires_at: str           # 过期时间
    max_users: int            # 最大用户数
    max_projects: int         # 最大项目数
    max_cases_per_project: int # 每项目最大用例数
    max_executions_per_day: int  # 每日最大执行次数
    features: list            # 可用功能列表
    signature: str = ""       # 签名（用于验证）

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['license_type'] = self.license_type
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LicenseInfo':
        # 兼容旧版数据：将 max_executions_per_month 映射到 max_executions_per_day
        if 'max_executions_per_month' in data and 'max_executions_per_day' not in data:
            data['max_executions_per_day'] = data.pop('max_executions_per_month')
        return cls(**data)


class LicenseManager:
    """License 管理器"""

    # 功能定义
    FEATURES = {
        'basic': ['project_management', 'case_management', 'test_execution', 'basic_report'],
        'free': ['project_management', 'case_management', 'test_execution', 'basic_report'],
        'professional': [
            'project_management', 'case_management', 'test_execution',
            'advanced_report', 'schedule', 'data_driven',
            'export_pdf', 'export_excel', 'webhook', 'email_notification'
        ],
        'enterprise': [
            'project_management', 'case_management', 'test_execution',
            'advanced_report', 'schedule', 'webhook', 'data_driven',
            'parallel_execution', 'audit_log', 'export_pdf', 'export_excel',
            'email_notification', 'api_access', 'team_collaboration'
        ]
    }

    # 各版本限制 - 按照截图中的版本对比表配置
    LIMITS = {
        LicenseType.FREE: {
            'max_users': 1,
            'max_projects': 3,
            'max_cases_per_project': 50,
            'max_executions_per_day': 10,  # 每日执行次数
            'history_retention_days': 7,
            'features': FEATURES['free']
        },
        LicenseType.PROFESSIONAL: {
            'max_users': 1,
            'max_projects': 10,
            'max_cases_per_project': 200,
            'max_executions_per_day': -1,  # 无限
            'history_retention_days': 30,
            'features': FEATURES['professional']
        },
        LicenseType.ENTERPRISE: {
            'max_users': -1,  # 无限制
            'max_projects': -1,
            'max_cases_per_project': -1,
            'max_executions_per_day': -1,
            'history_retention_days': -1,
            'features': FEATURES['enterprise']
        }
    }

    def __init__(self, license_file: str = "license.key"):
        self.license_file = license_file
        self._secret_key = "UAT-Platform-2024-Secret-Key"  # 用于签名的密钥
        self._cached_license: Optional[LicenseInfo] = None

    def _generate_signature(self, data: Dict[str, Any]) -> str:
        """生成数据签名"""
        # 移除签名字段
        data_copy = {k: v for k, v in data.items() if k != 'signature'}
        # 排序并序列化
        content = json.dumps(data_copy, sort_keys=True, separators=(',', ':'))
        # 使用 HMAC-SHA256 生成签名
        signature = hashlib.sha256(
            (content + self._secret_key).encode()
        ).hexdigest()[:32]
        return signature

    def _verify_signature(self, license_info: LicenseInfo) -> bool:
        """验证签名"""
        expected = self._generate_signature(license_info.to_dict())
        return license_info.signature == expected

    def generate_license(self, license_type: LicenseType, issued_to: str,
                        expires_days: int = 365, custom_limits: Dict[str, Any] = None) -> str:
        """
        生成 License

        Args:
            license_type: 许可证类型
            issued_to: 授权对象
            expires_days: 有效期天数
            custom_limits: 自定义限制（可选）

        Returns:
            License 字符串
        """
        now = datetime.now()
        expires = now + timedelta(days=expires_days)

        limits = self.LIMITS[license_type].copy()
        if custom_limits:
            limits.update(custom_limits)

        license_info = LicenseInfo(
            license_type=license_type.value,
            issued_to=issued_to,
            issued_at=now.isoformat(),
            expires_at=expires.isoformat(),
            max_users=limits['max_users'],
            max_projects=limits['max_projects'],
            max_cases_per_project=limits['max_cases_per_project'],
            max_executions_per_day=limits.get('max_executions_per_day', limits.get('max_executions_per_month', 10)),
            features=limits['features']
        )

        # 生成签名
        license_info.signature = self._generate_signature(license_info.to_dict())

        # 编码为字符串
        json_data = json.dumps(license_info.to_dict())
        encoded = base64.b64encode(json_data.encode()).decode()

        return encoded

    def validate_license(self, license_str: str = None) -> Dict[str, Any]:
        """
        验证 License

        Returns:
            {
                'valid': bool,
                'license_type': str,
                'message': str,
                'info': LicenseInfo (if valid)
            }
        """
        if license_str is None:
            license_str = self._load_license_file()

        if not license_str:
            # 无 License 文件，使用免费版默认配置
            return {
                'valid': True,
                'license_type': LicenseType.FREE.value,
                'message': '使用免费版',
                'info': self._create_default_free_license()
            }

        try:
            # 解码
            json_data = base64.b64decode(license_str.encode()).decode()
            data = json.loads(json_data)
            license_info = LicenseInfo.from_dict(data)

            # 验证签名
            if not self._verify_signature(license_info):
                return {
                    'valid': False,
                    'license_type': None,
                    'message': 'License 签名无效，可能已被篡改',
                    'info': None
                }

            # 验证过期时间
            expires = datetime.fromisoformat(license_info.expires_at)
            if datetime.now() > expires:
                return {
                    'valid': False,
                    'license_type': license_info.license_type,
                    'message': f'License 已过期（过期时间: {license_info.expires_at}）',
                    'info': license_info
                }

            # 缓存验证结果
            self._cached_license = license_info

            return {
                'valid': True,
                'license_type': license_info.license_type,
                'message': f'License 有效（类型: {license_info.license_type}，到期: {license_info.expires_at}）',
                'info': license_info
            }

        except Exception as e:
            return {
                'valid': False,
                'license_type': None,
                'message': f'License 格式错误: {str(e)}',
                'info': None
            }

    def _create_default_free_license(self) -> LicenseInfo:
        """创建默认免费版 License"""
        limits = self.LIMITS[LicenseType.FREE]
        return LicenseInfo(
            license_type=LicenseType.FREE.value,
            issued_to='Free User',
            issued_at=datetime.now().isoformat(),
            expires_at='2099-12-31T23:59:59',  # 免费版永不过期
            max_users=limits['max_users'],
            max_projects=limits['max_projects'],
            max_cases_per_project=limits['max_cases_per_project'],
            max_executions_per_day=limits.get('max_executions_per_day', 10),
            features=limits['features'],
            signature=''
        )

    def _load_license_file(self) -> Optional[str]:
        """从文件加载 License"""
        if os.path.exists(self.license_file):
            with open(self.license_file, 'r') as f:
                return f.read().strip()
        return None

    def save_license(self, license_str: str) -> bool:
        """保存 License 到文件"""
        try:
            with open(self.license_file, 'w') as f:
                f.write(license_str)
            return True
        except Exception:
            return False

    def get_current_license(self) -> LicenseInfo:
        """获取当前 License 信息"""
        if self._cached_license:
            return self._cached_license

        result = self.validate_license()
        if result['valid'] and result['info']:
            return result['info']

        return self._create_default_free_license()

    def check_feature_available(self, feature_name: str) -> bool:
        """检查某功能是否可用"""
        license_info = self.get_current_license()
        return feature_name in license_info.features

    def get_limits(self) -> Dict[str, Any]:
        """获取当前 License 的限制"""
        license_info = self.get_current_license()
        return {
            'max_users': license_info.max_users,
            'max_projects': license_info.max_projects,
            'max_cases_per_project': license_info.max_cases_per_project,
            'max_executions_per_day': license_info.max_executions_per_day,
            'license_type': license_info.license_type,
            'expires_at': license_info.expires_at
        }

    def check_limit(self, limit_type: str, current_value: int) -> Dict[str, Any]:
        """
        检查是否超出限制

        Returns:
            {'allowed': bool, 'message': str, 'limit': int, 'current': int}
        """
        limits = self.get_limits()
        limit_value = limits.get(limit_type, -1)

        if limit_value == -1:  # 无限制
            return {'allowed': True, 'message': '', 'limit': -1, 'current': current_value}

        if current_value >= limit_value:
            license_type = limits['license_type']
            if license_type == LicenseType.FREE.value:
                message = f'已达到免费版限制（{limit_type}: {limit_value}）。请升级到个人专业版或企业版解除限制。'
            elif license_type == LicenseType.PROFESSIONAL.value:
                message = f'已达到个人专业版限制（{limit_type}: {limit_value}）。请升级到企业版解除限制。'
            else:
                message = f'已达到限制（{limit_type}: {limit_value}）。'

            return {
                'allowed': False,
                'message': message,
                'limit': limit_value,
                'current': current_value
            }

        return {'allowed': True, 'message': '', 'limit': limit_value, 'current': current_value}


# 全局 License 管理器实例
license_manager = LicenseManager()


if __name__ == '__main__':
    # 测试代码
    lm = LicenseManager()

    # 生成企业版 License
    enterprise_license = lm.generate_license(
        LicenseType.ENTERPRISE,
        issued_to='Test Company Ltd.',
        expires_days=365
    )
    print(f"企业版 License: {enterprise_license}")

    # 生成个人专业版 License
    professional_license = lm.generate_license(
        LicenseType.PROFESSIONAL,
        issued_to='Professional User',
        expires_days=365
    )
    print(f"\n个人专业版 License: {professional_license}")

    # 验证
    result = lm.validate_license(enterprise_license)
    print(f"\n验证结果: {result}")
