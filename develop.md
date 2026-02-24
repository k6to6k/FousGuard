专注限额工具 (FocusGuard) MVP 开发文档

## 1. 项目概述

- **项目目标**：开发一款 Windows 平台的极简效率工具，通过监控前台窗口，阻断用户对娱乐软件和网站的访问，强制保持专注。
- **核心原则**：最小可行产品（MVP），不包含复杂社交功能，注重后台静默运行的丝滑体验与高阻力/高约束的专注机制。

- **技术栈**：
  - 语言：Python 3.10+
  - 核心库：`pywin32`（Windows API 交互）、`psutil`（进程管理）、`pystray`（系统托盘）、`CustomTkinter`（现代化深色模式 UI，支持无边框窗口与圆角设计）、`Pillow`（托盘图标处理）。

## 2. 系统架构与模块划分

项目采用轻量级的模块化设计，分为以下几个核心文件。请在 Cursor 中按此结构创建文件：

```plaintext
focus_guard/
├── main.py          # 程序主入口，负责初始化和整合各模块
├── config.json      # 配置文件，存储黑名单（进程名和网页标题关键字）
├── monitor.py       # 监控模块，负责调用 Win32 API 获取当前活动窗口
├── blocker.py       # 阻断模块，负责结束违规进程
├── tray_app.py      # UI 模块，负责系统托盘图标和菜单逻辑
├── dashboard_ui.py  # FocusGuard 控制中心（主控窗口），集成专注启动、规则管理与数据统计
├── timer_widget.py  # 悬浮倒计时模块，专注期间的视觉反馈小窗口
├── emergency_ui.py  # 紧急退出高阻力安全阀模块，专注期间唯一的极高阻力出口
└── focus_log.txt    # 专注历史日志，记录每次专注的目标、时长与时间戳
```

**注意**：`setup_ui.py` 将在阶段六被废弃，其功能完全合并入 `dashboard_ui.py` 的“专注台”标签页中。

## 3. 核心模块详细设计（供 Cursor 生成代码参考）

### 3.1 配置文件 (`config.json`)

需求： 使用 JSON 格式存储两类黑名单数据。

数据结构： 包含 process_blacklist (如 "steam.exe") 和 title_blacklist (如 "bilibili", "微博")。

### 3.2 监控模块 (`monitor.py`)

核心方法： get_active_window_info()

逻辑： 1. 使用 win32gui.GetForegroundWindow() 获取前台窗口句柄。
2. 使用 win32gui.GetWindowText() 获取窗口标题（转换为小写）。
3. 使用 win32process.GetWindowThreadProcessId() 获取 PID。
4. 使用 psutil.Process(pid).name() 获取进程名（转换为小写）。

返回值： 返回一个元组 (process_name, window_title)。需处理权限异常或空句柄情况。

**防御性异常：** 必须显式捕获 `psutil.NoSuchProcess` 与 `psutil.AccessDenied`。遇到时直接 `pass` 或返回安全默认值，绝不能因进程已退出或权限不足而导致监控线程崩溃。

### 3.3 阻断模块 (`blocker.py`)

核心方法： enforce_rules(process_name, window_title, config)

逻辑：

接收 monitor.py 传来的数据。

匹配 config.json 中的黑名单规则。

若匹配成功，调用 psutil 找到对应进程并执行 kill()。

**防御性异常：** 执行 `kill()` 或通过 PID 查进程前，必须显式捕获 `psutil.NoSuchProcess` 和 `psutil.AccessDenied`。遇到时 `pass` 或 `continue`，绝不能让后台守护线程因异常而终止。

记录拦截日志（打印到控制台即可）。

**匹配算法（扩展性）：** 进程黑名单在程序初始化时转为 `set` 做 O(1) 精确查找；网页标题关键字在初始化时预编译为正则表达式（如 `re.compile(keyword, re.I)` 列表），避免长黑名单下的全量字符串遍历。

### 3.4 托盘与守护进程 (`tray_app.py` & `main.py`)

逻辑：

main.py 启动时，开启一个后台线程运行死循环：每 1.5 秒执行一次 monitor 和 blocker 的检测逻辑。

主线程运行 pystray 创建系统托盘图标。

**启动流程升级**：
- `main.py` 启动时，除了挂载托盘图标，应默认通过 `subprocess.Popen([sys.executable, "dashboard_ui.py"])` **非阻塞拉起** `dashboard_ui.py` 主窗口（FocusGuard 控制中心），作为程序的图形化入口。
- 托盘图标的**双击事件或左键点击事件**，应绑定为“显示控制中心”：检查 `dashboard_ui.py` 子进程是否仍在运行，若已退出则重新启动，确保用户随时可以通过托盘快速呼出主界面。

