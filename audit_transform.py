# -*- coding: utf-8 -*-
"""因子变换 / 量纲漂移诊断（只读、零网络、仅 numpy+pandas）。

输出 data/transform_audit.json。只读现有产物，不接 DAG、不改任何生产口径。
所有变换均为 PIT（只使用 t 及之前的信息），前瞻收益按 T 日收盘理想化。
"""
import json, math
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
START = pd.Timestamp("2016-01-11")
H_MAIN = 60
SEGS = [("2016-2018", "2016-01-11", "2018-12-31"),
        ("2019-2021", "2019-01-01", "2021-12-31"),
        ("2022-2024", "2022-01-01", "2024-12-31"),
        ("2025-2026", "2025-01-01", "2026-12-31")]

m = json.loads((DATA / "mood_data.json").read_text(encoding="utf-8"))
hs = pd.Series({pd.Timestamp(d): float(v) for d, v in m["hs300_close"]}).sort_index()
DATES = hs.index
fwd60 = hs.shift(-H_MAIN) / hs - 1.0
R = {"meta": {"base_date": str(DATES[-1].date()), "start": str(START.date()),
              "h": H_MAIN, "n_trade_days": int(len(DATES))}}

# ---------------- 1. 收集原始值 + 现用 expanding 分位 ----------------
F = {}
for grp, src, keyfield in [("主指标", "panels", "order"),
                           ("结构", "macro_panels", "macro_order"),
                           ("顶底", "signal_panels", "signal_order")]:
    for k in m[keyfield]:
        p = m[src][k]
        h = p["history"]
        raw = pd.Series({pd.Timestamp(x["date"]): float(x["value"])
                         for x in h if x.get("value") is not None}).sort_index()
        pct = pd.Series({pd.Timestamp(x["date"]): float(x["pct"])
                         for x in h if x.get("pct") is not None}).sort_index()
        raw = raw[~raw.index.duplicated(keep="last")].replace([np.inf, -np.inf], np.nan).dropna()
        pct = pct[~pct.index.duplicated(keep="last")]
        if len(raw) < 30:
            continue
        gaps = np.diff(raw.index.values).astype("timedelta64[D]").astype(float)
        med = float(np.median(gaps))
        freq = "日" if med <= 3 else ("周" if med <= 15 else "月")
        F[k] = {"name": p["name"], "group": grp, "freq": freq, "reverse": bool(p.get("reverse")),
                "raw": raw, "pct_exp": pct, "n_obs": len(raw),
                "start": str(raw.index[0].date()), "end": str(raw.index[-1].date())}

LIMIT = {"日": 5, "周": 12, "月": 45}
DAILY = [k for k in F if F[k]["freq"] == "日" and
         F[k]["raw"].reindex(DATES).ffill(limit=5).loc[START:].notna().mean() >= 0.80]
R["meta"]["n_factors_all"] = len(F)
R["meta"]["n_daily"] = len(DAILY)
R["meta"]["excluded"] = [{"key": k, "name": F[k]["name"], "freq": F[k]["freq"]} for k in F if k not in DAILY]


# ---------------- 2. 10 种 PIT 变换 ----------------
def make_transforms(raw, pct_exp):
    t = {"raw": raw, "pct_exp": pct_exp}
    t["pct_r126"] = raw.rolling(126, min_periods=60).rank(pct=True) * 100
    t["pct_r250"] = raw.rolling(250, min_periods=60).rank(pct=True) * 100
    t["pct_r500"] = raw.rolling(500, min_periods=120).rank(pct=True) * 100
    mu = raw.rolling(250, min_periods=60).mean()
    sd = raw.rolling(250, min_periods=60).std(ddof=0).replace(0, np.nan)
    t["z_r250"] = (raw - mu) / sd
    med = raw.rolling(250, min_periods=60).median()
    iqr = (raw.rolling(250, min_periods=60).quantile(0.75)
           - raw.rolling(250, min_periods=60).quantile(0.25)).replace(0, np.nan)
    t["robust_r250"] = (raw - med) / iqr
    t["detrend_r500"] = raw - raw.rolling(500, min_periods=120).median()
    t["chg60"] = raw - raw.shift(60)
    t["logmod"] = np.sign(raw) * np.log1p(np.abs(raw))
    q = (pct_exp / 100).clip(0.005, 0.995)
    t["logit_pct"] = np.log(q / (1 - q))
    t["dual_gap"] = t["pct_r250"] - pct_exp.reindex(t["pct_r250"].index)
    return t

