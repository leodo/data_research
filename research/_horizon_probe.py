# -*- coding: utf-8 -*-
"""只读探针 2：同一批因子在不同前瞻期上的秩 IC。
检验「是不是 horizon 选错」而非「没信号」。
"""
import json, numpy as np, pandas as pd, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = ["rp","dgz","pgjj_zsy","qschsl","zzpgnh","magic","etf300_ratio","margin_growth",
     "breadth_ma200","erp_attrib","below_net","nhnl","adv_ratio","hs300_rsi","arms",
     "breadth_score","mcclellan"]
HS = [20, 60, 120, 250, 500]
N_SHIFT = 400
RNG = np.random.default_rng(7)

df = pd.read_csv(os.path.join(BASE, "data", "factor_matrix.csv"), index_col=0)
ret = df["hs300"].astype(float).values


def rank01(x):
    s = pd.Series(x)
    return s.rank(method="average").values.astype(float)


def corr_pairs(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 200:
        return np.nan
    x, z = a[m] - a[m].mean(), b[m] - b[m].mean()
    d = np.sqrt((x * x).sum() * (z * z).sum())
    return float((x * z).sum() / d) if d > 0 else np.nan


out = {}
for h in HS:
    fwd = np.full(len(ret), np.nan)
    fwd[:-h] = np.log(ret[h:] / ret[:-h])
    ry = rank01(fwd)
    idx = np.arange(len(ret))
    rows = []
    for f in F:
        rx = rank01(df[f + "__raw"].values.astype(float))
        obs = corr_pairs(rx, ry)
        null = np.empty(N_SHIFT)
        for i in range(N_SHIFT):
            k = int(RNG.integers(300, len(ret) - 300))
            null[i] = corr_pairs(rx[idx], ry[(idx + k) % len(ret)])
        p = (1 + np.sum(np.abs(null) >= abs(obs))) / (1 + N_SHIFT)
        rows.append(dict(factor=f, ic=obs, p=p))
    ps = np.array([r["p"] for r in rows])
    order = np.argsort(ps); m = len(ps); q = np.empty(m); prev = 1.0
    for rk in range(m - 1, -1, -1):
        i = order[rk]; q[i] = prev = min(prev, ps[i] * m / (rk + 1))
    for r, qq in zip(rows, q):
        r["q"] = float(qq)
    out[h] = rows

print(f"{'factor':16s}" + "".join(f"  h={h:<5d}" for h in HS))
for i, f in enumerate(F):
    line = f"{f:16s}"
    for h in HS:
        r = out[h][i]
        star = "*" if r["q"] < 0.05 else ("+" if r["p"] < 0.05 else " ")
        line += f" {r['ic']:+.3f}{star}  "
    print(line)
print("\n* = BH q<0.05 in that horizon family, + = raw p<0.05")
for h in HS:
    s = [abs(r["ic"]) for r in out[h] if not np.isnan(r["ic"])]
    print(f"h={h:4d}  median|IC|={np.median(s):.3f}  FDR存活={sum(1 for r in out[h] if r['q']<0.05)}")

with open(os.path.join(BASE, "data", "_horizon_probe.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
