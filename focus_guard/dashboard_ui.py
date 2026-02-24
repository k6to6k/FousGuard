"""
FocusGuard 控制中心 (dashboard_ui.py)

定位：程序的主控窗口，作为全局图形化入口，集成专注启动、规则管理和数据统计于一体。

职责：
- 提供三大核心功能区：专注台、规则库、统计局
- 专注台：接管原 setup_ui.py 的功能，作为专注启动的图形化入口
- 规则库：可视化黑名单管理与 Smart Picker（建设中）
- 统计局：读取并解析 focus_log.txt（建设中）
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import customtkinter as ctk


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


def _write_focus_command(minutes: int) -> None:
    """
    将专注命令写入 focus_command.json，供主进程读取。
    格式：{"minutes": X}
    """
    command_path = Path(__file__).with_name("focus_command.json")
    command_data = {"minutes": minutes}

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
        
        # ② 将 {"minutes": X} 以 JSON 格式写入 focus_command.json
        _write_focus_command(minutes)
        
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
    
    building_label = ctk.CTkLabel(
        blacklist_tab,
        text="建设中...",
        font=ctk.CTkFont(size=16),
    )
    building_label.pack(expand=True)

    # Tab 3: 统计局 (Statistics)
    stats_tab = tabview.add("统计局")
    
    building_label2 = ctk.CTkLabel(
        stats_tab,
        text="建设中...",
        font=ctk.CTkFont(size=16),
    )
    building_label2.pack(expand=True)

    root.mainloop()


if __name__ == "__main__":
    run_dashboard()
