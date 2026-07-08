# 邮箱注册与找回密码模块 — 实施计划

## 一、需求摘要

| 需求 | 描述 |
|------|------|
| 邮箱注册 | 每个用户注册时填写自己的邮箱 + SMTP 授权码，系统用用户自己的 SMTP 发验证码到自己邮箱。不区分第一个/后续用户，注册页面永远包含 SMTP 配置+注册表单 |
| 找回密码 | 用户输入邮箱 → 用数据库存储的用户 SMTP 发验证码 → 输入验证码+新密码完成重置 |
| SMTP 存储 | 每个用户一条 SMTP 配置记录（user_smtp_configs 表），user_id 关联 |
| 记住我 | 登录时可选 7天/30天/永久 |
| 去 Admin 自建 | 删除 `_ensure_admin()`，第一个注册成功的人自动为 admin |
| 登录页文案 | 替换「本机模式默认管理员 admin…」为「没有账号？立即注册」+ 找回密码链接 |

**核心设计**：没有系统级 SMTP。每个用户在注册时提供自己的邮箱+SMTP授权码，系统用该 SMTP 向该邮箱发验证码。注册成功后 SMTP 配置持久化，供找回密码时复用。

## 二、当前状态

| 项目 | 当前状态 | 目标状态 |
|------|---------|---------|
| 用户创建方式 | admin 手动创建 / `_ensure_admin()` 自动创建 | 邮箱验证码注册 |
| 找回密码 | 不存在 | 邮箱验证码找回 |
| SMTP 配置 | 不存在 | 数据库表 + API + admin 管理页 |
| 验证码存储 | 不存在 | 内存 dict，5分钟过期 |
| 记住我 | `login_user(remember=True)` 固定 365 天 | 可选项：7天/30天/永久 |
| 登录页底栏 | admin 初始密码日志提示 | 注册 + 找回密码链接 |

## 三、数据库变更

### 3.1 新增表 `user_smtp_configs`

```sql
CREATE TABLE IF NOT EXISTS user_smtp_configs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL UNIQUE,
    email       TEXT NOT NULL,
    host        TEXT NOT NULL,
    port        INTEGER NOT NULL DEFAULT 587,
    username    TEXT NOT NULL,
    password    TEXT NOT NULL,
    use_tls     INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
```

说明：
- 每个用户一条 SMTP 配置（user_id UNIQUE）
- `email` = 用户注册邮箱，也是收验证码的地址
- `username` = SMTP 登录账号（通常同 email）
- `password` = SMTP 授权码（非邮箱登录密码）
- `host/port/use_tls` = 由前端模板自动填入，用户无需关心
- 注册时创建，找回密码时读取复用

### 3.2 新增数据库方法

`database.py` 新增：
- `save_user_smtp_config(user_id, email, host, port, username, password, use_tls)` — 插入/更新
- `get_user_smtp_config_by_email(email)` — 按邮箱查找（找回密码用）
- `get_user_smtp_config_by_user_id(user_id)` — 按用户 ID 查找

### 3.3 新增 `get_user_by_email` 方法

`database.py` 新增：
- `get_user_by_email(email)` → 按邮箱查询用户（注册时查重 / 找回密码时查找）

## 四、新增文件

| 文件 | 说明 |
|------|------|
| `mail_service.py` | SMTP 邮件发送 + 验证码生成/验证/过期管理 |
| `templates/register.html` | 注册页面（两步：填写信息 → 验证邮箱） |
| `templates/forgot_password.html` | 找回密码页面（三步：输入邮箱 → 验证码 → 新密码） |
| `templates/smtp_settings.html` | SMTP 配置管理页（admin only） |

## 五、修改文件

