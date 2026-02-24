"""
托盘 UI 模块 (tray_app.py)

职责：
- 使用 pystray 创建系统托盘图标
- 提供切换专注模式、紧急退出占位、彻底退出等菜单项
- 与 main.FocusState 协同工作，不直接处理监控与阻断逻辑
"""

from __future__ import annotations

from typing import Any

import pystray
from PIL import Image, ImageDraw
import subprocess
import sys
from pathlib import Path


def _create_icon(color: tuple[int, int, int]) -> Image.Image:
    """
    创建一个简单的 64x64 纯色方块图标。
    例如：红色代表专注模式，灰色代表休息/未开启。
    """
    size = (64, 64)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 4
    draw.rectangle(
        [margin, margin, size[0] - margin, size[1] - margin],
        fill=color,
        outline=(255, 255, 255, 255),
    )
    return image


def run_tray_app(state_controller: Any) -> None:
    """
    启动托盘应用。

    参数：
    - state_controller: 来自 main.py 的 FocusState 实例，用于读写专注模式状态和请求退出。
    """

    active_icon = _create_icon((220, 20, 60))  # 红色：专注模式
    inactive_icon = _create_icon((128, 128, 128))  # 灰色：未开启/休息

    def get_current_icon():
        return active_icon if state_controller.is_active() else inactive_icon

    def update_icon(icon: pystray.Icon) -> None:
        icon.icon = get_current_icon()
        icon.visible = True

    def on_start_focus(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        # 通过子进程调用 setup_ui.py 设置专注时长
        setup_path = Path(__file__).with_name("setup_ui.py")
        try:
            result = subprocess.run([sys.executable, str(setup_path)])
        except Exception as e:
            print(f"[FocusGuard] 启动专注时长设置界面失败：{e}")
            return

        # 安全校验：为了兼顾测试按钮，只要 returncode >= 1 就开启专注
        if result.returncode >= 1:
            state_controller.start_focus(result.returncode)
            update_icon(icon)
            print(f"[FocusGuard] 专注模式已启动：{result.returncode} 分钟")
        else:
            # 返回 0 表示用户取消，无事发生
            print("[FocusGuard] 用户取消了专注时长设置")

    def on_show_timer(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        # 重新显示悬浮倒计时窗口
        state_controller.show_timer_widget()

    def on_emergency_exit(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        # 通过子进程调用 emergency_ui.py 进行高阻力退出确认
        emergency_path = Path(__file__).with_name("emergency_ui.py")
        try:
            result = subprocess.run([sys.executable, str(emergency_path)])
        except Exception as e:
            print(f"[FocusGuard] 启动紧急退出界面失败：{e}")
            return

        if result.returncode == 0:
            # 用户完成冷静期并确认退出
            state_controller.emergency_stop()
            update_icon(icon)
            print("[FocusGuard] 紧急退出成功：专注模式已解除")
        else:
            # 用户中途放弃或关闭窗口
            print("[FocusGuard] 紧急退出已取消，保持专注模式")

    def on_quit(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        # 请求停止后台线程，并退出托盘
        state_controller.request_stop()
        icon.stop()

    # 动态菜单：根据专注状态显示不同项
    menu = pystray.Menu(
        pystray.MenuItem(
            "开始专注",
            on_start_focus,
            visible=lambda item: not state_controller.is_active(),
        ),
        pystray.MenuItem(
            "显示倒计时",
            on_show_timer,
            visible=lambda item: state_controller.is_active(),
        ),
        pystray.MenuItem(
            "紧急退出 (需冷静)",
            on_emergency_exit,
            visible=lambda item: state_controller.is_active(),
        ),
        pystray.MenuItem(
            "专注中 (不可退出)",
            lambda icon, item: None,  # 空回调，不可点击
            enabled=False,
            visible=lambda item: state_controller.is_active(),
        ),
        pystray.MenuItem(
            "彻底退出",
            on_quit,
            enabled=lambda item: not state_controller.is_active(),
        ),
    )

    icon = pystray.Icon(
        "FocusGuard",
        icon=get_current_icon(),
        title="FocusGuard",
        menu=menu,
    )

    # 重要：icon.run() 是阻塞的，必须在主线程中调用
    icon.run()