TRANS = ["raw", "pct_exp", "pct_r126", "pct_r250", "pct_r500", "z_r250",
         "robust_r250", "detrend_r500", "chg60", "logmod", "logit_pct", "dual_gap"]
ALIGN = {}
for k in F:
    ALIGN[k] = {n: s.reindex(DATES).ffill(limit=LIMIT[F[k]["freq"]])
                for n, s in make_transforms(F[k]["raw"], F[k]["pct_exp"]).items()}


# ---------------- 3. 基础统计工具 ----------------
def spearman(x, y):
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 30:
        return float("nan")
    a, b = x[ok], y[ok]
    ra, rb = pd.Series(a).rank().values, pd.Series(b).rank().values
    if ra.std() < 1e-9 or rb.std() < 1e-9:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def p_approx(ic, n, h):
    """重叠样本修正后的双侧显著性近似（正态），非精确检验。"""
    ne = max(4, n / h)
    if not np.isfinite(ic) or abs(ic) >= 1:
        return 1.0
    t = abs(ic) * math.sqrt(ne - 2) / math.sqrt(max(1e-9, 1 - ic * ic))
    return min(1.0, math.erfc(t / math.sqrt(2)))


def ks_stat(a, b):
    a, b = np.sort(a[np.isfinite(a)]), np.sort(b[np.isfinite(b)])
    if len(a) < 20 or len(b) < 20:
        return float("nan")
    allv = np.concatenate([a, b])
    ca = np.searchsorted(a, allv, side="right") / len(a)
    cb = np.searchsorted(b, allv, side="right") / len(b)
    return float(np.max(np.abs(ca - cb)))


def year_ks(s):
    """相邻年份分布 KS 距离的均值；越大表示跨年量纲/分布漂移越严重。"""
    yrs = sorted(set(s.index.year))
    yrs = [y for y in yrs if y >= 2016]
    vals = [ks_stat(s[s.index.year == y].to_numpy(float),
                    s[s.index.year == y + 1].to_numpy(float))
            for y in yrs[:-1] if (y + 1) in yrs]
    vals = [v for v in vals if np.isfinite(v)]
    return round(float(np.mean(vals)), 3) if vals else None, len(vals)


def year_medians(s):
    out = []
    for y in sorted(set(s.index.year)):
        if y < 2016:
            continue
        v = s[s.index.year == y].to_numpy(float)
        v = v[np.isfinite(v)]
        if len(v) >= 30:
            out.append((y, round(float(np.median(v)), 3)))
    return out


# ---------------- 4. 分布漂移实证 ----------------
drift = []
for k in DAILY:
    # 统一用「对齐到交易日历后的日频序列」算 KS，与 evaluate() 同口径
    raw = ALIGN[k]["raw"].dropna()
    pe = ALIGN[k]["pct_exp"].dropna()
    r250 = ALIGN[k]["pct_r250"].dropna()
    ks_raw, npair = year_ks(raw)
    ks_exp, _ = year_ks(pe)
    ks_r250, _ = year_ks(r250)
    rsub = raw.loc[START:]
    trend_rho = spearman(rsub.to_numpy(float), np.arange(len(rsub), dtype=float))
    meds = year_medians(raw)
    # 每年原始值中位数所对应的 expanding 分位（用当年观测的 pct 中位数近似）
    pmeds = dict(year_medians(pe))
    path = [{"year": y, "raw_med": v, "pct_med": pmeds.get(y)} for y, v in meds]
    span_pct = (round(max([p["pct_med"] for p in path if p["pct_med"] is not None]) -
                      min([p["pct_med"] for p in path if p["pct_med"] is not None]), 1)
                if sum(p["pct_med"] is not None for p in path) >= 3 else None)
    drift.append({"key": k, "name": F[k]["name"], "n": len(raw), "start": F[k]["start"],
                  "ks_raw": ks_raw, "ks_pct_exp": ks_exp, "ks_pct_r250": ks_r250,
                  "trend_rho": round(float(trend_rho), 3),
                  "n_year_pairs": npair, "path": path, "pct_med_span": span_pct,
                  "year_mean_sd": round(float(np.std([v for _, v in meds], ddof=0)), 3) if meds else None})
