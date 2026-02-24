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

    def on_show_dashboard(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        # 防作弊：专注期间禁止呼出控制中心（左键/双击触发时也在此拦截）
        if state_controller.is_active():
            return
        # 显示控制中心：检查 dashboard_process 是否还在运行，如果已退出则重新拉起
        dashboard_process = getattr(state_controller, "dashboard_process", None)
        
        if dashboard_process is not None:
            # 检查进程是否仍在运行
            if dashboard_process.poll() is None:
                # 进程仍在运行，无需重新启动
                print("[FocusGuard] Dashboard UI is already running")
                return
            # 进程已退出，清理引用
            state_controller.dashboard_process = None
        
        # 重新启动控制中心
        try:
            dashboard_script = Path(__file__).with_name("dashboard_ui.py")
            state_controller.dashboard_process = subprocess.Popen(
                [sys.executable, str(dashboard_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[FocusGuard] Dashboard UI restarted (pid={state_controller.dashboard_process.pid})")
        except Exception as e:
            print(f"[FocusGuard] Failed to restart dashboard UI: {e}")
            state_controller.dashboard_process = None

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
            "显示控制中心",
            on_show_dashboard,
            enabled=lambda item: not state_controller.is_active(),
            default=True,  # 左键单击托盘图标时触发此项；专注时 enabled=False，回调内也有 guard
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

    # 注册图标更新回调，使 monitor_loop 在异步触发 start_focus/set_active 后能同步更新托盘图标
    state_controller.set_icon_update_callback(lambda: update_icon(icon))

    # 重要：icon.run() 是阻塞的，必须在主线程中调用
    icon.run()
