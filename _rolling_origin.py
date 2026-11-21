# -*- coding: utf-8 -*-
"""R2 滚动起点：每个起点只用历史数据定「有效尺度带」，再看后续 4 年带是否还成立。
只读，输出 data/rolling_origin.json
"""
import json, os, numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
F = ["rp","dgz","pgjj_zsy","qschsl","zzpgnh","magic","etf300_ratio","margin_growth",
     "breadth_ma200","erp_attrib","below_net","nhnl","adv_ratio","hs300_rsi","arms",
     "breadth_score","mcclellan"]
NAME = {
 "rp":"A股风险溢价","dgz":"低估指数行业","pgjj_zsy":"正收益偏股基金占比","qschsl":"全市场换手率",
 "zzpgnh":"股基滚动年化收益","magic":"神奇指标","etf300_ratio":"头部300ETF占流通市值",
 "margin_growth":"两融余额60日增速","breadth_ma200":"全A站上200日均线占比",
 "erp_attrib":"ERP股债归因","below_net":"破净率","nhnl":"新高新低差NH-NL",
 "adv_ratio":"上涨家数占比与动能广度","hs300_rsi":"沪深300 RSI14","arms":"阿姆氏指标TRIN",
 "breadth_score":"市场宽广度综合","mcclellan":"麦克莱伦摆动指标"}
HS = [1,2,3,5,8,10,15,20,30,40,60,90,120,180,250,375,500,750]
OOS_W = 1000          # 样本外窗口 4 年
MIN_IC = 0.15
MIN_OOS = 0.10

df = pd.read_csv(os.path.join(BASE, "data", "factor_matrix.csv"), index_col=0, parse_dates=True)
px = df["hs300"].astype(float).values
n = len(px)
mood = json.load(open(os.path.join(BASE, "data", "mood_data.json"), encoding="utf-8"))
cs = pd.Series({pd.Timestamp(x["date"]): float(x["score"])
                for x in mood["composite"]["history"] if x.get("score") is not None}).sort_index()
cs_d = cs.reindex(cs.index.union(df.index)).ffill().reindex(df.index)
SERIES = {k: df[k+"__raw"].astype(float).values for k in F}
SERIES["composite"] = cs_d.values
NAME["composite"] = "8指标等权综合分"

FWD = {}
for h in HS:
    a = np.full(n, np.nan)
    a[:-h] = np.log(px[h:] / px[:-h])
    FWD[h] = a


