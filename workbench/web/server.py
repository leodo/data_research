# -*- coding: utf-8 -*-
"""静态站点 Handler：旧页面 URL 302 到 /web/，其余按项目根直接提供。

302 而不是内部改写，是为了让浏览器基地址变成 /web/，
页面里的 app.css / cb.js / ../data / ../vendor 等相对路径才会正确解析。
"""
from http.server import SimpleHTTPRequestHandler
from urllib.parse import urlsplit

PAGE_MAP = {
    "/": "/web/index.html",
    "/index.html": "/web/index.html",
    "/indicators.html": "/web/indicators.html",
    "/mood.html": "/web/mood.html",
    "/cb.html": "/web/cb.html",
    "/us.html": "/web/us.html",
}


class SiteHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        u = urlsplit(self.path)
        target = PAGE_MAP.get(u.path)
        if target:
            if u.query:
                target += "?" + u.query
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 静默访问日志，保持控制台干净
        pass
