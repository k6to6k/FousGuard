"""
专注时长设置模块 (setup_ui.py)

说明：
- 作为独立子进程运行，在专注开始前收集用户对专注时长的预承诺。
- 使用 CustomTkinter 构建现代化深色模式界面。
- 通过退出码传递专注分钟数：sys.exit(N) 表示用户承诺专注 N 分钟，sys.exit(0) 表示用户取消。
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import customtkinter as ctk


def _center_window(root: ctk.CTk, width: int = 420, height: int = 380) -> None:
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


def run_setup_flow() -> None:
    """
    运行专注时长设置流程。
    用户选择时长后，以 sys.exit(分钟数) 结束；取消或关闭窗口则以 sys.exit(0) 结束。
    """

    # 设置 CustomTkinter 主题
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("FocusGuard 专注时长设置")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    _center_window(root)

    # 统一的取消处理：关闭窗口并以退出码 0 结束
    def cancel_and_exit() -> None:
        try:
            root.destroy()
        except Exception:
            pass
        sys.exit(0)

    root.protocol("WM_DELETE_WINDOW", cancel_and_exit)

    # 主容器
    container = ctk.CTkFrame(root)
    container.pack(fill="both", expand=True, padx=20, pady=20)

    # 顶部提示
    tip_label = ctk.CTkLabel(
        container,
        text="请选择本次专注时长（一旦开始，不可退出）：",
        font=ctk.CTkFont(size=13, weight="bold"),
        anchor="w",
    )
    tip_label.pack(fill="x", pady=(0, 16))

    # 专注目标输入框
    target_label = ctk.CTkLabel(
        container,
        text="专注目标：",
        font=ctk.CTkFont(size=12),
        anchor="w",
    )
    target_label.pack(fill="x", pady=(0, 8))

    target_entry = ctk.CTkEntry(
        container,
        placeholder_text="请输入专注目标 (例如：搜广推算法复习 / 高级数据库报告)",
        font=ctk.CTkFont(size=11),
        height=36,
    )
    target_entry.pack(fill="x", pady=(0, 20))

    # 按钮容器
    btn_frame = ctk.CTkFrame(container)
    btn_frame.pack(fill="x", pady=(0, 12))

    def start_focus(minutes: int) -> None:
        # 获取专注目标文本
        target_text = target_entry.get().strip() or "未设置目标"
        # 写入日志
        _log_focus_session(target_text, minutes)
        # 关闭窗口并退出
        try:
            root.destroy()
        except Exception:
            pass
        sys.exit(minutes)

    # 时长选项按钮
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

    # 测试按钮（开发用）
    btn_test = ctk.CTkButton(
        btn_frame,
        text="1 分钟 (测试)",
        font=ctk.CTkFont(size=11),
        command=lambda: start_focus(1),
        height=32,
        corner_radius=6,
        fg_color=("gray70", "gray30"),
    )
    btn_test.pack(fill="x", pady=(0, 12))

    # 取消按钮
    cancel_btn = ctk.CTkButton(
        btn_frame,
        text="取消",
        font=ctk.CTkFont(size=12),
        command=cancel_and_exit,
        height=36,
        corner_radius=8,
        fg_color=("gray70", "gray30"),
        hover_color=("gray60", "gray40"),
    )
    cancel_btn.pack(fill="x")

    # 避免在某些环境中窗口被其他程序抢前，确保保持在最前方
    root.after(200, lambda: root.attributes("-topmost", True))

    root.mainloop()
    # 理论上不会执行到这里，兜底处理为取消
    sys.exit(0)


if __name__ == "__main__":
    run_setup_flow()