托盘右键菜单重构为六个固定入口：

- 【显示控制中心】：始终可用。点击后检查 `dashboard_ui.py` 子进程是否仍在运行，若已退出则通过 `subprocess.Popen([sys.executable, "dashboard_ui.py"])` 非阻塞重新启动主窗口。该菜单项与托盘双击/左键点击事件功能一致，确保用户随时可以呼出主界面。
- 【显示倒计时】：仅在专注状态下可用。点击后调用 `state.show_timer_widget()` 重新拉起悬浮倒计时子进程（若用户之前隐藏了悬浮窗，可通过此菜单项重新显示）。
- 【紧急退出 (需冷静)】：仅在专注状态下可用。点击后通过 `subprocess.run([sys.executable, "emergency_ui.py"])` 阻塞调用紧急安全阀界面（见 3.8 节）。若返回码为 0（用户完成 60 秒冷静期并确认退出），则调用 `state.emergency_stop()` 解除专注并恢复 UI；若返回码为 1（用户中途放弃或关闭窗口），则保持专注状态不变。
- 【专注中（不可退出）】：在专注进行中显示为高亮或带勾选状态，但**始终不可点击**，用于告知用户当前处于锁定专注期。
- 【彻底退出】：仅在非专注状态下可用；一旦专注开始，该入口在整个专注周期内完全置灰禁用，防止用户绕过约束“强行退场”。

状态管理：

- 引入布尔状态 `is_focus_mode_active` 表示当前是否处于专注模式。
- 引入时间戳 `end_time`（例如使用 `time.time()` 的绝对时间），记录本轮专注的“结束时间点”。
- 引入悬浮倒计时子进程对象 `timer_process`，用于管理 `timer_widget.py` 的生命周期。
- **状态控制器需新增方法**：
  - `emergency_stop()`：紧急退出方法，调用 `set_active(False)` 解除专注，并清理 `timer_process`，恢复托盘 UI 为闲置状态。
  - `show_timer_widget()`：重新拉起悬浮倒计时窗口。检查 `timer_process` 是否仍在运行，若已退出则使用 `subprocess.Popen` 重新启动 `timer_widget.py` 并更新 `timer_process`。
- 后台循环在每次轮询时：
  - 若 `not is_focus_mode_active`，则不执行阻断逻辑。
  - 若 `is_focus_mode_active` 且当前时间 `>= end_time`，则自动将 `is_focus_mode_active` 设为 False，同时重置 `end_time`，视为本轮番茄时段自然结束。**必须**使用 `ctypes.windll.user32.MessageBoxW` 弹出系统原生提示框（例如：标题“FocusGuard 专注完成”，内容“专注番茄钟已完成！辛苦了，休息一下吧”），确保用户及时感知到可以休息。托盘 UI 应同步恢复为“可开始专注 / 可彻底退出”的闲置状态。
  - 若 `is_focus_mode_active` 且当前时间 `< end_time`，则按正常逻辑调用 `blocker.enforce_rules()` 进行阻断，且不允许用户通过 UI 解除或退出当前专注（除紧急安全阀外）。

### 3.5 FocusGuard 控制中心 (`dashboard_ui.py`)

**定位**：程序的主控窗口，作为全局图形化入口，集成专注启动、规则管理和数据统计于一体。

**运行方式**：

- 在 `main.py` 启动时，默认通过 `subprocess.Popen([sys.executable, "dashboard_ui.py"])` **非阻塞拉起**，作为程序的图形化入口。
- 托盘图标的双击事件或左键点击事件，应绑定为“显示控制中心”，检查子进程是否仍在运行，若已退出则重新启动。
- 该模块以**独立子进程**方式运行，不在主进程内直接调用 CustomTkinter，以避免与 pystray 抢占主线程事件循环导致卡死。

**界面架构**：

- 使用 **CustomTkinter** 构建现代化深色模式主窗口。
- 采用 **TabView** 组件（或侧边栏导航）进行功能路由，打造 All-in-One 的现代软件体验。
- 窗口标题为“FocusGuard 控制中心”，支持最小化到托盘，关闭窗口时仅隐藏窗口而不退出子进程（或询问用户是否退出程序）。

**三大核心功能区**：

