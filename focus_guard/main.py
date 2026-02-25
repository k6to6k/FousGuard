"""
FocusGuard 程序主入口 (main.py)

职责：
- 读取配置 config.json
- 启动后台守护线程，周期性调用 monitor 与 blocker
- 在主线程运行托盘 UI（pystray），作为状态与退出控制中心
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import blocker
import monitor
import tray_app
import server

# 被视为“浏览器进程”的可执行文件名（小写）
BROWSER_PROCESSES = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
}


class FocusState:
    """
    专注模式状态控制器。
    - is_active: 是否开启专注模式
    - stop_flag: 是否请求停止后台监控循环（用于“彻底退出”）
    """

    def __init__(self) -> None:
        self._is_active: bool = False
        # 当前专注目标（由 dashboard_ui 通过 focus_command.json 下发）
        self.focus_target: str = ""
        self.end_time: float = 0.0
        self.timer_process: Optional[subprocess.Popen[bytes]] = None
        self.dashboard_process: Optional[subprocess.Popen[bytes]] = None
        self._stop_flag: bool = False
        self._lock = threading.Lock()
        # 由 tray_app 注册，用于在专注状态变化时同步更新托盘图标（避免 monitor_loop 异步触发时图标不变色）
        self._on_icon_update: Optional[Callable[[], None]] = None

    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    def set_active(self, value: bool) -> None:
        with self._lock:
            self._is_active = bool(value)
            if not value:
                self.end_time = 0.0
                # 清理悬浮倒计时进程
                if self.timer_process is not None:
                    try:
                        if self.timer_process.poll() is None:
                            self.timer_process.terminate()
                            self.timer_process.wait(timeout=2.0)
                    except Exception as e:
                        print(f"[FocusGuard] Failed to terminate timer widget: {e}")
                    finally:
                        self.timer_process = None
        print(f"[FocusGuard] Focus mode set to: {self._is_active}")
        self._notify_icon_update()

    def set_icon_update_callback(self, callback: Callable[[], None]) -> None:
        """由 tray_app 调用，注册托盘图标更新回调；专注状态变化时会在无锁情况下调用，避免死锁。"""
        self._on_icon_update = callback

    def _notify_icon_update(self) -> None:
        """在未持锁状态下调用注册的图标更新回调，供 set_active/start_focus 使用。"""
        cb = self._on_icon_update
        if cb is not None:
            try:
                cb()
            except Exception as e:
                print(f"[FocusGuard] Icon update callback error: {e}")

    def start_focus(self, minutes: int, target: str = "") -> None:
        """
        开启专注模式，设置结束时间戳，并启动悬浮倒计时窗口。
        """
        with self._lock:
            self._is_active = True
            self.focus_target = target or ""
            self.end_time = time.time() + minutes * 60

            # 启动悬浮倒计时子进程（非阻塞）
            try:
                timer_script = Path(__file__).with_name("timer_widget.py")
                self.timer_process = subprocess.Popen(
                    [sys.executable, str(timer_script), str(self.end_time)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print(f"[FocusGuard] Timer widget started (pid={self.timer_process.pid})")
            except Exception as e:
                print(f"[FocusGuard] Failed to start timer widget: {e}")
                self.timer_process = None

        print(f"[FocusGuard] Focus mode started: {minutes} minutes, ends at {self.end_time:.1f}")
        self._notify_icon_update()

    def get_end_time(self) -> float:
        with self._lock:
            return self.end_time

    def show_timer_widget(self) -> None:
        """
        重新拉起悬浮倒计时窗口。
        检查 timer_process 是否仍在运行，若已退出则重新启动。
        """
        with self._lock:
            # 检查进程是否仍在运行
            if self.timer_process is not None:
                if self.timer_process.poll() is None:
                    # 进程仍在运行，无需重新启动
                    print("[FocusGuard] Timer widget is already running")
                    return
                # 进程已退出，清理引用
                self.timer_process = None

            # 重新启动悬浮倒计时
            if not self._is_active or self.end_time <= 0:
                print("[FocusGuard] Cannot show timer widget: focus mode not active")
                return

            try:
                timer_script = Path(__file__).with_name("timer_widget.py")
                self.timer_process = subprocess.Popen(
                    [sys.executable, str(timer_script), str(self.end_time)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print(f"[FocusGuard] Timer widget restarted (pid={self.timer_process.pid})")
            except Exception as e:
                print(f"[FocusGuard] Failed to restart timer widget: {e}")
                self.timer_process = None

    def emergency_stop(self) -> None:
        """
        紧急退出：解除专注模式，清理 timer_process，重置 end_time。
        """
        with self._lock:
            self._is_active = False
            self.end_time = 0.0

            # 清理悬浮倒计时进程
            if self.timer_process is not None:
                try:
                    if self.timer_process.poll() is None:
                        self.timer_process.terminate()
                        self.timer_process.wait(timeout=2.0)
                except Exception as e:
                    print(f"[FocusGuard] Failed to terminate timer widget during emergency stop: {e}")
                finally:
                    self.timer_process = None

        print("[FocusGuard] Emergency stop: focus mode disabled")
        self._notify_icon_update()

    def request_stop(self) -> None:
        with self._lock:
            self._stop_flag = True
        print("[FocusGuard] Stop requested. Background monitor loop will exit.")

    def should_stop(self) -> bool:
        with self._lock:
            return self._stop_flag


def load_config() -> Dict[str, Any]:
    """
    从与 main.py 同目录的 config.json 读取配置。
    """
    config_path = Path(__file__).with_name("config.json")
    if not config_path.exists():
        raise SystemExit(f"config.json not found at {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        data: Dict[str, Any] = json.load(f)

    # 确保 os_whitelist 字段存在，并提供安全默认值，防止配置被误删导致异常
    default_os_whitelist = [
        "explorer.exe",
        "taskmgr.exe",
        "searchhost.exe",
        "cmd.exe",
        "python.exe",
        "focus_guard.exe",
        "anydesk.exe",
        "todesk.exe",
    ]
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("os_whitelist"), list):
        data["os_whitelist"] = default_os_whitelist

    return data


def monitor_loop(state: FocusState, config: Dict[str, Any]) -> None:
    """
    后台守护线程：每隔 1.5 秒轮询当前前台窗口，并在专注模式开启时调用阻断模块。
    同时检查专注是否到期，到期时自动结束并弹出提示。
    每次循环检查 focus_command.json，如果存在则读取并启动专注。

    所有异常必须被捕获，绝不能导致线程崩溃退出。
    """
    print("[FocusGuard] Background monitor loop started.")
    command_file = Path(__file__).with_name("focus_command.json")
    
    while not state.should_stop():
        try:
            # 检查是否存在 focus_command.json（专注启动命令）
            if command_file.exists():
                try:
                    with command_file.open("r", encoding="utf-8") as f:
                        command_data: Dict[str, Any] = json.load(f)
                    
                    minutes = command_data.get("minutes", 0)
                    if minutes > 0:
                        # 读取成功后，立即删除文件
                        os.remove(command_file)
                        # 读取专注目标（可选字段），并做安全 strip 处理
                        raw_target = command_data.get("target", "")
                        target = str(raw_target).strip() if raw_target is not None else ""
                        # 调用 start_focus 开启专注，并记录本轮专注目标
                        state.start_focus(minutes, target=target)
                        print(
                            f"[FocusGuard] Focus command received: {minutes} minutes, target={target!r}"
                        )
                except (json.JSONDecodeError, KeyError, OSError) as e:
                    # 文件格式错误或删除失败，记录日志但不影响主循环
                    print(f"[FocusGuard] Failed to process focus command: {e}")
                    # 尝试删除损坏的命令文件
                    try:
                        if command_file.exists():
                            os.remove(command_file)
                    except Exception:
                        pass

            current_time = time.time()

            if state.is_active():
                end_time = state.get_end_time()

                # 检查专注是否到期
                if current_time >= end_time:
                    # 专注结束：关闭专注模式并弹出提示
                    state.set_active(False)
                    try:
                        MB_ICONINFORMATION = 0x40
                        MB_TOPMOST = 0x00040000
                        flags = MB_ICONINFORMATION | MB_TOPMOST

                        ctypes.windll.user32.MessageBoxW(
                            None,
                            "专注番茄钟已完成！辛苦了，休息一下吧。",
                            "FocusGuard 专注完成",
                            flags,
                        )
                    except Exception as e:
                        # 弹窗失败不影响主逻辑
                        print(f"[FocusGuard] Failed to show completion dialog: {e}")

                    print("[FocusGuard] Focus session completed naturally.")
                else:
                    # 专注进行中：执行阻断逻辑
                    process_name, window_title, pid = monitor.get_active_window_info()

                    browser_url = ""
                    browser_title = ""
                    # 若当前前台为浏览器进程，则尝试从 server.CURRENT_BROWSER_TAB 中补齐标签页特征
                    if process_name and process_name.lower() in BROWSER_PROCESSES:
                        try:
                            with server.tab_lock:
                                browser_url = str(
                                    server.CURRENT_BROWSER_TAB.get("url", "") or ""
                                )
                                browser_title = str(
                                    server.CURRENT_BROWSER_TAB.get("title", "") or ""
                                )
                        except Exception as e:
                            print(f"[FocusGuard] Failed to bridge browser tab data: {e}")

                    blocker.enforce_rules(
                        process_name,
                        window_title,
                        pid,
                        config,
                        browser_url=browser_url,
                        browser_title=browser_title,
                        focus_target=state.focus_target,
                    )
        except Exception as e:
            # 任何未预料异常都只记录，不让线程退出
            print(f"[FocusGuard] Unexpected error in monitor loop: {e}")
        finally:
            # 无论本轮是否异常，都等待下一轮
            time.sleep(1.5)

    print("[FocusGuard] Background monitor loop exited.")


def main() -> None:
    config = load_config()
    state = FocusState()

    # 启动本地 HTTP 微服务，用于接收浏览器扩展上报的标签页特征
    http_thread = threading.Thread(
        target=server.start_server,
        daemon=True,
        name="FocusGuardHttpServer",
    )
    http_thread.start()

    # 进入后台循环前清除遗留指令，防止开机或重启后误触发专注
    command_file = Path(__file__).with_name("focus_command.json")
    if command_file.exists():
        try:
            os.remove(command_file)
            print("[FocusGuard] Removed stale focus_command.json")
        except OSError as e:
            print(f"[FocusGuard] Failed to remove focus_command.json: {e}")

    # 启动后台守护线程
    monitor_thread = threading.Thread(
        target=monitor_loop,
        args=(state, config),
        daemon=True,
        name="FocusGuardMonitor",
    )
    monitor_thread.start()

    # 非阻塞拉起 dashboard_ui.py（控制中心主窗口）
    try:
        dashboard_script = Path(__file__).with_name("dashboard_ui.py")
        state.dashboard_process = subprocess.Popen(
            [sys.executable, str(dashboard_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[FocusGuard] Dashboard UI started (pid={state.dashboard_process.pid})")
    except Exception as e:
        print(f"[FocusGuard] Failed to start dashboard UI: {e}")
        state.dashboard_process = None

    # 在主线程启动托盘应用（阻塞式）
    tray_app.run_tray_app(state)

    # 退出前强杀控制中心子进程，避免幽灵进程与残留指令
    if state.dashboard_process is not None and state.dashboard_process.poll() is None:
        try:
            state.dashboard_process.terminate()
            state.dashboard_process.wait(timeout=3.0)
        except Exception as e:
            print(f"[FocusGuard] Failed to terminate dashboard process: {e}")
        state.dashboard_process = None

    # 托盘退出后，确保后台线程尽快结束
    state.request_stop()
    # 给监控线程一点时间清理（非强制）
    monitor_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
