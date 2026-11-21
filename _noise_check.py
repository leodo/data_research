# -*- coding: utf-8 -*-
"""噪声与前提条件体检：秩 IC 对离群值本来就稳健，真正影响结论的是
(1) 阶梯/ffill 造成的重复观测  (2) 极端行情日  (3) 数值分布重尾。
只读，输出 data/noise_check.json
"""
import json, os, numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(BASE, "data", "horizon_profile.json"), encoding="utf-8"))
F = d["factors"]
df = pd.read_csv(os.path.join(BASE, "data", "factor_matrix.csv"), index_col=0, parse_dates=True)
px = df["hs300"].astype(float).values
n = len(px)
mood = json.load(open(os.path.join(BASE, "data", "mood_data.json"), encoding="utf-8"))
cs = pd.Series({pd.Timestamp(x["date"]): float(x["score"])
                for x in mood["composite"]["history"] if x.get("score") is not None}).sort_index()
cs_d = cs.reindex(cs.index.union(df.index)).ffill().reindex(df.index)
SER = {k: df[k + "__raw"].astype(float).values for k in F if k != "composite"}
SER["composite"] = cs_d.values


def sp(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 60:
        return np.nan
    return float(pd.Series(a[m]).corr(pd.Series(b[m]), method="spearman"))


out = {}
for k, f in F.items():
    h = int(np.median(f["band"])) if f["band"] else 250
    v = SER[k].astype(float)
    fwd = np.full(n, np.nan)
    fwd[:-h] = np.log(px[h:] / px[:-h])
    base = sp(v, fwd)
    chg = np.abs(np.diff(v, prepend=np.nan)) > 1e-12
    ic_chg = sp(np.where(chg, v, np.nan), fwd)
    lo, hi = np.nanpercentile(fwd, [1, 99])
    keep = np.where((fwd > lo) & (fwd < hi), v, np.nan)
    ic_ex = sp(keep, fwd)
    q1, q99 = np.nanpercentile(v, [1, 99])
    vw = np.clip(v, q1, q99)
    ic_w = sp(vw, fwd)
    out[k] = dict(name=f["name"], h=h, ic_base=round(base, 3) if base == base else None,
                  ic_chg=round(ic_chg, 3) if ic_chg == ic_chg else None,
                  ic_ex_extreme=round(ic_ex, 3) if ic_ex == ic_ex else None,
                  ic_winsor=round(ic_w, 3) if ic_w == ic_w else None,
                  chg_rate=round(float(np.nanmean(chg)), 2),
                  n_base=int(np.sum(~(np.isnan(v) | np.isnan(fwd)))),
                  n_chg=int(np.sum(~(np.isnan(np.where(chg, v, np.nan)) | np.isnan(fwd)))))
    print(f"{f['name'][:16]:18s} h={h:4d} 基准{out[k]['ic_base']:+.3f} "
          f"仅更新日{out[k]['ic_chg']:+.3f} 剔极端{out[k]['ic_ex_extreme']:+.3f} "
          f"缩尾{out[k]['ic_winsor']:+.3f}  更新率{out[k]['chg_rate']:.2f}")

json.dump(dict(meta=dict(note="同一因子、同一前瞻期（取有效带中点）下，四种处理方式的 IC 对比",
                         criteria="IC 变化 >0.05 视为该处理有实质影响"),
               factors=out),
          open(os.path.join(BASE, "data", "noise_check.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nwrote data/noise_check.json")
