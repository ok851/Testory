# UI自动化测试平台 - 部署配置清单

## 一、系统要求

### 操作系统
- Windows 10/11（推荐）
- macOS 10.15+
- Linux（Ubuntu 18.04+）

### 硬件要求
- CPU：双核及以上
- 内存：4GB及以上
- 硬盘：至少10GB可用空间

---

## 二、环境准备

### 1. Python环境
- **Python版本要求**：3.8 或更高版本
- **下载地址**：https://www.python.org/downloads/
- **安装注意事项**：
  - 安装时勾选 "Add Python to PATH"
  - 验证安装：在命令行输入 `python --version`

### 2. Git（可选，用于代码版本管理）
- **下载地址**：https://git-scm.com/downloads

---

## 三、项目部署步骤

### 步骤1：获取项目代码
```bash
# 如果从Git仓库获取
git clone <项目仓库地址>
cd NewUITestPlatform

# 或者直接解压项目压缩包到目标目录
```

### 步骤2：创建虚拟环境（推荐）
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS/Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 步骤3：安装Python依赖包
```bash
pip install -r requirements.txt
```

**requirements.txt 包含的依赖：**
- Flask==2.3.3 - Web框架
- Flask-CORS==4.0.0 - 跨域支持
- playwright==1.40.0 - 浏览器自动化

### 步骤4：安装Playwright浏览器
```bash
# 安装Chromium、Firefox、WebKit浏览器
playwright install

# 或者只安装Chromium（推荐，体积较小）
playwright install chromium
```

**注意**：首次安装可能需要几分钟，需要下载浏览器二进制文件。

---

## 四、数据库配置

项目使用 **SQLite** 数据库，无需额外安装数据库软件。

- **数据库文件**：`test_cases.db`
- **自动初始化**：首次运行时会自动创建数据库和表结构

---

## 五、启动项目

### 方式1：直接运行
```bash
python app.py
```

### 方式2：使用虚拟环境运行
```bash
# Windows
.venv\Scripts\activate
python app.py

# macOS/Linux
source .venv/bin/activate
python app.py
```

启动成功后，控制台会显示：
```
* Serving Flask app 'app'
* Debug mode: on
* Running on all addresses (0.0.0.0)
* Running on http://127.0.0.1:5000
* Running on http://<本机IP>:5000
```

---

## 六、访问系统

打开浏览器访问以下地址：
- **本地访问**：http://127.0.0.1:5000
- **局域网访问**：http://<本机IP>:5000

---

## 七、可选依赖（OCR功能）

如果需要使用OCR文字识别功能，需要安装以下组件：

### 1. Tesseract OCR
- **Windows下载**：https://github.com/UB-Mannheim/tesseract/wiki
- **安装路径**：建议安装到 `C:\Program Files\Tesseract-OCR\`
- **配置环境变量**：将Tesseract安装路径添加到系统PATH

### 2. 安装Python OCR库
```bash
pip install opencv-python numpy pytesseract
```

---

## 八、常见问题解决

### 问题1：playwright install 下载失败
**解决方案**：
- 配置代理：`set HTTPS_PROXY=http://your-proxy:port`
- 或者手动下载浏览器包

### 问题2：端口5000被占用
**解决方案**：
- 修改app.py最后一行，更换端口：
  ```python
  app.run(debug=True, host='0.0.0.0', port=5001)
  ```

### 问题3：数据库文件权限问题
**解决方案**：
- Windows：以管理员身份运行
- Linux/macOS：`chmod 755 .`

### 问题4：虚拟环境激活失败
**解决方案**：
- Windows PowerShell：`Set-ExecutionPolicy RemoteSigned -Scope CurrentUser`
- 然后重新执行激活命令

---

## 九、项目目录结构

```
NewUITestPlatform/
├── app.py                      # Flask主应用
├── database.py                 # 数据库操作
├── playwright_automation.py    # Playwright自动化
├── test_report.py             # 测试报告生成
├── report_exporter.py         # 报告导出
├── logger.py                  # 日志模块
├── requirements.txt           # Python依赖
├── test_cases.db             # SQLite数据库（自动生成）
├── templates/                 # HTML模板
│   ├── index.html
│   ├── list_projects.html
│   ├── list_cases_v2.html
│   ├── list_steps.html
│   ├── create_case_v2.html
│   ├── run_history.html
│   └── test_report.html
├── exports/                   # 导出的报告
└── logs/                      # 日志文件（自动生成）
```

---

## 十、验证部署

1. 访问 http://127.0.0.1:5000
2. 创建一个测试项目
3. 创建一个测试用例
4. 添加几个测试步骤
5. 尝试运行测试用例

如果以上步骤都能正常完成，说明部署成功！

---

## 十一、技术支持

如遇到问题，请检查：
1. Python版本是否符合要求
2. 所有依赖是否正确安装
3. Playwright浏览器是否成功安装
4. 控制台错误日志
