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
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import blocker
import monitor
import tray_app


class FocusState:
    """
    专注模式状态控制器。
    - is_active: 是否开启专注模式
    - stop_flag: 是否请求停止后台监控循环（用于“彻底退出”）
    """

    def __init__(self) -> None:
        self._is_active: bool = False
        self.end_time: float = 0.0
        self.timer_process: Optional[subprocess.Popen[bytes]] = None
        self._stop_flag: bool = False
        self._lock = threading.Lock()

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

    def start_focus(self, minutes: int) -> None:
        """
        开启专注模式，设置结束时间戳，并启动悬浮倒计时窗口。
        """
        with self._lock:
            self._is_active = True
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

    return data


def monitor_loop(state: FocusState, config: Dict[str, Any]) -> None:
    """
    后台守护线程：每隔 1.5 秒轮询当前前台窗口，并在专注模式开启时调用阻断模块。
    同时检查专注是否到期，到期时自动结束并弹出提示。

    所有异常必须被捕获，绝不能导致线程崩溃退出。
    """
    print("[FocusGuard] Background monitor loop started.")
    while not state.should_stop():
        try:
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
                    blocker.enforce_rules(process_name, window_title, pid, config)
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

    # 启动后台守护线程
    monitor_thread = threading.Thread(
        target=monitor_loop,
        args=(state, config),
        daemon=True,
        name="FocusGuardMonitor",
    )
    monitor_thread.start()

    # 在主线程启动托盘应用（阻塞式）
    tray_app.run_tray_app(state)

    # 托盘退出后，确保后台线程尽快结束
    state.request_stop()
    # 给监控线程一点时间清理（非强制）
    monitor_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
