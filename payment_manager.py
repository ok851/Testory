"""
支付管理模块 - 支持支付宝、微信支付
"""
import json
import hashlib
import hmac
import time
import uuid
import secrets
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from database import Database
from license_manager import license_manager, LicenseType
from time_utils import utc_now_sqlite_str, to_beijing_iso, utc_sqlite_str_plus_days


class PaymentMethod(Enum):
    """支付方式"""
    ALIPAY = "alipay"           # 支付宝
    WECHAT = "wechat"           # 微信支付
    MANUAL = "manual"           # 线下付款


class PaymentStatus(Enum):
    """支付状态"""
    PENDING = "pending"         # 待支付
    PAID = "paid"               # 已支付
    FAILED = "failed"           # 支付失败
    CANCELLED = "cancelled"     # 已取消
    REFUNDED = "refunded"       # 已退款


class PlanType(Enum):
    """套餐类型"""
    FREE = "free"               # 免费版
    PERSONAL_PRO = "personal_pro"  # 团队版（SaaS 付费），历史键名保留
    TEAM = "team"               # 团队版
    ENTERPRISE = "enterprise"   # 企业版
    FLAGSHIP = "flagship"       # 旗舰版


# 套餐定价配置 (单位: 分)
PLAN_PRICES = {
    PlanType.FREE.value: {
        'monthly': 0,
        'yearly': 0,
        'name': '免费版',
        'features': ['基础功能', '3个项目', '50个用例/项目', '10次/天执行']
    },
    PlanType.PERSONAL_PRO.value: {
        'monthly': 2900,        # ¥29/月
        'yearly': 29900,        # ¥299/年 (约8.3折)
        'name': '团队版',
        'features': ['无限执行', '10个项目', '200个用例/项目', '定时任务', '邮件通知', 'PDF/Excel导出']
    },
    PlanType.TEAM.value: {
        'monthly': 9900,        # ¥99/人/月
        'yearly': 99900,        # ¥999/人/年
        'name': '团队版',
        'features': ['专业版全部功能', '30个项目', '团队协作', '全渠道通知', 'API访问']
    },
    PlanType.ENTERPRISE.value: {
        'monthly': 29900,       # ¥299/人/月
        'yearly': 299900,       # ¥2999/人/年
        'name': '企业版',
        'features': ['团队版全部功能', '无限项目', '私有化部署', 'SSO单点登录', '专属客服']
    },
    PlanType.FLAGSHIP.value: {
        'monthly': 59900,       # ¥599/人/月
        'yearly': 599900,       # ¥5999/人/年
        'name': '旗舰版',
        'features': ['企业版全部功能', '定制开发', '现场支持', '专属培训']
    }
}


@dataclass
class PaymentConfig:
    """支付配置"""
    # 支付宝配置
    alipay_app_id: str = ""
    alipay_private_key: str = ""
    alipay_public_key: str = ""
    alipay_gateway: str = "https://openapi.alipay.com/gateway.do"
    alipay_notify_url: str = ""
    alipay_return_url: str = ""
    
    # 微信支付配置
    wechat_app_id: str = ""
    wechat_mch_id: str = ""
    wechat_api_key: str = ""
    wechat_notify_url: str = ""


