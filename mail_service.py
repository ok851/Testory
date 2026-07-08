import random
import smtplib
import threading
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict

_VERIFY_CODES: Dict[str, dict] = {}
_VERIFY_CODE_TTL = 300
_CLEANUP_LOCK = threading.Lock()


def _generate_code() -> str:
    return f"{random.randint(100000, 999999):06d}"


def send_verify_code(to_email: str, smtp_config: dict, purpose: str = "register") -> dict:
    code = _generate_code()
    expires_at = time.time() + _VERIFY_CODE_TTL

    subject_map = {"register": "邮箱验证码 - 注册 Testory", "reset_password": "邮箱验证码 - 找回密码"}
    subject = subject_map.get(purpose, f"邮箱验证码 - {purpose}")
    sender = smtp_config.get("sender_email") or smtp_config.get("username", "")
    body = f"""您的验证码是：{code}

有效期 5 分钟，请勿泄露给他人。

此邮件由系统自动发送，请勿回复。"""

    try:
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        server = smtplib.SMTP(smtp_config["host"], int(smtp_config.get("port", 587)), timeout=15)
        if smtp_config.get("use_tls", True):
            server.starttls()
        server.login(smtp_config["username"], smtp_config["password"])
        server.sendmail(sender, [to_email], msg.as_string())
        server.quit()

        with _CLEANUP_LOCK:
            _VERIFY_CODES[to_email] = {"code": code, "expires_at": expires_at, "purpose": purpose}

        return {"success": True, "message": "验证码已发送"}
    except Exception as e:
        return {"success": False, "message": f"邮件发送失败: {str(e)}"}


def verify_code(email: str, code: str, purpose: str = "register") -> dict:
    email = (email or "").strip().lower()
    code = (code or "").strip()

    with _CLEANUP_LOCK:
        entry = _VERIFY_CODES.get(email)
        if not entry:
            return {"success": False, "message": "请先发送验证码"}
        if entry["purpose"] != purpose:
            return {"success": False, "message": "验证码用途不匹配"}
        if time.time() > entry["expires_at"]:
            del _VERIFY_CODES[email]
            return {"success": False, "message": "验证码已过期，请重新发送"}
        if entry["code"] != code:
            return {"success": False, "message": "验证码错误"}
        del _VERIFY_CODES[email]
        return {"success": True, "message": "验证通过"}
