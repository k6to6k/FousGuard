"""
阻断模块 (blocker.py)

职责：
- 接收 monitor.py 提供的当前前台窗口信息 (process_name, window_title)
- 根据 config.json 中的黑名单规则，决定是否结束对应进程
- 遵守 develop.md 第 3.3 节与第 4 节的鲁棒性设计要求
"""

from __future__ import annotations

import ctypes
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import psutil


# 全局缓存：用于加速匹配（process_blacklist -> set, title_blacklist -> 预编译正则）
_PROCESS_BLACKLIST_SET: Optional[set[str]] = None
_TITLE_PATTERNS: Optional[list[re.Pattern[str]]] = None
_CONFIG_FINGERPRINT: Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]] = None


# 弹窗节流：记录最近一次针对每个应用名弹出警告的时间，避免“弹窗风暴”
_LAST_WARNING_TIME: Dict[str, float] = {}
_WARNING_COOLDOWN_SECONDS: float = 3.0


def show_block_warning(app_name: str) -> None:
    """
    使用 Windows 原生 MessageBox 弹出置顶警告提示，告知用户已拦截受限内容。
    为避免弹窗风暴，同一应用在冷却时间内只弹一次。
    """
    name = (app_name or "").strip() or "未知应用"
    key = name.lower()

    try:
        now = time.monotonic()
        last = _LAST_WARNING_TIME.get(key)
        if last is not None and now - last < _WARNING_COOLDOWN_SECONDS:
            return
        _LAST_WARNING_TIME[key] = now

        MB_ICONWARNING = 0x30
        MB_TOPMOST = 0x00040000
        MB_SETFOREGROUND = 0x00010000
        MB_SYSTEMMODAL = 0x00001000
        # SYSTEMMODAL + TOPMOST + SETFOREGROUND：尽可能压过其他普通窗口（包括资源管理器）
        flags = MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL

        ctypes.windll.user32.MessageBoxW(
            None,
            f"FocusGuard 提醒：检测到受限内容 [{name}]，已自动拦截。请保持专注！",
            "FocusGuard 阻断通知",
            flags,
        )
    except Exception:
        # 任何弹窗相关异常都不应影响主逻辑
        return