drift.sort(key=lambda d: -(d["ks_raw"] or 0))
R["drift"] = drift

# ---------------- 5. 相关性判据验证 ----------------
inv = []
for k in DAILY[:8] + DAILY[-4:]:
    a = ALIGN[k]["raw"].loc[START:].to_numpy(float)
    b = ALIGN[k]["logmod"].loc[START:].to_numpy(float)
    c = ALIGN[k]["pct_exp"].loc[START:].to_numpy(float)
    d = ALIGN[k]["logit_pct"].loc[START:].to_numpy(float)
    y = fwd60.loc[START:].to_numpy(float)
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(c) & np.isfinite(d) & np.isfinite(y)
    a, b, c, d, y = a[ok], b[ok], c[ok], d[ok], y[ok]
    if len(a) < 100:
        continue
    inv.append({"key": k, "name": F[k]["name"],
                "rho_raw_logmod": round(spearman(a, b), 6),
                "rho_pct_logit": round(spearman(c, d), 6),
                "rho_raw_pctexp": round(spearman(a, c), 4),
                "ic_raw": round(spearman(a, y), 4),
                "ic_logmod": round(spearman(b, y), 4),
                "ic_pctexp": round(spearman(c, y), 4),
                "ic_logit": round(spearman(d, y), 4),
                "pearson_raw": round(float(np.corrcoef(a, y)[0, 1]), 4),
                "pearson_logmod": round(float(np.corrcoef(b, y)[0, 1]), 4)})
R["invariance"] = inv

# ---------------- 6. 统一口径评价：IC / 分年稳定性 / 阈值可移植 / 分层 ----------------
def evaluate(s, h=H_MAIN):
    x = s.loc[START:].to_numpy(float)
    y = fwd60.loc[START:].to_numpy(float)
    ok = np.isfinite(x) & np.isfinite(y)
    xx, yy = x[ok], y[ok]
    if len(xx) < 200:
        return None
    ic = spearman(xx, yy)
    p = p_approx(ic, len(xx), h)
    # 分段 IC（按日期区间，不是按变换值切）
    seg_ic = []
    idx = s.loc[START:].index[ok]
    for label, a, b in SEGS:
        mask = (idx >= pd.Timestamp(a)) & (idx <= pd.Timestamp(b))
        if mask.sum() < 80:
            seg_ic.append({"seg": label, "ic": None, "n": int(mask.sum())})
        else:
            seg_ic.append({"seg": label, "ic": round(spearman(xx[mask], yy[mask]), 3),
                           "n": int(mask.sum())})
    vals = [z["ic"] for z in seg_ic if z["ic"] is not None]
    sign_all = bool(len(vals) >= 3 and (all(v > 0 for v in vals) or all(v < 0 for v in vals)))
    consist = round(float(np.mean([1 if np.sign(v) == np.sign(ic) else 0 for v in vals])) * 100, 1) if vals else None
    # 阈值触发率：raw 用「全样本 80 分位」固定阈值；分位类用 80（描述性，非样本外证据）
    thr = float(np.nanpercentile(xx, 80))
    rate = []
    for label, a, b in SEGS:
        mask = (idx >= pd.Timestamp(a)) & (idx <= pd.Timestamp(b))
        if mask.sum() < 80:
            continue
        rate.append(round(float(np.mean(xx[mask] > thr)) * 100, 1))
    trig_sd = round(float(np.std(rate, ddof=0)), 1) if len(rate) >= 3 else None
    # 分层单调（全样本分位切档，描述性对比，所有变换承受同一口径）
    edges = np.nanpercentile(xx, [20, 40, 60, 80])
    bins = np.digitize(xx, edges)
    qret = [round(float(np.mean(yy[bins == j]) * 100), 2) if (bins == j).sum() >= 30 else None
            for j in range(5)]
    valid = [(j, v) for j, v in enumerate(qret) if v is not None]
    mono = (round(float(np.corrcoef(pd.Series([v for _, v in valid]).rank().values,
                                    np.array([j for j, _ in valid], float))[0, 1]), 3)
            if len(valid) >= 3 else None)
    spread = (round(qret[4] - qret[0], 2) if qret[0] is not None and qret[4] is not None else None)
    ks, _ = year_ks(s.reindex(s.index).dropna())
    # 年内去均值 IC：剥离「跨年水平共同趋势」，只剩年内择时成分
    dfw = pd.DataFrame({"x": s.loc[START:], "y": fwd60.loc[START:]}).dropna()
    g = dfw.groupby(dfw.index.year)
    xw = (dfw["x"] - g["x"].transform("mean")).to_numpy(float)
    yw = (dfw["y"] - g["y"].transform("mean")).to_numpy(float)
    ic_wy = spearman(xw, yw)
    return {"n": int(len(xx)), "ic": round(ic, 4), "p": round(p, 4),
            "ic_wy": round(ic_wy, 4), "ic_wy_delta": round(abs(ic_wy) - abs(ic), 4),
            "ic_wy_signflip": bool(np.sign(ic_wy) != np.sign(ic)),
            "seg_ic": seg_ic, "sign_all": sign_all, "sign_consist": consist,
            "ic_seg_sd": round(float(np.std(vals, ddof=0)), 3) if len(vals) >= 3 else None,
            "trigger_rates": rate, "trigger_sd": trig_sd, "ks": ks,
            "q_ret": qret, "mono": mono, "q_spread": spread}