def corr(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 80:
        return np.nan
    x, z = a[m] - a[m].mean(), b[m] - b[m].mean()
    d = np.sqrt((x * x).sum() * (z * z).sum())
    return float((x * z).sum() / d) if d > 0 else np.nan


def ic_at(x, h, lo, hi):
    """在 [lo, hi) 区间内、且 t+h 不越界的样本上算秩 IC。"""
    seg = x[lo:hi]
    y = FWD[h][lo:hi]
    ok = ~(np.isnan(seg) | np.isnan(y))
    if ok.sum() < 80:
        return np.nan
    rx = pd.Series(seg[ok]).rank().values
    ry = pd.Series(y[ok]).rank().values
    return corr(rx, ry)


def band_of(xs):
    """给定各周期 IC，返回连续带（含最大 |IC| 的那段）。"""
    flag = []
    for i, h in enumerate(HS):
        v = xs.get(h, np.nan)
        flag.append(bool(not np.isnan(v) and abs(v) >= MIN_IC))
    best, cur = [], []
    for i, f in enumerate(flag + [False]):
        if f:
            cur.append(i)
        else:
            if len(cur) > len(best):
                best = cur
            cur = []
    if not best:
        return None
    return [HS[best[0]], HS[best[-1]]]


def core_band(xs):
    """核心区定义：|IC| >= 0.7×峰值 的连续段（含峰值），不再看 0.15 起效点。"""
    vals = {h: xs[h] for h in HS if not np.isnan(xs.get(h, np.nan))}
    if not vals:
        return None
    pk_h = max(vals, key=lambda h: abs(vals[h]))
    pk = abs(vals[pk_h])
    if pk < MIN_IC:
        return None
    ok = {h for h, v in vals.items() if abs(v) >= 0.7 * pk}
    i = HS.index(pk_h)
    lo = hi = i
    while lo - 1 >= 0 and HS[lo - 1] in ok:
        lo -= 1
    while hi + 1 < len(HS) and HS[hi + 1] in ok:
        hi += 1
    return [HS[lo], HS[hi]]


origins = pd.date_range("2019-01-01", "2023-07-01", freq="6MS")
FIXED = [180, 250, 375]          # 固定长带基线：不看因子，一律用这三个周期
out = {}
for key in SERIES:
    x = SERIES[key].astype(float)
    det = []
    base_pass = []
    core_pass = []
    core_bands = []
    for od in origins:
        i0 = int(np.searchsorted(df.index.values, np.datetime64(od)))
        if i0 < 400 or n - i0 < 300:
            continue
        xs = {h: ic_at(x, h, 0, i0) for h in HS}
        b = band_of(xs)
        cb = core_band(xs)
        # 固定长带基线（同一批起点、同一段样本外）
        oos_fix = [ic_at(x, h, i0, min(i0 + OOS_W, n)) for h in FIXED]
        if not any(np.isnan(v) for v in oos_fix):
            base_pass.append(bool(abs(np.nanmean(oos_fix)) >= MIN_OOS))
        if cb:
            hs_c = [h for h in HS if cb[0] <= h <= cb[1] and not np.isnan(xs[h])]
            is_c = float(np.mean([xs[h] for h in hs_c]))
            oo = [ic_at(x, h, i0, min(i0 + OOS_W, n)) for h in hs_c]
            oo_c = float(np.nanmean(oo)) if not all(np.isnan(oo)) else np.nan
            core_pass.append(bool((not np.isnan(oo_c)) and np.sign(oo_c) == np.sign(is_c)
                                  and abs(oo_c) >= MIN_OOS))
            core_bands.append(cb)
        if not b:
            det.append(dict(origin=str(od.date()), band=None))
            continue
        hs = [h for h in HS if b[0] <= h <= b[1] and not np.isnan(xs[h])]
        is_ic = float(np.mean([xs[h] for h in hs]))
        oos = {h: ic_at(x, h, i0, min(i0 + OOS_W, n)) for h in hs}
        oos_ic = float(np.nanmean([oos[h] for h in hs]))
        ok = (not np.isnan(oos_ic)) and np.sign(oos_ic) == np.sign(is_ic) and abs(oos_ic) >= MIN_OOS
        det.append(dict(origin=str(od.date()), band=b, is_ic=round(is_ic, 3),
                        oos_ic=round(oos_ic, 3), pass_=bool(ok)))
    found = [d for d in det if d["band"]]
    passed = [d for d in found if d.get("pass_")]
    # 相邻起点的带重叠度（Jaccard）
    js = []
    for a, b in zip(found, found[1:]):
        sa = set(range(a["band"][0], a["band"][1] + 1))
        sb = set(range(b["band"][0], b["band"][1] + 1))
        js.append(len(sa & sb) / max(1, len(sa | sb)))
    out[key] = dict(name=NAME[key], n_origin=len(det), n_band=len(found),
                    n_pass=len(passed), pass_rate=round(len(passed) / max(1, len(found)), 2),
                    fixed_pass_rate=round(sum(base_pass) / max(1, len(base_pass)), 2),
                    core_pass_rate=round(sum(core_pass) / max(1, len(core_pass)), 2),
                    n_core=len(core_pass),
                    core_band=core_bands[-1] if core_bands else None,
                    jaccard=round(float(np.mean(js)), 2) if js else None,
                    detail=det)
    print(f"{NAME[key][:16]:18s} 边缘带{out[key]['pass_rate']:.2f} "
          f"核心带{out[key]['core_pass_rate']:.2f} 固定长带{out[key]['fixed_pass_rate']:.2f} "
          f"重叠{out[key]['jaccard']}")

json.dump(dict(meta=dict(horizons=HS, oos_window=OOS_W, min_ic=MIN_IC, min_oos=MIN_OOS,
                         origins=[str(o.date()) for o in origins],
                         criteria="历史段|IC|>=0.15 定带；样本外 4 年均值同号且|IC|>=0.10 记为通过"),
               factors=out),
          open(os.path.join(BASE, "data", "rolling_origin.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nwrote data/rolling_origin.json")
