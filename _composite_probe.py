# -*- coding: utf-8 -*-
"""只读探针 3：生产用的 8 指标等权综合分，在各前瞻期上的预测力 + 回撤维度。"""
import json, os, numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
mood = json.load(open(os.path.join(BASE, "data", "mood_data.json"), encoding="utf-8"))
hist = mood["composite"]["history"]
cs = pd.Series({pd.Timestamp(x["date"]): float(x["score"]) for x in hist
                if x.get("score") is not None}).sort_index()
print("composite 点数", len(cs), cs.index.min().date(), "~", cs.index.max().date())
print("当前", mood["composite"]["score"], mood["composite"].get("state"))

F = pd.read_csv(os.path.join(BASE, "data", "factor_matrix.csv"), index_col=0,
                parse_dates=True)
px = F["hs300"].astype(float)
# 对齐到日频：周频点用 ffill 到两个日期之间（仅诊断，且只用历史值）
cs_d = cs.reindex(cs.index.union(px.index)).ffill().reindex(px.index)

N_SHIFT = 400
RNG = np.random.default_rng(11)


def rank01(s):
    return pd.Series(s).rank(method="average").values.astype(float)


def corr(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 100:
        return np.nan
    x, z = a[m] - a[m].mean(), b[m] - b[m].mean()
    d = np.sqrt((x * x).sum() * (z * z).sum())
    return float((x * z).sum() / d) if d > 0 else np.nan


rows = []
for h in [20, 60, 120, 250, 500]:
    fwd = np.full(len(px), np.nan)
    fwd[:-h] = np.log(px.values[h:] / px.values[:-h])
    # 未来 h 日内最大回撤
    dd = np.full(len(px), np.nan)
    v = px.values
    for i in range(len(v) - h):
        w = v[i:i + h + 1]
        dd[i] = (w.min() / v[i] - 1) * 100
    a = rank01(cs_d.values)
    b = rank01(fwd)
    obs = corr(a, b)
    null = np.empty(N_SHIFT)
    idx = np.arange(len(a))
    for k in range(N_SHIFT):
        sh = int(RNG.integers(60, len(a) - 60))
        null[k] = corr(a[idx], b[(idx + sh) % len(a)])
    p = (1 + np.sum(np.abs(null) >= abs(obs))) / (1 + N_SHIFT)
    dd_obs = corr(a, rank01(dd))
    rows.append(dict(h=h, ic=obs, p=p, dd_corr=dd_obs, n=int(np.sum(~np.isnan(b)))))

for r in rows:
    print(f"h={r['h']:4d}  IC={r['ic']:+.3f}  p={r['p']:.3f}   "
          f"corr(score, 未来最大回撤)={r['dd_corr']:+.3f}  n={r['n']}")

# 分位分组：未来 250 日收益 / 回撤
h = 250
fwd = np.full(len(px), np.nan)
fwd[:-h] = np.log(px.values[h:] / px.values[:-h])
dd = np.full(len(px), np.nan)
v = px.values
for i in range(len(v) - h):
    w = v[i:i + h + 1]
    dd[i] = (w.min() / v[i] - 1) * 100
df = pd.DataFrame({"score": cs_d.values, "fwd": fwd, "dd": dd}).dropna()
df["bucket"] = pd.cut(df["score"], [0, 20, 35, 50, 65, 80, 100])
g = df.groupby("bucket", observed=True).agg(n=("fwd", "size"),
                                           fwd_ann=("fwd", lambda s: (np.exp(s.mean()) - 1) * 100),
                                           dd_mean=("dd", "mean"),
                                           win=("fwd", lambda s: (s > 0).mean() * 100))
print("\n按综合分分组的未来 250 日：")
print(g.round(1).to_string())
