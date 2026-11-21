# -*- coding: utf-8 -*-
"""R4 跨市场复现：价格类指标（RSI14 / 200日均线偏离 / 60日动量 / 250日分位）
在 5 个海外市场 + A股（本库沪深300）各算一遍，检验「长端更强」是普遍规律还是本土特例。
海外数据：Yahoo Finance chart API（adjclose，10 年日频，已落盘 data/_yahoo）。
"""
import json, os
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
YH = os.path.join(BASE, "data", "_yahoo")
MK = [("GSPC", "标普500"), ("HSI", "恒生指数"), ("N225", "日经225"),
      ("GDAXI", "德国DAX"), ("FTSE", "英国FTSE100")]
HS = [20, 60, 120, 250, 500]
NS = 300
RNG = np.random.default_rng(20261121)


def load_yahoo(tag):
    j = json.load(open(os.path.join(YH, tag + ".json"), encoding="utf-8-sig"))
    r = j["chart"]["result"][0]
    df = pd.DataFrame({"date": pd.to_datetime(r["timestamp"], unit="s").normalize(),
                       "close": r["indicators"]["adjclose"][0]["adjclose"]}).dropna()
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def load_hs300():
    f = pd.read_csv(os.path.join(BASE, "data", "factor_matrix.csv"), index_col=0, parse_dates=True)
    return pd.DataFrame({"date": f.index, "close": f["hs300"].astype(float)}).dropna().reset_index(drop=True)


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def sp(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 120:
        return np.nan
    x = pd.Series(a[m]).rank().values
    y = pd.Series(b[m]).rank().values
    x = x - x.mean(); y = y - y.mean()
    d = np.sqrt((x * x).sum() * (y * y).sum())
    return float((x * y).sum() / d) if d > 0 else np.nan


out = {}
for tag, name in MK + [("HS300", "沪深300(A股本库)")]:
    df = load_hs300() if tag == "HS300" else load_yahoo(tag)
    c = df["close"].astype(float).values
    n = len(c)
    ind = {
        "rsi14": rsi(pd.Series(c)).values,
        "bias200": c / pd.Series(c).rolling(200).mean().values - 1,
        "mom60": c / pd.Series(c).shift(60).values - 1,
        "pct250": pd.Series(c).rolling(250).apply(lambda w: (w <= w[-1]).mean(), raw=True).values,
    }
    out[name] = {"tag": tag, "n": n, "start": str(df["date"].iloc[0].date()),
                 "end": str(df["date"].iloc[-1].date()), "ind": {}}
    for k, v in ind.items():
        rec = []
        for h in HS:
            fwd = np.full(n, np.nan)
            fwd[:-h] = np.log(c[h:] / c[:-h])
            ic = sp(v, fwd)
            span = min(max(h, 250), n // 3)
            idx = np.arange(n)
            null = np.empty(NS)
            for i in range(NS):
                sh = int(RNG.integers(span, n - span))
                null[i] = sp(v[idx], fwd[(idx + sh) % n])
            p = (1 + np.sum(np.abs(null) >= abs(ic))) / (1 + NS) if ic == ic else np.nan
            rec.append(dict(h=h, ic=round(float(ic), 3) if ic == ic else None,
                            p=round(float(p), 3) if p == p else None))
        out[name]["ind"][k] = rec

summary = {}
for k in ["rsi14", "bias200", "mom60", "pct250"]:
    for h in HS:
        ics = [out[s]["ind"][k][HS.index(h)]["ic"] for s in out
               if out[s]["ind"][k][HS.index(h)]["ic"] is not None]
        if not ics:
            continue
        signs = [1 if x > 0 else -1 for x in ics]
        summary[f"{k}@{h}"] = dict(n=len(ics),
                                   agreement=max(sum(1 for x in signs if x > 0),
                                                 sum(1 for x in signs if x < 0)),
                                   mean_ic=round(float(np.mean(ics)), 3))

pairs = []
for s in out:
    for k in ["rsi14", "bias200", "mom60", "pct250"]:
        short = np.nanmean([abs(out[s]["ind"][k][HS.index(h)]["ic"]) for h in (20, 60)])
        long_ = np.nanmean([abs(out[s]["ind"][k][HS.index(h)]["ic"]) for h in (250, 500)])
        if short == short and long_ == long_:
            pairs.append(dict(market=s, ind=k, short=round(float(short), 3),
                              long=round(float(long_), 3), up=bool(long_ > short)))

json.dump(dict(meta=dict(source="Yahoo Finance chart API adjclose 10y + 本库 hs300；仅价格类指标",
                         horizons=HS, n_shift=NS),
               markets=out, summary=summary,
               longer=dict(n=len(pairs), n_up=sum(1 for x in pairs if x["up"]), pairs=pairs)),
          open(os.path.join(BASE, "data", "cross_market.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

print("市场覆盖：")
for s, v in out.items():
    print(f"  {s:14s} {v['start']} ~ {v['end']}  n={v['n']}")
print("\n符号一致性：")
for k, v in summary.items():
    print(f"  {k:14s} n={v['n']} 同号={v['agreement']} 均值IC={v['mean_ic']:+.3f}")
print(f"\n长端更强：{sum(1 for x in pairs if x['up'])}/{len(pairs)} 对")
for p in pairs:
    if p["ind"] in ("rsi14",):
        print(f"   {p['market']:14s} {p['ind']:8s} 短{p['short']:.3f} 长{p['long']:.3f} {'↑' if p['up'] else '↓'}")