comp = []
for k in DAILY:
    for tn in TRANS:
        e = evaluate(ALIGN[k][tn])
        if e:
            comp.append({"key": k, "name": F[k]["name"], "t": tn, **e})
R["comparison"] = comp

# 按变换汇总
tsum = []
for tn in TRANS:
    rows = [c for c in comp if c["t"] == tn]
    tsum.append({
        "t": tn,
        "n": len(rows),
        "abs_ic": round(float(np.mean([abs(c["ic"]) for c in rows])), 4),
        "abs_ic_wy": round(float(np.mean([abs(c["ic_wy"]) for c in rows])), 4),
        "ic_wy_delta": round(float(np.mean([c["ic_wy_delta"] for c in rows])), 4),
        "signflip_rate": round(float(np.mean([c["ic_wy_signflip"] for c in rows])) * 100, 1),
        "sign_all_rate": round(float(np.mean([c["sign_all"] for c in rows])) * 100, 1),
        "sign_consist": round(float(np.nanmean([c["sign_consist"] for c in rows])), 1),
        "ic_seg_sd": round(float(np.nanmean([c["ic_seg_sd"] for c in rows])), 3),
        "ks": round(float(np.nanmean([c["ks"] for c in rows])), 3),
        "trigger_sd": round(float(np.nanmean([c["trigger_sd"] for c in rows])), 1),
        "abs_mono": round(float(np.nanmean([abs(c["mono"]) for c in rows])), 3),
        "n_sig": int(sum(c["p"] < 0.05 for c in rows)),
    })
R["transform_summary"] = tsum

# ---------------- 7. walk-forward：样本内挑变换是否可靠 ----------------
TRAIN = ("2016-01-11", "2021-12-31")
TEST = ("2022-01-01", "2026-12-31")


def seg_ic(s, a, b):
    x = s.loc[pd.Timestamp(a):pd.Timestamp(b)].to_numpy(float)
    y = fwd60.loc[pd.Timestamp(a):pd.Timestamp(b)].to_numpy(float)
    ok = np.isfinite(x) & np.isfinite(y)
    return spearman(x[ok], y[ok]) if ok.sum() >= 150 else None


wf = []
for k in DAILY:
    tr = {tn: seg_ic(ALIGN[k][tn], *TRAIN) for tn in TRANS}
    te = {tn: seg_ic(ALIGN[k][tn], *TEST) for tn in TRANS}
    tr = {t: v for t, v in tr.items() if v is not None}
    te = {t: v for t, v in te.items() if v is not None}
    if len(tr) < 6 or len(te) < 6:
        continue
    pick = max(tr, key=lambda t: abs(tr[t]))
    rank_te = sorted(te, key=lambda t: -abs(te[t])).index(pick) + 1 if pick in te else None
    wf.append({"key": k, "name": F[k]["name"], "pick": pick,
               "train_ic": round(tr[pick], 3),
               "test_ic": round(te[pick], 3) if pick in te else None,
               "test_rank": rank_te, "n_transforms": len(te),
               "sign_flip": bool(pick in te and np.sign(tr[pick]) != np.sign(te[pick])),
               "best_test": max(te, key=lambda t: abs(te[t]))})
