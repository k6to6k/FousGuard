专注限额工具 (FocusGuard) 开发过程记录

## 1. 阶段一：基础骨架与监控模块

- **目标**：搭建项目目录结构，完成前台窗口监控。
- **关键实现**：
  - 创建 `focus_guard/` 目录与 6 个核心文件：`main.py`, `config.json`, `monitor.py`, `blocker.py`, `tray_app.py`, `unlock_ui.py`。
  - 初始化 `config.json`，提供 `process_blacklist`（如 `notepad.exe`）和 `title_blacklist`（如 `bilibili`）用于测试。
  - 实现 `monitor.get_active_window_info()`：
    - 使用 Win32 API：`GetForegroundWindow` → `GetWindowText` → `GetWindowThreadProcessId`，再用 `psutil.Process(pid).name()` 获取进程名。
    - 返回值从最初的二元组 `(process_name, window_title)` 优化为三元组 `(process_name, window_title, pid)`，为后续“精准狙击”提供基础。
    - 严格遵守鲁棒性设计：
      - 显式捕获 `psutil.NoSuchProcess` 与 `psutil.AccessDenied`，避免进程瞬间消失或权限不足导致线程崩溃。
      - 顶层 `try/except` 兜底，任何异常都返回 `(None, None, None)`，保持后台线程稳定。
- **测试方式**：
  - 在 `monitor.py` 中加入 `if __name__ == "__main__":` 自测代码，每秒打印当前前台窗口信息，验证进程名与标题获取正确。

## 2. 阶段二：阻断模块 blocker.py

### 2.1 初版设计与问题

- **初版行为**：
  - 简单根据 `process_blacklist` / `title_blacklist` 匹配后，直接按进程名遍历所有同名进程并 `kill()`。
- **暴露问题**：
  - 粒度过粗：当浏览器标题命中 `bilibili` 时，有可能一并杀掉整个浏览器进程，体验过于粗暴。
  - 仅靠线性列表匹配，未来黑名单变长时效率不佳。

### 2.2 优化一：匹配算法与缓存

- **优化点**：
  - 在模块级缓存配置：
    - 将 `process_blacklist` 在初始化时转换为小写 `set`，实现 O(1) 精确匹配。
    - 将 `title_blacklist` 预编译为正则列表 `[re.compile(keyword, re.I), ...]`，避免长列表下的重复字符串搜索。
  - 使用“配置指纹” `_CONFIG_FINGERPRINT`（排序后的元组）判断是否需要重建缓存，避免每次调用都重复构建索引。

### 2.3 优化二：防御性异常与“精准狙击”

- **问题**：Windows 进程状态瞬息万变，拿到 PID 后进程随时可能退出或被系统关闭。
- **改进**：
  - 遍历与 `kill()` 时显式捕获 `psutil.NoSuchProcess` 与 `psutil.AccessDenied`，只打印日志，绝不抛出异常。
  - 引入 PID 级别的 `_kill_process_by_pid(pid, process_name)`：
    - 优先按 PID 杀掉当前前台窗口对应的单个进程。
    - 若失败（无权限或进程已退出），才退回按进程名遍历同名进程。
  - 同时保留 `_kill_processes_by_name`，用于进程黑名单场景的“强力阻断”。

### 2.4 优化三：区分“进程黑名单”与“标题黑名单”

- **需求演进**：
  - 用户希望：黑名单应用（如游戏、桌面 App）可以直接被“秒杀”，但浏览器里只开了一个 Bilibili 标签时，不应把整个浏览器关掉。
- **解决方案**：
  - 将匹配结果拆分为二元组：
    - `matched_process`: 是否命中 `process_blacklist`。
    - `matched_title`: 是否命中 `title_blacklist`。
  - 在 `enforce_rules` 中按类型分支：
    - **命中进程黑名单**：仍使用 PID + 进程名的强力阻断策略（适用于 `notepad.exe` 等本地应用）。
    - **仅命中标题黑名单**：不再调用任何 `kill()`，避免误杀浏览器，仅进行“温和阻断”（见下一小节）。

### 2.5 优化四：对网页的“温和阻断”策略

- **问题**：
  - 对 B 站这类网页，如果只弹通知而不做任何动作，实际效果有限；如果直接杀浏览器进程，又过于激进。
- **最终策略**：
  - 在“仅命中标题黑名单”场景下，采用两步“软拦截”：
    1. 使用 Win32 键盘事件模拟 `Ctrl+W`，尝试关闭**当前活动标签页/文档**，而不是整个进程。
    2. 同时调用 `show_block_warning` 弹出原生系统模态提示，文案优先展示网页标题（例如 `[哔哩哔哩_bilibili]`），以便用户知道是哪个页面被限制。
  - 为防止弹窗风暴，引入全局节流：
    - 字典 `_LAST_WARNING_TIME` 记录每个应用/网页的最近弹窗时间。
    - 限制同一对象在 3 秒冷却时间内只弹一次提示。

