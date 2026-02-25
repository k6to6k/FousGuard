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
import llm_classifier


# 与 main.py 中保持一致：被视为“浏览器进程”的可执行文件名（小写）
BROWSER_PROCESSES = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
}

# 系统级免死金牌（OS 级白名单），这些进程永不送入 LLM 审计，避免误杀核心系统组件
OS_WHITELIST = {
    "explorer.exe",
    "taskmgr.exe",
    "searchhost.exe",
    "shellexperiencehost.exe",
    "cmd.exe",
    "conhost.exe",
    "python.exe",
    "focus_guard.exe",
}


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


def _match_rules(process_name: Optional[str], window_title: Optional[str]) -> Tuple[bool, bool]:
    """
    根据全局缓存的黑名单判断当前窗口是否违规，并返回命中类型。
    返回值:
    - (matched_process, matched_title)
        matched_process: 是否命中进程黑名单
        matched_title: 是否命中标题关键字/正则

    要求在调用前确保 _ensure_rule_index 已被执行。
    """
    if _PROCESS_BLACKLIST_SET is None or _TITLE_PATTERNS is None:
        return (False, False)

    pn = (process_name or "").lower()
    wt = (window_title or "").lower()

    matched_process = False
    matched_title = False

    # 进程名精确匹配
    if pn and pn in _PROCESS_BLACKLIST_SET:
        matched_process = True

    # 窗口标题关键字/正则匹配
    if wt:
        for pattern in _TITLE_PATTERNS:
            if pattern.search(wt):
                matched_title = True
                break

    return matched_process, matched_title


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


