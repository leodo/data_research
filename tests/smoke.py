# -*- coding: utf-8 -*-
"""无网络冒烟测试：包导入 + 旧 URL 302 + 静态资源 + 报告资源路径。

运行：python -m tests.smoke
"""
from __future__ import annotations

import http.client
import socketserver
import sys
import threading
from urllib.parse import quote

from workbench.paths import DATA, REPORTS, ROOT
from workbench.web.server import SiteHandler


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Location") or "", resp.read()
    finally:
        conn.close()


def main() -> int:
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print(f"  PASS  {name}")
        else:
            failures.append(name)
            print(f"  FAIL  {name}  {detail}")

    print("[1/4] 包导入与路径")
    import workbench.config  # noqa: F401
    import workbench.core  # noqa: F401
    import workbench.factor_utils  # noqa: F401
    import workbench.services.us_dark  # noqa: F401
    check("ROOT 指向项目根", (ROOT / "README.md").exists())
    check("DATA 存在", DATA.is_dir())
    check("REPORTS 存在", REPORTS.is_dir())

    print("[2/4] 启动静态服务")
    srv = Server(("127.0.0.1", 0), SiteHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        print("[3/4] 旧 URL 302 与资源")
        status, loc, _ = get(port, "/index.html")
        check("/index.html 302", status == 302 and loc.endswith("/web/index.html"), f"{status} {loc}")
        status, loc, _ = get(port, "/mood.html")
        check("/mood.html 302", status == 302 and loc.endswith("/web/mood.html"), f"{status} {loc}")

        status, _, body = get(port, "/web/index.html")
        text = body.decode("utf-8", "replace")
        check("/web/index.html 200", status == 200)
        check("index 引用 ../data", "../data/index_data.js" in text)
        check("index 引用 reports", "../reports/因子周期画像" in text)

        status, _, body = get(port, "/data/index_data.js")
        check("/data/index_data.js 200", status == 200 and body.startswith(b"window.FG_DATA"))
        status, _, body = get(port, "/vendor/echarts.min.js")
        check("/vendor/echarts.min.js 200", status == 200 and len(body) > 500000)
        status, _, body = get(port, "/web/app.css")
        check("/web/app.css 200", status == 200)

        print("[4/4] 报告与资源路径")
        report = REPORTS / "整体总结-20261120.html"
        if report.exists():
            status, _, body = get(port, "/reports/" + quote(report.name))
            check("报告 200", status == 200)
            check("报告用 ../vendor", 'src="../vendor/echarts.min.js"' in body.decode("utf-8", "replace"))
        else:
            check("报告存在", False, str(report))
        bad = []
        for p in REPORTS.glob("*.html"):
            t = p.read_text(encoding="utf-8")
            if 'src="vendor/' in t:
                bad.append(p.name)
        check("报告无裸 vendor 引用", not bad, ",".join(bad))
    finally:
        srv.shutdown()
        srv.server_close()

    print()
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
