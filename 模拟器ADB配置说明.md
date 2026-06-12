# 模拟器ADB配置说明

## 问题描述

平台默认使用插件市场的ADB（`platform-tools/adb.exe`），但第三方模拟器（雷电、夜神等）有自己的ADB。如果两者版本不一致，会导致无法检测到模拟器设备。

## 解决方案

### 方法一：设置环境变量（推荐）

#### Windows系统

1. **找到模拟器的ADB路径**
   - 雷电模拟器：`D:\leidian\LDPlayer14\adb.exe`（根据实际安装目录调整）
   - 夜神模拟器：`C:\Program Files\Nox\bin\adb.exe`
   - 其他模拟器：查找模拟器安装目录下的 `adb.exe`

2. **设置环境变量**
   - 右键"此电脑" → "属性" → "高级系统设置" → "环境变量"
   - 在"系统变量"中点击"新建"
   - 变量名：`ADB_PATH`
   - 变量值：`D:\leidian\LDPlayer14\adb.exe`（替换为实际路径）
   - 点击"确定"保存

3. **重启Flask服务**
   ```bash
   # 停止当前运行的Flask服务（Ctrl+C）
   # 重新启动
   python app.py
   ```

4. **验证配置**
   - 刷新移动端测试页面
   - 查看"环境准备"区域显示的ADB路径是否已更新
   - 启动模拟器后，点击"连接设备"下拉框应能看到模拟器设备

#### macOS/Linux系统

```bash
# 编辑 ~/.bashrc 或 ~/.zshrc
export ADB_PATH=/path/to/emulator/adb

# 重新加载配置
source ~/.bashrc  # 或 source ~/.zshrc

# 重启Flask服务
python app.py
```

### 方法二：添加到系统PATH

将模拟器ADB所在目录添加到系统PATH环境变量中：

#### Windows
1. 打开"环境变量"设置
2. 找到"Path"变量，点击"编辑"
3. 添加新条目：`D:\leidian\LDPlayer14`（模拟器ADB所在目录）
4. 将该条目移到最顶部（确保优先使用）
5. 重启Flask服务

#### macOS/Linux
```bash
export PATH=/path/to/emulator:$PATH
```

### 方法三：修改配置文件（持久化）

编辑项目根目录的 `.env` 文件，添加：

```env
# 指定ADB路径
ADB_PATH=D:\leidian\LDPlayer14\adb.exe
```

## ADB优先级说明

平台按以下顺序查找ADB：

1. **环境变量 `ADB_PATH`**（用户自定义，最高优先级）
2. **插件市场的 Platform-Tools**（默认）
3. **配置文件中的 `adb_path`**
4. **系统PATH中的 `adb`**（最低优先级）

## 常见问题

### Q: 如何确认当前使用的ADB路径？

A: 刷新移动端测试页面，在左侧"环境准备"区域会显示当前ADB路径和来源：
- `env` = 来自环境变量
- `plugin` = 来自插件市场
- `config` = 来自配置文件
- `default` = 来自系统PATH

### Q: 设置了环境变量后仍然检测不到模拟器？

A: 检查以下几点：
1. 环境变量是否正确设置（注意大小写和路径分隔符）
2. 是否重启了Flask服务
3. 模拟器是否已完全启动
4. 使用命令行测试：`%ADB_PATH% devices`（Windows）或 `$ADB_PATH devices`（Linux/macOS）

### Q: 多个模拟器同时运行怎么办？

A: 选择其中一个模拟器的ADB作为主ADB即可。大多数情况下，不同模拟器的ADB可以互相识别设备。

### Q: 真机和模拟器可以同时连接吗？

A: 可以。只要ADB能同时检测到两个设备，平台会自动列出所有可用设备供选择。

## 技术支持

如仍有问题，请提供以下信息：
1. 模拟器类型和版本
2. ADB路径截图
3. 浏览器控制台错误信息
4. Flask后端日志

联系邮箱：support@example.com