def _build_fingerprint(config: Dict[str, Any]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """
    将当前配置转换为可哈希的指纹，用于判断是否需要重建缓存。
    """
    process_list = tuple(sorted(name.lower() for name in config.get("process_blacklist", []) if isinstance(name, str)))
    title_list = tuple(sorted(str(name) for name in config.get("title_blacklist", [])))
    return process_list, title_list


def _ensure_rule_index(config: Dict[str, Any]) -> None:
    """
    在全局作用域维护基于 config 的匹配索引：
    - process_blacklist: 使用 set 做 O(1) 精确查找
    - title_blacklist: 在初始化时预编译为正则表达式列表
    仅当配置内容发生变化时才重建，避免重复工作。
    """
    global _PROCESS_BLACKLIST_SET, _TITLE_PATTERNS, _CONFIG_FINGERPRINT

    fingerprint = _build_fingerprint(config)
    if fingerprint == _CONFIG_FINGERPRINT:
        return

    process_list, title_list = fingerprint
    _PROCESS_BLACKLIST_SET = set(process_list)
    _TITLE_PATTERNS = [re.compile(keyword, re.I) for keyword in title_list]
    _CONFIG_FINGERPRINT = fingerprint


def _match_rules(process_name: Optional[str], window_title: Optional[str]) -> bool:
    """
    根据全局缓存的黑名单判断当前窗口是否违规。
    要求在调用前确保 _ensure_rule_index 已被执行。
    """
    if _PROCESS_BLACKLIST_SET is None or _TITLE_PATTERNS is None:
        return False

    pn = (process_name or "").lower()
    wt = (window_title or "").lower()

    # 进程名精确匹配
    if pn and pn in _PROCESS_BLACKLIST_SET:
        return True

    # 窗口标题关键字/正则匹配
    if wt:
        for pattern in _TITLE_PATTERNS:
            if pattern.search(wt):
                return True

    return False


def _kill_processes_by_name(process_name: str) -> None:
    """
    根据进程名结束所有同名进程。
    防御性异常处理：显式捕获 psutil.NoSuchProcess 与 psutil.AccessDenied。
    """
    target = (process_name or "").lower()
    if not target:
        return

    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            name = proc.info.get("name") or proc.name()
            if not name:
                continue
            if name.lower() != target:
                continue

            try:
                proc.kill()
                print(f"[FocusGuard] Killed process: {name} (pid={proc.pid})")
                # 异步弹出拦截提示，避免阻塞主线程
                threading.Thread(
                    target=show_block_warning,
                    args=(name,),
                    daemon=True,
                ).start()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # 进程可能瞬间退出或无权限，按鲁棒性设计要求仅记录，不抛异常
                print(
                    f"[FocusGuard] Failed to kill process: {name} (pid={proc.pid}) "
                    f"due to NoSuchProcess or AccessDenied"
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # 在遍历过程中进程列表也可能变化，继续下一项
            continue


def _kill_process_by_pid(pid: Optional[int], process_name: Optional[str] = None) -> bool:
    """
    根据 PID 精准结束对应进程。
    防御性异常处理：显式捕获 psutil.NoSuchProcess 与 psutil.AccessDenied。

    返回值：
    - True: 成功发送 kill
    - False: 未执行或失败（包括异常），调用方可根据需要决定是否降级为名称匹配
    """
    if pid is None or pid <= 0:
        return False

    try:
        proc = psutil.Process(pid)
        try:
            name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = process_name or "<unknown>"

        proc.kill()
        print(f"[FocusGuard] Killed process by pid: {name} (pid={pid})")
        # 异步弹出拦截提示，避免阻塞主线程
        threading.Thread(
            target=show_block_warning,
            args=(name,),
            daemon=True,
        ).start()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        # 进程可能已退出或无权限，按鲁棒性设计要求仅记录，不抛异常
        print(
            f"[FocusGuard] Failed to kill process by pid={pid}, process={process_name} "
            f"due to NoSuchProcess or AccessDenied"
        )
        return False
    except Exception:
        # 兜底保护，避免任何意外异常导致线程崩溃
        print(f"[FocusGuard] Unexpected error when killing pid={pid}, process={process_name}")
        return False


def enforce_rules(
    process_name: Optional[str],
    window_title: Optional[str],
    pid: Optional[int],
    config: Dict[str, Any],
) -> None:
    """
    核心方法：根据配置判断当前前台窗口是否违规，若违规则结束对应进程。

    参数：
    - process_name: 当前前台窗口的进程名（小写，可能为 None）
    - window_title: 当前前台窗口标题（小写，可能为 None）
    - pid: 当前前台窗口所属进程的 PID（可能为 None）
    - config: 从 config.json 读取的配置字典
    """
    if not process_name and not window_title:
        return

    # 确保全局匹配索引已根据最新配置构建
    _ensure_rule_index(config)

    if not _match_rules(process_name, window_title):
        return

    print(
        f"[FocusGuard] Block rule matched: process={process_name}, pid={pid}, title={window_title!r}"
    )

    # 优先尝试精准狙击：按 PID 结束当前窗口对应的单个进程
    killed = _kill_process_by_pid(pid, process_name)

    # 若无法使用 PID（例如无 PID 信息），可退回到按进程名结束同名进程
    if not killed and (pid is None or pid <= 0):
        _kill_processes_by_name(process_name or "")


if __name__ == "__main__":
    """
    测试代码：
    - 从同目录的 config.json 读取配置
    - 调用 monitor.get_active_window_info() 获取当前前台窗口（包括 PID）
    - 持续调用 enforce_rules()，验证当前台窗口在黑名单中时能否被“秒杀”

    使用建议：
    - 确保 config.json 中包含待测试的进程名（例如 "notepad.exe"）
    - 运行本文件后，切换前台到对应程序窗口，观察是否被结束
    """
    import time

    # 延迟导入以避免循环依赖问题
    import monitor

    config_path = Path(__file__).with_name("config.json")
    if not config_path.exists():
        raise SystemExit(f"config.json not found at {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        config_data = json.load(f)

    print(
        "[FocusGuard] blocker test running. "
        "Ensure target process (e.g. notepad.exe) is in process_blacklist."
    )
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            process_name, window_title, pid = monitor.get_active_window_info()
            print(f"Active window: process={process_name}, pid={pid}, title={window_title!r}")
            enforce_rules(process_name, window_title, pid, config_data)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[FocusGuard] blocker test stopped by user.")