| 文件 | 变更 |
|------|------|
| `app.py` | ① 删除 `_ensure_admin()` 及调用<br>② 新增 `/register`、`/forgot-password` 页面路由<br>③ 新增注册API（发送验证码 + 验证并注册）、找回密码API（发送验证码 + 重置）<br>④ 修改登录 API：支持 `remember_me` 参数控制 cookie 时长<br>⑤ 新增 SMTP 配置 API（GET/PUT，admin only）<br>⑥ 新增 `_basic_email_format()` 辅助函数 |
| `database.py` | ① `smtp_configs` 表建表 DDL<br>② `get_smtp_config()` / `save_smtp_config()`<br>③ `get_user_by_email()` |
| `templates/login.html` | ① 替换底栏文案<br>② 新增「记住我」复选框（含下拉选择 7天/30天/永久）<br>③ 新增「忘记密码？」链接 |

## 六、模块详细设计

### 6.1 `mail_service.py` — 邮件发送 & 验证码管理

```python
import random
import smtplib
import threading
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict

_VERIFY_CODES: Dict[str, dict] = {}   # key=email → {code, expires_at, purpose}
_VERIFY_CODE_TTL = 300                # 5 分钟
_CLEANUP_LOCK = threading.Lock()

def _generate_code() -> str:
    return f"{random.randint(100000, 999999):06d}"

def send_verify_code(to_email: str, smtp_config: dict, purpose: str = "register") -> dict:
    """用指定的 SMTP 配置发送验证码到 to_email。返回 {success, message}。
    
    smtp_config = {host, port, username, password, use_tls, sender_email}
    由调用方从 user_smtp_configs 表或前端传入。
    """
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
    """验证验证码。返回 {success, message}。验证通过后删除验证码。"""
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
```

**关键变化**（相比之前方案）：
- `send_verify_code` 接收 `smtp_config` 参数，不再从全局/系统级配置读取
- 调用方（API）负责从请求参数中构建 smtp_config 并传入、注册成功后写数据库

### 6.2 注册页面流程（每个用户都一样，自带 SMTP）

**页面结构**：`register.html` — 所有用户看到的完全一样，不区分先后：

```
/register 页面
  │
  ├─ 步骤①：SMTP 配置区
  │     ├─ 选择邮箱模板：[QQ邮箱] [163邮箱] [Gmail] [Outlook] [自定义]
  │     ├─ 模板自动填入服务器/端口/TLS（灰显不可编辑）
  │     ├─ 用户填写：邮箱地址 + SMTP 授权码
  │     └─ 「测试发送」按钮（可选，验证 SMTP 是否能发信）
  │
  ├─ 步骤②：注册表单
  │     ├─ 用户名 + 密码 + 确认密码
  │     ├─ 「发送验证码」按钮 → 用步骤①的 SMTP 发验证码到填写的邮箱
  │     ├─ 6 位验证码输入框
  │     └─ 「注册」按钮
  │
  └─ 注册成功 → 自动登录 → 跳转首页
       （count_users()==0 时 role=admin，否则 role=tester）
```

**SMTP 配置区**（始终显示）：
- 模板按钮一行：`[QQ邮箱] [163邮箱] [Gmail] [Outlook] [自定义]`
- 点击模板后自动填入服务器/端口/TLS，灰显不可编辑
- 用户只需填两个字段：邮箱地址 + SMTP 授权码
- 「测试发送」按钮 → 往自己邮箱发一封测试信 → 显示结果
  - 测试是可选的，不阻塞注册流程

**注册表单**：
- 用户名 + 密码 + 确认密码 — 请注意邮箱已在 SMTP 区填写，不再重复
- 「发送验证码」按钮 → 调用 `POST /api/auth/register/send-code`
  - 参数: `{username, email, password, confirm_password, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_tls}`
  - 后端：校验字段 → 构建 smtp_config → `send_verify_code(email, smtp_config, "register")`
- 6 位验证码 → 「注册」按钮 → `POST /api/auth/register/confirm`
  - 参数: `{username, email, password, code, smtp_host, ...}`
  - 后端：`verify_code()` → `count_users()==0 ? admin : tester` → `create_user()` → `save_user_smtp_config()` → `login_user()`

**找回密码时读取 SMTP**：
- 用户输入邮箱 → 后端用 `get_user_smtp_config_by_email()` 读取该用户的 SMTP 配置 → `send_verify_code(email, smtp_config, "reset_password")`

