# -*- coding: utf-8 -*-
"""敏感性：若把「有效 n」按更新率折算，各因子的可信度会怎么变（只算，不改任何生产数据）。"""
import json, os
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = json.load(open(os.path.join(BASE, "data", "horizon_profile.json"), encoding="utf-8"))
F = d["factors"]
out = []
for k, f in F.items():
    if not f["band"]:
        continue
    cells = [r for r in f["rows"] if f["band"][0] <= r["h"] <= f["band"][1]]
    for r in cells:
        neff, chg = r["n_eff"], max(f["chg_rate"], 1e-3)
        old = min(1.0, (neff / 10.0) ** 0.5)
        new = min(1.0, (neff * chg / 10.0) ** 0.5)
        ratio = new / old if old > 0 else 1.0
        out.append(dict(name=f["name"], h=r["h"], chg=chg, n_eff=round(neff, 1),
                        score=r["score"], adj=round(r["score"] * ratio, 1),
                        drop=round(r["score"] - r["score"] * ratio, 1)))
for o in out:
    if o["drop"] >= 3:
        print(f"{o['name'][:16]:18s} h={o['h']:4d} 更新率{o['chg']:.2f} 有效n{o['n_eff']:5.1f} "
              f"可信度 {o['score']:5.1f} → {o['adj']:5.1f}  (−{o['drop']:.1f})")
print("\n受影响（掉分≥3）格数：", sum(1 for o in out if o["drop"] >= 3), "/", len(out))
