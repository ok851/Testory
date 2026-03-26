"""
通知管理模块 - 支持钉钉、企微、飞书等告警通知
"""
import json
import requests
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import traceback


class NotificationType(Enum):
    DINGTALK = "dingtalk"
    WECHAT = "wechat"
    FEISHU = "feishu"
    WEBHOOK = "webhook"
    EMAIL = "email"


@dataclass
class NotificationConfig:
    """通知配置"""
    name: str
    type: str
    webhook_url: str
    enabled: bool = True
    secret: str = None  # 钉钉/企微的加签密钥
    template: str = None  # 自定义消息模板


class NotificationManager:
    """通知管理器"""

    def __init__(self):
        self.configs: List[NotificationConfig] = []
        self._load_configs()

    def _load_configs(self):
        """从数据库加载通知配置"""
        try:
            from database import Database
            db = Database()
            configs = db.get_active_notification_configs()
            for cfg in configs:
                self.configs.append(NotificationConfig(
                    name=cfg['name'],
                    type=cfg['type'],
                    webhook_url=cfg['config'].get('webhook_url', ''),
                    enabled=cfg['is_active'] == 1,
                    secret=cfg['config'].get('secret'),
                    template=cfg['config'].get('template')
                ))
        except Exception as e:
            print(f"加载通知配置失败: {e}")

    def reload_configs(self):
        """重新加载配置"""
        self.configs = []
        self._load_configs()

    def add_config(self, config: NotificationConfig):
        """添加通知配置"""
        self.configs.append(config)

    def send_notification(self, config: NotificationConfig, title: str, content: str,
                         data: Dict[str, Any] = None) -> bool:
        """发送通知"""
        if not config.enabled:
            return False

        try:
            if config.type == NotificationType.DINGTALK.value:
                return self._send_dingtalk(config, title, content, data)
            elif config.type == NotificationType.WECHAT.value:
                return self._send_wechat(config, title, content, data)
            elif config.type == NotificationType.FEISHU.value:
                return self._send_feishu(config, title, content, data)
            elif config.type == NotificationType.WEBHOOK.value:
                return self._send_webhook(config, title, content, data)
            elif config.type == NotificationType.EMAIL.value:
                return self._send_email(config, title, content, data)
            else:
                return False
        except Exception as e:
            print(f"发送通知失败: {e}")
            return False

    def _send_email(self, config: NotificationConfig, title: str, content: str,
                    data: Dict[str, Any] = None) -> bool:
        """发送邮件通知"""
        import smtplib
        import json
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        import ssl

        # 从 template 或 config 获取邮件配置
        email_config = {}
        if config.template:
            try:
                email_config = json.loads(config.template)
            except:
                pass

        smtp_server = email_config.get('smtp_server', 'smtp.qq.com')
        smtp_port = email_config.get('smtp_port', 587)
        username = email_config.get('username', '')
        password = email_config.get('password', '')
        to_emails = email_config.get('to_emails', [])

        if not all([smtp_server, username, password, to_emails]):
            print("邮件配置不完整")
            return False

        msg = MIMEMultipart()
        msg['From'] = username
        msg['To'] = ', '.join(to_emails)
        msg['Subject'] = title
        msg.attach(MIMEText(content, 'html', 'utf-8'))

        try:
            # 根据端口选择连接方式
            if smtp_port == 465:
                # SSL连接（如QQ邮箱）
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, context=context)
                server.login(username, password)
            else:
                # STARTTLS连接（如163邮箱、Gmail）
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
                server.login(username, password)
            
            server.send_message(msg)
            server.quit()
            print(f"邮件通知已发送至 {to_emails}")
            return True
        except Exception as e:
            print(f"发送邮件失败: {e}")
            return False

    def _send_dingtalk(self, config: NotificationConfig, title: str, content: str,
                       data: Dict[str, Any] = None) -> bool:
        """发送钉钉通知"""
        import time
        import hmac
        import hashlib
        import base64
        import urllib.parse

        webhook_url = config.webhook_url

        # 如果有密钥，计算签名
        if config.secret:
            timestamp = str(round(time.time() * 1000))
            secret_enc = config.secret.encode('utf-8')
            string_to_sign = f'{timestamp}\n{config.secret}'
            string_to_sign_enc = string_to_sign.encode('utf-8')
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            webhook_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"

        # 构建消息
        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {title}\n\n{content}"
            }
        }

        # 添加@功能
        if data and data.get('at_all'):
            message["at"] = {"isAtAll": True}
        elif data and data.get('at_mobiles'):
            message["at"] = {"atMobiles": data['at_mobiles']}

        response = requests.post(
            webhook_url,
            json=message,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )

        result = response.json()
        return result.get('errcode') == 0

    def _send_wechat(self, config: NotificationConfig, title: str, content: str,
                     data: Dict[str, Any] = None) -> bool:
        """发送企微通知"""
        message = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"**{title}**\n\n{content}"
            }
        }

        response = requests.post(
            config.webhook_url,
            json=message,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )

        result = response.json()
        return result.get('errcode') == 0

    def _send_feishu(self, config: NotificationConfig, title: str, content: str,
                     data: Dict[str, Any] = None) -> bool:
        """发送飞书通知"""
        import time
        import hmac
        import hashlib
        import base64

        webhook_url = config.webhook_url
        headers = {'Content-Type': 'application/json'}

        # 如果有密钥，计算签名
        if config.secret:
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{config.secret}"
            hmac_code = hmac.new(
                string_to_sign.encode('utf-8'),
                digestmod=hashlib.sha256
            ).digest()
            sign = base64.b64encode(hmac_code).decode('utf-8')

            headers['X-Lark-Request-Timestamp'] = timestamp
            headers['X-Lark-Request-Nonce'] = 'nonce'
            headers['X-Lark-Signature'] = sign

        message = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title
                    }
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": content
                        }
                    }
                ]
            }
        }

        response = requests.post(
            webhook_url,
            json=message,
            headers=headers,
            timeout=10
        )

        return response.status_code == 200

    def _send_webhook(self, config: NotificationConfig, title: str, content: str,
                      data: Dict[str, Any] = None) -> bool:
        """发送通用Webhook通知"""
        payload = {
            "title": title,
            "content": content,
            "timestamp": time.time(),
            "data": data or {}
        }

        response = requests.post(
            config.webhook_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )

        return response.status_code == 200

    def send_test_report(self, config: NotificationConfig, report_data: Dict[str, Any]) -> bool:
        """发送测试报告通知"""
        title = f"测试执行报告 - {report_data.get('project_name', '未知项目')}"

        # 根据状态设置颜色
        status = report_data.get('status', 'unknown')
        status_emoji = {
            'success': '✅',
            'failed': '❌',
            'partial': '⚠️',
            'unknown': '❓'
        }.get(status, '❓')

        content = f"""
**执行时间**: {report_data.get('execution_time', '-')}
**执行结果**: {status_emoji} {status.upper()}
**用例总数**: {report_data.get('total_cases', 0)}
**成功**: {report_data.get('successful_cases', 0)}
**失败**: {report_data.get('failed_cases', 0)}
**执行时长**: {report_data.get('duration', '-')}s

**失败用例**:
"""

        failed_cases = report_data.get('failed_case_names', [])
        if failed_cases:
            for case in failed_cases[:10]:  # 最多显示10个
                content += f"- {case}\n"
            if len(failed_cases) > 10:
                content += f"- ... 还有 {len(failed_cases) - 10} 个失败用例\n"
        else:
            content += "无\n"

        if report_data.get('report_url'):
            content += f"\n[查看详细报告]({report_data['report_url']})"

        return self.send_notification(config, title, content, report_data)

    def broadcast(self, title: str, content: str, data: Dict[str, Any] = None) -> Dict[str, bool]:
        """广播通知到所有配置"""
        results = {}
        for config in self.configs:
            if config.enabled:
                success = self.send_notification(config, title, content, data)
                results[config.name] = success
        return results


# 全局通知管理器实例
notification_manager = NotificationManager()


if __name__ == '__main__':
    # 测试代码
    import time

    nm = NotificationManager()

    # 添加测试配置
    nm.add_config(NotificationConfig(
        name="test_dingtalk",
        type="dingtalk",
        webhook_url="https://oapi.dingtalk.com/robot/send?access_token=xxx",
        secret="xxx"
    ))

    # 测试发送
    report_data = {
        "project_name": "测试项目",
        "status": "partial",
        "execution_time": "2024-01-01 12:00:00",
        "total_cases": 10,
        "successful_cases": 8,
        "failed_cases": 2,
        "duration": 120,
        "failed_case_names": ["登录测试", "下单测试"]
    }

    # nm.send_test_report(nm.configs[0], report_data)
