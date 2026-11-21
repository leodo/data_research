# -*- coding: utf-8 -*-
"""L5 去重综合分 v2（参考口径，不改生产 mood_data.json / 综合分）。

v1 = 8 指标等权（现有口径）
v2 = 去掉 magic 与 zzpgnh 后的 6 指标等权

去重依据（T010）：
  - magic 与 rp 情绪分相关 −0.92，zzpgnh 与 rp −0.81、与 pgjj +0.81 → 三者同一维度
  - 共同窗口（2015 起 6 个长历史指标）上，去 magic+zzpgnh 后 IC500 −0.519 → −0.564，
    有效维度 2.64 → 2.84：冗余降了、信号没丢
只读 data/mood_data.json，只写 data/composite_v2.json / .js
"""
import json, os, time
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
KEEP = ["rp", "dgz", "pgjj_zsy", "qschsl", "pcr", "fx_fe"]
DROP = ["magic", "zzpgnh"]


def log(m):
    print(f"[L5] {m}", flush=True)


def main():
    t0 = time.time()
    m = json.load(open(os.path.join(DATA, "mood_data.json"), encoding="utf-8"))

    def ser(k):
        p = m["panels"][k]
        h = p.get("history") or []
        s = pd.Series({x["date"]: float(x["pct"]) for x in h if x.get("pct") is not None}).sort_index()
        return 100 - s if p.get("reverse") else s

    A = {k: ser(k) for k in m["order"]}
    df = pd.DataFrame(A)
    df.index = pd.to_datetime(df.index)
    v1 = df[list(m["order"])].mean(axis=1)
    v2 = df[KEEP].mean(axis=1)

    hs = pd.Series({r[0]: float(r[1]) for r in m["hs300_close"]}).sort_index()
    hs.index = pd.to_datetime(hs.index)
    px = hs.reindex(df.index).ffill()

    def ic(s, h):
        fwd = np.log(px.shift(-h) / px)
        j = s.dropna().index.intersection(fwd.dropna().index)
        return round(float(s.loc[j].corr(fwd.loc[j], method="spearman")), 3) if len(j) >= 200 else None

    common = v1.dropna().index.intersection(v2.dropna().index)
    hist = []
    for d in common:
        hist.append(dict(date=str(d.date()), v1=round(float(v1.loc[d]), 2), v2=round(float(v2.loc[d]), 2)))

    today = dict(date=str(common[-1].date()),
                 v1=round(float(v1.loc[common[-1]]), 2), v2=round(float(v2.loc[common[-1]]), 2),
                 gap=round(float(v2.loc[common[-1]] - v1.loc[common[-1]]), 2))
    payload = dict(
        meta=dict(generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                  keep=KEEP, drop=DROP,
                  note="v1=8 指标等权（现行口径，保持历史可比）；v2=去重口径（去 magic 与 zzpgnh），"
                       "仅作参考线，尚未替换官方综合分。",
                  corr=round(float(v1.loc[common].corr(v2.loc[common], method="spearman")), 3),
                  ic=dict(v1_250=ic(v1, 250), v1_500=ic(v1, 500),
                          v2_250=ic(v2, 250), v2_500=ic(v2, 500))),
        today=today, history=hist)
    json.dump(payload, open(os.path.join(DATA, "composite_v2.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(os.path.join(DATA, "composite_v2.js"), "w", encoding="utf-8").write(
        "window.COMPOSITE_V2=" + json.dumps(payload, ensure_ascii=False) + ";\n")
    log(f"完成 {today['date']}：v1={today['v1']} v2={today['v2']} 差 {today['gap']:+}；"
        f"相关 {payload['meta']['corr']}；用时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