class PaymentManager:
    """支付管理器"""
    
    def __init__(self, db: Database = None, config: PaymentConfig = None):
        self.db = db or Database()
        self.config = config or PaymentConfig()
        self._ensure_order_license_table()

    def _ensure_order_license_table(self):
        """确保订单 License 映射表存在（兼容老库升级）"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                license_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    
    # ==================== 订单管理 ====================
    
    def generate_order_no(self) -> str:
        """生成订单号"""
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        random_part = secrets.token_hex(4).upper()
        return f"UAT{timestamp}{random_part}"
    
    def create_order(self, user_id: int, plan_type: str, period: str = 'monthly', 
                     quantity: int = 1, tenant_id: int = None) -> Dict[str, Any]:
        """
        创建订单
        
        Args:
            user_id: 用户ID
            plan_type: 套餐类型
            period: 周期 monthly/yearly
            quantity: 数量（用户数）
            tenant_id: 租户ID
            
        Returns:
            订单信息
        """
        import sqlite3
        
        plan_info = PLAN_PRICES.get(plan_type)
        if not plan_info:
            raise ValueError(f"无效的套餐类型: {plan_type}")
        
        price_key = 'yearly' if period == 'yearly' else 'monthly'
        unit_price = plan_info[price_key]
        total_amount = unit_price * quantity
        
        # 计算过期时间
        if period == 'yearly':
            expires_at_sql = utc_sqlite_str_plus_days(365)
        else:
            expires_at_sql = utc_sqlite_str_plus_days(30)
        
        order_no = self.generate_order_no()
        
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO orders (order_no, user_id, tenant_id, plan_type, amount, status, expires_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
        ''', (order_no, user_id, tenant_id, plan_type, total_amount, expires_at_sql))
        
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            'order_id': order_id,
            'order_no': order_no,
            'plan_type': plan_type,
            'plan_name': plan_info['name'],
            'amount': total_amount,
            'amount_yuan': total_amount / 100,
            'period': period,
            'quantity': quantity,
            'expires_at': to_beijing_iso(expires_at_sql),
        }
    
    def get_order(self, order_id: int = None, order_no: str = None) -> Optional[Dict[str, Any]]:
        """获取订单详情"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        if order_id:
            cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        elif order_no:
            cursor.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,))
        else:
            return None
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        plan_info = PLAN_PRICES.get(row[4], {})
        return {
            'id': row[0],
            'order_no': row[1],
            'user_id': row[2],
            'tenant_id': row[3],
            'plan_type': row[4],
            'plan_name': plan_info.get('name', row[4]),
            'amount': row[5],
            'amount_yuan': row[5] / 100,
            'currency': row[6],
            'status': row[7],
            'payment_method': row[8],
            'payment_channel': row[9],
            'transaction_id': row[10],
            'paid_at': to_beijing_iso(row[11]),
            'expires_at': to_beijing_iso(row[12]),
            'created_at': to_beijing_iso(row[13])
        }
    
    def get_user_orders(self, user_id: int, page: int = 1, page_size: int = 20) -> Tuple[list, int]:
        """获取用户订单列表"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        offset = (page - 1) * page_size
        
        cursor.execute(
            "SELECT COUNT(*) FROM orders WHERE user_id = ?",
            (user_id,)
        )
        total = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT * FROM orders WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ? OFFSET ?
        ''', (user_id, page_size, offset))
        
        rows = cursor.fetchall()
        conn.close()
        
        orders = []
        for row in rows:
            plan_info = PLAN_PRICES.get(row[4], {})
            orders.append({
                'id': row[0],
                'order_no': row[1],
                'plan_type': row[4],
                'plan_name': plan_info.get('name', row[4]),
                'amount_yuan': row[5] / 100,
                'status': row[7],
                'payment_method': row[8],
                'paid_at': to_beijing_iso(row[11]),
                'expires_at': to_beijing_iso(row[12]),
                'created_at': to_beijing_iso(row[13])
            })
        
        return orders, total
    
    def update_order_status(self, order_no: str, status: str, payment_method: str = None,
                           transaction_id: str = None) -> bool:
        """更新订单状态"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        paid_at = utc_now_sqlite_str() if status == PaymentStatus.PAID.value else None
        
        cursor.execute('''
            UPDATE orders 
            SET status = ?, payment_method = ?, transaction_id = ?, paid_at = ?
            WHERE order_no = ?
        ''', (status, payment_method, transaction_id, paid_at, order_no))
        
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        
        # 如果支付成功，激活订阅
        if success and status == PaymentStatus.PAID.value:
            self._activate_subscription(order_no)
        
        return success

    def cancel_order(self, order_no: str, user_id: int) -> Tuple[bool, str]:
        """取消订单（仅待支付订单可取消）"""
        import sqlite3
        order = self.get_order(order_no=order_no)
        if not order:
            return False, "订单不存在"
        if order['user_id'] != user_id:
            return False, "无权限取消此订单"
        if order['status'] != PaymentStatus.PENDING.value:
            return False, f"当前订单状态为 {order['status']}，无法取消"

        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE orders SET status = ? WHERE order_no = ?",
            (PaymentStatus.CANCELLED.value, order_no)
        )
        conn.commit()
        ok = cursor.rowcount > 0
        conn.close()
        return (ok, "" if ok else "取消失败")

    def get_or_create_order_license(self, order_no: str) -> Optional[str]:
        """为已支付订单获取或生成 license key。"""
        import sqlite3
        order = self.get_order(order_no=order_no)
        if not order or order['status'] != PaymentStatus.PAID.value:
            return None

        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT license_key FROM order_licenses WHERE order_no = ?", (order_no,))
        row = cursor.fetchone()
        if row and row[0]:
            conn.close()
            return row[0]

        # 根据套餐映射为 license 类型
        plan = order.get('plan_type')
        if plan in (PlanType.ENTERPRISE.value, PlanType.FLAGSHIP.value):
            lt = LicenseType.ENTERPRISE
        else:
            lt = LicenseType.PROFESSIONAL

        issued_to = f"user-{order.get('user_id')}"
        expires_days = 365
        try:
            exp = order.get('expires_at')
            if exp:
                exp_dt = datetime.fromisoformat(str(exp))
                delta_days = (exp_dt - datetime.now()).days
                if delta_days > 0:
                    expires_days = delta_days
        except Exception:
            pass

        key = license_manager.generate_license(lt, issued_to=issued_to, expires_days=expires_days)
        cursor.execute(
            "INSERT INTO order_licenses (order_no, user_id, license_key) VALUES (?, ?, ?)",
            (order_no, order['user_id'], key)
        )
        conn.commit()
        conn.close()
        return key
    
    def _activate_subscription(self, order_no: str):
        """激活订阅"""
        import sqlite3
        
        order = self.get_order(order_no=order_no)
        if not order:
            return
        
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # 检查是否已有订阅
        cursor.execute(
            "SELECT id FROM subscriptions WHERE user_id = ? AND status = 'active'",
            (order['user_id'],)
        )
        existing = cursor.fetchone()
        
        if existing:
            # 更新现有订阅
            cursor.execute('''
                UPDATE subscriptions 
                SET plan_type = ?, expires_at = ?
                WHERE id = ?
            ''', (order['plan_type'], order['expires_at'], existing[0]))
        else:
            # 创建新订阅
            cursor.execute('''
                INSERT INTO subscriptions (user_id, tenant_id, plan_type, status, expires_at)
                VALUES (?, ?, ?, 'active', ?)
            ''', (order['user_id'], order['tenant_id'], order['plan_type'], order['expires_at']))
        
        conn.commit()
        conn.close()
    
    # ==================== 支付宝接口 ====================
    
    def create_alipay_payment(self, order_no: str) -> Dict[str, Any]:
        """
        创建支付宝支付
        
        Returns:
            支付页面URL或二维码内容
        """
        order = self.get_order(order_no=order_no)
        if not order:
            raise ValueError("订单不存在")
        
        if order['status'] != PaymentStatus.PENDING.value:
            raise ValueError(f"订单状态异常: {order['status']}")
        
        plan_info = PLAN_PRICES.get(order['plan_type'], {})
        subject = f"AI自动化测试平台 - {plan_info.get('name', '套餐升级')}"
        
        # 构建支付宝请求参数
        biz_content = {
            "out_trade_no": order_no,
            "total_amount": f"{order['amount'] / 100:.2f}",
            "subject": subject,
            "product_code": "FAST_INSTANT_TRADE_PAY"
        }
        
        # 这里需要实际调用支付宝SDK
        # 由于需要支付宝密钥配置，这里返回模拟数据
        return {
            'success': True,
            'order_no': order_no,
            'amount': order['amount'] / 100,
            'payment_url': f"/api/payment/alipay/mock?order_no={order_no}",
            'qr_code': None,  # 扫码支付时返回二维码内容
            'message': '请使用支付宝扫码或点击链接完成支付'
        }
    
    def verify_alipay_callback(self, params: Dict[str, str]) -> Tuple[bool, str]:
        """
        验证支付宝回调
        
        Returns:
            (is_valid, order_no)
        """
        # 实际应用中需要验证签名
        order_no = params.get('out_trade_no')
        trade_status = params.get('trade_status')
        
        if trade_status in ('TRADE_SUCCESS', 'TRADE_FINISHED'):
            transaction_id = params.get('trade_no')
            self.update_order_status(
                order_no=order_no,
                status=PaymentStatus.PAID.value,
                payment_method=PaymentMethod.ALIPAY.value,
                transaction_id=transaction_id
            )
            return True, order_no
        
        return False, order_no
    
    # ==================== 微信支付接口 ====================
    
    def create_wechat_payment(self, order_no: str, payment_type: str = 'native') -> Dict[str, Any]:
        """
        创建微信支付
        
        Args:
            order_no: 订单号
            payment_type: 支付类型 native(扫码) / jsapi(公众号) / h5(H5支付)
            
        Returns:
            支付信息
        """
        order = self.get_order(order_no=order_no)
        if not order:
            raise ValueError("订单不存在")
        
        if order['status'] != PaymentStatus.PENDING.value:
            raise ValueError(f"订单状态异常: {order['status']}")
        
        plan_info = PLAN_PRICES.get(order['plan_type'], {})
        body = f"AI自动化测试平台 - {plan_info.get('name', '套餐升级')}"
        
        # 构建微信支付请求参数
        # 实际应用中需要调用微信支付API
        return {
            'success': True,
            'order_no': order_no,
            'amount': order['amount'] / 100,
            'payment_type': payment_type,
            'code_url': f"weixin://wxpay/bizpayurl?pr={order_no}",  # 模拟二维码内容
            'message': '请使用微信扫码完成支付'
        }
    
    def verify_wechat_callback(self, xml_data: str) -> Tuple[bool, str]:
        """
        验证微信支付回调
        
        Returns:
            (is_valid, order_no)
        """
        # 实际应用中需要解析XML并验证签名
        import xml.etree.ElementTree as ET
        
        try:
            root = ET.fromstring(xml_data)
            result_code = root.find('result_code').text
            order_no = root.find('out_trade_no').text
            
            if result_code == 'SUCCESS':
                transaction_id = root.find('transaction_id').text
                self.update_order_status(
                    order_no=order_no,
                    status=PaymentStatus.PAID.value,
                    payment_method=PaymentMethod.WECHAT.value,
                    transaction_id=transaction_id
                )
                return True, order_no
        except:
            pass
        
        return False, ""
    
    # ==================== 订阅管理 ====================
    
    def get_user_subscription(self, user_id: int) -> Optional[Dict[str, Any]]:
        """获取用户当前订阅"""
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM subscriptions 
            WHERE user_id = ? AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
        ''', (user_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        plan_info = PLAN_PRICES.get(row[3], {})
        
        return {
            'id': row[0],
            'user_id': row[1],
            'tenant_id': row[2],
            'plan_type': row[3],
            'plan_name': plan_info.get('name', row[3]),
            'features': plan_info.get('features', []),
            'status': row[4],
            'started_at': row[5],
            'expires_at': row[6],
            'auto_renew': bool(row[7])
        }
    
    def check_subscription_valid(self, user_id: int) -> Tuple[bool, str]:
        """
        检查用户订阅是否有效
        
        Returns:
            (is_valid, plan_type)
        """
        subscription = self.get_user_subscription(user_id)
        
        if not subscription:
            return True, PlanType.FREE.value  # 无订阅视为免费版
        
        expires_at = subscription.get('expires_at')
        if expires_at:
            try:
                expire_time = datetime.fromisoformat(expires_at)
                if datetime.now() > expire_time:
                    return True, PlanType.FREE.value  # 已过期，降级为免费版
            except:
                pass
        
        return True, subscription['plan_type']
    
    # ==================== 套餐信息 ====================
    
    def get_plan_list(self) -> list:
        """获取所有套餐列表"""
        plans = []
        for plan_type, info in PLAN_PRICES.items():
            plans.append({
                'type': plan_type,
                'name': info['name'],
                'monthly_price': info['monthly'] / 100,
                'yearly_price': info['yearly'] / 100,
                'features': info['features']
            })
        return plans
    
    def get_plan_info(self, plan_type: str) -> Optional[Dict[str, Any]]:
        """获取套餐详情"""
        info = PLAN_PRICES.get(plan_type)
        if not info:
            return None
        
        return {
            'type': plan_type,
            'name': info['name'],
            'monthly_price': info['monthly'] / 100,
            'yearly_price': info['yearly'] / 100,
            'features': info['features']
        }


# 全局支付管理器实例
payment_manager = PaymentManager()
