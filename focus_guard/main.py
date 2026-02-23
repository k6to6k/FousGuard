"""
FocusGuard 程序主入口 (main.py)

职责：
- 读取配置 config.json
- 启动后台守护线程，周期性调用 monitor 与 blocker
- 在主线程运行托盘 UI（pystray），作为状态与退出控制中心
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

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
        self._stop_flag: bool = False
        self._lock = threading.Lock()

    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    def set_active(self, value: bool) -> None:
        with self._lock:
            self._is_active = bool(value)
        print(f"[FocusGuard] Focus mode set to: {self._is_active}")

    def toggle(self) -> None:
        with self._lock:
            self._is_active = not self._is_active
            current = self._is_active
        print(f"[FocusGuard] Focus mode toggled to: {current}")

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

    所有异常必须被捕获，绝不能导致线程崩溃退出。
    """
    print("[FocusGuard] Background monitor loop started.")
    while not state.should_stop():
        try:
            process_name, window_title, pid = monitor.get_active_window_info()
            # 仅在专注模式开启时执行阻断逻辑
            if state.is_active():
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
