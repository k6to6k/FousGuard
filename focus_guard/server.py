"""
FocusGuard 浏览器扩展本地微服务 (server.py)

职责：
- 提供一个仅依赖标准库的极轻量 HTTP 服务，用于接收浏览器扩展上报的标签页特征。
- 处理 CORS 预检请求（OPTIONS），并接受 /api/tab_update 的 POST 请求。
"""

from __future__ import annotations

import http.server
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Tuple

# 最近的浏览器标签页特征缓存（按时间倒序），元素形如：
# {"title": str, "url": str, "timestamp": int}
RECENT_TABS: List[Dict[str, Any]] = []
CURRENT_BROWSER_TAB: Dict[str, Any] = {"title": "", "url": "", "timestamp": 0}
tab_lock = threading.Lock()


class ExtensionHandler(BaseHTTPRequestHandler):
    """
    处理来自浏览器扩展的 HTTP 请求：
    - OPTIONS：CORS 预检
    - POST /api/tab_update：接收当前标签页的 title/url/timestamp
    """

    server_version = "FocusGuardExtensionHTTP/1.0"

    def _set_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # type: ignore[override]
        """处理浏览器扩展的 CORS 预检请求。"""
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def do_POST(self) -> None:  # type: ignore[override]
        """仅响应 /api/tab_update，用于接收标签页特征 JSON。"""
        if self.path != "/api/tab_update":
            self.send_response(404)
            self._set_cors_headers()
            self.end_headers()
            return

        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except ValueError:
            length = 0

        raw_body = self.rfile.read(length) if length > 0 else b""

        title = ""
        url = ""
        timestamp: Any = None
        try:
            if raw_body:
                data: Dict[str, Any] = json.loads(raw_body.decode("utf-8"))
                title = str(data.get("title", "") or "")
                url = str(data.get("url", "") or "")
                timestamp = data.get("timestamp")
        except Exception as exc:
            print(f"[FocusGuard 本地服务] JSON 解析失败: {exc}")

        if title or url:
            print(
                f"\n[FocusGuard 本地服务] 收到浏览器特征 -> 标题: {title}\nURL: {url}\n"
            )

            # 更新内存缓存：去重（按 URL）、插入队首、限制长度为 20
            payload: Dict[str, Any] = {
                "title": title,
                "url": url,
                "timestamp": int(timestamp) if isinstance(timestamp, (int, float)) else 0,
            }
            with tab_lock:
                # 移除相同 URL 的旧记录
                RECENT_TABS[:] = [item for item in RECENT_TABS if item.get("url") != url]
                # 新记录插入开头
                RECENT_TABS.insert(0, payload)
                # 容量控制
                if len(RECENT_TABS) > 100:
                    del RECENT_TABS[100:]
                # 同步更新“当前活动标签页”状态
                CURRENT_BROWSER_TAB.update(payload)

        # 响应扩展，表明接收成功
        self.send_response(200)
        self._set_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(b'{"status":"ok"}')
        except Exception:
            # 不因写出错误影响主流程
            pass

    def do_GET(self) -> None:  # type: ignore[override]
        """返回最近的浏览器标签页特征列表。"""
        if self.path != "/api/recent_tabs":
            self.send_response(404)
            self._set_cors_headers()
            self.end_headers()
            return

        with tab_lock:
            data = list(RECENT_TABS)

        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self._set_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def log_message(self, format: str, *args: Tuple[Any, ...]) -> None:  # type: ignore[override]
        """静音默认的访问日志，避免控制台噪音。"""
        return


def start_server(host: str = "127.0.0.1", port: int = 11235) -> None:
    """
    在 127.0.0.1:11235 启动本地 HTTP 服务，供浏览器扩展调用。
    建议在后台守护线程中调用。
    """
    server_address = (host, port)
    httpd = HTTPServer(server_address, ExtensionHandler)
    print(f"[FocusGuard 本地服务] HTTP 服务器已启动：http://{host}:{port}/api/tab_update")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"[FocusGuard 本地服务] 服务器异常退出: {exc}")
    finally:
        httpd.server_close()
        print("[FocusGuard 本地服务] HTTP 服务器已关闭")


if __name__ == "__main__":
    # 允许独立运行以便调试
    start_server()

