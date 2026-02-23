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

    def on_toggle_focus(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        state_controller.toggle()
        # 根据当前状态更新托盘图标
        icon.icon = get_current_icon()
        icon.visible = True

    def on_emergency_exit(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        # 占位：下一阶段接入 unlock_ui 子进程调用
        print("[FocusGuard] 触发紧急退出，待接入 unlock_ui")

    def on_quit(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        # 请求停止后台线程，并退出托盘
        state_controller.request_stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("切换专注模式", on_toggle_focus),
        pystray.MenuItem("紧急退出", on_emergency_exit),
        pystray.MenuItem("彻底退出", on_quit),
    )

    icon = pystray.Icon(
        "FocusGuard",
        icon=get_current_icon(),
        title="FocusGuard",
        menu=menu,
    )

    # 重要：icon.run() 是阻塞的，必须在主线程中调用
    icon.run()