R["walkforward"] = {
    "train": TRAIN, "test": TEST, "rows": wf,
    "avg_test_rank": round(float(np.mean([r["test_rank"] for r in wf if r["test_rank"]])), 2),
    "random_rank": round((len(TRANS) + 1) / 2, 2),
    "sign_flip_rate": round(float(np.mean([r["sign_flip"] for r in wf])) * 100, 1),
    "same_pick_rate": round(float(np.mean([r["pick"] == r["best_test"] for r in wf])) * 100, 1),
}

# ---------------- 7b. 因子间结构：量纲污染对相关矩阵 / PCA 的影响 ----------------
Xraw = pd.DataFrame({k: ALIGN[k]["raw"].loc[START:] for k in DAILY})
Xpct = pd.DataFrame({k: ALIGN[k]["pct_exp"].loc[START:] for k in DAILY})
Xr250 = pd.DataFrame({k: ALIGN[k]["pct_r250"].loc[START:] for k in DAILY})
NAMES = [F[k]["name"] for k in DAILY]


def cmat(X, method):
    return X.corr(method=method, min_periods=250).to_numpy(float)


def pca_cum(C, k=3):
    Cm = np.nan_to_num(np.array(C, copy=True), nan=0.0)
    np.fill_diagonal(Cm, 1.0)
    ev = np.clip(np.linalg.eigvalsh(Cm)[::-1], 0, None)
    return round(float(ev[:k].sum() / ev.sum() * 100), 1)


def offdiag(C):
    iu = np.triu_indices(len(C), 1)
    v = C[iu]
    v = v[np.isfinite(v)]
    return round(float(np.mean(np.abs(v))), 3), round(float(np.max(np.abs(v))), 3)


C_raw_p, C_raw_s, C_pct_p, C_r250_p = (cmat(Xraw, "pearson"), cmat(Xraw, "spearman"),
                                       cmat(Xpct, "pearson"), cmat(Xr250, "pearson"))
diff = np.abs(C_raw_p - C_pct_p)
iu = np.triu_indices(len(DAILY), 1)
pairs = sorted([(round(float(diff[i, j]), 3), NAMES[i], NAMES[j],
                 round(float(C_raw_p[i, j]), 3), round(float(C_pct_p[i, j]), 3))
                for i, j in zip(*iu) if np.isfinite(diff[i, j])], reverse=True)[:6]
R["structure"] = {
    "variants": [
        {"label": "raw · Pearson", "avg_abs_r": offdiag(C_raw_p)[0], "max_abs_r": offdiag(C_raw_p)[1], "pca3": pca_cum(C_raw_p)},
        {"label": "raw · Spearman", "avg_abs_r": offdiag(C_raw_s)[0], "max_abs_r": offdiag(C_raw_s)[1], "pca3": pca_cum(C_raw_s)},
        {"label": "expanding pct · Pearson", "avg_abs_r": offdiag(C_pct_p)[0], "max_abs_r": offdiag(C_pct_p)[1], "pca3": pca_cum(C_pct_p)},
        {"label": "rolling250 pct · Pearson", "avg_abs_r": offdiag(C_r250_p)[0], "max_abs_r": offdiag(C_r250_p)[1], "pca3": pca_cum(C_r250_p)},
    ],
    "largest_pairs": [{"delta": p[0], "a": p[1], "b": p[2], "raw_r": p[3], "pct_r": p[4]} for p in pairs],
}

# ---------------- 7c. 按漂移幅度分组：分位是否只在高漂移因子上有增益 ----------------
high = [d["key"] for d in drift[:8]]
low = [d["key"] for d in drift[-9:]]
grp_rows = []
for label, keys in [("高漂移组(KS前8)", high), ("低漂移组(KS后9)", low)]:
    for tn in ["raw", "pct_exp", "pct_r126", "pct_r250", "chg60", "robust_r250"]:
        rows = [c for c in comp if c["key"] in keys and c["t"] == tn]
        if not rows:
            continue
        grp_rows.append({"group": label, "t": tn, "n": len(rows),
                         "abs_ic": round(float(np.mean([abs(c["ic"]) for c in rows])), 4),
         "abs_ic_wy": round(float(np.mean([abs(c["ic_wy"]) for c in rows])), 4),
         "ic_wy_delta": round(float(np.mean([c["ic_wy_delta"] for c in rows])), 4),
         "signflip_rate": round(float(np.mean([c["ic_wy_signflip"] for c in rows])) * 100, 1),
                         "signed_ic": round(float(np.mean([c["ic"] for c in rows])), 4),
                         "sign_consist": round(float(np.nanmean([c["sign_consist"] for c in rows])), 1),
                         "ks": round(float(np.nanmean([c["ks"] for c in rows])), 3),
                         "trigger_sd": round(float(np.nanmean([c["trigger_sd"] for c in rows])), 1),
                         "abs_mono": round(float(np.nanmean([abs(c["mono"]) for c in rows])), 3)})
