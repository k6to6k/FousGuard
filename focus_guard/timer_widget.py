"""
悬浮倒计时模块 (timer_widget.py)

说明：
- 作为独立子进程运行，在专注期间提供视觉倒计时反馈。
- 使用 CustomTkinter 构建极简无边框悬浮窗口。
- 支持自由拖拽与隐藏功能。
- 通过命令行参数接收 end_time（专注结束时间戳），每秒更新剩余时间。
"""

from __future__ import annotations

import sys
import time

import customtkinter as ctk


def format_time(seconds: float) -> str:
    """
    将秒数格式化为 MM:SS 字符串。
    """
    if seconds < 0:
        return "00:00"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def run_timer_widget() -> None:
    """
    运行悬浮倒计时窗口。
    从 sys.argv[1] 读取 end_time（时间戳字符串），每秒更新显示。
    """

    if len(sys.argv) < 2:
        print("[FocusGuard] timer_widget: missing end_time argument")
        sys.exit(1)

    try:
        end_time = float(sys.argv[1])
    except ValueError:
        print(f"[FocusGuard] timer_widget: invalid end_time: {sys.argv[1]}")
        sys.exit(1)

    # 设置 CustomTkinter 主题
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("")  # 无标题
    root.resizable(False, False)

    # 无标题栏、置顶
    root.overrideredirect(True)
    root.attributes("-topmost", True)

    # 尝试设置半透明（Windows 10+ 支持）
    try:
        root.attributes("-alpha", 0.85)
    except Exception:
        pass  # 不支持则忽略

    # 窗口大小
    width = 180
    height = 70

    # 放置在屏幕右上角
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    margin = 20
    x = sw - width - margin
    y = margin
    root.geometry(f"{width}x{height}+{x}+{y}")

    # 拖拽相关变量
    drag_start_x = 0
    drag_start_y = 0

    def start_drag(event):
        """记录拖拽起始位置"""
        nonlocal drag_start_x, drag_start_y
        drag_start_x = event.x_root - root.winfo_x()
        drag_start_y = event.y_root - root.winfo_y()

    def on_drag(event):
        """处理拖拽移动"""
        x = event.x_root - drag_start_x
        y = event.y_root - drag_start_y
        root.geometry(f"+{x}+{y}")

    # 绑定拖拽事件（在整个窗口上）
    root.bind("<Button-1>", start_drag)
    root.bind("<B1-Motion>", on_drag)

    # 主容器
    container = ctk.CTkFrame(root, corner_radius=12)
    container.pack(fill="both", expand=True, padx=2, pady=2)

    # 隐藏按钮容器（右上角）
    header_frame = ctk.CTkFrame(container, fg_color="transparent")
    header_frame.pack(fill="x", padx=4, pady=4)

    def hide_window() -> None:
        """隐藏窗口：仅销毁当前 UI 进程，不影响主进程专注状态"""
        try:
            root.destroy()
        except Exception:
            pass
        sys.exit(0)

    hide_btn = ctk.CTkButton(
        header_frame,
        text="×",
        width=20,
        height=20,
        font=ctk.CTkFont(size=14, weight="bold"),
        command=hide_window,
        fg_color="transparent",
        hover_color=("gray70", "gray30"),
        corner_radius=10,
    )
    hide_btn.pack(side="right")

    # 倒计时标签
    timer_label = ctk.CTkLabel(
        container,
        text="00:00",
        font=ctk.CTkFont(size=32, weight="bold"),
        anchor="center",
    )
    timer_label.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def update_timer() -> None:
        current_time = time.time()
        remaining = end_time - current_time

        if remaining <= 0:
            # 倒计时归零，自动退出
            try:
                root.destroy()
            except Exception:
                pass
            sys.exit(0)

        # 格式化并更新显示
        time_str = format_time(remaining)
        timer_label.configure(text=time_str)

        # 剩余时间 < 60 秒时，切换为红色高亮
        if remaining < 60:
            timer_label.configure(text_color="#ff4444")
        else:
            timer_label.configure(text_color=("gray10", "gray90"))

        # 每秒更新一次
        root.after(1000, update_timer)

    # 启动倒计时更新
    update_timer()

    # 主循环
    try:
        root.mainloop()
    except Exception:
        pass
    # 兜底退出
    sys.exit(0)


if __name__ == "__main__":
    run_timer_widget()