**Tab 1: 专注台 (Focus)**

- **功能定位**：接管原 `setup_ui.py` 的功能，作为专注启动的图形化入口。
- **界面元素**：
  - 顶部输入框：占位符为“请输入专注目标 (例如：搜广推算法复习 / 高级数据库报告)”。
  - 时长选择按钮：【25 分钟】、【45 分钟】、【60 分钟】以及【1 分钟 (测试)】。
  - 【开始专注】按钮：点击后获取目标文本与选择的分钟数，调用主进程的专注启动逻辑（见下方“与主进程通信”）。
- **数据记录**：点击【开始专注】时，将目标文本、时长、时间戳追加写入 `focus_log.txt`，格式为：`时间戳 | 目标 | 时长（分钟）`。

**Tab 2: 规则库 (Blacklist)**

- **功能定位**：可视化黑名单管理与 Smart Picker 进程一键抓取，彻底摆脱手动修改 JSON 的极客门槛。
- **功能 A：可视化黑名单管理**
  - 界面分为两个区域或子标签：
    - **进程黑名单**：以列表/表格形式展示 `config.json` 中的 `process_blacklist`，支持：
      - 显示当前所有进程黑名单项（如 `steam.exe`、`notepad.exe`）。
      - 添加新进程：通过文本输入框输入进程名（如 `chrome.exe`），点击“添加”后实时写入 `config.json`。
      - 删除进程：选中列表项后点击“删除”，立即从配置文件中移除。
      - 修改进程名：支持就地编辑或重新输入。
    - **网页标题黑名单**：以列表/表格形式展示 `title_blacklist`，支持：
      - 显示当前所有标题关键字（如 `bilibili`、`微博`）。
      - 添加新关键字：输入关键字后点击“添加”，实时写入 `config.json`。
      - 删除关键字：选中后删除。
      - 修改关键字：支持编辑。
  - 所有增删改操作均**实时同步到 `config.json`**，主进程的 `blocker.py` 模块会在下次调用 `_ensure_rule_index` 时自动检测配置变化并重建缓存。
- **功能 B：活跃进程嗅探器 (Smart Picker)**
  - 调用 `psutil.process_iter()` 遍历当前系统所有进程。
  - 过滤条件：仅显示**有窗口界面的活跃进程**（可通过 `psutil.Process(pid).status() == 'running'` 与 Win32 API `GetWindowThreadProcessId` 判断是否有窗口句柄）。
  - 界面展示：
    - 以列表/表格形式展示进程名、PID、窗口标题（如有）。
    - 每个进程项旁提供“一键加入黑名单”按钮。
    - 点击后自动将该进程名添加到 `process_blacklist` 并实时写入 `config.json`。
  - 刷新机制：提供“刷新进程列表”按钮，定期更新当前活跃进程快照。

**Tab 3: 统计局 (Statistics)**

- **功能定位**：读取并解析 `focus_log.txt`，提供专注数据的可视化反馈，形成多巴胺正向激励。
- **数据展示**：
  - **今日总时长**：解析 `focus_log.txt`，筛选今日记录，累加所有时长（分钟），转换为小时:分钟格式显示（例如：`今日专注：2 小时 35 分钟`）。
  - **累计总时长**：解析所有历史记录，累加总时长，转换为小时:分钟格式显示（例如：`累计专注：15 小时 42 分钟`）。
  - **历史专注记录列表**：以时间倒序展示所有历史记录，每行显示：`时间戳 | 目标 | 时长（分钟）`，支持滚动查看。
- **数据解析**：
  - 使用 `Path(__file__).with_name("focus_log.txt")` 定位日志文件。
  - 逐行解析，格式为：`YYYY-MM-DD HH:MM:SS | 目标文本 | 分钟数`。
  - 若日志文件不存在或格式异常，优雅降级：显示“暂无数据”或“0 小时 0 分钟”。

**与主进程通信**：

- **专注启动**：`dashboard_ui.py` 的“专注台”标签页中，用户点击【开始专注】后，需要通过进程间通信告知主进程启动专注。
  - **方案 A（推荐）**：通过文件系统通信。`dashboard_ui.py` 将专注信息（目标、分钟数）写入临时文件（如 `focus_command.json`），主进程的监控循环定期检查该文件，读取后删除文件并调用 `state.start_focus(minutes)`。
  - **方案 B**：通过命名管道或 socket 通信（复杂度较高，暂不采用）。
