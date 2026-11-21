# -*- coding: utf-8 -*-
"""T008 执行：原生频率重采样 vs 日频网格。
核心检验：n_eff 是否随采样频率变化（预期不变），IC 是否变化（预期变）。
"""
import json, os
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(os.path.join(BASE, "data", "factor_matrix.csv"), index_col=0, parse_dates=True)
px = df["hs300"].astype(float).values
n = len(px)
mood = json.load(open(os.path.join(BASE, "data", "mood_data.json"), encoding="utf-8"))
cs = pd.Series({pd.Timestamp(x["date"]): float(x["score"])
                for x in mood["composite"]["history"] if x.get("score") is not None}).sort_index()
cs_d = cs.reindex(cs.index.union(df.index)).ffill().reindex(df.index)
SER = {"composite": cs_d.values, "dgz": df["dgz__raw"].values,
       "pgjj_zsy": df["pgjj_zsy__raw"].values, "rsi": df["hs300_rsi__raw"].values,
       "magic": df["magic__raw"].values}
NAME = {"composite": "8指标等权综合分", "dgz": "低估指数行业", "pgjj_zsy": "正收益偏股基金占比",
        "rsi": "沪深300 RSI14（对照）", "magic": "神奇指标（对照）"}


def sp(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 40:
        return np.nan, int(m.sum())
    x = pd.Series(a[m]).rank().values
    y = pd.Series(b[m]).rank().values
    x -= x.mean(); y -= y.mean()
    d = np.sqrt((x * x).sum() * (y * y).sum())
    return (float((x * y).sum() / d) if d > 0 else np.nan), int(m.sum())


rows = []
for k, v in SER.items():
    v = np.asarray(v, float)
    chg = np.abs(np.diff(v, prepend=np.nan)) > 1e-12
    chg[0] = False
    ci = np.where(chg)[0]
    gap = float(np.median(np.diff(ci))) if len(ci) > 2 else np.nan
    for h in (250, 500):
        fwd = np.full(n, np.nan)
        fwd[:-h] = np.log(px[h:] / px[:-h])
        ic_d, nd = sp(v, fwd)
        ic_n, nn = sp(v[ci], fwd[ci])
        # 有效样本：两条路径都应 ≈ n/h
        neff_d = nd / h
        neff_n = nn / max(1.0, h / gap)
        rows.append(dict(k=k, name=NAME[k], h=h, gap=round(gap, 1),
                         ic_daily=None if ic_d != ic_d else round(ic_d, 3),
                         ic_native=None if ic_n != ic_n else round(ic_n, 3),
                         n_daily=nd, n_native=nn,
                         neff_daily=round(neff_d, 2), neff_native=round(neff_n, 2)))
        print(f"{NAME[k][:16]:18s} h={h:3d} 间隔{gap:5.1f}日 "
              f"日频IC {ic_d:+.3f}(n={nd}) 原生IC {ic_n:+.3f}(n={nn}) "
              f"有效n 日频{neff_d:.1f} / 原生{neff_n:.1f}")

json.dump(dict(meta=dict(note="n_eff = 观测数 / (h/间隔)；两条路径应相等，用于检验 T007 的折算近似是否成立"),
               rows=rows),
          open(os.path.join(BASE, "data", "native_freq.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nwrote data/native_freq.json")
