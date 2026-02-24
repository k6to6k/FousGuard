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

# 统计局：单条记录 (时间戳字符串, 目标文本, 分钟数)
StatsRecord = Tuple[str, str, int]
# 统计局：_load_statistics 返回结构
StatsData = Dict[str, Any]

import customtkinter as ctk
import psutil
import win32gui
import win32process

try:
    import win32com.client
except ImportError:
    win32com = None  # type: ignore[assignment]

# 与 dashboard_ui.py 同目录的 config.json
_CONFIG_PATH = Path(__file__).resolve().with_name("config.json")
_DEFAULT_CONFIG: Dict[str, List[str]] = {"process_blacklist": [], "title_blacklist": []}


def load_config() -> Dict[str, Any]:
    """
    读取同目录下的 config.json。
    若文件不存在或为空，返回默认结构 {"process_blacklist": [], "title_blacklist": []}。
    """
    if not _CONFIG_PATH.exists():
        return {"process_blacklist": [], "title_blacklist": []}
    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return {"process_blacklist": [], "title_blacklist": []}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"process_blacklist": [], "title_blacklist": []}
        return {
            "process_blacklist": list(data.get("process_blacklist") or []),
            "title_blacklist": list(data.get("title_blacklist") or []),
        }
    except Exception as e:
        print(f"[FocusGuard] load_config error: {e}")
        return {"process_blacklist": [], "title_blacklist": []}


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

    # 时长选择按钮容器
    btn_frame = ctk.CTkFrame(focus_tab)
    btn_frame.pack(fill="x", pady=(0, 20), padx=20)

    def start_focus(minutes: int) -> None:
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

    # 四个时长按钮
    btn_25 = ctk.CTkButton(
        btn_frame,
        text="25 分钟",
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: start_focus(25),
        height=40,
        corner_radius=8,
    )
    btn_25.pack(fill="x", pady=(0, 10))

    btn_45 = ctk.CTkButton(
        btn_frame,
        text="45 分钟",
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: start_focus(45),
        height=40,
        corner_radius=8,
    )
    btn_45.pack(fill="x", pady=(0, 10))

    btn_60 = ctk.CTkButton(
        btn_frame,
        text="60 分钟",
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: start_focus(60),
        height=40,
        corner_radius=8,
    )
    btn_60.pack(fill="x", pady=(0, 10))

    btn_test = ctk.CTkButton(
        btn_frame,
        text="1 分钟 (测试)",
        font=ctk.CTkFont(size=11),
        command=lambda: start_focus(1),
        height=32,
        corner_radius=6,
        fg_color=("gray70", "gray30"),
    )
    btn_test.pack(fill="x")

    # Tab 2: 规则库 (Blacklist)
    blacklist_tab = tabview.add("规则库")
    # 内存中的配置，与 config.json 同步
    config_data: Dict[str, List[str]] = load_config()
    if not isinstance(config_data.get("process_blacklist"), list):
        config_data["process_blacklist"] = []
    if not isinstance(config_data.get("title_blacklist"), list):
        config_data["title_blacklist"] = []

    # ---------- 左侧：进程黑名单 ----------
    left_frame = ctk.CTkFrame(blacklist_tab)
    left_frame.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

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

    # ---------- 右侧：标题黑名单 ----------
    right_frame = ctk.CTkFrame(blacklist_tab)
    right_frame.pack(side="right", fill="both", expand=True, padx=(8, 0), pady=8)

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

    # 顶部数据看板容器
    stats_top_frame = ctk.CTkFrame(stats_tab)
    stats_top_frame.pack(fill="x", padx=16, pady=(16, 12))

    today_label = ctk.CTkLabel(
        stats_top_frame,
        text="今日专注：0 分钟",
        font=ctk.CTkFont(size=24, weight="bold"),
        anchor="center",
    )
    today_label.pack(pady=(12, 4))

    total_label = ctk.CTkLabel(
        stats_top_frame,
        text="累计总专注：0 分钟",
        font=ctk.CTkFont(size=24, weight="bold"),
        anchor="center",
    )
    total_label.pack(pady=(0, 8))

    stats_scroll = ctk.CTkScrollableFrame(stats_tab)
    stats_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def refresh_stats_panel() -> None:
        data = _load_statistics()
        today_label.configure(text=f"今日专注：{data['today_str']}")
        total_label.configure(text=f"累计总专注：{data['total_str']}")
        for w in stats_scroll.winfo_children():
            w.destroy()
        records = data["records"]
        if not records:
            ctk.CTkLabel(
                stats_scroll,
                text="暂无专注数据，快去开启你的第一个番茄钟吧！",
                font=ctk.CTkFont(size=14),
                text_color=("gray50", "gray45"),
            ).pack(expand=True, pady=40)
            return
        for ts_str, target, minutes in reversed(records):
            row = ctk.CTkFrame(stats_scroll)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row, text=ts_str, font=ctk.CTkFont(size=11), anchor="w", width=180
            ).pack(side="left", padx=8, pady=6)
            ctk.CTkLabel(
                row, text=target or "—", font=ctk.CTkFont(size=11), anchor="w"
            ).pack(side="left", fill="x", expand=True, padx=8, pady=6)
            ctk.CTkLabel(
                row,
                text=_format_duration(minutes),
                font=ctk.CTkFont(size=11),
                anchor="e",
                width=80,
            ).pack(side="right", padx=8, pady=6)

    refresh_stats_panel()

    btn_refresh_stats = ctk.CTkButton(
        stats_top_frame,
        text="刷新数据",
        width=70,
        height=24,
        font=ctk.CTkFont(size=11),
        fg_color=("gray65", "gray35"),
        hover_color=("gray55", "gray45"),
        command=refresh_stats_panel,
    )
    btn_refresh_stats.place(relx=1.0, rely=0.0, x=-12, y=8, anchor="ne")

    root.mainloop()


if __name__ == "__main__":
    run_dashboard()
