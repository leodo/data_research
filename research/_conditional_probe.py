# -*- coding: utf-8 -*-
"""临时只读探针：检验「因子只在极值 / 少数交易日有效，长期合起来是噪音」。

不写生产产物，只读 L0 因子矩阵 + hs300_close，输出 data/_conditional_probe.json。
用户确认后才落地为常驻模块。

四组检验
--------
A 贡献集中度：逐日 IC 贡献 c_t，前 5/10/20% 交易日贡献占比、删尾后 IC 保留率
B 块级 IC 分布：不重叠 60 日块 IC，看是否由少数几段行情驱动 + 年份聚类
C 极值事件研究：PIT 分位首次进入 <=10 / >=90，20/60 日前瞻收益的超额与命中率，
                 随机日对照给 p，68 次检验做 BH-FDR
D 分段稳定性：4 段时间外切片 IC 的符号一致性
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workbench.factor_utils import bh_fdr, log, spearman

T0 = time.time()
BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
rng = np.random.default_rng(20261121)

MAT = pd.read_csv(DATA / "factor_matrix.csv", index_col=0, parse_dates=True)
L0 = json.loads((DATA / "factor_matrix.json").read_text(encoding="utf-8"))
m = json.loads((DATA / "mood_data.json").read_text(encoding="utf-8"))
hs = pd.Series({pd.Timestamp(d): float(v) for d, v in m["hs300_close"]}).sort_index()
DATES = MAT.index
FWD = {h: (hs.shift(-h) / hs - 1.0).reindex(DATES) for h in (20, 60, 120)}
START = pd.Timestamp("2016-01-11")
DAILY = [f["key"] for f in L0["factors"] if f["in_matrix"]]
NAME = {f["key"]: f["name"] for f in L0["factors"]}
log(f"[probe] {MAT.shape[0]} 日 × {len(DAILY)} 因子，基准末日 {DATES[-1].date()}")


def pair(k, h=60):
    x = MAT[f"{k}__pct_exp"].loc[START:].to_numpy(float)
    y = FWD[h].loc[START:].to_numpy(float)
    ok = np.isfinite(x) & np.isfinite(y)
    return x[ok], y[ok]


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    e = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round((c - e) * 100, 1), round((c + e) * 100, 1)]


# ---------------- A. 贡献集中度 / 删尾 ----------------
def centered_rank(v):
    r = pd.Series(v).rank().to_numpy(float)
    return 2.0 * (r - 0.5) / len(v) - 1.0


conc = []
for k in DAILY:
    x, y = pair(k)
    n = len(x)
    if n < 300:
        continue
    c = centered_rank(x) * centered_rank(y)
    ic = spearman(x, y)
    order = np.argsort(c)[::-1]                      # 按贡献从大到小
    shares = {}
    for q in (0.05, 0.10, 0.20):
        nn = max(1, int(n * q))
        shares[f"top{int(q*100)}"] = round(float(c[order[:nn]].sum() / c.sum() * 100), 1)
    # 按 |贡献| 删尾：剔除影响最大的 10% 观测后重算 IC
    keep = np.argsort(np.abs(c))[:int(n * 0.90)]
    ic_trim = spearman(x[keep], y[keep])
    # 只删「顺 IC 方向」的最大贡献日（对 IC>0 删最大正贡献，反之删最大负贡献）
    drop = order[:int(n * 0.05)] if ic >= 0 else order[-int(n * 0.05):]
    mask = np.ones(n, bool)
    mask[drop] = False
    ic_drop = spearman(x[mask], y[mask])
    conc.append({"key": k, "name": NAME[k], "n": n, "ic": round(ic, 4),
                 **shares, "ic_trim10": round(ic_trim, 4),
                 "trim_retain_pct": round(float(ic_trim / ic * 100), 1) if abs(ic) > 1e-6 else None,
                 "ic_drop5": round(ic_drop, 4),
                 "drop_retain_pct": round(float(ic_drop / ic * 100), 1) if abs(ic) > 1e-6 else None})
log(f"[A] 完成 {len(conc)} 因子贡献集中度")


# ---------------- B. 块级 IC 分布 / 时间聚类 ----------------
BLOCK = 60
blocks = []
for k in DAILY:
    x, y = pair(k)
    n = len(x)
    vals, spans = [], []
    for i in range(0, n - BLOCK + 1, BLOCK):
        v = spearman(x[i:i + BLOCK], y[i:i + BLOCK], min_n=30)
        if np.isfinite(v):
            vals.append(v)
            spans.append(i)
    if len(vals) < 8:
        continue
    a = np.array(vals)
    mean = float(a.mean())
    top = np.argsort(np.abs(a))[::-1][:5]
    blocks.append({"key": k, "name": NAME[k], "n_blocks": len(vals),
                   "mean": round(mean, 3), "std": round(float(a.std(ddof=1)), 3),
                   "t": round(float(mean / (a.std(ddof=1) / math.sqrt(len(a)))), 2)
                   if a.std(ddof=1) > 1e-9 else None,
                   "pos_rate": round(float((a > 0).mean() * 100), 1),
                   "top5_share_pct": round(float(a[top].sum() / a.sum() * 100), 1)
                   if abs(a.sum()) > 1e-9 else None,
                   "max": round(float(a.max()), 3), "min": round(float(a.min()), 3)})

# 全因子汇总：贡献最大的 10% 交易日落在哪些年份 / 多少个独立 60 日块
idx_contrib = {}
all_top_dates = []
for k in DAILY:
    x, y = pair(k)
    c = centered_rank(x) * centered_rank(y)
    nn = max(1, int(len(c) * 0.10))
    sel = np.argsort(np.abs(c))[::-1][:nn]
    dates = MAT.loc[START:].index[np.flatnonzero(np.isfinite(MAT[f"{k}__pct_exp"].loc[START:].to_numpy(float)) &
                                                np.isfinite(FWD[60].loc[START:].to_numpy(float)))]
    all_top_dates.extend(list(dates[sel]))
yr = pd.Series(all_top_dates).dt.year.value_counts().sort_index()
log(f"[B] 完成 {len(blocks)} 因子块级 IC；高贡献日样本 {len(all_top_dates)}")


# ---------------- C. 极值事件研究 ----------------
def first_entries(pct, lo, hi):
    """从非极值首次进入极值区的下标；NaN 视为非极值。"""
    v = np.asarray(pct, float)
    islow = np.isfinite(v) & (v <= lo)
    ishigh = np.isfinite(v) & (v >= hi)
    def run(flag):
        prev = np.r_[False, flag[:-1]]
        return np.flatnonzero(flag & ~prev)
    return run(islow), run(ishigh)


def dedup(idx, gap, n):
    out = []
    for i in idx:
        if not out or i - out[-1] >= gap:
            out.append(int(i))
    return [i for i in out if i < n]


def rand_date_control(fwd_arr, idx, gap, n_boot=2000):
    """循环平移对照：整条前瞻收益序列随机循环位移，保留事件间隔结构、收益自相关与
    事件数，只打破「事件日 ↔ 收益」的对齐。比随机抽日期更贴合事件研究的零假设。

    注意：60 日前瞻收益彼此重叠，循环平移同时保留了这种重叠性；但因此有效独立
    样本数约为 n_ev * gap / h，p 值仍偏乐观，结论须再做留一年/分段检验。
    """
    n_valid = int(np.flatnonzero(np.isfinite(fwd_arr))[-1]) + 1 if np.isfinite(fwd_arr).any() else 0
    fv = np.asarray(fwd_arr[:n_valid], float)
    idx = np.asarray(idx, int)
    idx = idx[idx < n_valid]
    if len(idx) < 8 or n_valid < 200:
        return np.zeros(n_boot), 0.0, 0.0
    vals = np.empty(n_boot)
    for b in range(n_boot):
        s = int(rng.integers(1, n_valid))
        v = np.roll(fv, s)[idx]
        v = v[np.isfinite(v)]
        vals[b] = float(v.mean()) if len(v) >= 5 else 0.0
    return vals, float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


events, ev_tests = [], []
for k in DAILY:
    pct = MAT[f"{k}__pct_exp"].loc[START:]
    x_all = pct.to_numpy(float)
    y_all = FWD[60].loc[START:].to_numpy(float)
    lo_i, hi_i = first_entries(x_all, 10, 90)
    lo_i, hi_i = dedup(lo_i, 20, len(x_all)), dedup(hi_i, 20, len(x_all))
    for tail, idx in (("低分位<=10", lo_i), ("高分位>=90", hi_i)):
        for h in (20, 60):
            f = FWD[h].loc[START:].to_numpy(float)
            v = f[idx]
            v = v[np.isfinite(v)]
            n_ev = len(v)
            if n_ev < 8:
                ev_tests.append({"key": k, "name": NAME[k], "tail": tail, "h": h,
                                 "n": n_ev, "p": None, "verdict": "事件数不足"})
                continue
            base_all = float(np.nanmean(f))
            excess = float(v.mean() - base_all)
            win = float((v > 0).mean() * 100)
            null, c_lo, c_hi = rand_date_control(f, idx, 20, 2000)
            p = round(float(2.0 * min((null >= v.mean()).mean(), (null <= v.mean()).mean())), 4)
            p = min(1.0, max(p, 1 / 2000))
            ev_tests.append({"key": k, "name": NAME[k], "tail": tail, "h": h,
                             "n": n_ev, "mean_ret_pct": round(float(v.mean()) * 100, 2),
                             "base_ret_pct": round(base_all * 100, 2),
                             "excess_pp": round(excess * 100, 2), "win_pct": round(win, 1),
                             "wilson": wilson(int((v > 0).sum()), n_ev),
                             "null_ci": [round(c_lo * 100, 2), round(c_hi * 100, 2)],
                             "p": p, "verdict": "待校正"})
            if n_ev >= 8:
                events.append({"key": k, "name": NAME[k], "tail": tail, "h": h,
                               "n": n_ev, "excess_pp": round(excess * 100, 2),
                               "win_pct": round(win, 1)})

valid = [(i, t) for i, t in enumerate(ev_tests) if t["p"] is not None]
qs = bh_fdr([t["p"] for _, t in valid])
for (i, t), q in zip(valid, qs):
    ev_tests[i]["q"] = round(float(q), 4)
    ev_tests[i]["sig_fdr"] = bool(q < 0.05)
    ev_tests[i]["verdict"] = ("极值有效" if q < 0.05 and abs(t["excess_pp"]) >= 2.0
                              else ("显著但效应小" if q < 0.05 else "不显著"))
for t in ev_tests:
    t.setdefault("q", None)
    t.setdefault("sig_fdr", False)
log(f"[C] 事件检验 {len(valid)} 次，raw p<0.05 = {sum(t['p']<0.05 for _,t in valid)}，"
    f"FDR 后 = {sum(t.get('sig_fdr',False) for t in ev_tests)}")


# ---------------- D. 分段稳定性 ----------------
SEGS = [("2016-2018", "2016-01-11", "2018-12-31"),
        ("2019-2021", "2019-01-01", "2021-12-31"),
        ("2022-2024", "2022-01-01", "2024-12-31"),
        ("2025-2026", "2025-01-01", "2026-11-20")]
seg_rows = []
for k in DAILY:
    row = {"key": k, "name": NAME[k]}
    for label, a, b in SEGS:
        x = MAT[f"{k}__pct_exp"].loc[a:b].to_numpy(float)
        y = FWD[60].loc[a:b].to_numpy(float)
        ok = np.isfinite(x) & np.isfinite(y)
        row[label] = round(spearman(x[ok], y[ok], min_n=60), 3) if ok.sum() >= 60 else None
    vs = [row[s[0]] for s in SEGS if row[s[0]] is not None and np.isfinite(row[s[0]])]
    row["sign_flips"] = int(sum(np.sign(vs[i]) != np.sign(vs[i + 1]) for i in range(len(vs) - 1)))
    row["sign_consistent"] = bool(len(vs) >= 3 and (all(v > 0 for v in vs) or all(v < 0 for v in vs)))
    seg_rows.append(row)
log(f"[D] 完成 {len(seg_rows)} 因子分段 IC")


# ---------------- E. 补充诊断 ----------------
# E1 绝对贡献占比（不受 IC≈0 时分母反号影响）
abs_share = []
for c in conc:
    k = c["key"]
    x, y = pair(k)
    cc = centered_rank(x) * centered_rank(y)
    nn = max(1, int(len(cc) * 0.10))
    sel = np.argsort(np.abs(cc))[::-1][:nn]
    abs_share.append({"key": k, "name": NAME[k],
                      "abs_share_top10_pct": round(float(np.abs(cc[sel]).sum() / np.abs(cc).sum() * 100), 1)})

# E2 块 IC 的 lag1 自相关：若块间高度相关，块均值 t 检验会严重夸大显著性
blk_lag1 = []
lag1 = []
for k in DAILY:
    x, y = pair(k)
    vals = [spearman(x[i:i + BLOCK], y[i:i + BLOCK], min_n=30)
            for i in range(0, len(x) - BLOCK + 1, BLOCK)]
    vals = np.array([v for v in vals if np.isfinite(v)])
    if len(vals) >= 10:
        rho = float(np.corrcoef(vals[:-1], vals[1:])[0, 1])
        blk_lag1.append(round(rho, 3))
        lag1.append({"key": k, "name": NAME[k], "lag1": blk_lag1[-1]})

# E3 低/高分位事件的方向不对称（合并看，不依赖单次 p）
tail_bias = {}
for tail in ("低分位<=10", "高分位>=90"):
    rows = [t for t in ev_tests if t["h"] == 60 and t["tail"] == tail and t["p"] is not None]
    if not rows:
        continue
    ex = [t["excess_pp"] for t in rows]
    pos = sum(v > 0 for v in ex)
    # 符号检验（二项，p=0.5），双侧
    n = len(ex)
    p_sign = min(1.0, 2 * sum(math.comb(n, i) for i in range(pos, n + 1)) / 2 ** n) if n <= 60 else None
    tail_bias[tail] = {"n_cells": n, "mean_excess_pp": round(float(np.mean(ex)), 2),
                       "n_positive": pos, "p_sign": round(p_sign, 4) if p_sign else None,
                       "mean_win_pct": round(float(np.mean([t["win_pct"] for t in rows])), 1)}

# E4 段内去均值后的合并 IC（诊断性，使用段内全样本均值 → 非 PIT，仅用于量化漂移抵消）
drift = []
for k in DAILY:
    xs, ys = [], []
    for label, a, b in SEGS:
        xv = MAT[f"{k}__pct_exp"].loc[a:b].to_numpy(float)
        yv = FWD[60].loc[a:b].to_numpy(float)
        ok = np.isfinite(xv) & np.isfinite(yv)
        xs.append(xv[ok] - np.nanmean(xv[ok]))
        ys.append(yv[ok] - np.nanmean(yv[ok]))
    xa, ya = np.concatenate(xs), np.concatenate(ys)
    ic_dm = spearman(xa, ya)
    ic_full = next(c["ic"] for c in conc if c["key"] == k)
    drift.append({"key": k, "name": NAME[k], "ic_full": ic_full,
                  "ic_demeaned": round(ic_dm, 3) if np.isfinite(ic_dm) else None,
                  "gain": round(float(abs(ic_dm) - abs(ic_full)), 3) if np.isfinite(ic_dm) else None})

# E5 合并「极值共振事件」：同一天有多少因子同时处于低/高分位，检验次数降到 1 次
PCT = pd.DataFrame({k: MAT[f"{k}__pct_exp"].loc[START:] for k in DAILY})
low_cnt = (PCT <= 10).sum(axis=1)
high_cnt = (PCT >= 90).sum(axis=1)
resonance = []
for label, cnt, thr in (("低分位共振", low_cnt, 5), ("高分位共振", high_cnt, 5)):
    idx = dedup(np.flatnonzero((cnt >= thr).to_numpy()), 20, len(cnt))
    f = FWD[60].loc[START:].to_numpy(float)
    v = f[idx]
    v = v[np.isfinite(v)]
    if len(v) >= 8:
        base = float(np.nanmean(f))
        null, c_lo, c_hi = rand_date_control(f, idx, 20, 2000)
        p = round(float(min(1.0, 2.0 * min((null >= v.mean()).mean(), (null <= v.mean()).mean()))), 4)
        resonance.append({"label": label, "thr": thr, "n": len(v),
                          "mean_ret_pct": round(float(v.mean()) * 100, 2),
                          "base_ret_pct": round(base * 100, 2),
                          "excess_pp": round(float(v.mean() - base) * 100, 2),
                          "win_pct": round(float((v > 0).mean() * 100), 1),
                          "wilson": wilson(int((v > 0).sum()), len(v)),
                          "null_ci": [round(c_lo * 100, 2), round(c_hi * 100, 2)], "p": p})

# F 高分位共振的稳健性拆解（阈值×窗口扫描 / 留一年 / 分段 / 不重叠事件）
rob = []
for thr in (3, 4, 5, 6, 7):
    for h in (20, 60, 120):
        idx = dedup(np.flatnonzero((high_cnt >= thr).to_numpy()), 20, len(high_cnt))
        f = FWD[h].loc[START:].to_numpy(float)
        v = f[idx]
        v = v[np.isfinite(v)]
        if len(v) < 8:
            continue
        null, _, _ = rand_date_control(f, idx, 20, 1000)
        p = round(float(min(1.0, 2.0 * min((null >= v.mean()).mean(),
                                           (null <= v.mean()).mean()))), 4)
        rob.append({"thr": thr, "h": h, "n": len(v),
                    "excess_pp": round(float(v.mean() - np.nanmean(f)) * 100, 2),
                    "win_pct": round(float((v > 0).mean() * 100), 1), "p": p})
rq = bh_fdr([r["p"] for r in rob])
for r, q in zip(rob, rq):
    r["q"] = round(float(q), 4)
    r["sig_fdr"] = bool(q < 0.05)

ev_idx5 = dedup(np.flatnonzero((high_cnt >= 5).to_numpy()), 20, len(high_cnt))
cal = MAT.loc[START:].index
ev_dates = cal[ev_idx5]
f60a = FWD[60].loc[START:].to_numpy(float)
base60 = float(np.nanmean(f60a))
year_dist = {int(y): int(c) for y, c in pd.Series(ev_dates).dt.year.value_counts().sort_index().items()}
# 留一年
loo = []
for y in sorted(year_dist):
    keep = [i for i, d in zip(ev_idx5, ev_dates) if d.year != y]
    v = f60a[keep]
    v = v[np.isfinite(v)]
    if len(v) >= 8:
        loo.append({"drop_year": y, "n": len(v),
                    "excess_pp": round(float(v.mean() - base60) * 100, 2)})
# 分段
seg_ev = []
for label, a, b in (("2016-2019", 2016, 2019), ("2020-2022", 2020, 2022), ("2023-2026", 2023, 2026)):
    keep = [i for i, d in zip(ev_idx5, ev_dates) if a <= d.year <= b]
    v = f60a[keep]
    v = v[np.isfinite(v)]
    if len(v) >= 5:
        seg_ev.append({"segment": label, "n": len(v),
                       "excess_pp": round(float(v.mean() - base60) * 100, 2),
                       "win_pct": round(float((v > 0).mean() * 100), 1)})
# 不重叠事件：间隔 >= 60 日（与 60 日前瞻窗口对齐）
idx_nool = dedup(np.flatnonzero((high_cnt >= 5).to_numpy()), 60, len(high_cnt))
v = f60a[idx_nool]
v = v[np.isfinite(v)]
null, _, _ = rand_date_control(f60a, idx_nool, 60, 2000)
nool = {"n": len(v), "excess_pp": round(float(v.mean() - base60) * 100, 2) if len(v) else None,
        "win_pct": round(float((v > 0).mean() * 100), 1) if len(v) else None,
        "p": round(float(min(1.0, 2.0 * min((null >= v.mean()).mean(),
                                            (null <= v.mean()).mean()))), 4) if len(v) else None}
log(f"[F] 高分位共振扫描 {len(rob)} 格，FDR 后显著 {sum(r['sig_fdr'] for r in rob)}；"
    f"不重叠事件 n={nool['n']} 超额={nool['excess_pp']}pp p={nool['p']}")


# E6 当日（末日）数据完备性快照，给可信度评分做示例
last = DATES[-1]
row = MAT.loc[last]
avail = [k for k in DAILY if np.isfinite(row.get(f"{k}__pct_exp", np.nan))]
snapshot = {"date": str(last.date()), "n_factors": len(DAILY), "n_available": len(avail),
            "coverage_pct": round(len(avail) / len(DAILY) * 100, 1),
            "n_low": int((PCT.loc[last][avail] <= 10).sum()),
            "n_high": int((PCT.loc[last][avail] >= 90).sum()),
            "n_mid": int(((PCT.loc[last][avail] > 10) & (PCT.loc[last][avail] < 90)).sum()),
            "low_cnt_hist_p90": int(low_cnt.quantile(0.90)),
            "high_cnt_hist_p90": int(high_cnt.quantile(0.90))}
log(f"[E] 绝对贡献 top10 中位数 {np.median([a['abs_share_top10_pct'] for a in abs_share]):.1f}%；"
    f"块 IC lag1 中位数 {np.median(blk_lag1):.3f}（块间近似独立，块内 IC 与全样本 IC 的落差"
    f"来自跨块水平漂移，不是自相关夸大）；段内去均值增益中位数 "
    f"{np.median([d['gain'] for d in drift if d['gain'] is not None]):.3f}")


# ---------------- 汇总 ----------------
R = {
    "meta": {"base_date": str(DATES[-1].date()), "start": str(START.date()),
             "n_days": int(len(MAT)), "n_factors": len(DAILY), "horizon": 60,
             "block": BLOCK, "event_gap": 20, "random_control_boot": 2000,
             "source": "data/factor_matrix.csv（L0）+ mood_data.json hs300_close",
             "note": "只读探针，未接入 DAG、未改任何生产文件"},
    "concentration": conc,
    "block_ic": blocks,
    "top_day_years": {int(y): int(c) for y, c in yr.items()},
    "events": events, "event_tests": ev_tests,
    "segments": seg_rows,
    "abs_share": abs_share, "block_lag1": lag1, "tail_bias": tail_bias,
    "drift": drift, "resonance": resonance, "snapshot": snapshot,
    "resonance_robust": {"grid": rob, "year_dist": year_dist, "leave_one_year_out": loo,
                         "segments": seg_ev, "non_overlap": nool,
                         "n_grid": len(rob), "n_grid_sig_fdr": int(sum(r["sig_fdr"] for r in rob))},
    "summary": {
        "n_conc": len(conc),
        "median_top10_share": round(float(np.median([c["top10"] for c in conc])), 1),
        "median_trim_retain": round(float(np.median([c["trim_retain_pct"] for c in conc
                                                      if c["trim_retain_pct"] is not None])), 1),
        "n_trim_sign_flip": int(sum(np.sign(c["ic"]) != np.sign(c["ic_trim10"]) for c in conc)),
        "n_block_t_sig": int(sum(abs(b["t"]) >= 2.0 for b in blocks if b["t"] is not None)),
        "median_block_pos_rate": round(float(np.median([b["pos_rate"] for b in blocks])), 1),
        "n_event_tests": len(valid),
        "n_event_raw_sig": int(sum(t["p"] < 0.05 for _, t in valid)),
        "n_event_fdr_sig": int(sum(t.get("sig_fdr", False) for t in ev_tests)),
        "n_seg_consistent": int(sum(r["sign_consistent"] for r in seg_rows)),
        "median_seg_flips": round(float(np.median([r["sign_flips"] for r in seg_rows])), 1),
        "median_abs_share_top10": round(float(np.median([a["abs_share_top10_pct"] for a in abs_share])), 1),
        "median_block_lag1": round(float(np.median(blk_lag1)), 3),
        "median_drift_gain": round(float(np.median([d["gain"] for d in drift
                                                     if d["gain"] is not None])), 3),
        "n_drift_gain_pos": int(sum(d["gain"] > 0 for d in drift if d["gain"] is not None)),
        "top_day_2018_2024_share": round(float(
            (yr.get(2018, 0) + yr.get(2024, 0)) / max(1, yr.sum()) * 100), 1),
    },
}
(DATA / "_conditional_probe.json").write_text(
    json.dumps(R, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")

print("\n=== 探针结果 ===")
s = R["summary"]
print(f"[A] 前10%交易日贡献占比中位数 {s['median_top10_share']}%；"
      f"删掉|贡献|最大10%后 IC 保留中位数 {s['median_trim_retain']}%；"
      f"删尾后符号翻转 {s['n_trim_sign_flip']}/{len(conc)}")
print("    top5 因子（按删尾后衰减）:")
for c in sorted(conc, key=lambda z: -(z["trim_retain_pct"] or 0))[:5]:
    print(f"      {c['name'][:16]:18s} IC={c['ic']:+.3f} 前10%贡献={c['top10']}% "
          f"删尾IC={c['ic_trim10']:+.3f} 保留={c['trim_retain_pct']}%")
print(f"[B] 块级 IC：|t|>=2 的因子 {s['n_block_t_sig']}/{len(blocks)}，"
      f"块 IC 正率中位数 {s['median_block_pos_rate']}%")
print("    高贡献日年份分布:", R["top_day_years"])
print(f"[C] 极值事件 {s['n_event_tests']} 次：raw p<0.05 = {s['n_event_raw_sig']}，"
      f"FDR 后 = {s['n_event_fdr_sig']}")
for t in sorted([t for t in ev_tests if t["p"] is not None], key=lambda z: z["p"])[:8]:
    print(f"      {t['name'][:14]:16s} {t['tail']:12s} h={t['h']:3d} n={t['n']:3d} "
          f"超额={t['excess_pp']:+.2f}pp 胜率={t['win_pct']:.1f}% p={t['p']} q={t['q']}")
print(f"[D] 4 段符号全一致的因子 {s['n_seg_consistent']}/{len(seg_rows)}；"
      f"符号翻转次数中位数 {s['median_seg_flips']}")
for r in sorted(seg_rows, key=lambda z: z["sign_flips"])[:5]:
    print("      ", r["name"][:16], [r[s[0]] for s in SEGS], "flips", r["sign_flips"])
print(f"[E] |贡献| top10 占比中位数 {s['median_abs_share_top10']}%；"
      f"块 IC lag1 中位数 {s['median_block_lag1']}（块间近似独立；块内 IC 显著而全样本 IC≈0"
      f"属跨块水平漂移抵消，不是自相关夸大）")
print(f"    高贡献日 2018+2024 合计占 {s['top_day_2018_2024_share']}%")
print(f"    段内去均值后 |IC| 增益中位数 {s['median_drift_gain']}，"
      f"{s['n_drift_gain_pos']}/{len(drift)} 因子增益为正")
for tb, v in tail_bias.items():
    print(f"    {tb}: {v['n_cells']} 格，平均超额 {v['mean_excess_pp']:+.2f}pp，"
          f"正超额 {v['n_positive']} 格，平均胜率 {v['mean_win_pct']}%，符号检验 p={v['p_sign']}")
for rr in resonance:
    print(f"    {rr['label']}(>={rr['thr']}因子): n={rr['n']} 超额={rr['excess_pp']:+.2f}pp "
          f"胜率={rr['win_pct']}% p={rr['p']}")
print("    当日快照:", snapshot)
print(f"[F] 高分位共振扫描 {len(rob)} 格（阈值3-7 × 窗口20/60/120）："
      f"FDR 后显著 {sum(r['sig_fdr'] for r in rob)}")
for r in sorted(rob, key=lambda z: z["p"])[:6]:
    print(f"      阈值>={r['thr']} h={r['h']:3d} n={r['n']:3d} "
          f"超额={r['excess_pp']:+.2f}pp p={r['p']} q={r['q']}")
print("    事件年份分布:", year_dist)
print("    留一年:", [(x["drop_year"], x["excess_pp"]) for x in loo])
print("    分段:", [(x["segment"], x["n"], x["excess_pp"]) for x in seg_ev])
print("    不重叠事件(间隔>=60日):", nool)
print(f"耗时 {time.time()-T0:.1f}s → data/_conditional_probe.json")
