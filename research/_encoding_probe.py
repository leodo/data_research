# -*- coding: utf-8 -*-
"""只读探针：同一批因子，换编码方式（原始水平 vs 滚动标准化）后 IC 是否复活。
不写生产文件，输出 data/_encoding_probe.json。
"""
import json, numpy as np, pandas as pd, os
from itertools import combinations

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTORS = ["rp","dgz","pgjj_zsy","qschsl","zzpgnh","magic","etf300_ratio",
           "margin_growth","breadth_ma200","erp_attrib","below_net","nhnl",
           "adv_ratio","hs300_rsi","arms","breadth_score","mcclellan"]
ENC = ["raw","pct_exp","pct_r250","z_r250","robust_r250","detrend_r500","chg60"]
H = 60
N_SHIFT = 500
RNG = np.random.default_rng(20261121)

df = pd.read_csv(os.path.join(BASE,"data","factor_matrix.csv"), index_col=0)
ret = df["hs300"].astype(float)
fwd = np.log(ret.shift(-H) / ret)
y = fwd.values.astype(float)

def rank01(x):
    s = pd.Series(x)
    r = s.rank(method="average").values.astype(float)
    r[np.isnan(x)] = np.nan
    return r

def corr_pairs(a, b):
    """Pearson on pairwise-valid entries."""
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 300: return np.nan
    x, z = a[m], b[m]
    x = x - x.mean(); z = z - z.mean()
    d = np.sqrt((x*x).sum() * (z*z).sum())
    return float((x*z).sum()/d) if d > 0 else np.nan

ry = rank01(y)
results = {}
for enc in ENC:
    rows = []
    for f in FACTORS:
        col = df[f + "__" + enc].values.astype(float)
        rx = rank01(col)
        obs = corr_pairs(rx, ry)
        null = np.empty(N_SHIFT)
        idx = np.arange(len(rx))
        for i in range(N_SHIFT):
            k = int(RNG.integers(250, len(rx)-250))
            null[i] = corr_pairs(rx[idx], ry[(idx + k) % len(rx)])
        p = (1 + np.sum(np.abs(null) >= abs(obs))) / (1 + N_SHIFT)
        rows.append(dict(factor=f, ic=obs, p=p,
                         n=int(np.sum(~(np.isnan(rx) | np.isnan(ry))))))
    # BH-FDR within encoding
    ps = np.array([r["p"] for r in rows])
    order = np.argsort(ps); m = len(ps)
    q = np.empty(m)
    prev = 1.0
    for rank in range(m-1, -1, -1):
        i = order[rank]
        val = min(prev, ps[i]*m/(rank+1))
        q[i] = val; prev = val
    for r, qq in zip(rows, q): r["q"] = float(qq)
    results[enc] = rows

# 冗余结构：z_r250 上 17x17 相关
mat = df[[f+"__z_r250" for f in FACTORS]].astype(float).copy()
mat.columns = FACTORS
C = mat.corr(method="spearman")
pairs_hi = [(a,b,float(C.loc[a,b])) for a,b in combinations(FACTORS,2)
            if abs(C.loc[a,b]) >= 0.8]
# PCA on z-scores
Z = (mat - mat.mean())/mat.std()
Z = Z.dropna()
ev = np.linalg.eigvalsh(np.cov(Z.values.T))[::-1]
evr = ev/ev.sum()
# 重复列检查
dup = {}
for a, b in combinations(FACTORS, 2):
    d = np.nanmax(np.abs(df[a+"__raw"].values - df[b+"__raw"].values))
    if d < 1e-9: dup[f"{a}|{b}"] = "raw 完全相同"

out = dict(meta=dict(n=len(df), h=H, n_shift=N_SHIFT, encodings=ENC),
           results=results,
           redundancy=dict(n_pairs_absrho_ge_08=len(pairs_hi),
                           pairs=pairs_hi[:40],
                           pca_evr=[float(x) for x in evr[:8]],
                           pca_cum5=float(evr[:5].sum()),
                           identical=dup))
with open(os.path.join(BASE,"data","_encoding_probe.json"),"w",encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)

print(f"{'encoding':14s} {'med|IC|':>8s} {'sig p<.05':>9s} {'FDR':>4s} {'min p':>7s}")
for enc in ENC:
    rows = results[enc]
    mic = np.median([abs(r["ic"]) for r in rows if not np.isnan(r["ic"])])
    ns = sum(1 for r in rows if r["p"] < 0.05)
    nf = sum(1 for r in rows if r["q"] < 0.05)
    print(f"{enc:14s} {mic:8.4f} {ns:9d} {nf:4d} {min(r['p'] for r in rows):7.3f}")
print("\n-- 每因子 raw -> z_r250 --")
for a, b in zip(results["raw"], results["z_r250"]):
    print(f"{a['factor']:16s} raw IC={a['ic']:+.3f} p={a['p']:.3f}   z250 IC={b['ic']:+.3f} p={b['p']:.4f} q={b['q']:.3f}")
print("\n冗余: |rho|>=0.8 对数 =", len(pairs_hi), " PCA 前5解释 :", [round(float(x),3) for x in evr[:5]])
print("完全相同列:", dup)
for a,b,r_ in sorted(pairs_hi, key=lambda t:-abs(t[2]))[:12]:
    print(f"  {a:14s} ~ {b:14s} rho={r_:+.2f}")
