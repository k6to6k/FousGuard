"""
紧急安全阀模块 (emergency_ui.py)

说明：
- 作为独立子进程运行，作为专注期间唯一的极高阻力出口。
- 使用 CustomTkinter 构建置顶窗口，强制 60 秒冷静期。
- 通过退出码传递结果：sys.exit(0) 表示确认退出，sys.exit(1) 表示放弃。
"""

from __future__ import annotations

import sys

import customtkinter as ctk


def _center_window(root: ctk.CTk, width: int = 420, height: int = 280) -> None:
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - width) // 2
    y = (sh - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")


def run_emergency_flow() -> None:
    """
    运行紧急退出确认流程。
    用户必须等待 60 秒冷静期，倒计时结束后才能确认退出。
    """

    # 设置 CustomTkinter 主题
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("FocusGuard 紧急退出确认")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    _center_window(root)

    # 统一的放弃处理：关闭窗口并以退出码 1 结束
    def cancel_and_exit() -> None:
        try:
            root.destroy()
        except Exception:
            pass
        sys.exit(1)

    root.protocol("WM_DELETE_WINDOW", cancel_and_exit)

    container = ctk.CTkFrame(root)
    container.pack(fill="both", expand=True, padx=20, pady=20)

    # 顶部提示
    tip_label = ctk.CTkLabel(
        container,
        text="紧急退出需要强制冷静。请等待倒计时结束...",
        font=ctk.CTkFont(size=13, weight="bold"),
        wraplength=360,
        justify="left",
    )
    tip_label.pack(anchor="w", pady=(0, 16))

    # 倒计时显示
    countdown_label = ctk.CTkLabel(
        container,
        text="60 秒",
        font=ctk.CTkFont(size=36, weight="bold"),
        text_color="#ff4444",
    )
    countdown_label.pack(anchor="center", pady=(0, 20))

    # 按钮容器
    btn_frame = ctk.CTkFrame(container)
    btn_frame.pack(fill="x", pady=(8, 0))

    def confirm_exit() -> None:
        """确认退出：关闭窗口并以退出码 0 结束"""
        try:
            root.destroy()
        except Exception:
            pass
        sys.exit(0)

    confirm_btn = ctk.CTkButton(
        btn_frame,
        text="确认退出专注",
        font=ctk.CTkFont(size=12, weight="bold"),
        command=confirm_exit,
        height=40,
        corner_radius=8,
        state="disabled",  # 初始禁用
    )
    confirm_btn.pack(side="left", padx=(0, 10), fill="x", expand=True)

    cancel_btn = ctk.CTkButton(
        btn_frame,
        text="放弃退出",
        font=ctk.CTkFont(size=12),
        command=cancel_and_exit,
        height=40,
        corner_radius=8,
        fg_color=("gray70", "gray30"),
        hover_color=("gray60", "gray40"),
    )
    cancel_btn.pack(side="left", fill="x", expand=True)

    remaining = 60

    def update_countdown() -> None:
        nonlocal remaining
        if remaining > 0:
            countdown_label.configure(text=f"{remaining} 秒")
            remaining -= 1
            root.after(1000, update_countdown)
        else:
            # 倒计时结束：启用确认按钮
            countdown_label.configure(text="可以退出")
            countdown_label.configure(text_color="#4CAF50")
            confirm_btn.configure(state="normal")

    # 启动倒计时
    update_countdown()

    # 避免在某些环境中窗口被其他程序抢前，确保保持在最前方
    root.after(200, lambda: root.attributes("-topmost", True))

    root.mainloop()
    # 理论上不会执行到这里，兜底处理为放弃
    sys.exit(1)


if __name__ == "__main__":
    run_emergency_flow()