### 2.6 优化五：阻断提示的可见性与文案

- **问题过程**：
  - 初版 `MessageBoxW` 只使用 `MB_ICONWARNING | MB_TOPMOST`，在某些情况下不会真正抢到前台，容易被其他窗口遮挡。
  - 调参与测试过程中逐步引入 `MB_SETFOREGROUND` 与 `MB_SYSTEMMODAL`，实现更强的前台展示效果。
  - 初版文案优先显示进程名（如 `msedge.exe`），在网页场景下不友好。
- **最终状态**：
  - 使用组合标志：`MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL`，尽可能压过普通窗口（包括资源管理器）。
  - 在网页标题命中的场景下，提示文案优先展示窗口标题，其次才回退到进程名，确保用户看到的是“哪个页面被拦截”，而不是“哪个浏览器进程”。

## 3. 阶段三：托盘与守护进程 (main.py & tray_app.py)

### 3.1 主入口 main.py：状态管理与后台线程

- **状态控制器 `FocusState`**：
  - 通过加锁的布尔变量管理：
    - `is_active`: 是否开启专注模式（默认 `False`）。
    - `stop_flag`: 是否请求停止后台线程，用于“彻底退出”。
  - 提供：
    - `is_active()/set_active()/toggle()` 操作专注状态。
    - `request_stop()/should_stop()` 控制后台监控循环的生命周期。
- **后台监控线程**：
  - `monitor_loop(state, config)` 作为守护线程运行：
    - 每隔 1.5 秒调用 `monitor.get_active_window_info()`。
    - 仅在 `state.is_active()` 为 `True` 时调用 `blocker.enforce_rules()`。
  - 整个循环用 `try/except` 包裹：
    - 任何未预料异常都会被捕获并打印日志，线程不会退出，符合“后台守护线程绝对不能崩”的原则。
- **主线程职责**：
  - 加载配置、创建 `FocusState` 与后台线程。
  - 将 `tray_app.run_tray_app(state)` 作为主线程的阻塞调用，确保符合 pystray 对 UI 线程的要求。

### 3.2 托盘 UI tray_app.py：模式切换与退出

- **图标策略**：
  - 使用 Pillow 在内存中动态生成 64x64 的纯色方块图标：
    - 红色代表“专注模式开启”。
    - 灰色代表“未开启/休息”。
  - 避免对本地图标文件的依赖，减少部署复杂度。
- **菜单设计**：
  - 「切换专注模式」：
    - 调用 `state_controller.toggle()`。
    - 根据当前状态动态切换托盘图标颜色（红/灰），提供即时反馈。
  - 「紧急退出」：
    - 初版仅打印日志，未改变状态，导致用户以为“紧急退出”却依然被拦截。
    - 调整后，在接入解锁 UI 之前的占位实现为：
      - 直接 `state_controller.set_active(False)` 关闭专注模式。
      - 更新托盘图标为灰色，清晰表示当前不再拦截。
    - 为接入 `unlock_ui` 预留扩展位：未来只需在此处插入“子进程解锁成功 → set_active(False)”的逻辑。
  - 「彻底退出」：
    - 调用 `state_controller.request_stop()` 请求后台线程退出。
    - 调用 `icon.stop()` 结束 pystray 事件循环，从而终止主进程。

## 4. 当前状态与后续工作

- **当前完成度**：
  - 监控模块与阻断模块已按文档第 3.2、3.3 节并结合第 4 节鲁棒性要求实现完毕。
  - 托盘 UI 与守护线程结构稳定，支持：
    - 开启/关闭专注模式；
    - 紧急退出（当前为无验证的一键关闭，占位实现）；
    - 完整退出应用。
  - 对应用级黑名单提供“强力秒杀”，对网页级黑名单提供“Ctrl+W 软阻断 + 强提醒”，避免误杀整个浏览器。
- **后续计划（未完成部分）**：
  - 按 `develop.md` 第 3.5 节设计，实现 `unlock_ui.py`：
    - 以**独立子进程**的方式运行，避免与 pystray 争夺 UI 主线程。
    - 提供“两位数乘法 + 承诺语输入”的高阻力解锁流程。
    - 以退出码 `0/1` 向主进程报告解锁结果，由主进程决定是否关闭专注模式。
  - 在托盘的「紧急退出」菜单中接入上述解锁流程，替换当前的占位实现。

