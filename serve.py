# -*- coding: utf-8 -*-
"""纯静态预览服务（不更新数据）：python serve.py → http://localhost:8040/index.html

同时托管「暗盘行情」实时行情接口 /api/usdark/*（Hyperliquid HIP-3，按需采集）：
页面可见时才保持 WebSocket，切走后自动断开，不留下后台流量。

需要自动更新数据请用 python start_site.py 或根目录的 启动.bat。
"""
import http.server
import os
import socketserver
import sys

from config import PORT
import us_dark

os.chdir(os.path.dirname(os.path.abspath(__file__)))

FEED = us_dark.get_feed()


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if FEED.handle_api(self):
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 静默访问日志，保持控制台干净
        pass


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True      # 挂起的客户端连接不阻塞其他请求
    allow_reuse_address = True  # 端口可快速复用


def main():
    # 可选端口参数，便于在已被占用的 8040 之外做本地验证：python serve.py 8041
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    with ThreadedServer(("127.0.0.1", port), Handler) as httpd:
        print(f"Serving at http://localhost:{port}/index.html", flush=True)
        print("[暗盘行情] 行情接口 /api/usdark/* 已挂载（按需连接 Hyperliquid）", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
