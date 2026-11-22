# -*- coding: utf-8 -*-
"""拐点确认层：情绪-价格底背离 / 涨跌停与缩量止跌 / 波动率风险溢价 VRP
======================================================================
2026-11-21 新增。补的是系统最薄的一层「时点确认」，不是再加一个水平分位。

三项指标全部 **不进综合情绪分**、**不进 13 维共振板**，只写独立数据文件，
由 mood.html 的独立渲染器展示。现有 8 指标等权口径、概率评估报告的结论
都不受本脚本影响。

  P0-1 情绪-价格底背离（周频）
      近 8 周综合情绪分曾进悲观区(<=35)，随后 4 周回升 >=3 分，
      同期沪深300 仍未上涨 -> 「情绪已转向、价格滞后」。
      与 build_turning.py 的「价格新低 vs 宽度未新低」是两个不同维度。
  P0-2 涨跌停家数 + 缩量止跌（日频）
      5579 只逐股未复权收盘价按板块阈值(主板10/双创20/北交30)向量化，
      逐股成交额汇总出全市场量能，构造「缩量 + 不创新低 + 跌停缓解」。
  P0-3 VRP（日频）
      QVIX 隐含波动 - 沪深300 20日已实现波动(年化)，配下行波动占比。
      QVIX 是「市场为未来波动付的钱」，与 RSI/BIAS 这类已实现价格函数不同源。

PIT 纪律：所有分位用 expanding(min_periods=CONFIRM_MIN_HIST)；回测触发日
按 20 个观测去重，前瞻窗口 shift(-N) 自动保证 t+N <= 数据末日，无未来函数；
三档阈值稳健性与「方向相反」的反面证据都写进输出，不挑好看的数字报。

数据源（全部本地，默认零网络请求）：
  data/mood_data.json          composite.history（周频综合分）
  data/mood_hs300.csv          沪深300 收盘（2002 起 6035 日）
  data/breadth_prices/*.csv    5579 只逐股收盘价
  data/crowding_amounts/*.csv  5579 只逐股成交额
  data/index_data.json         恐贪链已抓的 300ETF QVIX 因子序列（降级源）
  data/mood_qvix.csv           可选长历史 QVIX 缓存（--qvix-full 一次性生成）

输出：data/confirm_data.json / .js  (前端独立渲染器 confirm_cards.js 消费)
      data/mood_confirm.csv      (日频序列，供复用与回放)
用法：python build_confirm.py                # 默认零网络，纯本地计算
      python build_confirm.py --qvix-full    # 一次性抓 2015 起 QVIX 全历史并落缓存
      python build_confirm.py --force        # 强制重算逐股矩阵（约 30~60 秒）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from workbench.config import (CONFIRM_FWD_WINS, CONFIRM_MIN_HIST, CONFIRM_START,
                    CONFIRM_TRIGGER_GAP, DIV_FWD_W, DIV_LOOKBACK_W,
                    DIV_PESSIMIST, DIV_PRICE_MAX, DIV_REBOUND_MIN,
                    DIV_REBOUND_W, DIV_ROBUST, LIMIT_BJ, LIMIT_DOWN_EASE,
                    LIMIT_EXFAT_MAX, LIMIT_GEM, LIMIT_MAIN, LIMIT_MIN_PRICE,
                    LIMIT_MIN_STOCKS, LIMIT_TOL, VOL_HIGH_WIN, VOL_RATIO_WIN,
                    VOL_SHRINK_MAX, VRP_ANNUAL_DAYS, VRP_DOWNSIDE_WIN,
                    VRP_EXTREME_HIGH, VRP_EXTREME_LOW, VRP_PANIC_EASE_WIN,
                    VRP_RV_WIN)
from workbench.core import DATA, TODAY, log

OUT_JSON = DATA / "confirm_data.json"
OUT_JS = DATA / "confirm_data.js"
OUT_CSV = DATA / "mood_confirm.csv"
LIMIT_CACHE = DATA / "mood_confirm_limits.csv"
QVIX_CACHE = DATA / "mood_qvix.csv"

DISCLAIMER = (
    "本层三项指标均为描述性历史标定，不是收益预测，也不构成投资建议。"
    "分位用 expanding(min_periods=250)；回测触发日按 20 个观测去重但收益窗口仍重叠，"
    "未计交易成本/滑点/分红，阈值由 config.py 设定且未经样本外检验。"
    "周频综合分早期为「≥4 指标即出分」的混合口径，与当前 8 指标等权不完全可比；"
    "未复权收盘价无法识别 ST 股 5% 限制与除权日，涨跌停家数是保守估计；"
    "QVIX 源端常滞后 1~2 日且末行可能为 NaN，已按「抓取成功才更新、失败用缓存兜底」处理。"
    "极端读数可长期钝化，改善的是赔率不是时点。市场有风险，投资需谨慎。"
)


# ---------------------------------------------------------------- 公共工具
def _s(fname: str, col: str) -> pd.Series:
    """读单列日频序列，去重排序并截断到今日（避免幽灵交易日）。"""
    f = DATA / fname
    if not f.exists():
        raise FileNotFoundError(f"缺少 {fname}；请先运行 python start_site.py 生成源缓存")
    d = pd.read_csv(f)
    first = d.columns[0]
    d[first] = pd.to_datetime(d[first], errors="coerce")
    s = pd.to_numeric(d.set_index(first)[col], errors="coerce").dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[s.index <= TODAY]


def _r(x, n=2):
    """四舍五入，NaN/inf 统一转 None，保证 JSON 合法。"""
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    return round(float(x), n)


def _hist(s: pd.Series, start: str | None = None, n: int | None = None):
    """序列转 [{date,value}]，供前端画图；值域自动取整到 2 位。"""
    s = s.dropna()
    if start:
        s = s[s.index >= pd.Timestamp(start)]
    if n:
        s = s.iloc[-n:]
    return [{"date": d.strftime("%Y-%m-%d"), "value": _r(v)} for d, v in s.items()]


def _thin(mask: pd.Series, gap: int = CONFIRM_TRIGGER_GAP) -> pd.DatetimeIndex:
    """按观测序号去重（不是日历日），与 build_turning.thin 同范式。"""
    mask = mask.fillna(False)
    last, res = -10 ** 9, []
    for i, dt in enumerate(mask.index):
        if bool(mask.iloc[i]) and i - last >= gap:
            res.append(dt)
            last = i
    return pd.DatetimeIndex(res)


def _stats(px: pd.Series, dates: pd.DatetimeIndex, wins) -> dict:
    """前瞻收益统计；shift(-w) 天然保证「样本日+w <= 末日」，无未来函数。"""
    out = {"n": len(dates)}
    for w in wins:
        r = (px.shift(-w) / px - 1.0).reindex(dates).dropna() * 100
        out[f"n{w}"] = len(r)
        out[f"win{w}"] = _r((r > 0).mean() * 100, 1) if len(r) else None
        out[f"avg{w}"] = _r(r.mean()) if len(r) else None
        out[f"med{w}"] = _r(r.median()) if len(r) else None
    return out


def _baseline(px: pd.Series, wins, gap=CONFIRM_TRIGGER_GAP) -> dict:
    return _stats(px, pd.DatetimeIndex(px.index[::gap]), wins)


# ---------------------------------------------------------------- P0-1 背离
def build_divergence(hs: pd.Series):
    """周频情绪-价格底背离：情绪先转向、价格滞后。"""
    mood_path = DATA / "mood_data.json"
    mood = json.loads(mood_path.read_text(encoding="utf-8"))
    comp = pd.Series(
        {pd.Timestamp(h["date"]): float(h["score"])
         for h in mood["composite"]["history"]}, name="mood"
    ).sort_index()
    comp = comp[comp.index <= TODAY]
    # 周五通常即交易日；非交易日取之前最近收盘（asof，绝不用未来价）
    px_w = hs.reindex(comp.index, method="ffill")

    d_mood = comp.diff(DIV_REBOUND_W)
    price_ret = (px_w / px_w.shift(DIV_REBOUND_W) - 1.0) * 100
    pessimistic = (comp.rolling(DIV_LOOKBACK_W, min_periods=DIV_LOOKBACK_W).min()
                   <= DIV_PESSIMIST).fillna(False)

    def signal(rebound: float, pessim: float):
        p = (comp.rolling(DIV_LOOKBACK_W, min_periods=DIV_LOOKBACK_W).min()
             <= pessim).fillna(False)
        return (p & (d_mood >= rebound) & (price_ret <= DIV_PRICE_MAX)).fillna(False)

    sig = signal(DIV_REBOUND_MIN, DIV_PESSIMIST)
    dates = _thin(sig)
    base = _baseline(px_w, DIV_FWD_W)
    bt = dict(key="divergence", label="情绪-价格底背离", **_stats(px_w, dates, DIV_FWD_W))
    bt_base = dict(key="baseline", label="全样本基准(每20周)", **base)

    # 三档稳健性：阈值不是挑出来的，要让使用者看到换个阈值会怎样
    robust = []
    for reb, pes in DIV_ROBUST:
        d = _thin(signal(reb, pes))
        st = _stats(px_w, d, DIV_FWD_W)
        robust.append({"rebound": reb, "pessimist": pes, "n": st["n"],
                       "win12": st["win12"], "avg12": st["avg12"],
                       "win4": st["win4"], "avg4": st["avg4"]})

    trig = []
    for dt in dates:
        trig.append({
            "date": dt.strftime("%Y-%m-%d"),
            "mood": _r(comp.get(dt)),
            "d_mood4": _r(d_mood.get(dt)),
            "price_ret4": _r(price_ret.get(dt)),
            "ret4": _r((px_w.shift(-4).get(dt) / px_w.get(dt) - 1) * 100),
            "ret12": _r((px_w.shift(-12).get(dt) / px_w.get(dt) - 1) * 100),
        })
    trig.sort(key=lambda x: x["date"], reverse=True)

    cur = bool(sig.iloc[-1])
    last8_min = _r(comp.iloc[-DIV_LOOKBACK_W:].min())
    detail = {
        "mood_now": _r(comp.iloc[-1]),
        "mood_chg4": _r(d_mood.iloc[-1]),
        "price_ret4": _r(price_ret.iloc[-1]),
        "pessim_last8_min": last8_min,
        "pessim_in_last8": bool(last8_min is not None and last8_min <= DIV_PESSIMIST),
        "hs300": _r(px_w.iloc[-1]),
        "n_weeks": len(comp),
        "start": comp.index[0].strftime("%Y-%m-%d"),
    }
    card = {
        "key": "divergence", "name": "情绪-价格底背离", "group": "拐点确认",
        "freq": "周频", "signal": cur,
        "state": "触发 · 情绪先转向" if cur else "未触发 · 情绪与价格同向",
        "level": "bottom" if cur else "mid",
        "value_text": "已触发" if cur else "未触发",
        "last_date": comp.index[-1].strftime("%Y-%m-%d"),
        "detail": detail,
        "charts": {
            "mood": _hist(comp, "2016-01-01"),
            "price": _hist(px_w, "2016-01-01"),
            "signals": [d.strftime("%Y-%m-%d") for d in dates if d >= pd.Timestamp("2016-01-01")],
        },
        "backtest": [bt, bt_base],
        "robust": robust,
        "triggers": trig,
        "definition": (f"近 {DIV_LOOKBACK_W} 周综合情绪分曾 ≤{DIV_PESSIMIST:g}；"
                       f"最近 {DIV_REBOUND_W} 周情绪分回升 ≥{DIV_REBOUND_MIN:g} 分；"
                       f"同期沪深300 涨幅 ≤{DIV_PRICE_MAX:g}%。"),
        "source": "data/mood_data.json 的 composite.history（1192 周点）+ data/mood_hs300.csv",
        "caveat": ("粗测阶段去重后仅 5 次信号，95%CI 极宽；本卡已做严格 PIT 回测与五档阈值稳健性，"
                   "但样本量仍小、周频综合分早期为混合口径，只能作观察不能当胜率。"
                   "若某档阈值下 4 周胜率低于基准，属于反面证据，已如实列出。"),
    }
    log(f"  [P0-1] 背离 当前={'触发' if cur else '未触发'}  历史信号 {len(dates)} 次  "
        f"12周胜率 {bt['win12']}% / 均 {bt['avg12']}%（基准 {base['win12']}% / {base['avg12']}%）")
    return card, comp.index


# ---------------------------------------------------------------- P0-2 涨跌停
def _board_limit(code: str) -> float:
    """按代码前缀给板块涨跌停幅度。ST 股 5% 无法从代码识别，属已知保守偏差。"""
    c = code.lower()
    if c.startswith("bj"):
        return LIMIT_BJ
    if c.startswith("sh688") or c.startswith("sh689") or c.startswith("sz30"):
        return LIMIT_GEM
    return LIMIT_MAIN


def build_limits(hs: pd.Series, cal: pd.DatetimeIndex):
    """全量逐股向量化；结果落 mood_confirm_limits.csv 供增量复用。"""
    force = "--force" in sys.argv
    if LIMIT_CACHE.exists() and not force:
        try:
            d = pd.read_csv(LIMIT_CACHE, index_col=0, parse_dates=True)
            if len(d) and d.index[-1] >= cal[-1] and "n_limit_up" in d.columns:
                log(f"  [P0-2] 复用缓存 {LIMIT_CACHE.name}（{len(d)} 日，末日 {d.index[-1].date()}），"
                    f"需重算加 --force")
                return d
        except Exception as e:
            log(f"  [P0-2] 缓存读取失败({type(e).__name__})，改为全量重算")

    pdir, adir = DATA / "breadth_prices", DATA / "crowding_amounts"
    files = sorted(pdir.glob("*.csv"))
    if not files:
        raise FileNotFoundError("缺少 data/breadth_prices/*.csv；请先跑 build_breadth.py")
    log(f"  [P0-2] 读取 {len(files)} 只逐股 收盘价/成交额 缓存 ...")
    t0 = time.time()
    prices, amounts, codes = [], [], []
    for p in files:
        code = p.stem
        try:
            dp = pd.read_csv(p, usecols=["date", "close"])
            dp["date"] = pd.to_datetime(dp["date"], errors="coerce")
            sp = dp.dropna().set_index("date")["close"].astype("float32")
            sp = sp[~sp.index.duplicated(keep="last")]
            ap = adir / p.name
            if ap.exists():
                da = pd.read_csv(ap, usecols=["date", "amount"])
                da["date"] = pd.to_datetime(da["date"], errors="coerce")
                sa = da.dropna().set_index("date")["amount"].astype("float32")
                sa = sa[~sa.index.duplicated(keep="last")]
            else:
                sa = pd.Series(dtype="float32")
            prices.append(sp.reindex(cal).to_numpy(dtype="float32"))
            amounts.append(sa.reindex(cal).to_numpy(dtype="float32"))
            codes.append(code)
        except Exception as e:
            log(f"    [跳过] {code}: {type(e).__name__}")
    if not codes:
        raise RuntimeError("逐股缓存全部读取失败")

    P = np.vstack(prices).T                       # (n_days, n_stocks)
    A = np.vstack(amounts).T
    prev = np.vstack([np.full((1, P.shape[1]), np.nan, dtype="float32"), P[:-1]])
    with np.errstate(divide="ignore", invalid="ignore"):
        R = (P / prev - 1.0) * 100.0
    valid = np.isfinite(P) & np.isfinite(prev) & np.isfinite(R) & (P >= LIMIT_MIN_PRICE)

    lim = np.array([_board_limit(c) for c in codes], dtype="float32")
    lo = lim - LIMIT_TOL
    # 涨幅上界：超过理论涨停 +3pp 视为除权/新股异常，不计
    is_up = (R >= lo) & (R <= lim + LIMIT_TOL + 3.0) & valid
    is_dn = (R <= -lo) & (R >= LIMIT_EXFAT_MAX) & valid
    n_valid = valid.sum(axis=1)
    n_up, n_dn = is_up.sum(axis=1), is_dn.sum(axis=1)
    amt = np.nansum(np.where(np.isfinite(A), A, np.nan), axis=1) / 1e8   # 亿元
    amt[np.isfinite(A).sum(axis=1) < LIMIT_MIN_STOCKS] = np.nan

    out = pd.DataFrame({
        "n_valid": n_valid, "n_limit_up": n_up, "n_limit_down": n_dn,
        "up_ratio": np.where(n_valid > 0, n_up / np.maximum(n_valid, 1) * 100, np.nan),
        "down_ratio": np.where(n_valid > 0, n_dn / np.maximum(n_valid, 1) * 100, np.nan),
        "amount_yi": amt,
    }, index=cal)
    out = out[out["n_valid"] >= LIMIT_MIN_STOCKS].copy()
    out.to_csv(LIMIT_CACHE, index_label="date")
    log(f"  [P0-2] 全量向量化完成 {P.shape[0]}日 × {P.shape[1]}股，用时 {time.time()-t0:.1f}s，"
        f"输出 {len(out)} 日")
    return out


def build_limit_card(hs: pd.Series, lim: pd.DataFrame):
    amt = lim["amount_yi"].replace(0, np.nan)
    vol_ratio = amt / amt.rolling(VOL_RATIO_WIN, min_periods=VOL_RATIO_WIN).mean()
    vol_vs_high = amt / amt.rolling(VOL_HIGH_WIN, min_periods=VOL_HIGH_WIN).max()
    hx = hs.reindex(lim.index)
    no_new_low = hx >= hx.shift(1).rolling(20, min_periods=20).min()
    dn = lim["down_ratio"]
    panic_ease = (dn.shift(5) - dn) >= LIMIT_DOWN_EASE
    shrink = vol_ratio <= VOL_SHRINK_MAX
    dry_up = (shrink & no_new_low & panic_ease).fillna(False)

    # 历史标定：缩量止跌 / 跌停恐慌极值 之后的 20/60 日收益，与基准对照
    fwd_px = hx
    groups = {
        "dry_up": _thin(dry_up),
        "panic": _thin(dn >= dn.expanding(min_periods=CONFIRM_MIN_HIST)
                       .quantile(0.90).fillna(1e9)),
        "baseline": pd.DatetimeIndex(lim.index[::CONFIRM_TRIGGER_GAP]),
    }
    labels = {"dry_up": "缩量止跌", "panic": "跌停占比历史前10%", "baseline": "全样本基准(每20日)"}
    bt = [dict(key=k, label=labels[k], **_stats(fwd_px, v, CONFIRM_FWD_WINS))
          for k, v in groups.items()]

    cur = {
        "n_valid": int(lim["n_valid"].iloc[-1]),
        "n_limit_up": int(lim["n_limit_up"].iloc[-1]),
        "n_limit_down": int(lim["n_limit_down"].iloc[-1]),
        "up_ratio": _r(lim["up_ratio"].iloc[-1]),
        "down_ratio": _r(dn.iloc[-1]),
        "down_ratio_5d_ago": _r(dn.iloc[-6]) if len(dn) >= 6 else None,
        "amount_yi": _r(amt.iloc[-1], 1),
        "vol_ratio20": _r(vol_ratio.iloc[-1]),
        "vol_vs_high250": _r(vol_vs_high.iloc[-1], 3),
        "no_new_low": bool(no_new_low.iloc[-1]) if pd.notna(no_new_low.iloc[-1]) else False,
        "shrink": bool(shrink.iloc[-1]) if pd.notna(shrink.iloc[-1]) else False,
        "panic_ease": bool(panic_ease.iloc[-1]),
        "dry_up": bool(dry_up.iloc[-1]),
        "hs300": _r(hx.iloc[-1]),
    }
    card = {
        "key": "limits", "name": "涨跌停家数 · 缩量止跌", "group": "拐点确认",
        "freq": "日频", "signal": cur["dry_up"],
        "state": "缩量止跌成立" if cur["dry_up"] else ("跌停扩散未缓解" if cur["down_ratio"] and cur["down_ratio"] > 2 else "常态"),
        "level": "bottom" if cur["dry_up"] else ("mid" if (cur["down_ratio"] or 0) <= 2 else "caution"),
        "value_text": f"跌停 {cur['n_limit_down']} 只 / {cur['down_ratio']}%",
        "last_date": lim.index[-1].strftime("%Y-%m-%d"),
        "detail": cur,
        "charts": {
            "dates": [d.strftime("%Y-%m-%d") for d in lim.index[-250:]],
            "up": [_r(x) for x in lim["n_limit_up"].iloc[-250:]],
            "down": [_r(x) for x in lim["n_limit_down"].iloc[-250:]],
            "down_ratio": [_r(x) for x in dn.iloc[-250:]],
            "amount": [_r(x, 1) for x in amt.iloc[-250:]],
            "vol_ratio": [_r(x) for x in vol_ratio.iloc[-250:]],
        },
        "backtest": bt,
        "definition": (f"涨跌停按板块阈值判定（主板 {LIMIT_MAIN:g}% / 双创 {LIMIT_GEM:g}% / "
                       f"北交所 {LIMIT_BJ:g}%，容差 {LIMIT_TOL:g}pp）；缩量止跌 = "
                       f"20日量能比 ≤{VOL_SHRINK_MAX:.2f} + 指数未创20日新低 + "
                       f"跌停占比较5日前回落 ≥{LIMIT_DOWN_EASE:g}pp。"),
        "source": "data/breadth_prices 5579 只逐股收盘价 + data/crowding_amounts 逐股成交额",
        "caveat": ("收盘价为未复权口径，无法识别 ST 股 5% 限制与除权日；已剔除单日跌幅 <"
                   f"{LIMIT_EXFAT_MAX:g}% 的除权异常、价格 <{LIMIT_MIN_PRICE:g} 元的低价股、"
                   "停牌跨日样本，因此涨跌停家数是保守下界。量能来自逐股成交额汇总，"
                   "与交易所官方两市成交额口径不同，只看相对变化不看绝对值。"),
    }
    log(f"  [P0-2] 11-20 涨停 {cur['n_limit_up']} / 跌停 {cur['n_limit_down']} "
        f"({cur['down_ratio']}%)，量能比 {cur['vol_ratio20']}，缩量止跌={cur['dry_up']}")
    for b in bt:
        log(f"    {b['label']:20s} n={b['n']:>3d} 20日胜率={b['win20']}% 60日胜率={b['win60']}%")
    return card, lim, vol_ratio, vol_vs_high


# ---------------------------------------------------------------- P0-3 VRP
def fetch_qvix_full():
    """可选：一次性抓 2015 起 QVIX 全历史并落缓存。默认 DAG 不调用。"""
    import akshare as ak
    parts, names = [], []
    for n in ("50etf", "300etf", "500etf"):
        try:
            d = getattr(ak, "index_option_" + n + "_qvix")()
            d["date"] = pd.to_datetime(d["date"], errors="coerce")
            d = d[d["close"].fillna(0) > 0].drop_duplicates("date", keep="last")
            s = d.set_index("date")["close"].astype(float).sort_index()
            # 3 日中值滤波，去掉期权流动性差时的孤立异常值（沿用恐贪链口径）
            med = s.rolling(3, center=True, min_periods=1).median()
            s = s.where((s / med - 1).abs() < 0.35, med)
            s = s[s.index <= TODAY]
            parts.append(s.rename("qvix_" + n))
            names.append(n)
            log(f"    [QVIX] {n}: {len(s)} 行 {s.index[0].date()} ~ {s.index[-1].date()}")
        except Exception as e:
            log(f"    [QVIX] {n} 抓取失败 {type(e).__name__}: {str(e)[:80]}")
    if not parts:
        raise RuntimeError("三个 QVIX 接口均失败；保留原有缓存/降级源")
    q = pd.concat(parts, axis=1).sort_index()
    q.to_csv(QVIX_CACHE, index_label="date")
    log(f"  [P0-3] QVIX 全历史缓存已写入 {QVIX_CACHE.name}（{len(q)} 行）")
    return q


def load_qvix():
    """主口径取 300ETF QVIX（与沪深300 RV 标的匹配），否则降级到恐贪链因子序列。

    实测权衡（2026-11-21）：50ETF QVIX 有 2015-02 起 2851 行长历史，但当前读数
    14.8，比 300ETF 的 20.46 低约 5.7pp；用它减沪深300 RV 会让 VRP 系统性偏低，
    分位虽仍可用、绝对水平却被污染。故主口径用 300ETF 真实序列（2019-12-23 起
    1663 行，覆盖 2020/2022/2024 各轮底与 2025 顶，分位样本足够），50ETF 长历史
    只作为对照值展示，不做回归拼接、不混用口径。
    """
    q, src, start, ref = None, "", "", None
    if QVIX_CACHE.exists():
        try:
            d = pd.read_csv(QVIX_CACHE, index_col=0, parse_dates=True)
            col = "qvix_300etf" if "qvix_300etf" in d.columns else d.columns[0]
            q = pd.to_numeric(d[col], errors="coerce").dropna().sort_index()
            q = q[~q.index.duplicated(keep="last")]
            q = q[q.index <= TODAY]
            if "qvix_50etf" in d.columns:
                r5 = pd.to_numeric(d["qvix_50etf"], errors="coerce").dropna()
                ref = _r(r5.iloc[-1]) if len(r5) else None
            src = f"本地缓存 {QVIX_CACHE.name}（{col}，真实序列 2019-12 起）"
            start = q.index[0].strftime("%Y-%m-%d")
        except Exception as e:
            log(f"  [P0-3] QVIX 缓存读取失败({type(e).__name__})，降级")
            q = None
    if q is None or len(q) < 100:
        f = json.loads((DATA / "index_data.json").read_text(encoding="utf-8"))
        fac = next((x for x in f["indices"]["hs300"]["factors"]
                    if x["key"] == "volatility"), None)
        if fac is None:
            raise RuntimeError("index_data.json 缺少 hs300 volatility 因子")
        q = pd.Series({pd.Timestamp(x["date"]): float(x["value"])
                       for x in fac["series"] if x.get("value") is not None}).sort_index()
        q = q[~q.index.duplicated(keep="last")][lambda s: s.index <= TODAY]
        src = "data/index_data.json 恐贪链 hs300 volatility 因子（300ETF QVIX，零请求降级源）"
        start = q.index[0].strftime("%Y-%m-%d")

    # 日常增量：恐贪链每天都会重抓 300ETF QVIX 并写进 index_data.json，
    # 把它的最新段并进来，VRP 才不会随着时间推移越来越旧。
    # 只在恐贪链末日 >= 缓存末日时才覆盖（拒绝源端回退，沿用项目既有纪律）。
    try:
        f = json.loads((DATA / "index_data.json").read_text(encoding="utf-8"))
        fac = next((x for x in f["indices"]["hs300"]["factors"]
                    if x["key"] == "volatility"), None)
        if fac:
            ext = pd.Series({pd.Timestamp(x["date"]): float(x["value"])
                             for x in fac["series"] if x.get("value") is not None}).sort_index()
            ext = ext[~ext.index.duplicated(keep="last")][lambda s: s.index <= TODAY]
            # 恐贪链对 qvix 做过 ffill，末尾常出现「假观测日」（如 11-19/11-20 复制 11-18 的
            # 20.46）。去掉连续重复值，只保留真实变动点，否则 VRP 会引入幽灵交易日。
            ext = ext[ext.diff().fillna(1.0) != 0]
            if len(ext) and ext.index[-1] >= q.index[-1]:
                before = q.index[-1]
                q = pd.concat([q, ext]).sort_index()
                q = q[~q.index.duplicated(keep="last")]
                src += " + 恐贪链当日增量段"
                log(f"  [P0-3] 隐波增量合并 {before.date()} -> {q.index[-1].date()}")
            else:
                log("  [P0-3] 恐贪链隐波落后于缓存，按「拒绝源端回退」保留缓存")
    except Exception as e:
        log(f"  [P0-3] 隐波增量合并跳过({type(e).__name__})，使用现有序列")
    return q, src, start, ref


def build_vrp_card(hs: pd.Series):
    q, src, qstart, ref50 = load_qvix()
    r = hs.pct_change() * 100
    rv = r.rolling(VRP_RV_WIN, min_periods=VRP_RV_WIN).std() * np.sqrt(VRP_ANNUAL_DAYS)
    sq = (r ** 2).rolling(VRP_DOWNSIDE_WIN, min_periods=VRP_DOWNSIDE_WIN).sum()
    dsq = (r.clip(upper=0) ** 2).rolling(VRP_DOWNSIDE_WIN,
                                        min_periods=VRP_DOWNSIDE_WIN).sum()
    downside = (dsq / sq.replace(0, np.nan)) * 100

    # 只用真实 QVIX 观测日，ffill 最多 3 日；末日截断到「最后一个有隐波的日子」
    qv = q.reindex(hs.index).ffill(limit=3)
    real_last = q.index[-1]
    qv = qv[qv.index <= real_last]
    vrp = (qv - rv.reindex(qv.index)).dropna()
    if vrp.empty:
        raise RuntimeError("QVIX 与 RV20 无重叠样本")
    vrp_pct = vrp.expanding(min_periods=CONFIRM_MIN_HIST).rank(pct=True) * 100
    panic_ease = ((vrp_pct >= VRP_EXTREME_HIGH)
                  & (vrp.diff(VRP_PANIC_EASE_WIN) < 0)).fillna(False)

    px = hs.reindex(vrp.index)
    groups = {
        "high": _thin(vrp_pct >= VRP_EXTREME_HIGH),
        "low": _thin(vrp_pct <= VRP_EXTREME_LOW),
        "ease": _thin(panic_ease),
        "baseline": pd.DatetimeIndex(vrp.index[::CONFIRM_TRIGGER_GAP]),
    }
    labels = {"high": f"VRP 高分位≥{VRP_EXTREME_HIGH}", "low": f"VRP 低分位≤{VRP_EXTREME_LOW}",
              "ease": "恐慌溢价由高位回落", "baseline": "全样本基准(每20日)"}
    bt = [dict(key=k, label=labels[k], **_stats(px, v, CONFIRM_FWD_WINS))
          for k, v in groups.items()]
    # 方向一致性自证伪：高 VRP 组 − 低 VRP 组 的 60 日均收益价差
    hi, lo = bt[0], bt[1]
    spread = (round(hi["avg60"] - lo["avg60"], 2)
              if hi.get("avg60") is not None and lo.get("avg60") is not None else None)
    verdict = ("符合预期(高VRP后收益更高)" if spread is not None and spread > 1 else
               "方向相反(高VRP后收益更低)" if spread is not None and spread < -1 else
               "不显著 / 样本不足")
    for b in bt:
        b["verdict"] = verdict if b["key"] == "high" else ""

    # 所有读数统一截到 vrp 末日（隐波源常滞后 1~2 日，不能拿 11-20 的 RV 配 11-18 的 QVIX）
    last = vrp.index[-1]
    cur = {
        "qvix": _r(qv.reindex(vrp.index).iloc[-1]), "qvix_50etf_ref": ref50,
        "qvix_last_date": real_last.strftime("%Y-%m-%d"),
        "rv20": _r(rv.reindex(vrp.index).iloc[-1]), "vrp": _r(vrp.iloc[-1]),
        "vrp_pct": _r(vrp_pct.iloc[-1], 1),
        "downside_share": _r(downside.reindex(vrp.index).iloc[-1], 1),
        "vrp_chg5": _r(vrp.diff(VRP_PANIC_EASE_WIN).iloc[-1]),
        "panic_ease": bool(panic_ease.iloc[-1]),
        "hs300": _r(hs.reindex(vrp.index).iloc[-1]),
    }
    card = {
        "key": "vrp", "name": "波动率风险溢价 VRP", "group": "拐点确认",
        "freq": "日频", "signal": cur["panic_ease"],
        "state": ("恐慌溢价高企" if (cur["vrp_pct"] or 0) >= VRP_EXTREME_HIGH else
                  "恐慌溢价由高位回落" if cur["panic_ease"] else
                  "隐波相对便宜" if (cur["vrp_pct"] or 100) <= VRP_EXTREME_LOW else "常态"),
        "level": ("caution" if (cur["vrp_pct"] or 0) >= VRP_EXTREME_HIGH
                  else "bottom" if cur["panic_ease"] else "mid"),
        "value_text": f"VRP {cur['vrp']}pp（分位 {cur['vrp_pct']}）",
        "last_date": vrp.index[-1].strftime("%Y-%m-%d"),
        "detail": cur,
        "charts": {
            # 三条线统一对齐 vrp.index，前端按位置画图才不会错位
            "qvix": _hist(qv.reindex(vrp.index), "2023-01-01"),
            "rv": _hist(rv.reindex(vrp.index), "2023-01-01"),
            "vrp": _hist(vrp.reindex(vrp.index), "2023-01-01"),
        },
        "backtest": bt,
        "spread60": spread, "verdict": verdict,
        "definition": (f"VRP = QVIX 隐含波动 − 沪深300 {VRP_RV_WIN} 日已实现波动"
                       f"(年化 {VRP_ANNUAL_DAYS} 日)；下行波动占比 = "
                       f"近 {VRP_DOWNSIDE_WIN} 日下跌收益平方和 ÷ 全部收益平方和。"
                       f"分位 ≥{VRP_EXTREME_HIGH} 视为恐慌溢价高，5 日回落视为消退。"),
        "source": src, "qvix_start": qstart,
        "caveat": ("QVIX 是市场为未来波动付的钱，与 RSI/BIAS 等已实现价格函数不同源。"
                   "主口径用 300ETF QVIX 真实序列（2019-12 起）配沪深300 RV，标的匹配优先；"
                   "50ETF QVIX 虽有 2015 起更长历史，但当前读数比 300ETF 低约 5~6pp，"
                   "拼接会污染 VRP 绝对水平，故只作对照、不混用。日常由恐贪链 "
                   "index_data.json 增量补最新隐波，已剔除其 ffill 造成的重复假观测日。"
                   f"隐波源端止于 {real_last.strftime('%Y-%m-%d')}，卡片内 QVIX/RV/VRP/指数"
                   "全部统一截到该日（不用 11-20 的 RV 配 11-18 的隐波）；"
                   "若未跑过 --qvix-full，历史仅覆盖恐贪链因子起点约 2022-10，"
                   "分位样本较少，结论方向可信、精度有限。"),
    }
    log(f"  [P0-3] VRP={cur['vrp']}pp 分位={cur['vrp_pct']}  QVIX={cur['qvix']} "
        f"RV20={cur['rv20']}  下行占比={cur['downside_share']}%  源={src[:36]}")
    log(f"    方向自证伪: 高VRP−低VRP 60日价差 {spread}pp -> {verdict}")
    return card, vrp, rv, downside, qv


# ---------------------------------------------------------------- 汇总输出
def main():
    t0 = time.time()
    log("=" * 66)
    log("拐点确认层 build_confirm.py（不进综合分 · 不改动现有任何口径）")
    log("=" * 66)

    hs = _s("mood_hs300.csv", "hs300")
    cal = hs.index[(hs.index >= pd.Timestamp(CONFIRM_START)) & (hs.index <= TODAY)]
    log(f"基准交易日历 {len(cal)} 日：{cal[0].date()} ~ {cal[-1].date()}；沪深300 {_r(hs.iloc[-1])}")

    if "--qvix-full" in sys.argv:
        log("[可选] 抓取 QVIX 全历史缓存 ...")
        try:
            fetch_qvix_full()
        except Exception as e:
            log(f"  [可选] 失败（不影响主流程，将用本地因子序列降级）：{type(e).__name__} {str(e)[:100]}")

    div_card, comp_idx = build_divergence(hs)
    lim = build_limits(hs, cal)
    lim_card, lim, vol_ratio, vol_vs_high = build_limit_card(hs, lim)
    vrp_card, vrp, rv, downside, qv = build_vrp_card(hs)

    cards = [div_card, lim_card, vrp_card]
    signals = [
        {"key": "divergence", "name": "情绪-价格底背离", "on": div_card["signal"],
         "note": div_card["state"]},
        {"key": "dry_up", "name": "缩量止跌", "on": lim_card["detail"]["dry_up"],
         "note": lim_card["state"]},
        {"key": "vrp_ease", "name": "恐慌溢价消退", "on": vrp_card["detail"]["panic_ease"],
         "note": vrp_card["state"]},
    ]
    n_on = sum(1 for x in signals if x["on"])
    layer = {
        "n_signals": n_on, "n_total": 3,
        "state": ("确认充分" if n_on >= 2 else "部分确认" if n_on == 1 else "未确认"),
        "items": signals,
        "note": "独立观察层，不参与 8 指标等权综合分，也不改变 13 维共振板的 bottom_score / confirm。",
    }

    out = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": cal[-1].strftime("%Y-%m-%d"),
        "cards": cards,
        "layer": layer,
        "disclaimer": DISCLAIMER,
        "thresholds": {
            "div": {"pessimist": DIV_PESSIMIST, "lookback_w": DIV_LOOKBACK_W,
                    "rebound_w": DIV_REBOUND_W, "rebound_min": DIV_REBOUND_MIN,
                    "price_max": DIV_PRICE_MAX, "robust": [list(x) for x in DIV_ROBUST]},
            "limit": {"main": LIMIT_MAIN, "gem": LIMIT_GEM, "bj": LIMIT_BJ,
                      "tol": LIMIT_TOL, "exfat_max": LIMIT_EXFAT_MAX},
            "vol": {"shrink_max": VOL_SHRINK_MAX, "ratio_win": VOL_RATIO_WIN,
                    "high_win": VOL_HIGH_WIN},
            "vrp": {"rv_win": VRP_RV_WIN, "downside_win": VRP_DOWNSIDE_WIN,
                    "low": VRP_EXTREME_LOW, "high": VRP_EXTREME_HIGH,
                    "annual_days": VRP_ANNUAL_DAYS},
            "min_hist": CONFIRM_MIN_HIST, "gap": CONFIRM_TRIGGER_GAP,
            "fwd_wins": list(CONFIRM_FWD_WINS),
        },
        "meta": {
            "hs300_last": _r(hs.iloc[-1]),
            "qvix_source": vrp_card["source"], "qvix_start": vrp_card["qvix_start"],
            "qvix_last": vrp_card["detail"]["qvix_last_date"],
            "n_limit_files": len(list((DATA / "breadth_prices").glob("*.csv"))),
            "composite_score": json.loads((DATA / "mood_data.json").read_text(encoding="utf-8"))["composite"]["score"],
            "note": "本层为新增确认层；综合情绪分与 13 维共振板数值均未被本脚本改写。",
        },
    }

    js = json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    OUT_JSON.write_text(js, encoding="utf-8")
    OUT_JS.write_text("window.CONFIRM_DATA=" + js + ";", encoding="utf-8")

    # 日频序列留档，供后续 PIT 回放 / 其它脚本复用
    daily = pd.DataFrame({
        "hs300": hs.reindex(lim.index),
        "n_valid": lim["n_valid"], "n_limit_up": lim["n_limit_up"],
        "n_limit_down": lim["n_limit_down"],
        "down_ratio": lim["down_ratio"], "amount_yi": lim["amount_yi"],
        "vol_ratio20": vol_ratio.reindex(lim.index),
        "vol_vs_high250": vol_vs_high.reindex(lim.index),
        "qvix": qv.reindex(lim.index), "rv20": rv.reindex(lim.index),
        "vrp": vrp.reindex(lim.index),
        "downside_share": downside.reindex(lim.index),
    }, index=lim.index)
    daily.to_csv(OUT_CSV, index_label="date")

    log("-" * 66)
    log(f"确认层：{layer['state']}（{n_on}/3 项触发）")
    for c in cards:
        log(f"  {c['name']:20s} {c['state']:<18s} 截止 {c['last_date']}")
    log(f"输出：{OUT_JSON.name} ({OUT_JSON.stat().st_size/1024:.0f}KB) / "
        f"{OUT_JS.name} / {OUT_CSV.name}")
    log(f"完成，用时 {time.time() - t0:.1f} 秒")


if __name__ == "__main__":
    main()