R["by_drift_group"] = grp_rows

# ---------------- 7d. 代表因子逐年表：分位值自己也在漂移 ----------------
REP = ["rp", "etf300_ratio", "below_net", "hs300_rsi", "qschsl"]
rep_rows = []
for k in REP:
    if k not in ALIGN:
        continue
    raw, pe, r250 = ALIGN[k]["raw"], ALIGN[k]["pct_exp"], ALIGN[k]["pct_r250"]
    thr = float(raw.loc[START:].quantile(0.8))
    for y in sorted(set(raw.loc[START:].index.year)):
        sl = raw.index.year == y
        rv = raw[sl].to_numpy(float); pv = pe[sl].to_numpy(float); qv = r250[sl].to_numpy(float)
        rv, pv, qv = rv[np.isfinite(rv)], pv[np.isfinite(pv)], qv[np.isfinite(qv)]
        if len(rv) < 60:
            continue
        rep_rows.append({"key": k, "name": F[k]["name"], "year": int(y), "n": len(rv),
                         "raw_med": round(float(np.median(rv)), 3),
                         "pct_exp_med": round(float(np.median(pv)), 1) if len(pv) else None,
                         "pct_r250_med": round(float(np.median(qv)), 1) if len(qv) else None,
                         "trigger_raw": round(float(np.mean(rv > thr)) * 100, 1),
                         "trigger_pct": round(float(np.mean(pv > 80)) * 100, 1) if len(pv) else None})
R["year_path"] = rep_rows

# ---------------- 7e. 多重检验：170 个评价单元的 BH-FDR ----------------
ps = np.array([c["p"] for c in comp])
o = np.argsort(ps); q = np.empty_like(ps)
for rank, i in enumerate(o, 1):
    q[i] = min(1.0, ps[i] * len(ps) / rank)
srt = np.minimum.accumulate(q[o][::-1])[::-1]
q[o] = srt
R["multi_test"] = {"n_tests": int(len(ps)), "raw_sig": int((ps < 0.05).sum()),
                   "fdr_sig": int((q < 0.05).sum()),
                   "min_q": round(float(q.min()), 4)}

# ---------------- 7f. 按经济含义分类的处理建议（实证 + 口径判断） ----------------
CLASS = {
    "rp": "比率·有理论锚", "dgz": "计数·趋势", "fx_fe": "金额·规模", "pgjj_zsy": "占比·bounded",
    "qschsl": "比率·体制切换", "pcr": "比率·bounded", "zzpgnh": "收益率·均值回复",
    "magic": "复合价差·体制切换", "etf300_ratio": "占比·结构趋势", "margin_growth": "增速·已去量纲",
    "breadth_ma200": "占比·bounded", "a_pb": "估值·有理论锚", "erp_attrib": "比率·有理论锚",
    "fund_position": "占比·bounded", "m1m2": "宏观·月频", "repurchase": "金额·规模",
    "holder_change": "金额·规模", "below_net": "占比·结构趋势", "nhnl": "占比·bounded",
    "adv_ratio": "占比·bounded", "hs300_rsi": "技术指标·已 0-100", "arms": "比率·厚尾",
    "breadth_score": "复合分·bounded", "mcclellan": "摆动量·均值回复",
}
def recommend(kind, ks):
    if "金额" in kind:
        b = "先相对化（除以流通市值 / 成交额），再算同比与分位"
    elif "0-100" in kind or "bounded" in kind:
        b = "已是 bounded，直接用现用 pct_exp，不做二次分位"
    elif "锚" in kind:
        b = "保留 raw 水平（有经济含义）+ 并列 rolling250 分位，绝不替换"
    elif "趋势" in kind:
        b = "双尺度：expanding 长期位置 + rolling250 边际，禁用单一 expanding"
    else:
        b = "rolling250 分位为主 + 保留 raw 作对照"
    if ks is not None and ks >= 0.5:
        b += "；高漂移，必须并列年内去均值 IC 复核"
    elif ks is not None and ks < 0.3:
        b += "；低漂移，变换增益有限，可不改"
    return b