- **配置同步**：规则库的修改直接写入 `config.json`，主进程自动检测配置变化并重建缓存（见 3.3 节）。

**废弃说明**：

- `setup_ui.py` 将在阶段六被完全废弃，其所有功能（专注目标输入、时长选择、日志记录）均合并入 `dashboard_ui.py` 的“专注台”标签页中。
- 托盘菜单中的【开始专注】项将被移除，用户统一通过控制中心的“专注台”启动专注。

作用：

- 提供一个由 **CustomTkinter** 构建的独立 GUI 窗口，用于可视化黑名单管理与进程嗅探，彻底摆脱手动修改 JSON 的极客门槛。

运行方式：

- 由托盘菜单【打开主控看板】通过 `subprocess.Popen([sys.executable, "dashboard_ui.py"])` 以**非阻塞子进程**方式启动，仅在非专注状态下可用。

功能 A：可视化黑名单管理

- 界面分为两个标签页或区域：
  - **进程黑名单**：以列表/表格形式展示 `config.json` 中的 `process_blacklist`，支持：
    - 显示当前所有进程黑名单项（如 `steam.exe`、`notepad.exe`）。
    - 添加新进程：通过文本输入框输入进程名（如 `chrome.exe`），点击“添加”后实时写入 `config.json`。
    - 删除进程：选中列表项后点击“删除”，立即从配置文件中移除。
    - 修改进程名：支持就地编辑或重新输入。
  - **网页标题黑名单**：以列表/表格形式展示 `title_blacklist`，支持：
    - 显示当前所有标题关键字（如 `bilibili`、`微博`）。
    - 添加新关键字：输入关键字后点击“添加”，实时写入 `config.json`。
    - 删除关键字：选中后删除。
    - 修改关键字：支持编辑。
- 所有增删改操作均**实时同步到 `config.json`**，主进程的 `blocker.py` 模块会在下次调用 `_ensure_rule_index` 时自动检测配置变化并重建缓存。

功能 B：活跃进程嗅探器 (Smart Picker)

- 调用 `psutil.process_iter()` 遍历当前系统所有进程。
- 过滤条件：仅显示**有窗口界面的活跃进程**（可通过 `psutil.Process(pid).status() == 'running'` 与 Win32 API `GetWindowThreadProcessId` 判断是否有窗口句柄）。
- 界面展示：
  - 以列表/表格形式展示进程名、PID、窗口标题（如有）。
  - 每个进程项旁提供“一键加入黑名单”按钮。
  - 点击后自动将该进程名添加到 `process_blacklist` 并实时写入 `config.json`。
- 刷新机制：提供“刷新进程列表”按钮，定期更新当前活跃进程快照。

### 3.7 悬浮倒计时模块 (`timer_widget.py`)

作用：

- 专注期间的视觉反馈，缓解“盲专注”带来的时间焦虑。提供一个无标题栏、置顶、不可点击的极简 CustomTkinter 小窗口，实时显示剩余专注时间。

运行方式：

- 在 `main.py` 开启专注时（调用 `state.start_focus(minutes)` 后），通过 `subprocess.Popen([sys.executable, "timer_widget.py", str(end_time)])` **非阻塞启动**，传入专注结束时间戳作为命令行参数。
- 该子进程独立运行，不阻塞主进程的监控循环与托盘 UI。

界面特性：

- 使用 **CustomTkinter** 构建极简悬浮窗口：
  - `overrideredirect(True)` 去除标题栏，实现无边框设计。
  - `attributes("-topmost", True)` 确保始终置顶。
  - 窗口大小适中（例如 200x80），圆角设计，深色模式。
  - 窗口位置可固定在屏幕右上角或用户自定义位置。

核心行为：

- 启动时从命令行参数读取 `end_time`（字符串格式的时间戳）。
- 每秒更新一次：
  - 计算 `remaining = end_time - time.time()`。
  - 将剩余秒数转换为“MM:SS”格式（例如：`25:00`、`14:32`）。
  - 以大号字体在窗口中央显示倒计时文本。
  - 当剩余时间 `< 60` 秒时，可切换为红色高亮，增强紧迫感。
- **自由拖拽交互**：
  - 必须绑定鼠标事件 `<Button-1>`（按下）与 `<B1-Motion>`（拖动）以支持在屏幕上随意拖动无边框窗口。
  - 实现逻辑：记录鼠标按下时的窗口位置与鼠标位置差值，拖动时根据鼠标移动距离更新窗口位置。
