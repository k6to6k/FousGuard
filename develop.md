专注限额工具 (FocusGuard) MVP 开发文档
1. 项目概述
项目目标： 开发一款 Windows 平台的极简效率工具，通过监控前台窗口，阻断用户对娱乐软件和网站的访问，强制保持专注。

核心原则： 最小可行产品（MVP），不包含复杂社交功能，注重后台静默运行的丝滑体验与高阻力的紧急解锁机制。

技术栈： * 语言：Python 3.10+

核心库：pywin32 (Windows API 交互), psutil (进程管理), pystray (系统托盘), tkinter (轻量级 UI), Pillow (托盘图标处理)。

2. 系统架构与模块划分
项目采用轻量级的模块化设计，分为以下几个核心文件。请在 Cursor 中按此结构创建文件：

Plaintext
focus_guard/
├── main.py          # 程序主入口，负责初始化和整合各模块
├── config.json      # 配置文件，存储黑名单（进程名和网页标题关键字）
├── monitor.py       # 监控模块，负责调用 Win32 API 获取当前活动窗口
├── blocker.py       # 阻断模块，负责结束违规进程
├── tray_app.py      # UI 模块，负责系统托盘图标和菜单逻辑
└── unlock_ui.py     # 紧急解锁模块，负责高阻力解锁界面的渲染和校验
3. 核心模块详细设计 (供 Cursor 生成代码参考)
3.1 配置文件 (config.json)

需求： 使用 JSON 格式存储两类黑名单数据。

数据结构： 包含 process_blacklist (如 "steam.exe") 和 title_blacklist (如 "bilibili", "微博")。

3.2 监控模块 (monitor.py)

核心方法： get_active_window_info()

逻辑： 1. 使用 win32gui.GetForegroundWindow() 获取前台窗口句柄。
2. 使用 win32gui.GetWindowText() 获取窗口标题（转换为小写）。
3. 使用 win32process.GetWindowThreadProcessId() 获取 PID。
4. 使用 psutil.Process(pid).name() 获取进程名（转换为小写）。

返回值： 返回一个元组 (process_name, window_title)。需处理权限异常或空句柄情况。

**防御性异常：** 必须显式捕获 `psutil.NoSuchProcess` 与 `psutil.AccessDenied`。遇到时直接 `pass` 或返回安全默认值，绝不能因进程已退出或权限不足而导致监控线程崩溃。

3.3 阻断模块 (blocker.py)

核心方法： enforce_rules(process_name, window_title, config)

逻辑：

接收 monitor.py 传来的数据。

匹配 config.json 中的黑名单规则。

若匹配成功，调用 psutil 找到对应进程并执行 kill()。

**防御性异常：** 执行 `kill()` 或通过 PID 查进程前，必须显式捕获 `psutil.NoSuchProcess` 和 `psutil.AccessDenied`。遇到时 `pass` 或 `continue`，绝不能让后台守护线程因异常而终止。

记录拦截日志（打印到控制台即可）。

**匹配算法（扩展性）：** 进程黑名单在程序初始化时转为 `set` 做 O(1) 精确查找；网页标题关键字在初始化时预编译为正则表达式（如 `re.compile(keyword, re.I)` 列表），避免长黑名单下的全量字符串遍历。

3.4 托盘与守护进程 (tray_app.py & main.py)

逻辑：

main.py 启动时，开启一个后台线程运行死循环：每 1.5 秒执行一次 monitor 和 blocker 的检测逻辑。

主线程运行 pystray 创建系统托盘图标。

托盘右键菜单包含：开启专注、紧急退出。

状态管理：引入一个全局变量 is_focus_mode_active 控制循环是否执行阻断。

3.5 紧急解锁模块 (unlock_ui.py)

**运行方式（见第 4 节）：** 本模块由主进程通过 `subprocess` 以独立子进程方式启动，不在主进程内直接调用 tkinter，以避免与 pystray 抢占主线程事件循环导致卡死。

核心方法： 作为可独立运行的脚本，通过退出码向主进程返回结果：答题成功 `sys.exit(0)`，关闭窗口或答题失败 `sys.exit(1)`。主程序根据 `subprocess.run(unlock_ui...)` 的 returncode 决定是否将 `is_focus_mode_active` 设为 False。

界面逻辑：

使用 tkinter 弹出置顶窗口（不依赖主窗口）。

**两道验证（兼顾阻力与体验）：**
* 第一道：一道两位数乘法题（如 14 * 23 = ?），用户输入正确答案。
* 第二道：要求用户输入一段固定承诺语（例如：「我自愿放弃接下来的专注时间」）。抄写既打断冲动，又避免纯计算带来的过度挫败感。

两道均正确则视为解锁成功（exit 0），否则或用户关闭窗口则视为失败（exit 1），主程序保持专注模式。

---

## 4. 致命坑规避与鲁棒性设计（Cursor 必须遵守）

### 4.1 致命坑规避：UI 线程冲突（pystray 与 tkinter）

**问题所在：** `pystray`（系统托盘）和 `tkinter`（解锁弹窗）都需要接管主线程的事件循环（Main Loop）。若在托盘菜单回调里直接调用 `Tk()` 画界面，极大概率导致程序卡死或崩溃。

**调整方案（必须按此实现）：**
* 主程序只运行 **pystray** 与后台监控线程，**不在主进程内调用 tkinter**。
* 当用户点击「紧急退出」时，使用 `subprocess` 模块，以**独立子进程**方式运行 `unlock_ui.py`（例如：`subprocess.run([sys.executable, "unlock_ui.py"], ...)`）。
* `unlock_ui.py` 内：答题成功并确认解锁 → `sys.exit(0)`；关闭窗口或答题失败 → `sys.exit(1)`。
* 主程序通过 `subprocess.run(..., returncode=...)` 的返回值判断：若为 0 则将 `is_focus_mode_active` 设为 `False`，否则保持专注模式。此种解耦方式最稳妥，避免 UI 线程死锁。

### 4.2 增强鲁棒性：防御性异常捕获

**问题所在：** Windows 进程状态瞬息万变，刚拿到 PID 准备查进程名或执行 `kill()` 时，进程可能已被关闭，导致 `psutil.NoSuchProcess`、`psutil.AccessDenied` 等异常。

**调整方案：** 在 `monitor.py` 和 `blocker.py` 中，**必须**显式捕获 `psutil.NoSuchProcess` 与 `psutil.AccessDenied`。遇到时直接 `pass` 或 `continue`，**绝不能让后台守护线程因未捕获异常而终止**。具体位置见 3.2、3.3 中的「防御性异常」说明。