R["classification"] = []
for k in DAILY:
    kind = CLASS.get(k, "未分类")
    ks = next((d["ks_raw"] for d in drift if d["key"] == k), None)
    R["classification"].append({
        "key": k, "name": F[k]["name"], "kind": kind, "ks_raw": ks,
        "priority": "高" if (ks is not None and ks >= 0.5) else ("中" if (ks is not None and ks >= 0.3) else "低"),
        "recommend": recommend(kind, ks)})

# ---------------- 8. 摘要 ----------------
R["summary"] = {
    "n_factors": len(DAILY), "n_transforms": len(TRANS), "n_eval": len(comp),
    "ks_raw_mean": round(float(np.nanmean([d["ks_raw"] for d in drift])), 3),
    "ks_exp_mean": round(float(np.nanmean([d["ks_pct_exp"] for d in drift])), 3),
    "ks_r250_mean": round(float(np.nanmean([d["ks_pct_r250"] for d in drift])), 3),
    "top_drift": [{"name": d["name"], "ks_raw": d["ks_raw"], "ks_exp": d["ks_pct_exp"],
                   "pct_med_span": d["pct_med_span"]} for d in drift[:6]],
    "spearman_invariant_maxdiff": round(max(abs(z["ic_raw"] - z["ic_logmod"]) for z in inv), 8),
    "pearson_changed_maxdiff": round(max(abs(z["pearson_raw"] - z["pearson_logmod"]) for z in inv), 4),
    "structure_max_delta": R["structure"]["largest_pairs"][0],
    "min_q": R["multi_test"]["min_q"],
    "wf": R["walkforward"]["avg_test_rank"],
    "wf_same_pick": R["walkforward"]["same_pick_rate"],
}
(DATA / "transform_audit.json").write_text(
    json.dumps(R, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")

print("=== 变换诊断完成 ===")
print(f"因子 {len(F)} / 日频入模 {len(DAILY)} / 变换 {len(TRANS)} / 评价单元 {len(comp)}")
print(f"跨年 KS 均值: raw={R['summary']['ks_raw_mean']}  pct_exp={R['summary']['ks_exp_mean']}  pct_r250={R['summary']['ks_r250_mean']}")
print("漂移 Top6:")
for d in drift[:6]:
    print(f"  {d['name'][:16]:18s} KS_raw={d['ks_raw']} KS_exp={d['ks_pct_exp']} 年度pct中位跨度={d['pct_med_span']}")
print("单调变换 Spearman 不变性最大差 =", R["summary"]["spearman_invariant_maxdiff"],
      "| Pearson 最大变化 =", R["summary"]["pearson_changed_maxdiff"])
print("变换汇总 (按 |IC| 排序):")
for t in sorted(tsum, key=lambda z: -z["abs_ic"]):
    print(f"  {t['t']:14s} |IC|={t['abs_ic']:.4f} 年内|IC|={t['abs_ic_wy']:.4f} Δ={t['ic_wy_delta']:+.4f} 符号翻转={t['signflip_rate']:.1f}% "
          f"全段同号={t['sign_all_rate']}% 符号一致={t['sign_consist']}% "
          f"IC段间SD={t['ic_seg_sd']} KS={t['ks']} 触发率SD={t['trigger_sd']} |mono|={t['abs_mono']} 显著数={t['n_sig']}")
print("年内去均值 IC（按漂移组）:")
for g in R["by_drift_group"]:
    print(f"  {g['group']:16s} {g['t']:14s} |IC|={g['abs_ic']:.4f} → 年内{g['abs_ic_wy']:.4f} Δ={g['ic_wy_delta']:+.4f} 翻转={g['signflip_rate']:.1f}% 有符号IC={g['signed_ic']:+.4f}")
print("walk-forward:", {k: v for k, v in R["walkforward"].items() if k != "rows"})
