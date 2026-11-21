# -*- coding: utf-8 -*-
"""一键启动器：按需更新数据 + 启动本地站点。

数据链按显式 DAG 编排（见 PIPELINES），情绪链的上游必须先于汇总脚本。
各构建脚本内部自行决定缓存复用与增量策略（PCR 真增量、申万行业缓存、
成分股强度按交易日滞后容忍），启动器只负责编排与"今天是否已经跑过"的去重。

新鲜度判据不是"数据日期 == 今天"：不同数据源发布时间不一致，个别源
（如 50ETF QVIX、申万行业）会长期落后，若以数据日期为判据，每次启动
都会触发全链重建且跑完依然到不了今天。改为用 data/last_update.json
记录今日是否已尝试更新，同一天重复启动直接跳过；需要重跑请加 --force。

用法：
    python start_site.py            # 今日未更新则重建，否则直接启动
    python start_site.py --force    # 强制重建全部数据后再启动
"""
from __future__ import annotations

import datetime
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import webbrowser

from config import PORT

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
DATA_JSON = os.path.join(BASE, "data", "index_data.json")
MOOD_JSON = os.path.join(BASE, "data", "mood_data.json")
UPDATE_STAMP = os.path.join(BASE, "data", "last_update.json")


def _file_date(path, kind):
    """从数据文件读出数据日期。kind='fg' 或 'mood'。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if kind == "fg":
            dates = [v.get("date") for v in d.get("indices", {}).values() if v.get("date")]
            return min(dates) if dates else None
        else:
            return d.get("data_date")
    except Exception:
        return None


def _read_stamp() -> dict:
    """读取今日更新标记；文件不存在或损坏时视为未更新。"""
    try:
        with open(UPDATE_STAMP, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_stamp(ok: bool, notes: str):
    """记录本次更新尝试。失败也记录，避免同一天反复重跑；重跑用 --force。"""
    try:
        os.makedirs(os.path.dirname(UPDATE_STAMP), exist_ok=True)
        with open(UPDATE_STAMP, "w", encoding="utf-8") as f:
            json.dump({
                "date": datetime.date.today().isoformat(),
                "attempted_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ok": bool(ok),
                "notes": notes,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[提示] 更新标记写入失败：{e}", flush=True)


def check_freshness() -> tuple[bool, str]:
    """返回 (是否需要更新, 说明)。

    两级判据：①两份数据都已是今天 → 直接跳过；
    ②否则看今日是否已尝试过更新 → 已尝试则跳过（数据源没发数时不做无效重建），
      未尝试才重建。数据日期仅用于展示说明，不再作为触发条件。
    """
    today = datetime.date.today().isoformat()
    notes = []
    all_current = True
    for path, kind, label in [(DATA_JSON, "fg", "恐贪指数"), (MOOD_JSON, "mood", "情绪面板")]:
        dt = _file_date(path, kind)
        if dt is None:
            all_current = False
            notes.append(f"{label}数据缺失")
        elif dt < today:
            all_current = False
            notes.append(f"{label}止于{dt}")
        else:
            notes.append(f"{label}已最新")
    summary = "；".join(notes)
    if all_current:
        return False, summary + "；两份数据均为今日，跳过更新"

    st = _read_stamp()
    if st.get("date") == today:
        state = "成功" if st.get("ok") else "部分失败"
        return False, (summary + f"；今日已尝试更新（{st.get('attempted_at', '时间未知')}，{state}），"
                                "跳过重复重建；需重跑请加 --force")
    return True, summary + "；今日尚未更新，开始重建"


# 显式依赖 DAG：情绪链的上游必须先于 build_mood.py，
# 否则 mood 读到的换手率/拥挤度/ETF占比仍是旧数据（这正是此前启动器更新不全的根因）。
PIPELINES = {
    "fg": [("恐贪指数(7指数)", "build_fear_greed.py")],
    "mood": [
        ("全市场换手率", "rebuild_turnover.py"),
        ("交易拥挤度", "build_crowding.py"),
        ("300ETF占比", "build_etf_ratio.py"),
        ("两融余额增速", "build_margin.py"),
        ("全A PB(月频)", "build_pb.py"),
        ("全A市场宽度", "build_breadth.py"),
        ("破净率", "build_below_net.py"),
        ("宽度扩展包", "build_breadth_extra.py"),
        # build_arms 依赖上面的宽度/收盘价/成交额缓存，必须排在其后；
        # 三个 B 类接口一次返回全历史，整抓即最新。
        ("阿姆氏指标与宽广度体系", "build_arms.py"),
        ("基金股票仓位", "build_fund_position.py"),
        ("M1-M2 剪刀差", "build_money_supply.py"),
        ("A股回购规模", "build_repurchase.py"),
        ("重要股东增减持", "build_holder_change.py"),
        # build_turning.py 由 build_mood.py 在 load_sources() 之后同进程调用，
        # 不单独登记：它依赖那些刚落盘的源缓存，并生成信号卡所需的 mood_tech.csv。
        # ERP 股债归因同样由 build_mood.py 同进程计算（复用已加载的 PE / 国债序列）。
        ("情绪面板(综合)", "build_mood.py"),
        # 拐点确认层必须排在 build_mood.py 之后：它要读当天的 mood_data.json
        # （composite.history 周频综合分）与 index_data.json（QVIX 因子降级源）。
        # 该层只写 confirm_data.* 与 mood_confirm.csv，不改 mood_data.json / 综合分。
        ("拐点确认层(背离/涨跌停/VRP)", "build_confirm.py"),
        # L0 → L1 顺序不可颠倒：L1 的因子值唯一来源是 L0 的 data/factor_matrix.csv。
        # 两步都只读现有产物、只写自己的文件，不回写 mood_data.json / 综合分 / 共振板。
        ("L0 因子变换矩阵(双轨PIT)", "build_factor_matrix.py"),
        ("L1 常驻效力审计(FDR/条件IC)", "audit_efficacy.py"),
        # L2 只读 L0 产物与 mood_data.json，产出周期画像 JSON/JS（供页面标注"该指标在哪个尺度上说话"），
        # 不回写 mood_data.json / 综合分 / 共振板。
        ("L2 周期画像(18周期/可信度)", "build_horizon_profile.py"),
        # L3 依赖 L2 的检验结论，产出每日可信度（元评分）JSON/JS，供 mood.html 的独立卡片渲染。
        ("L3 每日可信度(元评分)", "build_daily_confidence.py"),
    ],
}


def rebuild(which="all") -> bool:
    """按依赖顺序重建；which: 'fg'/'mood'/'all'。返回是否全部成功。"""
    groups = []
    if which in ("fg", "all"):
        groups.append(PIPELINES["fg"])
    if which in ("mood", "all"):
        groups.append(PIPELINES["mood"])
    all_ok = True
    total = sum(len(g) for g in groups)
    step = 0
    for group in groups:
        for label, script in group:
            step += 1
            extra = " --force" if "--force" in sys.argv else ""
            print(f"[更新 {step}/{total}] {label} ({script}{extra})...", flush=True)
            t0 = time.time()
            try:
                cmd = [sys.executable, script]
                if "--force" in sys.argv:
                    cmd.append("--force")
                r = subprocess.run(cmd, cwd=BASE)
                ok = r.returncode == 0
            except Exception as e:
                print(f"[错误] {label}更新进程启动失败：{e}", flush=True)
                ok = False
            all_ok = all_ok and ok
            print(f"[{'完成' if ok else '失败'}] {label} 耗时 {time.time() - t0:.1f} 秒", flush=True)
    return all_ok


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 静默访问日志，保持控制台干净
        pass


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    print("=" * 54)
    print("  投研指标工作台 · 本地站点启动器")
    print("=" * 54)

    force = "--force" in sys.argv
    if force:
        why = "强制更新模式"
        print(f"[检查] {why}", flush=True)
        need = True
    else:
        need, why = check_freshness()
        print(f"[检查] {why}", flush=True)

    if need:
        ok = rebuild("all")
        _write_stamp(ok, why)
        if not ok:
            print("[提示] 部分更新失败，将使用现有数据启动（页面可能显示旧数据）。", flush=True)
    else:
        print("[跳过] 直接启动。", flush=True)

    url = f"http://localhost:{PORT}/index.html"
    print(f"[服务] {url}", flush=True)
    print("[提示] 按 Ctrl+C 停止服务；关闭此窗口也会停止。", flush=True)

    # 服务起来后自动打开浏览器
    def open_browser():
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=open_browser, daemon=True).start()

    try:
        with ThreadedServer(("127.0.0.1", PORT), Handler) as httpd:
            httpd.serve_forever()
    except OSError as e:
        print(f"[错误] 端口 {PORT} 启动失败：{e}", flush=True)
        print("[提示] 可能已有服务在运行；关闭其他实例后重试，或修改 start_site.py 的 PORT。", flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[退出] 服务已停止。", flush=True)


if __name__ == "__main__":
    main()
