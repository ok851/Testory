# Testory 品牌与视觉规范

## 结论：打包成桌面软件后需要改什么？

桌面版通过 **pywebview** 加载与浏览器相同的 Flask 页面，**不必重写整套业务 UI**。需要统一的是：

| 层面 | 是否必须重做 | 本次处理 |
|------|-------------|----------|
| 品牌名 / Logo / 图标 | 是 | 统一为 **Testory** |
| 登录、首次配置 | 建议独立壳 | 深色 `auth_shell`，无功能导航 |
| 主工作台（项目/用例/AI） | 否 | 保留 Tailwind，换 Logo 与标题 |
| 官网 / 控制面 | 是 | 共用 SVG 图标与 favicon |
| 安装包 / 快捷方式图标 | 是 | `static/brand/app-icon.png` + `packaging/inno/testory.ico` |

## 品牌资源路径

```
static/brand/
  testory-mark.svg    # 应用图标（T + 绿点）
  testory-logo.svg    # 带字标
  favicon.svg
  app-icon.png        # Windows / 安装包用位图
website/static/brand/ # 官网副本（同内容）
platform_admin/static/brand/
packaging/inno/testory.ico
```

重新生成 `.ico`：

```powershell
python packaging/generate_brand_icons.py
```

## 视觉 Token

- 主渐变：`#6366f1` → `#8b5cf6` → `#06b6d4`
- 成功点缀：`#34d399`
- 深色背景：`#050816`（官网 / 登录壳）

## 代码入口

- 产品名常量：`brand_config.py`
- 应用注入：`deployment_config.deployment_context()`
- 应用样式：`static/css/testory-brand.css`
- 登录 / 首次配置：`templates/auth_shell.html`

## 安装包命名

- 窗口标题：`Testory`（`packaging/uat_desktop.py`）
- 安装包输出：`dist/testory_setup.exe`（Inno Setup）
