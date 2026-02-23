# 监控模块：调用 Win32 API 获取当前活动窗口信息
# 遵守文档 3.2 及第 4 节防御性异常要求

import win32gui
import win32process
import psutil


def get_active_window_info():
    """
    获取当前前台窗口的进程名、窗口标题与 PID。
    返回值: (process_name, window_title, pid)，均为小写/整数；
    异常或空句柄时返回 (None, None, None)。
    """
    process_name = None
    window_title = None
    pid = None

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return (None, None, None)

        window_title = win32gui.GetWindowText(hwnd)
        if window_title is not None:
            window_title = window_title.lower()
        else:
            window_title = ""

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == 0:
            return (None, window_title or None, None)

        try:
            proc = psutil.Process(pid)
            process_name = proc.name()
            if process_name is not None:
                process_name = process_name.lower()
        except psutil.NoSuchProcess:
            # 进程已退出，返回安全默认值
            pass
        except psutil.AccessDenied:
            # 权限不足，不崩溃
            pass

        return (process_name, window_title, pid)
    except Exception:
        # 其他 Win32/意外异常也不应导致监控线程崩溃
        return (None, None, None)


if __name__ == "__main__":
    import time
    print("监控模块测试：每秒打印当前前台窗口 (process_name, window_title, pid)，Ctrl+C 退出")
    print("-" * 60)
    while True:
        process_name, window_title, pid = get_active_window_info()
        print(f"process={process_name}, pid={pid}, title={window_title!r}")
        time.sleep(1)
