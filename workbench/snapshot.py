# -*- coding: utf-8 -*-
"""全市场当日快照：一次请求拿到 5500+ 只股票的当日收盘价 / 成交额。

背景
----
交易拥挤度与市场宽度都按「逐只股票取历史」实现（ak.stock_zh_a_hist_sina），
每只一次请求，5580 只 × 8 进程实测约 28 分钟一步，两步合计近 1 小时。
而 ak.stock_zh_a_spot_sina 一次就返回全部个股的当日快照，字段与历史接口逐值
一致（2026-11-24 收盘后抽样 400 只，收盘价与成交额 100% 完全相等）。

因此「缓存只差最新一个交易日」这种最常见的情形不必逐只请求：直接往每只的
缓存末尾追加一行即可，耗时从 28 分钟降到约 1 分钟。

安全边界（任一条件不满足就返回空集合，由调用方回落原逐只抓取）
----
  - 今天是交易日且已收盘（盘中价不是最终收盘价，不能落库）
  - 缓存最后日期正好是上一个交易日（差多天要补多天，快照只覆盖当天）
  - 该股当日成交量 > 0（停牌 / 无成交的股票不能落行，否则宽度与拥挤度失真）
  - 快照接口可用且行数 > 1000（防止接口降级返回残缺数据时被当成全市场）
"""
from __future__ import annotations

import datetime
from pathlib import Path

MIN_ROWS = 1000          # 快照行数下限，低于此视为接口异常


def _iso(tag: str) -> str:
    return f"{tag[:4]}-{tag[4:6]}-{tag[6:]}"


def _recent_trade_days() -> list[str]:
    """最近 60 天交易日（YYYYMMDD 升序）；失败返回空列表。"""
    try:
        from workbench.core import TODAY_TAG, trade_dates
        start = (datetime.date.today() - datetime.timedelta(days=60)).strftime("%Y%m%d")
        return sorted(trade_dates(start)[-70:])
    except Exception:
        return []


def latest_day_tags() -> tuple[str, str] | None:
    """返回 (今日 YYYY-MM-DD, 上一交易日 YYYY-MM-DD)；不可用返回 None。

    只在「今天已收盘」时返回，盘中调用一律视为不可用。
    """
    now = datetime.datetime.now()
    if (now.hour, now.minute) < (15, 30):
        return None
    days = _recent_trade_days()
    if not days:
        return None
    today_tag = now.strftime("%Y%m%d")
    if today_tag not in days:          # 非交易日
        return None
    i = days.index(today_tag)
    if i == 0:
        return None
    return _iso(today_tag), _iso(days[i - 1])


def load_snapshot() -> dict:
    """取全市场当日快照：{代码: {'close': float, 'amount': float, 'volume': float}}。"""
    import akshare as ak
    d = ak.stock_zh_a_spot_sina()
    if d is None or len(d) < MIN_ROWS:
        raise RuntimeError(f"快照行数异常：{0 if d is None else len(d)}")
    out = {}
    for _, r in d.iterrows():
        code = str(r.get("代码", "")).strip()
        if not code:
            continue
        try:
            out[code] = {
                "close": float(r.get("最新价")),
                "amount": float(r.get("成交额")),
                "volume": float(r.get("成交量")),
            }
        except Exception:
            continue
    if len(out) < MIN_ROWS:
        raise RuntimeError(f"快照有效行数异常：{len(out)}")
    return out


def last_date_of(fp: Path) -> str | None:
    """从缓存文件尾部读出最后一行的日期（O(1)，不解析整份 CSV）。"""
    try:
        with open(fp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            f.seek(max(0, size - 256))
            tail = f.read().decode("utf-8", "ignore")
        lines = [x for x in tail.strip().splitlines() if x.strip()]
        if not lines:
            return None
        return lines[-1].split(",")[0].strip()[:10]
    except Exception:
        return None


def fill_one_day(adir, field: str) -> set:
    """把当日快照追加到每只股票的缓存末尾，返回已处理（无需再逐只抓取）的代码集合。

    field: 'close'（市场宽度用）或 'amount'（交易拥挤度用）。
    返回空集合表示快路径不可用，调用方应回落逐只抓取。
    """
    days = latest_day_tags()
    if not days:
        return set()
    today, prev = days
    try:
        snap = load_snapshot()
    except Exception:
        return set()

    root = Path(adir)
    if not root.exists():
        return set()

    done = set()
    for fp in root.glob("*.csv"):
        code = fp.stem
        rec = snap.get(code)
        if not rec:
            continue
        vol = rec.get("volume") or 0.0
        val = rec.get(field)
        if vol <= 0 or val is None or not (val > 0):
            continue                      # 停牌 / 无成交：不落行
        if last_date_of(fp) != prev:
            continue                      # 差多于一天：交给逐只增量
        try:
            with open(fp, "rb") as f:
                f.seek(0, 2)
                need_nl = f.tell() > 0
                if need_nl:
                    f.seek(-1, 2)
                    need_nl = f.read(1) != b"\n"
            with open(fp, "a", encoding="utf-8", newline="") as f:
                if need_nl:
                    f.write("\n")
                f.write(f"{today},{float(val)!r}\n")
            done.add(code)
        except Exception:
            continue
    return done
