"""
FocusGuard 控制中心 (dashboard_ui.py)

定位：程序的主控窗口，作为全局图形化入口，集成专注启动、规则管理和数据统计于一体。

职责：
- 提供三大核心功能区：专注台、规则库、统计局
- 专注台：接管原 setup_ui.py 的功能，作为专注启动的图形化入口
- 规则库：可视化黑名单管理与 Smart Picker（进程/标题黑名单、一键嗅探活跃进程）
- 统计局：读取并解析 focus_log.txt，展示今日/累计专注与历史记录
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
import urllib.request
import urllib.error
import ctypes

# 统计局：单条记录 (时间戳字符串, 目标文本, 分钟数)
StatsRecord = Tuple[str, str, int]
# 统计局：_load_statistics 返回结构
StatsData = Dict[str, Any]

import customtkinter as ctk
import psutil
import win32gui
import win32process
import matplotlib
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# 全局配置 Matplotlib 的中文字体与负号显示，避免图表中文字变成方块或负号丢失
matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
matplotlib.rcParams["axes.unicode_minus"] = False

try:
    import win32com.client
except ImportError:
    win32com = None  # type: ignore[assignment]

# 与 dashboard_ui.py 同目录的 config.json
_CONFIG_PATH = Path(__file__).resolve().with_name("config.json")
_DEFAULT_CONFIG: Dict[str, List[str]] = {
    "process_blacklist": [],
    "title_blacklist": [],
    "os_whitelist": [
        "explorer.exe",
        "taskmgr.exe",
        "searchhost.exe",
        "cmd.exe",
        "python.exe",
        "focus_guard.exe",
        "anydesk.exe",
        "todesk.exe",
    ],
}


def load_config() -> Dict[str, Any]:
    """
    读取同目录下的 config.json。
    若文件不存在或为空，返回默认结构 {"process_blacklist": [], "title_blacklist": []}。
    """
    if not _CONFIG_PATH.exists():
        return dict(_DEFAULT_CONFIG)
    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return dict(_DEFAULT_CONFIG)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return dict(_DEFAULT_CONFIG)
        # 兼容老配置文件，补全缺失字段
        return {
            "process_blacklist": list(data.get("process_blacklist") or []),
            "title_blacklist": list(data.get("title_blacklist") or []),
            "os_whitelist": list(
                data.get("os_whitelist")
                or _DEFAULT_CONFIG["os_whitelist"]
            ),
        }
    except Exception as e:
        print(f"[FocusGuard] load_config error: {e}")
        return dict(_DEFAULT_CONFIG)


def save_config(config_data: Dict[str, Any]) -> None:
    """覆写同目录下的 config.json。"""
    try:
        with _CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[FocusGuard] save_config error: {e}")


def _format_duration(minutes: int) -> str:
    """将分钟数转为「X 小时 Y 分钟」展示。"""
    if minutes <= 0:
        return "0 分钟"
    h, m = divmod(minutes, 60)
    if h == 0:
        return f"{m} 分钟"
    if m == 0:
        return f"{h} 小时"
    return f"{h} 小时 {m} 分钟"


def _load_statistics() -> StatsData:
    """
    读取同目录 focus_log.txt，解析为统计局所需数据。
    格式约定：时间戳 | 目标文本 | 分钟数（如 2026-02-24 10:30:00 | 算法复习 | 25）。
    若文件不存在或解析失败，返回空结构。
    返回字段：today_minutes, total_minutes, today_str, total_str, records。
    """
    log_path = Path(__file__).resolve().with_name("focus_log.txt")
    empty: StatsData = {
        "today_minutes": 0,
        "total_minutes": 0,
        "today_str": "0 分钟",
        "total_str": "0 分钟",
        "records": [],
    }
    if not log_path.exists():
        return empty
    try:
        text = log_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[FocusGuard] _load_statistics read error: {e}")
        return empty

    today_date = datetime.date.today()
    total_minutes = 0
    today_minutes = 0
    records: List[StatsRecord] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" | ", 2)
        if len(parts) != 3:
            continue
        ts_str, target, min_str = parts[0].strip(), parts[1].strip(), parts[2].strip()
        try:
            minutes = int(min_str)
        except ValueError:
            continue
        try:
            dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        total_minutes += minutes
        if dt.date() == today_date:
            today_minutes += minutes
        records.append((ts_str, target, minutes))

    return {
        "today_minutes": today_minutes,
        "total_minutes": total_minutes,
        "today_str": _format_duration(today_minutes),
        "total_str": _format_duration(total_minutes),
        "records": records,
    }


def _sniff_active_process_names() -> List[str]:
    """
    使用 win32gui.EnumWindows 遍历所有窗口，仅保留可见且标题非空的窗口，
    通过 PID 取进程名并去重，返回当前真正活跃的桌面软件进程名列表（小写）。
    """
    collected: set = set()

    def _enum_cb(hwnd: int, _: None) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not (title and title.strip()):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return True
            name = psutil.Process(pid).name()
            if name:
                collected.add(name.lower())
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
        return True

    try:
        win32gui.EnumWindows(_enum_cb, None)
    except Exception as e:
        print(f"[FocusGuard] EnumWindows error: {e}")
    return list(collected)


def _scan_start_menu_shortcuts() -> List[Tuple[str, str]]:
    """
    扫描系统与当前用户的「开始菜单」快捷方式目录（.lnk），解析出目标 .exe 文件名。
    返回 [(显示名, exe 文件名), ...]，解析失败则跳过（try-except）。
    """
    if win32com is None:
        return []
    result: List[Tuple[str, str]] = []
    seen_exe: set = set()

    program_data = os.environ.get("ProgramData", "C:\\ProgramData")
    app_data = os.environ.get("APPDATA", "")
    roots = [
        Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]

    shell = win32com.client.Dispatch("WScript.Shell")
    for root in roots:
        if not root.exists():
            continue
        for lnk_path in root.rglob("*.lnk"):
            try:
                shortcut = shell.CreateShortCut(str(lnk_path.resolve()))
                target = shortcut.TargetPath
                if not target or not target.strip():
                    continue
                exe_name = os.path.basename(target).strip().lower()
                if not exe_name.endswith(".exe"):
                    continue
                if exe_name in seen_exe:
                    continue
                seen_exe.add(exe_name)
                display_name = lnk_path.stem or exe_name
                result.append((display_name, exe_name))
            except Exception:
                continue

    return result


def _sniff_active_window_titles() -> List[str]:
    """
    使用 win32gui.EnumWindows 遍历所有窗口，仅保留可见且标题非空的窗口，
    返回去重后的窗口标题列表（保留原样，不转小写）。
    """
    collected: List[str] = []
    seen: set = set()

    def _enum_cb(hwnd: int, _: None) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not (title and title.strip()):
                return True
            if title in seen:
                return True
            seen.add(title)
            collected.append(title)
        except OSError:
            pass
        return True

    try:
        win32gui.EnumWindows(_enum_cb, None)
    except Exception as e:
        print(f"[FocusGuard] EnumWindows (titles) error: {e}")
    return collected


def _center_window(root: ctk.CTk, width: int = 700, height: int = 500) -> None:
    """将窗口居中显示"""
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - width) // 2
    y = (sh - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")


def _log_focus_session(target: str, minutes: int) -> None:
    """
    将专注会话信息追加写入 focus_log.txt。
    格式：时间戳 | 目标 | 时长（分钟）
    """
    log_path = Path(__file__).with_name("focus_log.txt")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"{timestamp} | {target} | {minutes}\n"

    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as e:
        # 日志写入失败不影响主流程
        print(f"[FocusGuard] Failed to write focus log: {e}")


def _write_focus_command(minutes: int, target: str) -> None:
    """
    将专注命令写入 focus_command.json，供主进程读取。
    格式：{"minutes": X, "target": "专注目标文本"}
    """
    command_path = Path(__file__).with_name("focus_command.json")
    command_data = {"minutes": minutes, "target": target}

    try:
        with command_path.open("w", encoding="utf-8") as f:
            json.dump(command_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[FocusGuard] Failed to write focus command: {e}")


def run_dashboard() -> None:
    """
    运行 FocusGuard 控制中心主窗口。
    """
    # 设置 CustomTkinter 主题
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("FocusGuard 控制中心")
    root.resizable(True, True)

    _center_window(root, width=700, height=500)

    # 创建 TabView
    tabview = ctk.CTkTabview(root)
    tabview.pack(fill="both", expand=True, padx=20, pady=20)

    # Tab 1: 专注台 (Focus)
    focus_tab = tabview.add("专注台")
    
    # 顶部提示
    tip_label = ctk.CTkLabel(
        focus_tab,
        text="请输入专注目标并选择时长，点击按钮开始专注：",
        font=ctk.CTkFont(size=13, weight="bold"),
        anchor="w",
    )
    tip_label.pack(fill="x", pady=(0, 16))

    # 专注目标输入框
    target_entry = ctk.CTkEntry(
        focus_tab,
        placeholder_text="请输入专注目标",
        font=ctk.CTkFont(size=12),
        height=40,
    )
    target_entry.pack(fill="x", pady=(0, 20), padx=20)

    # 时长选择按钮容器（快捷时长）
    btn_frame = ctk.CTkFrame(focus_tab)
    btn_frame.pack(fill="x", pady=(0, 12), padx=20)

    def _start_focus_with_minutes(minutes: int) -> None:
        """开始专注：写入日志、写入命令文件、退出界面进程"""
        # 获取专注目标文本
        target_text = target_entry.get().strip() or "未设置目标"
        
        # ① 将时间戳、目标、时长追加写入 focus_log.txt
        _log_focus_session(target_text, minutes)
        
        # ② 将 {"minutes": X, "target": "..."} 以 JSON 格式写入 focus_command.json
        _write_focus_command(minutes, target_text)
        
        # ③ 调用 sys.exit(0) 退出当前界面进程
        try:
            root.destroy()
        except Exception:
            pass
        sys.exit(0)

    # 四个快捷时长按钮
    btn_25 = ctk.CTkButton(
        btn_frame,
        text="25 分钟",
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: _start_focus_with_minutes(25),
        height=40,
        corner_radius=8,
    )
    btn_25.pack(fill="x", pady=(0, 10))

    btn_45 = ctk.CTkButton(
        btn_frame,
        text="45 分钟",
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: _start_focus_with_minutes(45),
        height=40,
        corner_radius=8,
    )
    btn_45.pack(fill="x", pady=(0, 10))

    btn_60 = ctk.CTkButton(
        btn_frame,
        text="60 分钟",
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: _start_focus_with_minutes(60),
        height=40,
        corner_radius=8,
    )
    btn_60.pack(fill="x", pady=(0, 10))

    btn_test = ctk.CTkButton(
        btn_frame,
        text="1 分钟 (测试)",
        font=ctk.CTkFont(size=11),
        command=lambda: _start_focus_with_minutes(1),
        height=32,
        corner_radius=6,
        fg_color=("gray70", "gray30"),
    )
    btn_test.pack(fill="x")

    # 自定义时长区域（水平排列）
    custom_frame = ctk.CTkFrame(focus_tab)
    custom_frame.pack(fill="x", pady=(8, 20), padx=20)

    custom_label = ctk.CTkLabel(
        custom_frame,
        text="自定义时长(分钟)：",
        font=ctk.CTkFont(size=11),
        anchor="w",
    )
    custom_label.pack(side="left", padx=(4, 8), pady=8)

    custom_time_entry = ctk.CTkEntry(
        custom_frame,
        width=70,
        font=ctk.CTkFont(size=11),
    )
    custom_time_entry.pack(side="left", padx=(0, 8), pady=8)

    def _start_custom_focus() -> None:
        """从输入框读取自定义分钟数并启动专注（含防御性校验）。"""
        raw_value = custom_time_entry.get().strip()
        try:
            minutes = int(raw_value)
        except Exception:
            minutes = -1

        if not raw_value or minutes <= 0 or minutes > 360:
            try:
                MB_ICONERROR = 0x10
                MB_TOPMOST = 0x00040000
                flags = MB_ICONERROR | MB_TOPMOST
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "请输入 1 到 360 之间的有效整数！",
                    "FocusGuard 输入错误",
                    flags,
                )
            except Exception:
                print("[FocusGuard] 无效的自定义时长输入")
            return

        # 校验通过，复用统一的启动逻辑
        _start_focus_with_minutes(minutes)

    custom_start_btn = ctk.CTkButton(
        custom_frame,
        text="开始",
        font=ctk.CTkFont(size=11, weight="bold"),
        command=_start_custom_focus,
        height=32,
        corner_radius=8,
    )
    custom_start_btn.pack(side="left", padx=(0, 4), pady=8, fill="x", expand=True)

    # Tab 2: 规则库 (Blacklist + OS 白名单)
    blacklist_tab = tabview.add("规则库")
    # 内存中的配置，与 config.json 同步
    config_data: Dict[str, List[str]] = load_config()
    if not isinstance(config_data.get("process_blacklist"), list):
        config_data["process_blacklist"] = []
    if not isinstance(config_data.get("title_blacklist"), list):
        config_data["title_blacklist"] = []
    if not isinstance(config_data.get("os_whitelist"), list):
        config_data["os_whitelist"] = _DEFAULT_CONFIG["os_whitelist"][:]

    # 规则库内再引入 TabView，分为三类规则
    rules_tabview = ctk.CTkTabview(blacklist_tab)
    rules_tabview.pack(fill="both", expand=True, padx=8, pady=8)

    process_tab = rules_tabview.add("进程黑名单")
    title_tab = rules_tabview.add("标题黑名单")
    os_tab = rules_tabview.add("系统免疫白名单")

    # ---------- 进程黑名单 Tab ----------
    left_frame = ctk.CTkFrame(process_tab)
    left_frame.pack(fill="both", expand=True, padx=8, pady=8)

    ctk.CTkLabel(
        left_frame,
        text="进程黑名单",
        font=ctk.CTkFont(size=13, weight="bold"),
        anchor="w",
    ).pack(fill="x", padx=12, pady=(12, 8))

    add_row_left = ctk.CTkFrame(left_frame)
    add_row_left.pack(fill="x", padx=12, pady=(0, 8))

    entry_process = ctk.CTkEntry(
        add_row_left,
        placeholder_text="输入进程名，如 notepad.exe",
        font=ctk.CTkFont(size=11),
        height=32,
    )
    entry_process.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def add_process() -> None:
        name = entry_process.get().strip()
        if not name:
            return
        name = name.lower()
        if name not in config_data["process_blacklist"]:
            config_data["process_blacklist"].append(name)
            save_config(config_data)
            refresh_process_list()
        entry_process.delete(0, "end")

    ctk.CTkButton(
        add_row_left,
        text="添加",
        width=60,
        height=32,
        command=add_process,
    ).pack(side="left", padx=(0, 6))

    def open_installed_scan() -> None:
        items = _scan_start_menu_shortcuts()
        if not items:
            return
        toplevel = ctk.CTkToplevel(root)
        toplevel.title("扫描已安装软件")
        toplevel.attributes("-topmost", True)
        toplevel.geometry("480x420")
        toplevel.transient(root)

        ctk.CTkLabel(
            toplevel,
            text="以下为开始菜单中的快捷方式解析出的程序，点击「加入黑名单」即可封杀。",
            font=ctk.CTkFont(size=12),
            anchor="w",
            wraplength=440,
        ).pack(fill="x", padx=16, pady=(16, 8))

        scroll = ctk.CTkScrollableFrame(toplevel)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        for display_name, exe_name in sorted(items, key=lambda x: x[0].lower()):
            row = ctk.CTkFrame(scroll)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row,
                text=f"{display_name} ({exe_name})",
                font=ctk.CTkFont(size=11),
                anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=8, pady=6)

            def _add_to_blacklist(exe: str) -> None:
                key = exe.lower()
                if key not in config_data["process_blacklist"]:
                    config_data["process_blacklist"].append(key)
                    save_config(config_data)
                    refresh_process_list()

            ctk.CTkButton(
                row,
                text="加入黑名单",
                width=90,
                height=28,
                command=lambda e=exe_name: _add_to_blacklist(e),
            ).pack(side="right", padx=8, pady=4)

    ctk.CTkButton(
        add_row_left,
        text="扫描已安装软件",
        width=100,
        height=28,
        font=ctk.CTkFont(size=10),
        fg_color=("gray65", "gray35"),
        hover_color=("gray55", "gray45"),
        command=open_installed_scan,
    ).pack(side="left")

    process_scroll = ctk.CTkScrollableFrame(left_frame)
    process_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 8))

    def refresh_process_list() -> None:
        for w in process_scroll.winfo_children():
            w.destroy()
        for name in config_data["process_blacklist"]:
            row = ctk.CTkFrame(process_scroll)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=name, font=ctk.CTkFont(size=11), anchor="w").pack(
                side="left", fill="x", expand=True, padx=8, pady=6
            )

            def _remove(proc: str) -> None:
                if proc in config_data["process_blacklist"]:
                    config_data["process_blacklist"].remove(proc)
                    save_config(config_data)
                    refresh_process_list()

            ctk.CTkButton(
                row,
                text="删除",
                width=50,
                height=28,
                fg_color=("#c0392b", "#a93226"),
                hover_color=("#e74c3c", "#cb4335"),
                command=lambda p=name: _remove(p),
            ).pack(side="right", padx=8, pady=4)

    refresh_process_list()

    def open_smart_picker() -> None:
        process_names = _sniff_active_process_names()
        if not process_names:
            return
        toplevel = ctk.CTkToplevel(root)
        toplevel.title("选择要封杀的程序")
        toplevel.attributes("-topmost", True)
        toplevel.geometry("400x400")
        toplevel.transient(root)

        ctk.CTkLabel(
            toplevel,
            text="以下为当前可见窗口对应的进程，点击「加入黑名单」即可封杀。",
            font=ctk.CTkFont(size=12),
            anchor="w",
            wraplength=360,
        ).pack(fill="x", padx=16, pady=(16, 8))

        scroll = ctk.CTkScrollableFrame(toplevel)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        for proc in sorted(process_names):
            row = ctk.CTkFrame(scroll)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=proc, font=ctk.CTkFont(size=11), anchor="w").pack(
                side="left", fill="x", expand=True, padx=8, pady=6
            )

            def _add_to_blacklist(p: str) -> None:
                key = p.lower()
                if key not in config_data["process_blacklist"]:
                    config_data["process_blacklist"].append(key)
                    save_config(config_data)
                    refresh_process_list()
                toplevel.destroy()

            ctk.CTkButton(
                row,
                text="加入黑名单",
                width=90,
                height=28,
                command=lambda p=proc: _add_to_blacklist(p),
            ).pack(side="right", padx=8, pady=4)

    ctk.CTkButton(
        left_frame,
        text="一键嗅探活跃进程",
        font=ctk.CTkFont(size=12, weight="bold"),
        height=36,
        fg_color=("#16a085", "#1e8449"),
        hover_color=("#1abc9c", "#27ae60"),
        command=open_smart_picker,
    ).pack(fill="x", padx=12, pady=(0, 12))

    # ---------- 标题黑名单 Tab ----------
    right_frame = ctk.CTkFrame(title_tab)
    right_frame.pack(fill="both", expand=True, padx=8, pady=8)

    ctk.CTkLabel(
        right_frame,
        text="标题黑名单（网页/窗口标题关键字）",
        font=ctk.CTkFont(size=13, weight="bold"),
        anchor="w",
    ).pack(fill="x", padx=12, pady=(12, 8))

    add_row_right = ctk.CTkFrame(right_frame)
    add_row_right.pack(fill="x", padx=12, pady=(0, 8))

    entry_title = ctk.CTkEntry(
        add_row_right,
        placeholder_text="输入关键字，如 bilibili",
        font=ctk.CTkFont(size=11),
        height=32,
    )
    entry_title.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def add_title() -> None:
        kw = entry_title.get().strip()
        if not kw:
            return
        if kw not in config_data["title_blacklist"]:
            config_data["title_blacklist"].append(kw)
            save_config(config_data)
            refresh_title_list()
        entry_title.delete(0, "end")

    ctk.CTkButton(
        add_row_right,
        text="添加",
        width=60,
        height=32,
        command=add_title,
    ).pack(side="left")

    title_scroll = ctk.CTkScrollableFrame(right_frame)
    title_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 8))

    def refresh_title_list() -> None:
        for w in title_scroll.winfo_children():
            w.destroy()
        for kw in config_data["title_blacklist"]:
            row = ctk.CTkFrame(title_scroll)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=kw, font=ctk.CTkFont(size=11), anchor="w").pack(
                side="left", fill="x", expand=True, padx=8, pady=6
            )

            def _remove(k: str) -> None:
                if k in config_data["title_blacklist"]:
                    config_data["title_blacklist"].remove(k)
                    save_config(config_data)
                    refresh_title_list()

            ctk.CTkButton(
                row,
                text="删除",
                width=50,
                height=28,
                fg_color=("#c0392b", "#a93226"),
                hover_color=("#e74c3c", "#cb4335"),
                command=lambda k=kw: _remove(k),
            ).pack(side="right", padx=8, pady=4)

    refresh_title_list()

    # ---------- 系统免疫白名单 Tab ----------
    os_frame = ctk.CTkFrame(os_tab)
    os_frame.pack(fill="both", expand=True, padx=8, pady=8)

    ctk.CTkLabel(
        os_frame,
        text="系统免疫白名单（不会交给 AI 审计的系统/基础进程）",
        font=ctk.CTkFont(size=13, weight="bold"),
        anchor="w",
    ).pack(fill="x", padx=12, pady=(12, 8))

    os_add_row = ctk.CTkFrame(os_frame)
    os_add_row.pack(fill="x", padx=12, pady=(0, 8))

    os_entry = ctk.CTkEntry(
        os_add_row,
        placeholder_text="输入进程名，如 todesk.exe",
        font=ctk.CTkFont(size=11),
        height=32,
    )
    os_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

    os_scroll = ctk.CTkScrollableFrame(os_frame)
    os_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 8))

    def refresh_os_whitelist() -> None:
        for w in os_scroll.winfo_children():
            w.destroy()
        for name in config_data["os_whitelist"]:
            row = ctk.CTkFrame(os_scroll)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row, text=name, font=ctk.CTkFont(size=11), anchor="w"
            ).pack(side="left", fill="x", expand=True, padx=8, pady=6)

            def _remove_os(proc: str) -> None:
                if proc in config_data["os_whitelist"]:
                    config_data["os_whitelist"].remove(proc)
                    save_config(config_data)
                    refresh_os_whitelist()

            ctk.CTkButton(
                row,
                text="删除",
                width=60,
                height=28,
                fg_color=("#c0392b", "#a93226"),
                hover_color=("#e74c3c", "#cb4335"),
                command=lambda p=name: _remove_os(p),
            ).pack(side="right", padx=8, pady=4)

    def add_os_entry() -> None:
        name = os_entry.get().strip()
        if not name:
            return
        key = name.lower()
        if key not in config_data["os_whitelist"]:
            config_data["os_whitelist"].append(key)
            save_config(config_data)
            refresh_os_whitelist()
        os_entry.delete(0, "end")

    ctk.CTkButton(
        os_add_row,
        text="添加",
        width=70,
        height=32,
        command=add_os_entry,
    ).pack(side="left")

    refresh_os_whitelist()

    def open_title_picker() -> None:
        # 从本地 FocusGuard HTTP 服务获取最近浏览器标签页特征
        api_url = "http://127.0.0.1:11235/api/recent_tabs"
        try:
            with urllib.request.urlopen(api_url, timeout=2) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, list) or not data:
                info = ctk.CTkToplevel(root)
                info.title("暂无标签页数据")
                info.attributes("-topmost", True)
                info.geometry("360x120")
                info.transient(root)
                ctk.CTkLabel(
                    info,
                    text="当前没有可用的浏览器标签页数据。\n请在浏览器中切换或打开几个标签页后重试。",
                    font=ctk.CTkFont(size=12),
                    anchor="center",
                    justify="center",
                    wraplength=320,
                ).pack(fill="both", expand=True, padx=16, pady=16)
                return
        except (urllib.error.URLError, TimeoutError, OSError):
            warn = ctk.CTkToplevel(root)
            warn.title("无法连接 FocusGuard 本地服务")
            warn.attributes("-topmost", True)
            warn.geometry("360x120")
            warn.transient(root)
            ctk.CTkLabel(
                warn,
                text="请先开启 FocusGuard 主程序以接收浏览器数据。",
                font=ctk.CTkFont(size=12),
                anchor="center",
                justify="center",
                wraplength=320,
            ).pack(fill="both", expand=True, padx=16, pady=16)
            return
        except Exception as e:
            print(f"[FocusGuard] 获取 recent_tabs 失败: {e}")
            return

        # 构建标签页选择窗口
        toplevel = ctk.CTkToplevel(root)
        toplevel.title("嗅探近期浏览器标签页")
        toplevel.attributes("-topmost", True)
        toplevel.geometry("600x420")
        toplevel.transient(root)

        ctk.CTkLabel(
            toplevel,
            text="以下为近期浏览器标签页。点击「提取标题」或「提取 URL」可将文本填入右侧输入框，便于精修为关键字后再点击【添加】。",
            font=ctk.CTkFont(size=12),
            anchor="w",
            wraplength=560,
        ).pack(fill="x", padx=16, pady=(16, 8))

        scroll = ctk.CTkScrollableFrame(toplevel)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        # data 形如 [{"title": ..., "url": ..., "timestamp": ...}, ...]，server 已按时间倒序
        for item in data:
            title_val = str(item.get("title", "") or "")
            url_val = str(item.get("url", "") or "")
            display = title_val or url_val or "(空标题)"

            row = ctk.CTkFrame(scroll)
            row.pack(fill="x", pady=3)

            ctk.CTkLabel(
                row,
                text=f"{display} ({url_val})",
                font=ctk.CTkFont(size=11),
                anchor="w",
                wraplength=360,
            ).pack(side="left", fill="x", expand=True, padx=8, pady=6)

            def _fill_with(text: str) -> None:
                entry_title.delete(0, "end")
                entry_title.insert(0, text)
                toplevel.destroy()

            ctk.CTkButton(
                row,
                text="提取标题",
                width=80,
                height=26,
                fg_color=("#16a085", "#1e8449"),
                hover_color=("#1abc9c", "#27ae60"),
                command=lambda t=title_val: _fill_with(t),
            ).pack(side="right", padx=(4, 4), pady=4)

            ctk.CTkButton(
                row,
                text="提取 URL",
                width=80,
                height=26,
                fg_color=("#566573", "#2c3e50"),
                hover_color=("#808b96", "#34495e"),
                command=lambda u=url_val: _fill_with(u),
            ).pack(side="right", padx=(4, 4), pady=4)

    ctk.CTkButton(
        right_frame,
        text="嗅探近期浏览器标签页",
        font=ctk.CTkFont(size=12, weight="bold"),
        height=36,
        fg_color=("#16a085", "#1e8449"),
        hover_color=("#1abc9c", "#27ae60"),
        command=open_title_picker,
    ).pack(fill="x", padx=12, pady=(0, 12))

    # Tab 3: 统计局 (Statistics)
    stats_tab = tabview.add("统计局")

    # 顶层：使用可滚动容器承载整个统计局内容
    stats_scroll = ctk.CTkScrollableFrame(stats_tab)
    stats_scroll.pack(fill="both", expand=True, padx=16, pady=16)

    # Top Layer: KPI 数据卡片区域
    kpi_frame = ctk.CTkFrame(stats_scroll)
    kpi_frame.pack(fill="x", pady=(0, 16))

    today_label = ctk.CTkLabel(
        kpi_frame,
        text="今日专注：0 分钟",
        font=ctk.CTkFont(size=18, weight="bold"),
        anchor="center",
    )
    today_label.pack(side="left", expand=True, padx=8, pady=8)

    total_label = ctk.CTkLabel(
        kpi_frame,
        text="累计总专注：0 分钟",
        font=ctk.CTkFont(size=18, weight="bold"),
        anchor="center",
    )
    total_label.pack(side="left", expand=True, padx=8, pady=8)

    top_target_label = ctk.CTkLabel(
        kpi_frame,
        text="最常目标：—",
        font=ctk.CTkFont(size=18, weight="bold"),
        anchor="center",
    )
    top_target_label.pack(side="left", expand=True, padx=8, pady=8)

    # Middle Layer: 近 7 日趋势图容器
    trend_frame = ctk.CTkFrame(stats_scroll)
    trend_frame.pack(fill="both", expand=True, pady=(0, 16))

    # Bottom Layer: 环形图 + 历史记录
    bottom_frame = ctk.CTkFrame(stats_scroll)
    bottom_frame.pack(fill="both", expand=True)

    pie_frame = ctk.CTkFrame(bottom_frame)
    pie_frame.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

    history_frame = ctk.CTkFrame(bottom_frame)
    history_frame.pack(side="right", fill="both", expand=True, padx=(8, 0), pady=8)

    history_box = ctk.CTkTextbox(
        history_frame,
        wrap="none",
        font=ctk.CTkFont(size=11),
    )
    history_box.pack(fill="both", expand=True, padx=8, pady=8)

    def _refresh_statistics() -> None:
        """刷新统计局数据与图表（暗黑主题 Dashboard）。"""
        # 全局暗黑样式（每次刷新确保样式一致）
        BG_COLOR = "#2B2B2B"
        matplotlib.rcParams["axes.facecolor"] = BG_COLOR
        matplotlib.rcParams["figure.facecolor"] = BG_COLOR

        data = _load_statistics()
        records = data["records"]

        # 计算 KPI：今日总时长、累计总时长、最常目标
        today_label.configure(text=f"今日专注：{data['today_str']}")
        total_label.configure(text=f"累计总专注：{data['total_str']}")

        target_to_minutes: Dict[str, int] = {}
        for ts_str, target, minutes in records:
            key = (target or "").strip() or "未设置目标"
            target_to_minutes[key] = target_to_minutes.get(key, 0) + minutes

        if target_to_minutes:
            top_target, _ = max(target_to_minutes.items(), key=lambda x: x[1])
            top_target_label.configure(text=f"最常目标：{top_target}")
        else:
            top_target_label.configure(text="最常目标：—")

        # 清空旧图表 Canvas，防止刷新叠加与内存泄漏
        for frame in (trend_frame, pie_frame):
            for w in frame.winfo_children():
                w.destroy()

        # 清理历史记录
        history_box.configure(state="normal")
        history_box.delete("1.0", "end")

        if not records:
            history_box.insert("end", "暂无专注数据，快去开启你的第一个番茄钟吧！\n")
            history_box.configure(state="disabled")
            return

        # 统计近 7 日每天专注总分钟数
        today_date = datetime.date.today()
        days = [today_date - datetime.timedelta(days=i) for i in range(6, -1, -1)]
        day_labels = [d.strftime("%Y-%m-%d") for d in days]
        day_set = {d for d in days}
        date_to_minutes: Dict[str, int] = {label: 0 for label in day_labels}

        for ts_str, target, minutes in records:
            try:
                dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            d = dt.date()
            if d in day_set:
                key = d.strftime("%Y-%m-%d")
                date_to_minutes[key] = date_to_minutes.get(key, 0) + minutes

        # Middle：近 7 日专注趋势柱状图（暗黑主题）
        fig_trend = Figure(figsize=(8, 3), dpi=100, facecolor=BG_COLOR)
        ax1 = fig_trend.add_subplot(111, facecolor=BG_COLOR)
        y_values = [date_to_minutes[label] for label in day_labels]
        bars = ax1.bar(range(len(day_labels)), y_values, color="#4AA3DF")
        ax1.set_title("近 7 日专注时长（分钟）", color="white")
        ax1.set_xlabel("日期", color="white")
        ax1.set_ylabel("专注分钟数", color="white")
        ax1.set_xticks(range(len(day_labels)))
        ax1.set_xticklabels(day_labels, rotation=45, ha="right", color="white")

        # 隐藏多余边框，仅保留底边框
        for spine in ("top", "left", "right"):
            ax1.spines[spine].set_visible(False)
        ax1.spines["bottom"].set_color("white")

        # 横向虚线网格
        ax1.yaxis.grid(True, linestyle="--", alpha=0.3, color="white")
        ax1.tick_params(axis="y", colors="white")

        canvas_trend = FigureCanvasTkAgg(fig_trend, master=trend_frame)
        widget_trend = canvas_trend.get_tk_widget()
        widget_trend.pack(fill="both", expand=True, padx=8, pady=8)
        canvas_trend.draw()

        # Bottom Left：按目标累计时长环形图（Donut Chart）
        if target_to_minutes:
            sorted_items = sorted(
                target_to_minutes.items(), key=lambda x: x[1], reverse=True
            )
            top_items = sorted_items[:5]
            if len(sorted_items) > 5:
                other_sum = sum(v for _, v in sorted_items[5:])
                top_items.append(("其他", other_sum))

            labels2 = [k for k, _ in top_items]
            sizes2 = [v for _, v in top_items]

            fig_pie = Figure(figsize=(4, 4), dpi=100, facecolor=BG_COLOR)
            ax2 = fig_pie.add_subplot(111, facecolor=BG_COLOR)
            wedges, texts = ax2.pie(
                sizes2,
                labels=labels2,
                startangle=140,
                textprops={"color": "white", "fontsize": 9},
            )
            # 环形图中间挖空
            centre_circle = matplotlib.patches.Circle(
                (0, 0), 0.70, fc=BG_COLOR
            )
            ax2.add_artist(centre_circle)
            ax2.set_title("按专注目标累计时长分布", color="white")

            canvas_pie = FigureCanvasTkAgg(fig_pie, master=pie_frame)
            widget_pie = canvas_pie.get_tk_widget()
            widget_pie.pack(fill="both", expand=True, padx=8, pady=8)
            canvas_pie.draw()

        # Bottom Right：最近 20 条历史记录
        recent_records = list(reversed(records))[:20]
        for ts_str, target, minutes in recent_records:
            line = f"{ts_str}  |  {target or '—'}  |  {_format_duration(minutes)}\n"
            history_box.insert("end", line)
        history_box.see("end")
        history_box.configure(state="disabled")

    _refresh_statistics()

    root.mainloop()


if __name__ == "__main__":
    run_dashboard()
