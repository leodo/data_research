# -*- coding: utf-8 -*-
"""一键启动器：按需更新数据 + 启动本地站点。

数据链按显式 DAG 编排（见 STEPS），情绪链的上游必须先于汇总脚本。

新鲜度判据（2026-11-24 改为逐步骤探针）：
    每一步都登记了「产物文件 → 读取其最后数据日期」的探针，以及允许落后的
    交易日数 lag（T+1 发布的两融/ETF 占比 lag=1，周频 lag=5，月频 lag=45）。
    启动时把每个探针的日期与该步「应到日期」（最近已收盘交易日回退 lag 个交易日）
    比较，只有落后的步骤才跑；某步要跑时，登记了 depends 的下游一并跑（保证
    汇总层不吃旧缓存）。全部最新时只做探针读取，约 1 秒，不联网。

    旧实现只有「全跳过 / 全跑 22 步」两档，且用 data/last_update.json 按天去重：
    同一天里只要跑过一次（哪怕失败），再启动就完全跳过，导致数据停在旧日期；
    而真正要跑时又 22 步全跑，其中交易拥挤度、市场宽度各约 28 分钟，一天一次
    就要 70+ 分钟。

失败节流：
    某步当日失败达 MAX_ATTEMPTS 次后不再自动重试（避免上游源长期故障时每次
    启动都白等超时），记录在 data/step_state.json；手动重试用 --force。

用法：
    python start_site.py             # 逐步骤检查，只增量更新落后的部分
    python start_site.py --dry-run   # 只打印检查结果与执行计划，不动数据
    python start_site.py --force     # 忽略新鲜度，全部步骤按增量口径重跑
    python start_site.py --deep      # 与 --force 同时用：向子脚本传 --force 做全历史重算（很慢）
    python start_site.py --only=cb,mood   # 只允许指定分组（或步骤名）参与更新
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import webbrowser

from workbench.config import PORT
from workbench.paths import ROOT
from workbench.services import us_dark
from workbench.web.server import SiteHandler

BASE = str(ROOT)
os.chdir(BASE)
DATA = os.path.join(BASE, "data")
UPDATE_STAMP = os.path.join(DATA, "last_update.json")
STEP_STATE = os.path.join(DATA, "step_state.json")

MAX_ATTEMPTS = 2          # 单日单步最大自动尝试次数
MARKET_CLOSE = (15, 30)   # 收盘后约 30 分钟视为当日数据已可获取


# ---------------------------------------------------------------- 日期工具
def _iso(v) -> str | None:
    """把各种日期写法归一成 YYYY-MM-DD；无法识别返回 None。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    s = s.replace("/", "-").replace(".", "-")
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    digits = "".join(ch for ch in s[:8] if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return None


def _csv_last(path, col=None) -> str | None:
    """读 CSV 最后一行指定列的日期。col=None 表示取首列（索引列常存日期）。"""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            header = next(r, None)
            if not header:
                return None
            i = header.index(col) if (col and col in header) else 0
            last = None
            for row in r:
                if row and len(row) > i and row[i].strip():
                    last = row[i]
            return _iso(last)
    except Exception:
        return None


def _json_date(path, *keys) -> str | None:
    """依次尝试 keys 里的路径取值（支持 'meta.date' 这种点号路径）。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    for k in keys:
        cur = d
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        got = _iso(cur)
        if got:
            return got
    return None


def _file_day(path) -> str | None:
    """文件修改日期的自然日（用于没有数据日期字段的派生层）。"""
    try:
        return datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    except Exception:
        return None


_CAL: list | None = None


def _calendar() -> list[str] | None:
    """最近 60 天交易日（YYYYMMDD）。带 8 秒超时，失败返回 None 走工作日近似。"""
    global _CAL
    if _CAL is not None:
        return _CAL or None
    box: dict = {}

    def work():
        try:
            from workbench.core import trade_dates
            start = (datetime.date.today() - datetime.timedelta(days=60)).strftime("%Y%m%d")
            box["v"] = trade_dates(start)
        except Exception:
            box["v"] = []

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(8)
    _CAL = sorted(box.get("v") or [])
    return _CAL or None


def _weekday_days(n=60) -> list[str]:
    """工作日近似交易日（无网络时的兜底，忽略法定节假日）。"""
    today = datetime.date.today()
    out = []
    for i in range(n):
        d = today - datetime.timedelta(days=i)
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
    return sorted(out)


def last_closed_trade_day() -> str:
    """最近一个「已经收盘且数据可获取」的交易日，YYYY-MM-DD。"""
    today = datetime.date.today()
    tag = today.strftime("%Y%m%d")
    now = datetime.datetime.now()
    closed = (now.hour, now.minute) >= MARKET_CLOSE
    days = _calendar() or _weekday_days()
    picked = [d for d in days if d < tag or (d == tag and closed)]
    if picked:
        d = max(picked)
    else:  # 极端兜底：往前找最近工作日
        d = tag
        while True:
            d = (datetime.datetime.strptime(d, "%Y%m%d").date()
                 - datetime.timedelta(days=1)).strftime("%Y%m%d")
            if datetime.datetime.strptime(d, "%Y%m%d").weekday() < 5:
                break
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def expected_date(lag: int = 0) -> str:
    """某步「应到」的数据日期 = 最近收盘交易日回退 lag 个交易日。"""
    days = _calendar() or _weekday_days()
    target = last_closed_trade_day().replace("-", "")
    idx = [i for i, d in enumerate(days) if d <= target]
    if not idx:
        return last_closed_trade_day()
    i = max(0, idx[-1] - max(0, int(lag)))
    d = days[i]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


# ---------------------------------------------------------------- 步骤登记
def _p_csv(name, col=None):
    return lambda: _csv_last(os.path.join(DATA, name), col)


def _p_json(name, *keys):
    return lambda: _json_date(os.path.join(DATA, name), *keys)


def _p_file(name):
    return lambda: _file_day(os.path.join(DATA, name))


def _p_index_min():
    """恐贪指数的产物是多指数字典，取最早的日期作为「整包是否最新」的判据。"""
    try:
        with open(os.path.join(DATA, "index_data.json"), encoding="utf-8") as f:
            d = json.load(f)
        ds = [_iso(v.get("date")) for v in (d.get("indices") or {}).values() if v.get("date")]
        ds = [x for x in ds if x]
        return min(ds) if ds else None
    except Exception:
        return None


def _guard_qvix() -> tuple[bool, str]:
    """隐波源 k.csv 是 4 个 QVIX 因子的唯一来源，也是整条恐贪链的单点故障。

    源不可达时 build_fear_greed 会对每个 kind 各重试 3 次、累计数分钟才降级，
    而下游日期判据取 7 个指数的 min，源没恢复时这一天怎么跑都不会前进。
    因此先花最多 6 秒探一次，不可达就直接跳过，源恢复后重启即自动补上。
    """
    import urllib.request
    url = "http://1.optbbs.com/d/csv/d/k.csv"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            code = getattr(r, "status", 200)
            return (True, "") if code == 200 else (False, f"隐波源 HTTP {code}")
    except Exception as e:  # noqa: BLE001
        return False, f"隐波源不可达（{type(e).__name__}）"


# lag = 允许落后的交易日数：0 日频当日可获取，1 为 T+1 发布，5 周频，45 月频。
# depends = 该步读了哪些步骤的产物；上游本次跑了，本步也必须重算，避免吃旧缓存。
STEPS = [
    # ---- 恐贪链 ----
    dict(key="fg", group="fg", label="恐贪指数(7指数)",
         module="workbench.pipeline.build_fear_greed", probe=_p_index_min, lag=0,
         guard=_guard_qvix),

    # ---- 情绪链上游（互不依赖，可各自独立增量）----
    dict(key="turnover", group="mood", label="全市场换手率",
         module="workbench.pipeline.rebuild_turnover", probe=_p_csv("mood_turnover_rate.csv"), lag=0),
    dict(key="crowding", group="mood", label="交易拥挤度",
         module="workbench.pipeline.build_crowding", probe=_p_csv("mood_crowding.csv", "date"), lag=0),
    dict(key="etf_ratio", group="mood", label="300ETF占比",
         module="workbench.pipeline.build_etf_ratio", probe=_p_csv("mood_etf_ratio.csv"), lag=1),
    dict(key="margin", group="mood", label="两融余额增速",
         module="workbench.pipeline.build_margin", probe=_p_csv("mood_margin.csv", "date"), lag=1),
    dict(key="pb", group="mood", label="全A PB(月频)",
         module="workbench.pipeline.build_pb", probe=_p_csv("mood_a_pb.csv", "date"), lag=0),
    dict(key="breadth", group="mood", label="全A市场宽度",
         module="workbench.pipeline.build_breadth", probe=_p_csv("mood_breadth.csv", "date"), lag=0),
    dict(key="below_net", group="mood", label="破净率",
         module="workbench.pipeline.build_below_net", probe=_p_csv("mood_below_net.csv", "date"), lag=1),
    dict(key="breadth_extra", group="mood", label="宽度扩展包",
         module="workbench.pipeline.build_breadth_extra", probe=_p_csv("mood_breadth_extra.csv", "date"), lag=0),
    dict(key="arms", group="mood", label="阿姆氏指标与宽广度体系",
         module="workbench.pipeline.build_arms", probe=_p_csv("mood_arms.csv", "date"), lag=0,
         depends=("breadth", "turnover")),
    dict(key="fund_position", group="mood", label="基金股票仓位",
         module="workbench.pipeline.build_fund_position", probe=_p_csv("mood_fund_position.csv", "date"), lag=5),
    dict(key="money_supply", group="mood", label="M1-M2 剪刀差",
         module="workbench.pipeline.build_money_supply", probe=_p_csv("mood_m1m2.csv", "date"), lag=45),
    dict(key="repurchase", group="mood", label="A股回购规模",
         module="workbench.pipeline.build_repurchase", probe=_p_csv("mood_repurchase.csv", "date"), lag=45),
    dict(key="holder_change", group="mood", label="重要股东增减持",
         module="workbench.pipeline.build_holder_change", probe=_p_csv("mood_holder_change.csv", "date"), lag=45),

    # ---- 汇总层：只读上游产物，上游一动就必须重算 ----
    dict(key="mood", group="mood", label="情绪面板(综合)",
         module="workbench.pipeline.build_mood", probe=_p_json("mood_data.json", "data_date"), lag=0,
         depends=("turnover", "crowding", "etf_ratio", "margin", "pb", "breadth", "below_net",
                  "breadth_extra", "arms", "fund_position", "money_supply", "repurchase",
                  "holder_change")),
    dict(key="confirm", group="mood", label="拐点确认层(背离/涨跌停/VRP)",
         module="workbench.pipeline.build_confirm", probe=_p_json("confirm_data.json", "data_date"), lag=0,
         depends=("mood", "fg")),

    # ---- L0→L4：全部读上游产物，L0 落后或其上游动了才跑 ----
    dict(key="factor_matrix", group="mood", label="L0 因子变换矩阵(双轨PIT)",
         module="workbench.pipeline.build_factor_matrix", probe=_p_csv("factor_matrix.csv", "date"), lag=0,
         depends=("mood",)),
    dict(key="audit_efficacy", group="mood", label="L1 常驻效力审计(FDR/条件IC)",
         module="workbench.pipeline.audit_efficacy", probe=_p_json("efficacy_audit.json", "meta.base_date"), lag=0,
         depends=("factor_matrix",)),
    dict(key="horizon_profile", group="mood", label="L2 周期画像(18周期/可信度)",
         module="workbench.pipeline.build_horizon_profile", probe=_p_file("horizon_profile.json"), lag=0,
         depends=("factor_matrix",)),
    dict(key="daily_confidence", group="mood", label="L3 每日可信度(元评分)",
         module="workbench.pipeline.build_daily_confidence", probe=_p_file("confidence_data.json"), lag=0,
         depends=("horizon_profile", "audit_efficacy")),
    dict(key="rsi_breadth", group="mood", label="L4 大小盘RSI分化(观察)",
         module="workbench.pipeline.build_rsi_breadth", probe=_p_json("rsi_breadth.json", "today.date"), lag=0,
         depends=("breadth",)),

    # ---- 可转债：独立链 ----
    dict(key="cb", group="cb", label="可转债数据(指数/双低/强赎/新债)",
         module="workbench.pipeline.build_cb", probe=_p_json("cb_data.json", "meta.date"), lag=0),
]

BY_KEY = {s["key"]: s for s in STEPS}


# ---------------------------------------------------------------- 状态读写
def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[提示] 写入 {os.path.basename(path)} 失败：{e}", flush=True)


def _step_state() -> dict:
    st = _load_json(STEP_STATE, {})
    if not isinstance(st, dict) or not isinstance(st.get("steps"), dict):
        st = {"steps": {}}
    return st


def _record_state(key, ok, note=""):
    st = _step_state()
    today = datetime.date.today().isoformat()
    recs = st["steps"]
    rec = recs.get(key) if isinstance(recs.get(key), dict) else {}
    if rec.get("date") != today:
        rec = {"date": today, "attempts": 0}
    rec["attempts"] = int(rec.get("attempts", 0)) + 1
    rec["ok"] = bool(ok)
    rec["at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if note:
        rec["note"] = note
    recs[key] = rec
    _write_json(STEP_STATE, st)


def _write_stamp(ok: bool, notes: str):
    """人读的汇总标记；判据已不再依赖它，仅保留兼容与排查用。"""
    _write_json(UPDATE_STAMP, {
        "date": datetime.date.today().isoformat(),
        "attempted_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ok": bool(ok),
        "notes": notes,
    })


# ---------------------------------------------------------------- 计划
def check_deps() -> tuple[bool, str]:
    """更新前检查关键 akshare 接口，避免版本漂移时连跑 20 多个脚本、刷满 traceback。"""
    try:
        import akshare as ak
    except Exception as e:  # noqa: BLE001
        return False, f"无法导入 akshare：{type(e).__name__}: {e}"
    need = ("calendar_trade_date_hist_sina", "index_zh_daily_sina", "stock_zh_a_spot_sina")
    missing = [n for n in need if not hasattr(ak, n)]
    if missing:
        return False, (f"当前 akshare {getattr(ak, '__version__', '?')} 缺少接口："
                       f"{', '.join(missing)}；请运行 python -m pip install -r requirements.txt")
    return True, f"akshare {getattr(ak, '__version__', '?')}"


def build_plan(force_all=False, only=()) -> tuple[list, list, str]:
    """逐步骤判断。返回 (要跑的步骤列表, 明细行, 基准交易日)。"""
    base = last_closed_trade_day()
    state = _step_state()["steps"]
    today = datetime.date.today().isoformat()
    rows, plan, ran = [], [], set()

    for s in STEPS:
        if only and s["group"] not in only and s["key"] not in only:
            continue
        exp = expected_date(int(s.get("lag", 0)))
        own = s["probe"]()
        rec = state.get(s["key"]) if isinstance(state.get(s["key"]), dict) else {}
        if rec.get("date") != today:
            rec = {}

        stale = own is None or own < exp
        upstream = [k for k in s.get("depends", ()) if k in ran]
        reasons = []
        if own is None:
            reasons.append("无产物")
        elif stale:
            reasons.append(f"止于 {own}")
        if upstream:
            reasons.append("上游已更新")

        run = force_all or stale or bool(upstream)
        note = ""
        if run and not force_all:
            attempts = int(rec.get("attempts", 0))
            if attempts >= MAX_ATTEMPTS and not rec.get("ok", True):
                run = False
                note = f"今日已试 {attempts} 次未成功，跳过（--force 可重试）"
        if run and not force_all and s.get("guard"):
            src_ok, src_why = s["guard"]()
            if not src_ok:
                run = False
                note = f"{src_why}；本次跳过，源恢复后重启即补"

        if run:
            plan.append(s)
            ran.add(s["key"])
        rows.append(dict(key=s["key"], label=s["label"], own=own, exp=exp,
                         run=run, why="、".join(reasons) or "已最新", note=note))
    return plan, rows, base


def print_plan(rows, base, plan):
    print(f"[检查] 基准交易日 {base}（最近已收盘交易日）；逐步骤核对产物日期", flush=True)
    width = max(len(r["label"]) for r in rows) if rows else 10
    for r in rows:
        flag = "更新" if r["run"] else "最新"
        detail = r["why"] if not r["run"] else (r["why"] or "已最新")
        if r["note"]:
            detail += f"；{r['note']}"
        print(f"   [{flag}] {r['label']:<{width}}  产物 {r['own'] or '—':<10} 应到 {r['exp']}  {detail}", flush=True)
    print(f"[计划] 需更新 {len(plan)} 步，跳过 {len(rows) - len(plan)} 步", flush=True)


# ---------------------------------------------------------------- 执行
def run_steps(plan, deep=False) -> bool:
    total = len(plan)
    all_ok = True
    t_all = time.time()
    for i, s in enumerate(plan, 1):
        print(f"[更新 {i}/{total}] {s['label']} ({s['module']})...", flush=True)
        t0 = time.time()
        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = BASE + os.pathsep + env.get("PYTHONPATH", "")
            cmd = [sys.executable, "-m", s["module"]]
            if deep:
                cmd.append("--force")
            r = subprocess.run(cmd, cwd=BASE, env=env)
            ok = r.returncode == 0
        except Exception as e:
            print(f"[错误] {s['label']} 更新进程启动失败：{e}", flush=True)
            ok = False
        all_ok = all_ok and ok
        got = s["probe"]()
        _record_state(s["key"], ok, f"产物 {got or '—'}")
        print(f"[{'完成' if ok else '失败'}] {s['label']} 耗时 {time.time() - t0:.1f} 秒；数据 {got or '—'}",
              flush=True)
    print(f"[汇总] {total} 步，总耗时 {(time.time() - t_all) / 60:.1f} 分钟", flush=True)
    return all_ok


FEED = None


class Handler(SiteHandler):
    def do_GET(self):
        # 暗盘行情接口：同一进程内的采集线程，按需连接 Hyperliquid WS
        if FEED is not None and FEED.handle_api(self):
            return
        super().do_GET()


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    # Windows 的 SO_REUSEADDR 语义与 POSIX 相反：它允许第二个进程绑定已在监听的端口，
    # 于是两个实例会同时「监听成功」、请求随机落到其中之一（且各自持有不同的数据进程）。
    # 因此仅在非 Windows 上启用端口复用，让重复启动直接报「端口被占用」。
    allow_reuse_address = (os.name != "nt")


def main():
    global FEED
    print("=" * 54)
    print("  投研指标工作台 · 本地站点启动器")
    print("=" * 54)

    argv = sys.argv[1:]
    force = "--force" in argv
    deep = "--deep" in argv
    dry = "--dry-run" in argv
    only = ()
    for a in argv:
        if a.startswith("--only="):
            only = tuple(x.strip() for x in a.split("=", 1)[1].split(",") if x.strip())

    plan, rows, base = build_plan(force_all=force, only=only)
    print_plan(rows, base, plan)

    if dry:
        print("[干跑] 仅检查，不执行更新、不启动服务。", flush=True)
        return

    if plan:
        deps_ok, deps_msg = check_deps()
        print(f"[检查] 运行依赖：{deps_msg}", flush=True)
        if not deps_ok:
            print("[跳过] 依赖不满足，本次不更新数据，直接使用现有数据启动。", flush=True)
            print("[提示] 修复后重新运行即可：python -m pip install -r requirements.txt", flush=True)
            plan = []
        elif deep:
            print("[提示] --deep：将向子脚本传 --force 做全历史重算，耗时很长", flush=True)

    if plan:
        ok = run_steps(plan, deep=deep)
        _write_stamp(ok, f"逐步骤增量：执行 {len(plan)} 步 / 共 {len(rows)} 步")
        if not ok:
            print("[提示] 部分步骤失败，将使用现有数据启动（相关卡片可能显示旧数据）。", flush=True)
    else:
        print("[跳过] 全部数据均为最新，直接启动。", flush=True)

    url = f"http://localhost:{PORT}/index.html"
    print(f"[服务] {url}", flush=True)
    print("[提示] 按 Ctrl+C 停止服务；关闭此窗口也会停止。", flush=True)

    # 暗盘行情：只启动线程，真正连接 Hyperliquid WS 要等页面心跳（按需采集）
    FEED = us_dark.get_feed()
    print("[暗盘行情] 行情接口 /api/usdark/* 已挂载（页面可见时才连接行情源）", flush=True)

    # 服务起来后自动打开浏览器（--no-browser 可关闭，供后台/测试启动）
    def open_browser():
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    if "--no-browser" not in argv:
        threading.Thread(target=open_browser, daemon=True).start()

    try:
        with ThreadedServer(("127.0.0.1", PORT), Handler) as httpd:
            httpd.serve_forever()
    except OSError as e:
        print(f"[错误] 端口 {PORT} 启动失败：{e}", flush=True)
        print("[提示] 可能已有服务在运行；关闭其他实例后重试，或修改 workbench/config.py 的 PORT。", flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[退出] 服务已停止。", flush=True)


if __name__ == "__main__":
    main()