def _send_ctrl_w_to_foreground() -> None:
    """
    尝试向当前前台窗口发送 Ctrl+W 快捷键，以关闭当前标签页/文档，而非整个进程。
    使用 Windows keybd_event，任何异常都会被吞掉以保证鲁棒性。
    """
    try:
        user32 = ctypes.windll.user32
        VK_CONTROL = 0x11
        VK_W = 0x57
        KEYEVENTF_KEYUP = 0x0002

        # Ctrl down + W down
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_W, 0, 0, 0)
        # W up + Ctrl up
        user32.keybd_event(VK_W, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        # 任何键盘模拟异常都不应影响主逻辑
        return


def enforce_rules(
    process_name: Optional[str],
    window_title: Optional[str],
    pid: Optional[int],
    config: Dict[str, Any],
    browser_url: str = "",
    browser_title: str = "",
    focus_target: str = "",
) -> None:
    """
    核心方法：根据配置判断当前前台窗口是否违规，并按命中类型执行不同策略。

    参数：
    - process_name: 当前前台窗口的进程名（小写，可能为 None）
    - window_title: 当前前台窗口标题（小写，可能为 None）
    - pid: 当前前台窗口所属进程的 PID（可能为 None）
    - config: 从 config.json 读取的配置字典
    - browser_url: 来自浏览器扩展的当前标签页 URL（可能为空）
    - browser_title: 来自浏览器扩展的当前标签页标题（可能为空）
    - focus_target: 当前专注目标文本（空字符串表示非专注状态或未设置目标）
    """
    if not process_name and not window_title:
        return

    # 确保全局匹配索引已根据最新配置构建
    _ensure_rule_index(config)

    matched_process, matched_title_window = _match_rules(process_name, window_title)

    # 使用浏览器扩展提供的 title/url 进行补充匹配
    matched_title_browser = False
    if _TITLE_PATTERNS is not None:
        bt = (browser_title or "").lower()
        bu = (browser_url or "").lower()
        for pattern in _TITLE_PATTERNS:
            if (bt and pattern.search(bt)) or (bu and pattern.search(bu)):
                matched_title_browser = True
                break

    matched_title = matched_title_window or matched_title_browser

    # 第一关：本地进程黑名单强力狙杀（永远最高优先级）
    if matched_process:
        print(
            f"[FocusGuard] Process blacklist hit: process={process_name}, pid={pid}, "
            f"window_title={window_title!r}"
        )
        killed = _kill_process_by_pid(pid, process_name)
        if not killed:
            _kill_processes_by_name(process_name or "")
        return

    # 第二关：LLM 全场景智能审查（专注模式下，非 OS 白名单进程一律送入 LLM）
    is_browser = bool(process_name and process_name.lower() in BROWSER_PROCESSES)
    pn_lower = (process_name or "").lower()

    if focus_target and pn_lower and pn_lower not in OS_WHITELIST:
        # 浏览器宽限期：扩展尚未上报 URL 或标题只是 URL 片段（SPA 过渡期）→ 暂缓决策
        bt_lower = (browser_title or "").lower()
        bu_lower = (browser_url or "").lower()
        if is_browser and ((not browser_url) or (bt_lower and bt_lower in bu_lower)):
            print(
                f"[FocusGuard] Browser title loading grace period: "
                f"browser_title={browser_title!r}, url={browser_url!r}, focus_target={focus_target!r}"
            )
            return

        # 统一调用 LLM 审计当前行为（本地应用或网页）
        try:
            is_block = llm_classifier.evaluate_intent(
                focus_target,
                process_name or "",
                browser_title or window_title or "",
                browser_url,
            )
        except Exception as exc:
            is_block = None
            print(f"[FocusGuard] LLM API exception, degrading to static rules: {exc}")

        if is_block is True:
            # AI 判定为偏离目标 → 浏览器采用软阻断，本地应用采用硬阻断
            if is_browser:
                target_name = browser_title or window_title or (process_name or "受限内容")

                def _ai_soft_block() -> None:
                    _send_ctrl_w_to_foreground()
                    try:
                        name = (target_name or "").strip() or "未知页面"
                        MB_ICONWARNING = 0x30
                        MB_TOPMOST = 0x00040000
                        MB_SETFOREGROUND = 0x00010000
                        MB_SYSTEMMODAL = 0x00001000
                        flags = (
                            MB_ICONWARNING
                            | MB_TOPMOST
                            | MB_SETFOREGROUND
                            | MB_SYSTEMMODAL
                        )
                        ctypes.windll.user32.MessageBoxW(
                            None,
                            f"[AI 护航拦截] 偏离目标：{name}",
                            "FocusGuard 智能阻断",
                            flags,
                        )
                    except Exception:
                        # 弹窗异常不影响主逻辑
                        return

                threading.Thread(
                    target=_ai_soft_block,
                    daemon=True,
                ).start()
            else:
                # 本地应用：使用 psutil 静默秒杀（不复用旧版通用 kill 辅助函数，以避免旧版警告弹窗叠加），并仅弹出 AI 护航专属提示
                target_name = window_title or process_name or "受限应用"

                # 静默硬阻断：优先按 PID 精准结束，失败时按进程名遍历结束；过程中不触发任何旧版 UI 提示
                killed = False
                if pid is not None and pid > 0:
                    try:
                        proc = psutil.Process(pid)
                        name = proc.name()
                        proc.kill()
                        print(f"[FocusGuard] AI hard-kill local app by pid: {name} (pid={pid})")
                        killed = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        print(
                            f"[FocusGuard] AI hard-kill by pid failed: pid={pid}, process={process_name}"
                        )
                    except Exception:
                        print(
                            f"[FocusGuard] Unexpected error during AI hard-kill by pid={pid}, process={process_name}"
                        )

                if not killed and process_name:
                    target = process_name.lower()
                    for proc in psutil.process_iter(attrs=["pid", "name"]):
                        try:
                            name = proc.info.get("name") or proc.name()
                            if not name or name.lower() != target:
                                continue
                            try:
                                proc.kill()
                                print(
                                    f"[FocusGuard] AI hard-kill local app by name: {name} (pid={proc.pid})"
                                )
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                print(
                                    f"[FocusGuard] AI hard-kill by name failed: {name} (pid={proc.pid})"
                                )
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                        except Exception:
                            print(
                                f"[FocusGuard] Unexpected error during AI hard-kill by name={process_name}"
                            )

                # 仅展示 AI 护航专属 UI 提示
                try:
                    name = (target_name or "").strip() or "未知应用"
                    MB_ICONWARNING = 0x30
                    MB_TOPMOST = 0x00040000
                    MB_SETFOREGROUND = 0x00010000
                    MB_SYSTEMMODAL = 0x00001000
                    flags = (
                        MB_ICONWARNING
                        | MB_TOPMOST
                        | MB_SETFOREGROUND
                        | MB_SYSTEMMODAL
                    )
                    ctypes.windll.user32.MessageBoxW(
                        None,
                        f"[AI 护航拦截] 本地应用偏离目标：{name}",
                        "FocusGuard 智能阻断",
                        flags,
                    )
                except Exception:
                    pass

            return
        elif is_block is False:
            # LLM 明确判定放行：直接返回，跳过静态规则
            print(
                f"[FocusGuard] LLM explicitly allowed, skip static rules: "
                f"target={focus_target!r}, process={process_name!r}, title={browser_title or window_title!r}, url={browser_url!r}"
            )
            return
        elif is_block is None:
            # LLM 不可用（超时/解析错误/结构异常），降级到静态标题规则兜底
            print(
                f"[FocusGuard] LLM unavailable, falling back to static title rules: "
                f"target={focus_target!r}, process={process_name!r}, title={browser_title or window_title!r}, url={browser_url!r}"
            )
            # 不 return，由后续“第三关：静态规则兜底”继续处理

    # 第三关：静态规则兜底（仅非浏览器场景或无专注目标时生效）
    if not matched_title:
        return

    print(
        f"[FocusGuard] Static title rule matched: process={process_name}, pid={pid}, "
        f"window_title={window_title!r}, browser_title={browser_title!r}, browser_url={browser_url!r}, "
        f"by_title_window={matched_title_window}, by_title_browser={matched_title_browser}"
    )

    # 对于网页类拦截，更希望提示“哪个页面”而不是“哪个进程”
    target_name = browser_title or window_title or (process_name or "受限内容")

    def _soft_block() -> None:
        _send_ctrl_w_to_foreground()
        show_block_warning(target_name)

    threading.Thread(
        target=_soft_block,
        daemon=True,
    ).start()


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
