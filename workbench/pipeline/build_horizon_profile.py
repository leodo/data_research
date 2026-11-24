# -*- coding: utf-8 -*-
"""L2 周期画像常驻模块。

职责（只读上游、只写自己的产物，不改 mood_data.json / 综合分 / 共振板）：
  1. 18 个前瞻期 × 18 个标的的秩 IC / 循环平移 p / 族内 BH / 三段一致性 / 可信度分
  2. 起效点、有效带、四档可信度
  3. R2 滚动起点（历史定带 → 后续 4 年验证）
  4. R3 状态分层（高/低波动 × 均线上/下）
  5. 输出 data/horizon_profile.json、data/rolling_origin.json、data/state_strat.json，
     以及给 mood.html 用的 data/horizon_profile.js

口径冻结于 trial_log T002（18 周期网格）与 T008（原生频率复核）。
任何口径改动必须先登记 trial log，再改本文件的 SPEC；run log 会记录指纹变化。
"""
import json, os, time
import numpy as np
import pandas as pd

from workbench.paths import ROOT, DATA as _DATA_ROOT
BASE = str(ROOT)
DATA = str(_DATA_ROOT)

SPEC = dict(
    version="L2-v1",
    horizons=[1, 2, 3, 5, 8, 10, 15, 20, 30, 40, 60, 90, 120, 180, 250, 375, 500, 750],
    n_shift=400,
    shift_span="max(h, 250)",
    fdr="BH 族内(18)",
    score="100*eff^0.8*stat^0.5*stab*samp",
    band_gate="|IC|>=0.15 且 三段同号>=2/3 且 可信度>=55",
    origins="2019-01~2023-07 半年",
    oos_window=1000,
)

FACTORS = ["rp", "dgz", "pgjj_zsy", "qschsl", "zzpgnh", "magic", "etf300_ratio",
           "margin_growth", "breadth_ma200", "erp_attrib", "below_net", "nhnl",
           "adv_ratio", "hs300_rsi", "arms", "breadth_score", "mcclellan"]
NAME = {
    "rp": "A股风险溢价", "dgz": "低估指数行业", "pgjj_zsy": "正收益偏股基金占比",
    "qschsl": "全市场换手率", "zzpgnh": "股基滚动年化收益", "magic": "神奇指标",
    "etf300_ratio": "头部300ETF占流通市值", "margin_growth": "两融余额60日增速",
    "breadth_ma200": "全A站上200日均线占比", "erp_attrib": "ERP股债归因",
    "below_net": "破净率", "nhnl": "新高新低差NH-NL",
    "adv_ratio": "上涨家数占比与动能广度", "hs300_rsi": "沪深300 RSI14",
    "arms": "阿姆氏指标TRIN", "breadth_score": "市场宽广度综合",
    "mcclellan": "麦克莱伦摆动指标", "composite": "加权综合分",
}


def log(msg):
    print(f"[L2] {msg}", flush=True)


def _rank(v):
    return pd.Series(np.asarray(v, float)).rank(method="average").values


def _corr(a, b):
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 100:
        return np.nan
    x, z = a[m] - a[m].mean(), b[m] - b[m].mean()
    d = np.sqrt((x * x).sum() * (z * z).sum())
    return float((x * z).sum() / d) if d > 0 else np.nan


def _bh(ps):
    ps = np.asarray(ps, float)
    m = len(ps)
    order = np.argsort(ps)
    q = np.empty(m)
    prev = 1.0
    for rk in range(m - 1, -1, -1):
        i = order[rk]
        prev = min(prev, ps[i] * m / (rk + 1))
        q[i] = prev
    return q


def load_series():
    f = pd.read_csv(os.path.join(DATA, "factor_matrix.csv"), index_col=0, parse_dates=True)
    px = f["hs300"].astype(float)
    mood = json.load(open(os.path.join(DATA, "mood_data.json"), encoding="utf-8"))
    cs = pd.Series({pd.Timestamp(x["date"]): float(x["score"])
                    for x in mood["composite"]["history"] if x.get("score") is not None}).sort_index()
    series = {k: f[k + "__raw"].astype(float).values for k in FACTORS}
    series["composite"] = cs.reindex(cs.index.union(f.index)).ffill().reindex(f.index).values
    return px.values, series


