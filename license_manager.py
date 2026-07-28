"""
License 管理模块 — 产品与部署形态（商业策略）

- **免费版（free）**：公有云 / 线上实例，低门槛拉新，配额内使用。
- **团队版（professional，对外也称「团队版」）**：SaaS 付费，多协作、更高配额与高级能力；枚举名保持 PROFESSIONAL 以兼容已有 license.key。
- **企业版（enterprise）**：最高档；**私有化部署、专有云、内网发行**等主要作为本档增值项以提升利润；含 SSO、审计、集成等。

功能开关用 `check_feature_available()`；企业专属能力见 FEATURES['enterprise'] 中的 `private_deployment` 等标识。
"""
import json
import hashlib
import base64
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum


def resolve_license_file_path() -> Path:
    """License 持久化路径：桌面版写入 UAT_DATA_DIR，避免安装目录只读。"""
    explicit = (os.environ.get("LICENSE_FILE") or "").strip()
    if explicit:
        return Path(explicit)
    uat_data = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if uat_data:
        return Path(uat_data) / "license.key"
    return Path(__file__).resolve().parent / "license.key"


class LicenseType(Enum):
    FREE = "free"  # 免费版 · 线上使用（SaaS 免费层）
    # 团队版 · 付费 Web（SaaS）；代码与存量证书仍用 professional
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"  # 企业版 · 含私有化部署/专有云权益（高毛利档）


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
    license_id: str = ""      # 平台签发 ID
    binding_type: str = ""    # none | machine | instance
    binding_id: str = ""      # machine_id 或 instance_id
    seat_count: int = 0       # 团队席位（0=按 max_users）

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['license_type'] = self.license_type
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LicenseInfo':
        # 兼容旧版数据：将 max_executions_per_month 映射到 max_executions_per_day
        if 'max_executions_per_month' in data and 'max_executions_per_day' not in data:
            data['max_executions_per_day'] = data.pop('max_executions_per_month')
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        defaults = {
            'signature': '',
            'license_id': '',
            'binding_type': '',
            'binding_id': '',
            'seat_count': 0,
        }
        for k, v in defaults.items():
            filtered.setdefault(k, v)
        return cls(**filtered)


# 对外展示用名称（与 LicenseType 枚举值对应）
TIER_DISPLAY_NAME: Dict[LicenseType, str] = {
    LicenseType.FREE: "免费版",
    LicenseType.PROFESSIONAL: "团队版",
    LicenseType.ENTERPRISE: "企业版",
}

# 商业形态简述（用于控制台 / 升级文案，非 i18n 键）
TIER_OFFERING_SUMMARY: Dict[LicenseType, str] = {
    LicenseType.FREE: "线上免费使用（SaaS）",
    LicenseType.PROFESSIONAL: "团队付费（SaaS）· 高阶自动化与协作",
    LicenseType.ENTERPRISE: "企业协议 · 含私有化 / 专有云等交付选项（高价值档）",
}


