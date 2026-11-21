# -*- coding: utf-8 -*-
"""周期画像：每个因子 × 12 个前瞻期 的 IC / 有效样本 / 显著度 / 分段一致性 / 综合可信度。
只读，不写生产文件。输出 data/horizon_profile.json
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
N_SHIFT = 400
RNG = np.random.default_rng(20261121)

df = pd.read_csv(os.path.join(BASE,"data","factor_matrix.csv"), index_col=0, parse_dates=True)
px = df["hs300"].astype(float)
# 综合分（8 指标等权）补进来
mood = json.load(open(os.path.join(BASE,"data","mood_data.json"), encoding="utf-8"))
cs = pd.Series({pd.Timestamp(x["date"]): float(x["score"])
                for x in mood["composite"]["history"] if x.get("score") is not None}).sort_index()
cs_d = cs.reindex(cs.index.union(px.index)).ffill().reindex(px.index)

SERIES = {k: df[k+"__raw"].astype(float) for k in F}
SERIES["composite"] = cs_d
NAME["composite"] = "8指标等权综合分"

def rank01(s):
    v = pd.Series(s).astype(float)
    return v.rank(method="average").values.astype(float)

def corr(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 100: return np.nan
    x, z = a[m]-a[m].mean(), b[m]-b[m].mean()
    d = np.sqrt((x*x).sum()*(z*z).sum())
    return float((x*z).sum()/d) if d > 0 else np.nan

def bh(ps):
    ps = np.asarray(ps, float); m = len(ps); order = np.argsort(ps)
    q = np.empty(m); prev = 1.0
    for rk in range(m-1, -1, -1):
        i = order[rk]; prev = min(prev, ps[i]*m/(rk+1)); q[i] = prev
    return q

n = len(px)
idx = np.arange(n)
out = {}
for key in list(SERIES):
    rx_all = rank01(SERIES[key])
    rows = []
    for h in HS:
        fwd = np.full(n, np.nan)
        fwd[:-h] = np.log(px.values[h:]/px.values[:-h])
        ry = rank01(fwd)
        ic = corr(rx_all, ry)
        span = max(h, 250)   # 至少平移一年，保证零分布不与被检验窗口重叠
        null = np.empty(N_SHIFT)
        for i in range(N_SHIFT):
            k = int(RNG.integers(span, n-span))
            null[i] = corr(rx_all[idx], ry[(idx+k) % n])
        p = (1+np.sum(np.abs(null) >= abs(ic)))/(1+N_SHIFT)
        nv = int(np.sum(~(np.isnan(rx_all) | np.isnan(ry))))
        # 三段
        segs = []
        for a, b in [(0, n//3), (n//3, 2*n//3), (2*n//3, n)]:
            segs.append(corr(rx_all[a:b], ry[a:b]))
        cons = sum(1 for s in segs if not np.isnan(s) and np.sign(s) == np.sign(ic))/3.0
        rows.append(dict(h=h, ic=ic if not np.isnan(ic) else 0.0, p=float(p),
                         n_valid=nv, n_eff=nv/h, seg=segs, seg_cons=cons))
    q = bh([r["p"] for r in rows])
    for r, qq in zip(rows, q):
        r["q"] = float(qq)
        # 乘性：效应太小则整体归零，避免「大样本 + 微小 IC」拿到高分
        eff = min(1.0, abs(r["ic"])/0.35) ** 0.8
        stat = (1.0-min(qq, 1.0)) ** 0.5
        stab = 0.5 + 0.5*r["seg_cons"]
        samp = min(1.0, (r["n_eff"]/10.0) ** 0.5)
        r["score"] = round(100*eff*stat*stab*samp, 1)
        r["stat_ok"] = bool(qq < 0.10 and r["seg_cons"] >= 2/3)
        r["econ_ok"] = bool(abs(r["ic"]) >= 0.15)
    scores = np.array([r["score"] for r in rows])
    ics = np.array([abs(r["ic"]) for r in rows])
    peak_i = int(np.argmax(ics))
    # 有效尺度带：效应够大 + 三段稳 + 综合可信度过线，取包含最高分格子的连续区段
    ok = [i for i, r in enumerate(rows)
          if abs(r["ic"]) >= 0.15 and r["seg_cons"] >= 2/3 and r["score"] >= 55]
    if ok:
        best = max(ok, key=lambda i: rows[i]["score"])
        lo = hi = best
        while lo-1 in ok: lo -= 1
        while hi+1 in ok: hi += 1
        band = [HS[lo], HS[hi]]; bw = hi-lo+1
        band_cells = rows[lo:hi+1]
        proven = any(r["q"] < 0.10 for r in band_cells)
    else:
        band = None; bw = 0; proven = False
    if band and proven:
        verdict = "可用尺度"
    elif band:
        verdict = "待验证"
    elif ics.max() >= 0.30:
        verdict = "效应可见·统计不可证"
    elif any(r["q"] < 0.10 for r in rows):
        verdict = "效应过小"
    else:
        verdict = "无信号"
    # 剖面单调性：h 与 |IC| 的秩相关
    mono = float(pd.Series(HS).corr(pd.Series(ics), method="spearman"))
    micro = max(r["score"] for r in rows if r["h"] <= 5)
    short = max(r["score"] for r in rows if 8 <= r["h"] <= 30)
    mid = max(r["score"] for r in rows if 40 <= r["h"] <= 120)
    long = max(r["score"] for r in rows if r["h"] >= 180)
    # 起效点：第一个 |IC|>=0.15 且此后均值不再跌回 0.15 的周期
    onset = None
    for i in range(len(HS)):
        if ics[i] >= 0.15 and ics[i:].mean() >= 0.15:
            onset = HS[i]
            break
    # 更新频率：日频序列里有多少天真的在变（慢更新序列在微观尺度上是阶梯函数）
    v = pd.Series(SERIES[key]).astype(float).dropna()
    chg = float((v.diff().abs() > 1e-12).mean()) if len(v) > 10 else np.nan
    out[key] = dict(name=NAME[key], rows=rows,
                    peak_h=HS[peak_i], peak_ic=rows[peak_i]["ic"],
                    band=band, band_width=bw, verdict=verdict, onset_h=onset,
                    monotonicity=round(mono, 2), chg_rate=round(chg, 3),
                    score_micro=micro, score_short=short, score_mid=mid, score_long=long,
                    peak_score=float(scores.max()))
    print(f"{NAME[key][:16]:18s} peak h={HS[peak_i]:4d} IC={rows[peak_i]['ic']:+.3f} "
          f"band={band} mono={mono:+.2f} [{micro:.0f}/{short:.0f}/{mid:.0f}/{long:.0f}] "
          f"chg={chg:.2f} {verdict}")

meta = dict(n=n, horizons=HS, n_shift=N_SHIFT,
            weight_note="score=100*(0.40*min(1,|IC|/0.35)+0.20*三段同号率+0.30*(1-q)+0.10*min(1,n_eff/20))",
            gate="q<0.10 且 三段同号率>=2/3")
json.dump(dict(meta=meta, factors=out),
          open(os.path.join(BASE,"data","horizon_profile.json"),"w",encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nwrote data/horizon_profile.json")