def profile(px, series):
    n = len(px)
    HS = SPEC["horizons"]
    rng = np.random.default_rng(20261121)
    idx = np.arange(n)
    out, results = {}, {}
    for key, raw in series.items():
        rx = _rank(raw)
        rows = []
        for h in HS:
            fwd = np.full(n, np.nan)
            fwd[:-h] = np.log(px[h:] / px[:-h])
            ry = _rank(fwd)
            ic = _corr(rx, ry)
            span = min(max(h, 250), n // 3)
            null = np.empty(SPEC["n_shift"])
            for i in range(SPEC["n_shift"]):
                k = int(rng.integers(span, n - span))
                null[i] = _corr(rx[idx], ry[(idx + k) % n])
            p = (1 + np.sum(np.abs(null) >= abs(ic))) / (1 + SPEC["n_shift"])
            nv = int(np.sum(~(np.isnan(rx) | np.isnan(ry))))
            segs = [_corr(rx[a:b], ry[a:b]) for a, b in
                    [(0, n // 3), (n // 3, 2 * n // 3), (2 * n // 3, n)]]
            cons = sum(1 for s in segs if not np.isnan(s) and np.sign(s) == np.sign(ic)) / 3.0
            rows.append(dict(h=h, ic=float(ic) if ic == ic else 0.0, p=float(p),
                             n_valid=nv, n_eff=nv / h, seg=segs, seg_cons=cons))
        for r, q in zip(rows, _bh([r["p"] for r in rows])):
            r["q"] = float(q)
            eff = min(1.0, abs(r["ic"]) / 0.35) ** 0.8
            stat = (1.0 - min(q, 1.0)) ** 0.5
            stab = 0.5 + 0.5 * r["seg_cons"]
            samp = min(1.0, (r["n_eff"] / 10.0) ** 0.5)
            r["score"] = round(100 * eff * stat * stab * samp, 1)
            r["stat_ok"] = bool(q < 0.10 and r["seg_cons"] >= 2 / 3)
            r["econ_ok"] = bool(abs(r["ic"]) >= 0.15)
        ics = np.array([abs(r["ic"]) for r in rows])
        peak_i = int(np.argmax(ics))
        ok = [i for i, r in enumerate(rows)
              if abs(r["ic"]) >= 0.15 and r["seg_cons"] >= 2 / 3 and r["score"] >= 55]
        if ok:
            best = max(ok, key=lambda i: rows[i]["score"])
            lo = hi = best
            while lo - 1 in ok:
                lo -= 1
            while hi + 1 in ok:
                hi += 1
            band = [HS[lo], HS[hi]]
            proven = any(r["q"] < 0.10 for r in rows[lo:hi + 1])
        else:
            band, proven = None, False
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
        onset = None
        for i in range(len(HS)):
            if ics[i] >= 0.15 and ics[i:].mean() >= 0.15:
                onset = HS[i]
                break
        v = pd.Series(raw).astype(float).dropna()
        chg = float((v.diff().abs() > 1e-12).mean()) if len(v) > 10 else np.nan
        out[key] = dict(name=NAME[key], rows=rows, peak_h=HS[peak_i],
                        peak_ic=rows[peak_i]["ic"], band=band,
                        band_width=(band and HS.index(band[1]) - HS.index(band[0]) + 1) or 0,
                        verdict=verdict, onset_h=onset, chg_rate=round(chg, 3),
                        monotonicity=round(float(pd.Series(HS).corr(pd.Series(ics), method="spearman")), 2),
                        score_micro=max(r["score"] for r in rows if r["h"] <= 5),
                        score_short=max(r["score"] for r in rows if 8 <= r["h"] <= 30),
                        score_mid=max(r["score"] for r in rows if 40 <= r["h"] <= 120),
                        score_long=max(r["score"] for r in rows if r["h"] >= 180),
                        peak_score=float(max(r["score"] for r in rows)))
        results[key] = out[key]
    return out


def rolling_origin_dated(px, dates, series):
    HS = SPEC["horizons"]
    n = len(px)
    FWD = {}
    for h in HS:
        a = np.full(n, np.nan)
        a[:-h] = np.log(px[h:] / px[:-h])
        FWD[h] = a

    def ic_at(x, h, lo, hi):
        seg, y = x[lo:hi], FWD[h][lo:hi]
        ok = ~(np.isnan(seg) | np.isnan(y))
        if ok.sum() < 80:
            return np.nan
        return _corr(_rank(seg[ok]), _rank(y[ok]))

    def band_of(xs):
        flag = [bool(not np.isnan(xs.get(h, np.nan)) and abs(xs[h]) >= 0.15) for h in HS]
        best, cur = [], []
        for i, f in enumerate(flag + [False]):
            if f:
                cur.append(i)
            else:
                if len(cur) > len(best):
                    best = cur
                cur = []
        return [HS[best[0]], HS[best[-1]]] if best else None

    def core_band(xs):
        vals = {h: xs[h] for h in HS if not np.isnan(xs.get(h, np.nan))}
        if not vals:
            return None
        pk_h = max(vals, key=lambda h: abs(vals[h]))
        if abs(vals[pk_h]) < 0.15:
            return None
        ok = {h for h, v in vals.items() if abs(v) >= 0.7 * abs(vals[pk_h])}
        i = HS.index(pk_h)
        lo = hi = i
        while lo - 1 >= 0 and HS[lo - 1] in ok:
            lo -= 1
        while hi + 1 < len(HS) and HS[hi + 1] in ok:
            hi += 1
        return [HS[lo], HS[hi]]

    origins = pd.date_range("2019-01-01", "2023-07-01", freq="6MS")
    FIXED = [180, 250, 375]
    W = SPEC["oos_window"]
    out = {}
    for key, raw in series.items():
        x = np.asarray(raw, float)
        det, base, core = [], [], []
        cb_list = []
        for od in origins:
            i0 = int(np.searchsorted(dates.values, np.datetime64(od)))
            if i0 < 400 or n - i0 < 300:
                continue
            xs = {h: ic_at(x, h, 0, i0) for h in HS}
            b, cb = band_of(xs), core_band(xs)
            fix = [ic_at(x, h, i0, min(i0 + W, n)) for h in FIXED]
            if not any(np.isnan(v) for v in fix):
                base.append(bool(abs(np.nanmean(fix)) >= 0.10))
            if cb:
                hs_c = [h for h in HS if cb[0] <= h <= cb[1] and not np.isnan(xs[h])]
                isc = float(np.mean([xs[h] for h in hs_c]))
                oo = [ic_at(x, h, i0, min(i0 + W, n)) for h in hs_c]
                oc = float(np.nanmean(oo)) if not all(np.isnan(oo)) else np.nan
                core.append(bool((oc == oc) and np.sign(oc) == np.sign(isc) and abs(oc) >= 0.10))
                cb_list.append(cb)
            if not b:
                det.append(dict(origin=str(od.date()), band=None))
                continue
            hs_b = [h for h in HS if b[0] <= h <= b[1] and not np.isnan(xs[h])]
            isb = float(np.mean([xs[h] for h in hs_b]))
            ob = [ic_at(x, h, i0, min(i0 + W, n)) for h in hs_b]
            oc2 = float(np.nanmean(ob)) if not all(np.isnan(ob)) else np.nan
            det.append(dict(origin=str(od.date()), band=b, is_ic=round(isb, 3),
                            oos_ic=round(oc2, 3) if oc2 == oc2 else None,
                            pass_=bool((oc2 == oc2) and np.sign(oc2) == np.sign(isb) and abs(oc2) >= 0.10)))
        found = [d for d in det if d["band"]]
        passed = [d for d in found if d.get("pass_")]
        js = []
        for a, c in zip(found, found[1:]):
            sa = set(range(a["band"][0], a["band"][1] + 1))
            sc = set(range(c["band"][0], c["band"][1] + 1))
            js.append(len(sa & sc) / max(1, len(sa | sc)))
        out[key] = dict(name=NAME[key], n_origin=len(det), n_band=len(found),
                        n_pass=len(passed),
                        pass_rate=round(len(passed) / max(1, len(found)), 2),
                        fixed_pass_rate=round(sum(base) / max(1, len(base)), 2),
                        core_pass_rate=round(sum(core) / max(1, len(core)), 2),
                        n_core=len(core),
                        core_band=cb_list[-1] if cb_list else None,
                        jaccard=round(float(np.mean(js)), 2) if js else None,
                        detail=det)
    return out


def state_strat(px, series):
    n = len(px)
    r = np.diff(np.log(px), prepend=np.nan)
    vol60 = pd.Series(r).rolling(60).std().values
    vol_med = pd.Series(vol60).expanding(min_periods=250).median().values
    ma200 = pd.Series(px).rolling(200).mean().values
    hi, up = vol60 > vol_med, px > ma200
    HS = SPEC["horizons"]
    out = {}
    for key, raw in series.items():
        x = np.asarray(raw, float)
        h = 250
        rows = profile_res[key]["rows"] if profile_res.get(key) else None
        if rows:
            b = profile_res[key].get("band")
            h = int(np.median(b)) if b else 250
        fwd = np.full(n, np.nan)
        fwd[:-h] = np.log(px[h:] / px[:-h])

        cells = {}
        for lab, m in [("高波动", hi), ("低波动", ~hi), ("均线上", up), ("均线下", ~up)]:
            mm = m & ~np.isnan(fwd) & ~np.isnan(x)
            cells[lab] = dict(ic=round(float(pd.Series(x[mm]).corr(pd.Series(fwd[mm]), method="spearman")), 3)
                              if mm.sum() >= 100 else None, n=int(mm.sum()))
        ics = [c["ic"] for c in cells.values() if c["ic"] is not None]
        if len(ics) < 3:
            verdict = "样本不足"
        elif all(v > 0 for v in ics) or all(v < 0 for v in ics):
            verdict = "全状态一致" if min(abs(v) for v in ics) >= 0.10 else "全状态同号但弱"
        else:
            verdict = "状态依赖（方向反转）"
        out[key] = dict(name=NAME[key], h=h, cells=cells,
                        spread=round(max(ics) - min(ics), 3) if len(ics) >= 2 else None,
                        verdict=verdict)
    return out


profile_res = {}


def main():
    t0 = time.time()
    log("开始：18 周期 × 18 标的")
    px, series = load_series()
    dates = pd.read_csv(os.path.join(DATA, "factor_matrix.csv"), usecols=[0]).iloc[:, 0]
    dates = pd.to_datetime(dates)

    global profile_res
    profile_res = profile(px, series)
    log(f"周期剖面完成：{len(profile_res)} 个标的")

    ro = rolling_origin_dated(px, dates, series)
    log("R2 滚动起点完成")

    ss = state_strat(px, series)
    log("R3 状态分层完成")

    json.dump(dict(meta=dict(n=len(px), horizons=SPEC["horizons"], n_shift=SPEC["n_shift"],
                             weight_note=SPEC["score"], gate=SPEC["band_gate"],
                             spec_version=SPEC["version"]),
                   factors=profile_res),
              open(os.path.join(DATA, "horizon_profile.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(dict(meta=dict(horizons=SPEC["horizons"], oos_window=SPEC["oos_window"],
                             origins="2019-01~2023-07 半年",
                             criteria="历史段|IC|>=0.15 定带；样本外 4 年均值同号且|IC|>=0.10 记为通过"),
                   factors=ro),
              open(os.path.join(DATA, "rolling_origin.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(dict(meta=dict(note="四状态 IC；状态判定 PIT；h 取该因子有效带中点，无带者取 250",
                             share=dict(hi_vol=round(float(np.nanmean(
                                 pd.Series(np.diff(np.log(px), prepend=np.nan)).rolling(60).std().values >
                                 pd.Series(np.diff(np.log(px), prepend=np.nan)).rolling(60).std()
                                 .expanding(min_periods=250).median().values)), 2),
                                 up=round(float(np.nanmean(px > pd.Series(px).rolling(200).mean().values)), 2)),
                             criteria="四格同号且 min|IC|>=0.10 记全状态一致；出现反号记状态依赖"),
                   factors=ss),
              open(os.path.join(DATA, "state_strat.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 给前端用的精简包
    brief = {}
    for k, f in profile_res.items():
        brief[k] = dict(name=f["name"], band=f["band"], onset=f["onset_h"],
                        verdict=f["verdict"], peak=f["peak_h"],
                        regime=ss[k]["verdict"], chg=f["chg_rate"],
                        s_micro=f["score_micro"], s_mid=f["score_mid"], s_long=f["score_long"])
    js = "window.HORIZON_PROFILE=" + json.dumps(brief, ensure_ascii=False) + ";\n"
    open(os.path.join(DATA, "horizon_profile.js"), "w", encoding="utf-8").write(js)
    log("已写 horizon_profile.js")

    # run log：口径指纹未变则只追加一行；变了要提示登记 trial log
    import hashlib
    sha = hashlib.sha1(json.dumps(SPEC, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    rl = os.path.join(DATA, "horizon_profile_runs.jsonl")
    prev = ""
    if os.path.exists(rl):
        with open(rl, encoding="utf-8") as fh:
            lines = [x for x in fh.read().splitlines() if x.strip()]
        if lines:
            prev = json.loads(lines[-1]).get("spec_sha1", "")
    rec = dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"), spec_sha1=sha, n=len(px),
               n_available=sum(1 for k in profile_res if k != "composite"),
               secs=round(time.time() - t0, 1),
               verdicts={v: sum(1 for f in profile_res.values() if f["verdict"] == v)
                         for v in set(f["verdict"] for f in profile_res.values())})
    with open(rl, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if prev and prev != sha:
        log(f"[警告] 口径指纹由 {prev} 变为 {sha}：请先在 trial_log 登记新口径再发布")
    log(f"完成，用时 {rec['secs']} 秒；指纹 {sha}")


if __name__ == "__main__":
    main()
