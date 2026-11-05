# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## v1.0.1 — 2026-11-05

修复 Windows 端口被系统随机占用导致服务启动即报错的问题。

### 修复

- **`WinError 10013` 启动失败**：Hyper-V / WSL 的 `winnat` 服务每次开机可能随机保留一段 TCP 端口，恰好盖住 8000 时绑定被系统拒绝，打包版会弹出错误框并卡住进程。现在自动依次尝试 8001–8020、9001–9020，不再崩溃
- **单实例检测在端口被保留时失真**：改为以 pid 文件为主判断已有实例，避免误判成「服务已在运行」而直接跳过启动
- 端口发生变化时写入 `pcmonitor.log`，并弹出托盘气泡提示新的手机访问地址；托盘的「复制地址」「打开监控页面」同步跟随实际端口

### 备注

- 手机端 APK 未改动，内容与 v1.0.0 相同，仅随版本号重新打包
- 若希望固定占用 8000，可用管理员权限执行一次：

  ```bat
  net stop winnat
  netsh int ipv4 add excludedportrange protocol=tcp startport=8000 numberofports=1 store=persistent
  net start winnat
  ```

## v1.0.0 — 2026-11-01

首个公开发行版。

### 新增

- **电脑端服务**：单文件 HTTP 服务，1 秒采样一次，提供 CPU / GPU / 内存 / 显存 / 磁盘 / 网络 / 进程快照
- **GPU 全厂商支持**：`nvidia-smi` → LibreHardwareMonitor → Windows PDH 计数器三级自动回退，NVIDIA / AMD / Intel 均可读取占用率与温度
- **CPU 温度**：经 LibreHardwareMonitor 的 Web 服务读取真实核心温度，注册计划任务 `PCMonitorTempSensor` 静默拉起，只在首次需要过 UAC
- **托盘常驻**：纯 ctypes 调 Win32，无第三方 GUI 依赖；悬停显示 CPU / GPU / 内存，右键可打开页面、复制地址、设开机自启、启用温度、退出
- **静默运行**：打包为 GUI 子系统的单文件 exe，双击不弹黑框、不自动打开浏览器，日志写入 `pcmonitor.log`
- **开机自启**：写入 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，可随时从托盘取消
- **手机端页面**：横屏一屏布局、环形占比、深色主题，弱网自动重连
- **安卓 App**：强制横屏全屏、隐藏状态栏；连不上主机时显示时钟、农历、星期与天气

### 修复

- 托盘图标右键无反应（回调字典被当成对象取属性，异常被静默吞掉，菜单根本没建出来）
- 弹出菜单时未抢占前台，菜单弹出瞬间被系统关掉
- 悬停提示每秒刷新过于频繁，导致打开中的菜单被刷新顶掉
- CPU 温度恒为空（传感器值带单位 `°C`，大小写比较不匹配）
- CPU 温度传感器启动方式：LibreHardwareMonitor 清单要求管理员权限，改为计划任务静默拉起，避免每次开机弹 UAC
- 竖屏下环形图横向溢出（canvas 固有宽度撑破容器）
- 小屏下核心占用被遮挡、进程列表过宽
- `启用CPU温度.bat` 中文乱码（改为 GBK + CRLF）
- 页面与手机端长时间运行后偶发「连接中断」

### 已知限制

- 仅支持 Windows：GPU、温度、托盘、计划任务均依赖 Windows API
- 局域网内无鉴权，默认监听 `0.0.0.0:8000`，请勿直接暴露到公网
- 安卓 App 为自签名，安装时需要允许「未知来源」
