# -*- coding: utf-8 -*-
"""R3 状态分层：长端信号在「高/低波动 × 均线上/下」四种状态下是否一致。
状态判定全部 PIT（波动中位数用 expanding，均线用滚动 200 日）。
只读，输出 data/state_strat.json
"""
import json, os, numpy as np, pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = json.load(open(os.path.join(BASE, "data", "horizon_profile.json"), encoding="utf-8"))
F = d["factors"]
df = pd.read_csv(os.path.join(BASE, "data", "factor_matrix.csv"), index_col=0, parse_dates=True)
px = df["hs300"].astype(float)
p = px.values
n = len(p)
mood = json.load(open(os.path.join(BASE, "data", "mood_data.json"), encoding="utf-8"))
cs = pd.Series({pd.Timestamp(x["date"]): float(x["score"])
                for x in mood["composite"]["history"] if x.get("score") is not None}).sort_index()
cs_d = cs.reindex(cs.index.union(df.index)).ffill().reindex(df.index)
SER = {k: df[k + "__raw"].astype(float).values for k in F if k != "composite"}
SER["composite"] = cs_d.values

r = np.diff(np.log(p), prepend=np.nan)
vol60 = pd.Series(r).rolling(60).std().values
vol_med = pd.Series(vol60).expanding(min_periods=250).median().values
hi_vol = vol60 > vol_med
ma200 = pd.Series(p).rolling(200).mean().values
up = p > ma200


def sp(a, b, mask):
    m = mask & ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 100:
        return None, int(m.sum())
    return round(float(pd.Series(a[m]).corr(pd.Series(b[m]), method="spearman")), 3), int(m.sum())


out = {}
for k, f in F.items():
    h = int(np.median(f["band"])) if f["band"] else 250
    v = SER[k].astype(float)
    fwd = np.full(n, np.nan)
    fwd[:-h] = np.log(p[h:] / p[:-h])
    cells = {}
    for lab, m in [("高波动", hi_vol), ("低波动", ~hi_vol), ("均线上", up), ("均线下", ~up)]:
        ic, cnt = sp(v, fwd, m & ~np.isnan(fwd))
        cells[lab] = dict(ic=ic, n=cnt)
    ics = [c["ic"] for c in cells.values() if c["ic"] is not None]
    if len(ics) < 3:
        verdict = "样本不足"
    else:
        pos = sum(1 for x in ics if x > 0)
        if pos in (0, len(ics)):
            verdict = "全状态一致" if min(abs(x) for x in ics) >= 0.10 else "全状态同号但弱"
        else:
            verdict = "状态依赖（方向反转）"
    spread = round(max(ics) - min(ics), 3) if len(ics) >= 2 else None
    out[k] = dict(name=f["name"], h=h, cells=cells, spread=spread, verdict=verdict)
    print(f"{f['name'][:16]:18s} h={h:4d} 高{str(cells['高波动']['ic']):>7s} 低{str(cells['低波动']['ic']):>7s} "
          f"上{str(cells['均线上']['ic']):>7s} 下{str(cells['均线下']['ic']):>7s} "
          f"极差{spread} {verdict}")

share = dict(hi_vol=round(float(np.nanmean(hi_vol)), 2), up=round(float(np.nanmean(up)), 2))
json.dump(dict(meta=dict(note="四状态 IC；状态判定 PIT；h 取该因子有效带中点，无带者取 250",
                         share=share,
                         criteria="四格同号且 min|IC|>=0.10 记全状态一致；出现反号记状态依赖"),
               factors=out),
          open(os.path.join(BASE, "data", "state_strat.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n状态占比", share)
print("wrote data/state_strat.json")