### 6.3 找回密码 API 流程（两步）

**Step 1**: `POST /api/auth/forgot-password/send-code`
- 参数: `{email}`
- 查找用户 → 注册邮箱存在时发送验证码
- 统一返回 `{success: True, message: "若邮箱已注册，验证码已发送"}`（防枚举）

**Step 2**: `POST /api/auth/forgot-password/reset`
- 参数: `{email, code, new_password, confirm_password}`
- 验证 `verify_code(email, code, "reset_password")`
- 校验新密码 → `db.update_user(user_id, password_hash=new_hash)`
- 返回 `{success, message}`

### 6.4 "记住我"功能

**登录页**: 新增复选框 `#rememberMe` + 下拉 `#rememberDuration`（7天 / 30天 / 永久）

**登录 API**: `POST /api/auth/login` 新增可选参数 `remember_me`（bool）和 `remember_duration`（7/30/365）

```python
# app.py — api_login 中修改
remember = body.get('remember_me', False)
duration_days = int(body.get('remember_duration', 7) or 7)
if remember and duration_days > 0:
    # Flask-Login 默认 365 天；通过 session.permanent 控制
    session.permanent = True
    app.permanent_session_lifetime = datetime.timedelta(days=duration_days)
login_user(user, remember=remember)
```

**前端 JS**: 勾选"记住我"时显示时长下拉；提交时将 `remember_me` 和 `remember_duration` 一同发送。

### 6.5 SMTP 模板 & 测试发送

**内置 SMTP 模板**（前端 JS 常量，注册页使用）：

| 模板 | 服务器 | 端口 | TLS | 说明 |
|------|--------|------|-----|------|
| QQ邮箱 | smtp.qq.com | 587 | ✅ | 需在 QQ邮箱设置中开启 POP3/SMTP 服务获取授权码 |
| 163邮箱 | smtp.163.com | 465 | ✅ | 需在 163邮箱设置中开启 SMTP 服务获取授权码 |
| Gmail | smtp.gmail.com | 587 | ✅ | 需开启两步验证 + 应用专用密码 |
| Outlook | smtp.office365.com | 587 | ✅ | 使用登录密码或应用密码 |
| 自定义 | 手动填 | 手动填 | 可切换 | 适用于其他邮件服务商 |

**测试发送 API**：

| 路由 | 方法 | 权限 | 说明 |
|------|------|------|------|
| `/api/auth/smtp-test` | POST | 公开 | 注册页「测试发送」按钮，参数: `{host, port, username, password, use_tls, to_email}` |

> 注：SMTP 配置不再有系统级 CRUD API。每个用户的 SMTP 配置通过注册 API 的 send-code/confirm 参数传入，注册成功后由 `save_user_smtp_config()` 存入 `user_smtp_configs` 表。找回密码时由 `get_user_smtp_config_by_email()` 读取。

### 6.6 登录页修改

**底栏文案替换**（`login.html:L28-L35`）:
```html
<!-- 替换前 -->
{% if is_local_standalone %}
本机模式默认管理员 admin，初始密码见 ...
{% else %}
请使用团队服务器账号登录（账号由管理员创建）
{% endif %}

<!-- 替换后 -->
<p style="margin-top:1.25rem;text-align:center;font-size:0.8rem;color:#64748b">
    没有账号？<a href="/register">立即注册</a>
    &nbsp;&nbsp;|&nbsp;&nbsp;
    <a href="/forgot-password">忘记密码？</a>
</p>
```

**新增"记住我"区域**（密码框下方，登录按钮上方）:
```html
<div style="display:flex;align-items:center;gap:8px;margin-bottom:1rem;font-size:0.8rem;color:#94a3b8;">
    <label style="display:flex;align-items:center;gap:4px;">
        <input type="checkbox" id="rememberMe" style="accent-color:#a5b4fc;">
        记住登录状态
    </label>
    <select id="rememberDuration" style="...">
        <option value="7">7 天</option>
        <option value="30">30 天</option>
        <option value="365">永久</option>
    </select>
</div>
```

