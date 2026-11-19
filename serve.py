# -*- coding: utf-8 -*-
"""纯静态预览服务（不更新数据）：python serve.py → http://localhost:8040/index.html

需要自动更新数据请用 python start_site.py 或根目录的 启动.bat。
"""
import http.server
import socketserver
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
PORT = 8040

class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True      # 挂起的客户端连接不阻塞其他请求
    allow_reuse_address = True  # 端口可快速复用

with ThreadedServer(("127.0.0.1", PORT), Handler) as httpd:
    print(f"Serving at http://localhost:{PORT}/index.html")
    httpd.serve_forever()