class LicenseManager:
    """License 管理器"""

    # 功能定义（颗粒度供 check_feature_available / 升级页）
    FEATURES = {
        'basic': ['project_management', 'case_management', 'test_execution', 'basic_report'],
        'free': ['project_management', 'case_management', 'test_execution', 'basic_report'],
        'professional': [
            'project_management', 'case_management', 'test_execution',
            'advanced_report', 'schedule', 'data_driven',
            'export_pdf', 'export_excel', 'webhook', 'email_notification',
            'defect_management',
            'team_collaboration',  # 团队版（SaaS）协作能力
            'cross_end',  # 跨端编排（不含企业治理导出）
        ],
        'enterprise': [
            'project_management', 'case_management', 'test_execution',
            'advanced_report', 'schedule', 'webhook', 'data_driven',
            'parallel_execution', 'audit_log', 'export_pdf', 'export_excel',
            'email_notification', 'api_access', 'team_collaboration',
            'defect_management', 'sso', 'custom_integration',
            'cross_end',
            'ci_integration',  # CI 触发 / JUnit 门禁深度对接
            'customer_audit_export',  # 客户向审计包 ZIP
            # 商业位：可用于 UI/运维区分「可签私有化合同」的租户（具体交付仍由商务与部署方式决定）
            'private_deployment', 'dedicated_support',
        ]
    }

    # 能力目录：最低档位（叙事）与说明；执行诚实相关能力不得锁死开源核心
    FEATURE_CATALOG: Dict[str, Dict[str, str]] = {
        'test_execution': {
            'min_tier': 'free',
            'title': '用例执行',
            'note': '开源核心；须遵守执行诚实标准',
        },
        'basic_report': {
            'min_tier': 'free',
            'title': '基础报告',
            'note': '运行历史与基础统计',
        },
        'cross_end': {
            'min_tier': 'professional',
            'title': '跨端编排',
            'note': '多端计划执行；standalone 开源默认可试用',
        },
        'advanced_report': {
            'min_tier': 'professional',
            'title': '高级报告',
            'note': '趋势/导出等',
        },
        'schedule': {'min_tier': 'professional', 'title': '定时任务', 'note': ''},
        'data_driven': {'min_tier': 'professional', 'title': '数据驱动', 'note': ''},
        'defect_management': {'min_tier': 'professional', 'title': '缺陷管理', 'note': ''},
        'team_collaboration': {'min_tier': 'professional', 'title': '团队协作', 'note': ''},
        'sso': {
            'min_tier': 'enterprise',
            'title': 'SSO / LDAP',
            'note': '企业单点登录配置与回调',
        },
        'audit_log': {
            'min_tier': 'enterprise',
            'title': '审计日志',
            'note': '操作审计与登录审计查询/导出',
        },
        'customer_audit_export': {
            'min_tier': 'enterprise',
            'title': '客户审计包',
            'note': '批量证据 ZIP（含 auth_events）',
        },
        'ci_integration': {
            'min_tier': 'enterprise',
            'title': 'CI 深度集成',
            'note': '触发运行 / JUnit / build 关联',
        },
        'api_access': {'min_tier': 'enterprise', 'title': '开放 API', 'note': ''},
        'parallel_execution': {'min_tier': 'enterprise', 'title': '执行节点', 'note': '远程执行机登记与探测'},
        'private_deployment': {
            'min_tier': 'enterprise',
            'title': '私有化权益',
            'note': '商务交付标识，非运行时锁',
        },
        'dedicated_support': {
            'min_tier': 'enterprise',
            'title': '专属支持',
            'note': '商务交付标识',
        },
        'custom_integration': {'min_tier': 'enterprise', 'title': '定制集成', 'note': ''},
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

    def __init__(self, license_file: str | None = None):
        self.license_file = str(license_file or resolve_license_file_path())
        self._secret_key = "UAT-Platform-2024-Secret-Key"  # 用于签名的密钥
        self._cached_license: Optional[LicenseInfo] = None
        self._migrate_legacy_license_file()

    def _migrate_legacy_license_file(self) -> None:
        target = Path(self.license_file)
        if target.is_file():
            return
        legacy_candidates = (
            Path.cwd() / "license.key",
            Path(__file__).resolve().parent / "license.key",
        )
        for legacy in legacy_candidates:
            try:
                if legacy.is_file() and legacy.resolve() != target.resolve():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(legacy, target)
                    return
            except OSError:
                continue

    def _encode_license_str(self, license_info: LicenseInfo) -> str:
        payload = json.dumps(license_info.to_dict(), sort_keys=True, separators=(",", ":"))
        return base64.b64encode(payload.encode()).decode()

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
                        expires_days: int = 365, custom_limits: Dict[str, Any] = None,
                        license_id: str = "",
                        binding_type: str = "",
                        binding_id: str = "",
                        seat_count: int = 0) -> str:
        """
        生成 License

        Args:
            license_type: 许可证类型
            issued_to: 授权对象
            expires_days: 有效期天数
            custom_limits: 自定义限制（可选）
            license_id: 平台签发 ID（创始人后台）
            binding_type: none | machine | instance
            binding_id: 绑定的 machine_id 或 instance_id
            seat_count: 团队席位数

        Returns:
            License 字符串
        """
        now = datetime.now()
        expires = now + timedelta(days=expires_days)

        limits = self.LIMITS[license_type].copy()
        if custom_limits:
            limits.update(custom_limits)

        if not license_id:
            import uuid
            license_id = f"lic_{uuid.uuid4().hex[:16]}"

        license_info = LicenseInfo(
            license_type=license_type.value,
            issued_to=issued_to,
            issued_at=now.isoformat(),
            expires_at=expires.isoformat(),
            max_users=limits['max_users'],
            max_projects=limits['max_projects'],
            max_cases_per_project=limits['max_cases_per_project'],
            max_executions_per_day=limits.get('max_executions_per_day', limits.get('max_executions_per_month', 10)),
            features=limits['features'],
            license_id=license_id,
            binding_type=(binding_type or "").strip(),
            binding_id=(binding_id or "").strip(),
            seat_count=int(seat_count or 0),
        )

        # 生成签名
        license_info.signature = self._generate_signature(license_info.to_dict())

        return self._encode_license_str(license_info)

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

            bind_err = self.check_binding(license_info)
            if bind_err:
                return {
                    'valid': False,
                    'license_type': license_info.license_type,
                    'message': bind_err,
                    'info': license_info,
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
        path = Path(self.license_file)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        return None

    def save_license(self, license_str: str) -> bool:
        """保存 License 到文件"""
        try:
            path = Path(self.license_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(license_str.strip(), encoding="utf-8")
            return True
        except OSError:
            return False

    def get_current_license(self) -> LicenseInfo:
        """获取当前 License 信息"""
        if self._cached_license:
            return self._cached_license

        result = self.validate_license()
        if result['valid'] and result['info']:
            return result['info']

        return self._create_default_free_license()

    def features_unlocked_for_open_core(self) -> bool:
        """开源/本机 standalone 默认可试用企业能力；商业强制门禁用 LICENSE_ENFORCE_FEATURES=1。"""
        raw = (os.environ.get("LICENSE_ENFORCE_FEATURES") or "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return False
        if (os.environ.get("UAT_OPEN_FEATURES") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return True
        try:
            from deployment_config import is_standalone_mode

            return bool(is_standalone_mode())
        except Exception:
            return False

    def _normalize_license_type(self, license_type: Any) -> str:
        lt = str(license_type or LicenseType.FREE.value).strip().lower()
        if lt in ("pro", "team", "professional"):
            return LicenseType.PROFESSIONAL.value
        if lt in ("ent", "enterprise"):
            return LicenseType.ENTERPRISE.value
        if lt in ("basic", "free"):
            return LicenseType.FREE.value
        return lt

    def entitled_features_for_license(self, license_info: Optional[LicenseInfo] = None) -> List[str]:
        """当前证书档位应得能力（合并证书内 features 与产品目录，避免旧企业证缺新键）。"""
        info = license_info or self.get_current_license()
        lt = self._normalize_license_type(info.license_type)
        entitled = set(info.features or [])
        entitled |= set(self.FEATURES.get(lt) or self.FEATURES.get("free") or [])
        if lt == LicenseType.ENTERPRISE.value:
            entitled |= set(self.FEATURES.get("professional") or [])
            entitled |= set(self.FEATURES.get("enterprise") or [])
        elif lt == LicenseType.PROFESSIONAL.value:
            entitled |= set(self.FEATURES.get("professional") or [])
            entitled |= set(self.FEATURES.get("free") or [])
        else:
            entitled |= set(self.FEATURES.get("free") or [])
        return sorted(entitled)

    def check_feature_available(self, feature_name: str) -> bool:
        """检查某功能是否可用。

        - ``test_execution`` / ``basic_report`` / ``project_management`` / ``case_management``
          始终可用（开源核心，不因档位假锁执行）。
        - 其余：当前档位目录能力；standalone 开源默认解锁（可 LICENSE_ENFORCE_FEATURES=1 关闭）。
        - **企业版包含企业档全部能力**（含后续新增键，不因旧证书 features 列表滞后而误拦）。
        """
        name = (feature_name or "").strip()
        if not name:
            return False
        # 开源核心：不得因免费档锁死基础执行/用例/项目
        if name in (
            "project_management",
            "case_management",
            "test_execution",
            "basic_report",
        ):
            return True
        if self.features_unlocked_for_open_core():
            return True
        license_info = self.get_current_license()
        return name in set(self.entitled_features_for_license(license_info))

    def build_feature_denied_message(self, feature_name: str) -> str:
        """面向用户的门禁说明（不含环境变量等运维术语）。"""
        gate = self.describe_feature_gate(feature_name)
        msg = (gate.get("user_message") or "").strip()
        if msg:
            return msg
        title = gate.get("title") or feature_name or "该功能"
        return f"「{title}」当前不可用，请升级授权或联系管理员。"

    def describe_feature_gate(self, feature_name: str) -> Dict[str, Any]:
        """供 API/前端：是否可用、最低档、升级文案。"""
        name = (feature_name or "").strip()
        meta = dict(self.FEATURE_CATALOG.get(name) or {})
        ok = self.check_feature_available(name)
        limits = self.get_limits()
        title = meta.get("title") or name
        user_message = ""
        if not ok:
            # 避免递归：内联友好文案
            display = limits.get("product_display_name") or limits.get("license_type") or "当前版本"
            min_tier = str(meta.get("min_tier") or "enterprise").strip().lower()
            need_map = {
                "free": "免费版",
                "professional": "团队版",
                "enterprise": "企业版",
            }
            need = need_map.get(min_tier, min_tier)
            lt = self._normalize_license_type(limits.get("license_type"))
            tier_rank = {
                LicenseType.FREE.value: 0,
                LicenseType.PROFESSIONAL.value: 1,
                LicenseType.ENTERPRISE.value: 2,
            }
            if tier_rank.get(lt, 0) >= tier_rank.get(min_tier, 2):
                user_message = (
                    f"「{title}」当前暂时不可用。请到「License」页重新激活授权，"
                    f"或联系管理员检查授权配置。"
                )
            else:
                user_message = (
                    f"「{title}」需要{need}及以上授权。"
                    f"当前为{display}。请升级授权后使用。"
                )
        return {
            "feature": name,
            "available": ok,
            "min_tier": meta.get("min_tier") or "",
            "title": title,
            "note": meta.get("note") or "",
            "license_type": limits.get("license_type"),
            "product_display_name": limits.get("product_display_name"),
            "open_core_unlocked": self.features_unlocked_for_open_core(),
            "user_message": user_message,
            "enforce": (os.environ.get("LICENSE_ENFORCE_FEATURES") or "").strip().lower()
            in ("1", "true", "yes", "on"),
        }

    def get_limits(self) -> Dict[str, Any]:
        """获取当前 License 的限制与产品档位元数据（供 API / 前端展示）。"""
        license_info = self.get_current_license()
        lt = license_info.license_type
        try:
            enum_t = LicenseType(lt)
        except ValueError:
            enum_t = LicenseType.FREE
        features = list(license_info.features or [])
        if self.features_unlocked_for_open_core():
            # 展示层标明「开源本机已解锁试用」；证书 features 仍保留原档
            unlocked = sorted(
                set(features)
                | set(self.FEATURES.get("enterprise") or [])
                | set(self.FEATURES.get("professional") or [])
            )
        else:
            unlocked = self.entitled_features_for_license(license_info)
        return {
            'max_users': license_info.max_users,
            'max_projects': license_info.max_projects,
            'max_cases_per_project': license_info.max_cases_per_project,
            'max_executions_per_day': license_info.max_executions_per_day,
            'license_type': license_info.license_type,
            'expires_at': license_info.expires_at,
            'features': features,
            'effective_features': unlocked,
            'product_display_name': TIER_DISPLAY_NAME.get(enum_t, lt),
            'offering_summary': TIER_OFFERING_SUMMARY.get(enum_t, ''),
            # 企业版：商务上可交付私有化；是否已实施由部署环境决定
            'private_deployment_eligible': enum_t == LicenseType.ENTERPRISE,
            'open_core_features_unlocked': self.features_unlocked_for_open_core(),
            'feature_catalog': self.FEATURE_CATALOG,
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
                message = (
                    f'已达到免费版限制（{limit_type}: {limit_value}）。'
                    '请升级到团队版或企业版解除限制。'
                )
            elif license_type == LicenseType.PROFESSIONAL.value:
                message = (
                    f'已达到团队版限制（{limit_type}: {limit_value}）。'
                    '请升级到企业版解除限制。'
                )
            else:
                message = f'已达到限制（{limit_type}: {limit_value}）。'

            return {
                'allowed': False,
                'message': message,
                'limit': limit_value,
                'current': current_value
            }

        return {'allowed': True, 'message': '', 'limit': limit_value, 'current': current_value}

    def check_binding(self, license_info: LicenseInfo) -> Optional[str]:
        """校验 License 绑定（machine / instance）。"""
        btype = (license_info.binding_type or "").strip().lower()
        bid = (license_info.binding_id or "").strip()
        if not btype or btype == "none" or not bid:
            return None
        try:
            from instance_identity import get_instance_id, get_machine_id
        except ImportError:
            return None
        if btype == "machine":
            if get_machine_id() != bid:
                return f"License 已绑定其他设备（期望 {bid[:8]}…）"
        elif btype == "instance":
            if get_instance_id() != bid:
                return f"License 已绑定其他服务器实例（期望 {bid[:8]}…）"
        return None

    def activate_license_key(
        self,
        license_str: str,
        binding_type: str = "",
        binding_id: str = "",
    ) -> Dict[str, Any]:
        """激活 License；若未绑定则写入 binding。"""
        result = self.validate_license(license_str)
        if not result["valid"]:
            return result
        info = result["info"]
        if info and binding_type and binding_id:
            try:
                json_data = base64.b64decode(license_str.encode()).decode()
                data = json.loads(json_data)
                existing_bid = (data.get("binding_id") or "").strip()
                if not existing_bid:
                    data["binding_type"] = binding_type
                    data["binding_id"] = binding_id
                    renewed = LicenseInfo.from_dict(data)
                    renewed.signature = self._generate_signature(renewed.to_dict())
                    license_str = self._encode_license_str(renewed)
            except Exception:
                pass
        if self.save_license(license_str):
            self._cached_license = None
            return self.validate_license(license_str)
        return {"valid": False, "message": "保存 License 失败", "info": None}


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

    # 团队版（professional）License
    professional_license = lm.generate_license(
        LicenseType.PROFESSIONAL,
        issued_to='Professional User',
        expires_days=365
    )
    print(f"\n团队版 (professional) License: {professional_license}")

    # 验证
    result = lm.validate_license(enterprise_license)
    print(f"\n验证结果: {result}")