- **隐藏功能**：
  - 提供一个极简的隐藏按钮（如右上角 "×" 图标），点击执行 `sys.exit(0)` 仅销毁当前 UI 进程，不影响主进程的专注状态。
  - 用户可通过托盘菜单【显示倒计时】重新拉起悬浮窗。
- 倒计时归零时：
  - 窗口自动 `sys.exit(0)` 销毁，不干扰主进程的完成提示弹窗。
- 防御性设计：
  - 所有异常（如时间戳解析失败、窗口创建失败）均被捕获，子进程静默退出，不影响主进程稳定性。

### 3.8 紧急安全阀模块 (`emergency_ui.py`)

作用：

- 作为专注期间唯一的极高阻力出口，为真实突发情况（如工作紧急需求）提供安全阀，同时通过极高的时间成本有效遏制多巴胺驱使下的随手毁约行为。

运行方式：

- 由托盘菜单【紧急退出 (需冷静)】通过 `subprocess.run([sys.executable, "emergency_ui.py"])` 以**阻塞子进程**方式启动，仅在专注状态下可用。

界面实现：

- 使用 **CustomTkinter** 构建置顶窗口，深色模式，居中显示。
- 窗口标题为“FocusGuard 紧急退出确认”。

核心逻辑：

- **强制 60 秒递减倒计时**：
  - 窗口顶部显示提示文字：“紧急退出需要强制冷静。请等待倒计时结束...”
  - 中间以大号字体显示倒计时（例如：`60 秒`、`59 秒`、...、`1 秒`）。
  - 使用 `root.after(1000, update_countdown)` 每秒递减。
- **按钮状态控制**：
  - 【确认退出】按钮：初始状态为 `state=DISABLED`，倒计时结束前完全不可点击。
  - 当倒计时归零时，按钮文字变为“可以退出”，并将状态改为 `NORMAL`，允许用户点击。
  - 【放弃，继续专注】按钮：始终可用，点击后立即关闭窗口。

退出码约定：

- 用户熬过 60 秒冷静期并点击【确认退出】→ `sys.exit(0)`，主进程视为紧急退出成功，调用 `state.emergency_stop()` 解除专注。
- 用户中途点击【放弃，继续专注】或直接关闭窗口（右上角 X）→ `sys.exit(1)`，主进程视为放弃退出，保持专注状态不变。

设计意图：

- 通过“60 秒强制冷静期”的极高时间成本，既保证了真实突发情况的出口，又有效遏制了冲动性毁约行为。
- 与“彻底退出”菜单在专注期间被禁用形成对比，紧急安全阀是专注期间唯一可控的退出路径，但需要付出足够的时间成本。

---

## 4. 致命坑规避与鲁棒性设计

### 4.1 致命坑规避：UI 线程冲突（pystray 与 tkinter）

**问题所在：** `pystray`（系统托盘）和 `CustomTkinter`（设置/弹窗界面）都需要接管主线程的事件循环（Main Loop）。若在托盘菜单回调里直接调用 `ctk.CTk()` 画界面，极大概率导致程序卡死或崩溃。

**调整方案（必须按此实现）：**

- 主程序只运行 **pystray** 与后台监控线程，**不在主进程内调用 CustomTkinter**。
- `dashboard_ui.py` 作为独立子进程运行，通过文件系统（`focus_command.json`）或直接修改 `config.json` 与主进程通信。
- 主进程的监控循环定期检查 `focus_command.json`（若存在），读取专注命令后删除文件并调用 `state.start_focus(minutes)`。
- 所有 CustomTkinter 界面（`dashboard_ui.py`、`timer_widget.py`、`emergency_ui.py`）均以独立子进程方式启动，避免与 pystray 抢占主线程事件循环导致卡死。
- 此种解耦方式既避免 UI 线程死锁，又确保主进程的稳定性和鲁棒性。

### 4.2 增强鲁棒性：防御性异常捕获

**问题所在：** Windows 进程状态瞬息万变，刚拿到 PID 准备查进程名或执行 `kill()` 时，进程可能已被关闭，导致 `psutil.NoSuchProcess`、`psutil.AccessDenied` 等异常。

**调整方案：** 在 `monitor.py` 和 `blocker.py` 中，**必须**显式捕获 `psutil.NoSuchProcess` 与 `psutil.AccessDenied`。遇到时直接 `pass` 或 `continue`，**绝不能让后台守护线程因未捕获异常而终止**。具体位置见 3.2、3.3 中的「防御性异常」说明。