### 6.7 页面路由汇总

| 路由 | 方法 | 权限 | 说明 |
|------|------|------|------|
| `/register` | GET | 公开 | 注册页面 |
| `/forgot-password` | GET | 公开 | 找回密码页面 |
| `/admin/smtp` | GET | admin | SMTP 配置页面 |
| `/api/auth/register/send-code` | POST | 公开 | 发送注册验证码 |
| `/api/auth/register/confirm` | POST | 公开 | 验证码 + 完成注册 |
| `/api/auth/forgot-password/send-code` | POST | 公开 | 发送找回密码验证码（读取用户 SMTP） |
| `/api/auth/forgot-password/reset` | POST | 公开 | 验证码 + 重置密码 |
| `/api/auth/smtp-test` | POST | 公开 | 测试 SMTP 发送（注册页用） |

## 七、变更汇总

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 1 | `app.py` | 删除 | `_ensure_admin()` 函数定义 + 调用（~30行） |
| 2 | `app.py` | 新增 | `/register` 路由 |
| 3 | `app.py` | 新增 | `/forgot-password` 路由 |
| 4 | `app.py` | 新增 | 2 个注册 API (`send-code` + `confirm`) |
| 5 | `app.py` | 新增 | 2 个找回密码 API (`send-code` + `reset`) |
| 6 | `app.py` | 新增 | 1 个 SMTP 测试 API (`smtp-test`) |
| 7 | `app.py` | 修改 | 登录 API：支持 `remember_me` + `remember_duration` 参数 |
| 8 | `app.py` | 新增 | `_basic_email_format()` 辅助函数 |
| 9 | `database.py` | 新增 | `user_smtp_configs` 表 DDL（init_db） |
| 10 | `database.py` | 新增 | `save_user_smtp_config()` + `get_user_smtp_config_by_email()` + `get_user_smtp_config_by_user_id()` |
| 11 | `database.py` | 新增 | `get_user_by_email()` |
| 12 | `mail_service.py` | **新建** | SMTP 邮件发送 + 验证码管理（接收 smtp_config 参数） |
| 13 | `templates/register.html` | **新建** | 注册页面（SMTP 配置区 + 注册表单） |
| 14 | `templates/forgot_password.html` | **新建** | 两步找回密码页面 |
| 15 | `templates/login.html` | 修改 | 底栏文案替换 + 记住我 + 忘记密码链接 |
| 16 | `docs/DESKTOP_PROTECTED_BUILD.md` | 可选 | 更新 backend_startup.log 引用 |

## 八、不需要修改的文件（补充）

## 九、校验步骤

1. **Python 语法**: `py_compile` 编译 `app.py`、`database.py`、`mail_service.py`
2. **Rust**: `cargo check` — 无影响，Rust 代码不引用 admin 创建逻辑
3. **现有测试**: `pytest tests/ -v` — 删除 `_ensure_admin` 不影响已有测试
4. **手测流程**:
   - 删除 `test_cases.db` → 启动 → `/login` 看到「没有账号？立即注册」+「忘记密码？」
   - 点击注册 → 进入 `/register` → 看到 SMTP 配置区 + 注册表单（每个用户都一样）
   - 点击「QQ邮箱」模板 → 自动填入 smtp.qq.com:587
   - 填写自己的 QQ邮箱地址 + QQ邮箱授权码
   - 填写用户名 + 密码 + 确认密码 → 点「发送验证码」→ 邮箱收到 6 位码
   - 输入验证码 → 点「注册」→ 自动登录跳转首页，角色为 admin（首个用户）
   - 退出 → 另一个用户访问 `/register` → 同样看到 SMTP 区 + 表单（无区别）
   - 用 163邮箱填写 → 发送验证码 → 注册成功 → 角色为 tester
   - 登录页勾选「记住我 30天」→ 登录 → 关闭浏览器重开 → 仍为登录态
   - 退出 → 点击「忘记密码？」→ 输入邮箱 → 系统读取该用户的 SMTP 发验证码 → 重置密码 → 用新密码登录
