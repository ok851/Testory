"""
通知模块 - 支持邮件、钉钉、企业微信、飞书等多种通知方式
"""
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any
import requests
from logger import uat_logger


class NotificationManager:
    """通知管理器"""

    @staticmethod
    def send_notification(config: Dict[str, Any], event_type: str, data: Dict[str, Any]):
        """
        发送通知

        Args:
            config: 通知配置
            event_type: 事件类型 (schedule_success, schedule_failed, case_failed)
            data: 通知数据
        """
        notify_type = config.get('type')

        try:
            if notify_type == 'email':
                return NotificationManager._send_email(config, event_type, data)
            elif notify_type == 'dingtalk':
                return NotificationManager._send_dingtalk(config, event_type, data)
            elif notify_type == 'wechat':
                return NotificationManager._send_wechat(config, event_type, data)
            elif notify_type == 'feishu':
                return NotificationManager._send_feishu(config, event_type, data)
            else:
                uat_logger.warning(f"未知的通知类型: {notify_type}")
                return False
        except Exception as e:
            uat_logger.error(f"发送通知失败: {e}")
            return False

    @staticmethod
    def _send_email(config: Dict[str, Any], event_type: str, data: Dict[str, Any]):
        """发送邮件通知"""
        cfg = config.get('config', {})
        smtp_server = cfg.get('smtp_server')
        smtp_port = cfg.get('smtp_port', 587)
        username = cfg.get('username')
        password = cfg.get('password')
        to_emails = cfg.get('to_emails', [])

        if not all([smtp_server, username, password, to_emails]):
            uat_logger.error("邮件配置不完整")
            return False

        # 构建邮件内容
        subject, body = NotificationManager._build_message(event_type, data, 'email')

        msg = MIMEMultipart()
        msg['From'] = username
        msg['To'] = ', '.join(to_emails)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html', 'utf-8'))

        try:
            # 根据端口选择连接方式：465使用SSL，其他使用STARTTLS
            if smtp_port == 465:
                import ssl
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, context=context)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
            server.login(username, password)
            server.send_message(msg)
            server.quit()
            uat_logger.info(f"邮件通知已发送至 {to_emails}")
            return True
        except Exception as e:
            uat_logger.error(f"发送邮件失败: {e}")
            return False

    @staticmethod
    def _send_dingtalk(config: Dict[str, Any], event_type: str, data: Dict[str, Any]):
        """发送钉钉通知"""
        cfg = config.get('config', {})
        webhook_url = cfg.get('webhook_url')
        secret = cfg.get('secret')  # 可选的安全密钥

        if not webhook_url:
            uat_logger.error("钉钉 webhook 未配置")
            return False

        # 构建消息内容
        title, content = NotificationManager._build_message(event_type, data, 'markdown')

        # 钉钉消息格式
        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": content
            }
        }

        try:
            response = requests.post(
                webhook_url,
                json=message,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            result = response.json()
            if result.get('errcode') == 0:
                uat_logger.info("钉钉通知发送成功")
                return True
            else:
                uat_logger.error(f"钉钉通知发送失败: {result}")
                return False
        except Exception as e:
            uat_logger.error(f"发送钉钉通知失败: {e}")
            return False

    @staticmethod
    def _send_wechat(config: Dict[str, Any], event_type: str, data: Dict[str, Any]):
        """发送企业微信通知"""
        cfg = config.get('config', {})
        webhook_url = cfg.get('webhook_url')

        if not webhook_url:
            uat_logger.error("企业微信 webhook 未配置")
            return False

        title, content = NotificationManager._build_message(event_type, data, 'markdown')

        # 企业微信消息格式
        message = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"**{title}**\n\n{content}"
            }
        }

        try:
            response = requests.post(
                webhook_url,
                json=message,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            result = response.json()
            if result.get('errcode') == 0:
                uat_logger.info("企业微信通知发送成功")
                return True
            else:
                uat_logger.error(f"企业微信通知发送失败: {result}")
                return False
        except Exception as e:
            uat_logger.error(f"发送企业微信通知失败: {e}")
            return False

    @staticmethod
    def _send_feishu(config: Dict[str, Any], event_type: str, data: Dict[str, Any]):
        """发送飞书通知"""
        cfg = config.get('config', {})
        webhook_url = cfg.get('webhook_url')

        if not webhook_url:
            uat_logger.error("飞书 webhook 未配置")
            return False

        title, content = NotificationManager._build_message(event_type, data, 'markdown')

        # 飞书消息格式
        message = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "red" if "failed" in event_type else "green"
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": content}
                    }
                ]
            }
        }

        try:
            response = requests.post(
                webhook_url,
                json=message,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            result = response.json()
            if result.get('code') == 0:
                uat_logger.info("飞书通知发送成功")
                return True
            else:
                uat_logger.error(f"飞书通知发送失败: {result}")
                return False
        except Exception as e:
            uat_logger.error(f"发送飞书通知失败: {e}")
            return False

    @staticmethod
    def _build_message(event_type: str, data: Dict[str, Any], format_type: str):
        """
        构建通知消息

        Returns:
            (title, content) 或 (subject, body)
        """
        if event_type == 'schedule_success':
            title = "✅ 定时任务执行成功"
            schedule_name = data.get('schedule_name', '未知任务')
            success_count = data.get('success_count', 0)
            total_count = data.get('total_count', 0)
            duration = data.get('duration', 0)

            content = f"""
**任务名称**: {schedule_name}
**执行结果**: {success_count}/{total_count} 成功
**执行耗时**: {duration:.2f}秒
**执行时间**: {data.get('executed_at', '-')}
            """.strip()

        elif event_type == 'schedule_failed':
            title = "❌ 定时任务执行失败"
            schedule_name = data.get('schedule_name', '未知任务')
            retry_count = data.get('retry_count', 0)
            error = data.get('error', '未知错误')

            content = f"""
**任务名称**: {schedule_name}
**重试次数**: {retry_count}
**错误信息**: {error}
**执行时间**: {data.get('executed_at', '-')}
            """.strip()

        elif event_type == 'case_failed':
            title = "⚠️ 测试用例执行失败"
            case_name = data.get('case_name', '未知用例')
            error = data.get('error', '未知错误')

            content = f"""
**用例名称**: {case_name}
**错误信息**: {error}
**执行时间**: {data.get('executed_at', '-')}
            """.strip()

        elif event_type == 'case_success':
            title = "✅ 测试用例执行成功"
            case_name = data.get('case_name', '未知用例')
            duration = data.get('duration', 0)

            content = f"""
**用例名称**: {case_name}
**执行耗时**: {duration:.2f}秒
**执行时间**: {data.get('executed_at', '-')}
            """.strip()

        else:
            title = "🔔 测试平台通知"
            content = str(data)

        if format_type == 'email':
            # 构建 HTML 邮件
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6;">
                <h2>{title}</h2>
                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px;">
                    {content.replace(chr(10), '<br>')}
                </div>
                <hr style="margin-top: 20px;">
                <p style="color: #999; font-size: 12px;">
                    本邮件由 UI自动化测试平台 自动发送
                </p>
            </body>
            </html>
            """
            return title, html_body

        return title, content


def notify(event_type: str, data: Dict[str, Any]):
    """
    便捷函数：发送通知

    Args:
        event_type: 事件类型
        data: 通知数据
    """
    from database import Database

    db = Database()
    configs = db.get_active_notification_configs(event_type)

    if not configs:
        uat_logger.debug(f"没有配置 {event_type} 事件的通知")
        return

    for config in configs:
        NotificationManager.send_notification(config, event_type, data)
