# -*- coding: utf-8 -*-
"""L1 常驻指标效力审计（只读 / 零网络 / 接入 DAG，排在 L0 之后）。

输入  data/factor_matrix.csv    L0 统一因子矩阵（唯一因子值来源）
      data/factor_matrix.json   L0 元数据（unit/kind/as_of/stale_days/history_start...）
      data/mood_data.json       仅取市场状态（turning_panel）与 hs300_close 基准价格
输出  data/efficacy_audit.json / .js（window.EFFICACY_AUDIT）

相对首版（2026-11-21 上午）的升级
--------------------------------
1. 因子值不再各读各的卡片，统一从 L0 因子矩阵取，输入口径唯一。
2. 所有因子补齐 publish_date / stale_days / history_start 元数据（L0 提供；
   源端确实没有 publish_date 时如实写 null，不伪造）。
3. 条件 IC 也纳入 BH-FDR：给出族内校正 + 与单因子主检验的联合校正，
   并写试验日志用于估计有效检验数（应对「跑一批→看结果→加指标」的序贯检验）。
4. 新增多轨道稳健性：换一条变换轨道后结论是否改变（用解析近似 p，快）。
5. 保留全部原有字段与反面证据，首版的 51 次检验结果可逐条复现。

纪律：在 walk-forward 样本外证明有稳定增量之前，绝不改 8 指标等权。
用法：python audit_efficacy.py   （--force 由 DAG 透传，本脚本只读，忽略该参数）
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from workbench import config as C
from workbench.factor_utils import (DATA, bh_fdr, block_boot_p, log, p_approx, spearman,
                          write_json_js)

T0 = time.time()
rng = np.random.default_rng(C.AUDIT_SEED)
HORIZONS = list(C.AUDIT_HORIZONS)
BLOCK = C.AUDIT_BLOCK
BOOT = C.AUDIT_BOOT
BOOT_COND = C.AUDIT_BOOT_COND
START = pd.Timestamp(C.FACTOR_START)

# ---------------- 0. L0 产物检查 ----------------
CSV = DATA / "factor_matrix.csv"
L0JSON = DATA / "factor_matrix.json"
if not CSV.exists() or not L0JSON.exists():
    raise SystemExit("[L1] 缺少 L0 产物，请先运行 python build_factor_matrix.py")

L0 = json.loads(L0JSON.read_text(encoding="utf-8"))
MAT = pd.read_csv(CSV, index_col=0, parse_dates=True)
m = json.loads((DATA / "mood_data.json").read_text(encoding="utf-8"))
tp = m["turning_panel"]
hs = pd.Series({pd.Timestamp(d): float(v) for d, v in m["hs300_close"]}).sort_index()
DATES = hs.index

R = {"meta": {"base_date": str(DATES[-1].date()), "start": str(START.date()),
              "n_trade_days": int(len(DATES)), "horizons": HORIZONS,
              "bootstrap": BOOT, "bootstrap_conditional": BOOT_COND, "block": BLOCK,
              "factor_source": "data/factor_matrix.csv（L0 统一因子矩阵）",
              "main_track": C.FACTOR_MAIN_TRACK, "l0_version": "2026-11-21"},
     "l0": {"base_date": L0["meta"]["base_date"], "n_factors_all": L0["meta"]["n_factors_all"],
            "n_in_matrix": L0["meta"]["n_in_matrix"], "n_transforms": L0["meta"]["n_transforms"],
            "pit_check": L0["meta"]["pit_check"], "prod_track_sync": L0["meta"]["prod_track_sync"],
            "min_coverage": L0["meta"]["min_coverage"],
            "note": L0["meta"]["note"]}}
log(f"[L1] 读取 L0 因子矩阵 {MAT.shape[0]} 行 × {MAT.shape[1]} 列")


# ---------------- 1. 因子元数据（统一来自 L0）----------------
F, A = {}, {}
for rec in L0["factors"]:
    k = rec["key"]
    F[k] = {"name": rec["name"], "group": rec["group"], "freq": rec["freq"],
            "reverse": rec["reverse"], "medgap": rec["medgap"],
            "n_obs": rec["n_obs"], "start": rec["history_start"], "end": rec["as_of"],
            "score": rec.get("score"), "unit": rec["unit"], "kind": rec["kind"],
            "as_of": rec["as_of"], "publish_date": rec["publish_date"],
            "publish_date_status": rec["publish_date_status"],
            "stale_trade_days": rec["stale_trade_days"],
            "stale_calendar_days": rec["stale_calendar_days"],
            "coverage_pct": rec["coverage_pct"], "in_matrix": rec["in_matrix"],
            "history_start": rec["history_start"], "ks_raw": rec["ks_raw"],
            "ks_pct_exp": rec["ks_pct_exp"], "priority": rec["priority"],
            "recommend": rec["recommend"]}
    col = f"{k}__{C.FACTOR_MAIN_TRACK}"
    if rec["in_matrix"] and col in MAT.columns:
        A[k] = MAT[col]

R["coverage"] = [{"key": k, **F[k], "coverage_pct": F[k]["coverage_pct"]} for k in F]
DAILY = [k for k in F if F[k]["in_matrix"]]
R["meta"]["daily_factors"] = len(DAILY)
R["meta"]["excluded"] = [{"key": e["key"], "freq": e["freq"],
                          "coverage_pct": e["coverage_pct"], "reason": e["reason"]}
                         for e in L0["excluded"]]

# 元数据审计：发布滞后与口径可用性
R["metadata_audit"] = {
    "n_total": len(F), "n_in_matrix": len(DAILY),
    "publish_date_available": int(sum(F[k]["publish_date"] is not None for k in F)),
    "publish_date_note": ("卡片 history 只有 date/value/pct，源端未提供真实 publish_date；"
                          "L0 如实写 null，用 as_of + stale_days 近似。任何 PIT 结论仍可能含"
                          "1~3 日发布滞后，属已知上限而非已消除。"),
    "stale_distribution": {
        "0": int(sum(F[k]["stale_trade_days"] == 0 for k in F)),
        "1-3": int(sum(1 <= F[k]["stale_trade_days"] <= 3 for k in F)),
        ">3": int(sum(F[k]["stale_trade_days"] > 3 for k in F)),
    },
    "max_stale_days": max(F[k]["stale_trade_days"] for k in F),
    "kind_counts": {v: int(sum(F[k]["kind"] == v for k in F))
                    for v in sorted(set(F[k]["kind"] for k in F))},
    "freq_counts": {v: int(sum(F[k]["freq"] == v for k in F)) for v in ["日", "周", "月"]},
}


# ---------------- 2. 前瞻收益（PIT：shift(-h) 保证 t+h <= 末日）----------------
fwd = {h: (hs.shift(-h) / hs - 1.0) for h in HORIZONS}


def ir_series(x, y):
    """不重叠 60 日块 IC 序列 -> IR / 正率"""
    vals, n = [], len(x)
    for i in range(0, n - BLOCK + 1, BLOCK):
        v = spearman(x[i:i + BLOCK], y[i:i + BLOCK])
        if np.isfinite(v):
            vals.append(v)
    if len(vals) < 4:
        return None, None, 0
    a = np.array(vals)
    return (round(float(a.mean() / a.std(ddof=1)), 3) if a.std(ddof=1) > 1e-9 else None,
            round(float((a > 0).mean() * 100), 1), len(vals))


def verdict_of(ic, sig, ir):
    if not np.isfinite(ic):
        return "样本不足"
    if sig and abs(ic) >= 0.05 and ir is not None and abs(ir) >= 0.5:
        return "有效"
    if sig and abs(ic) >= 0.03:
        return "弱有效"
    if sig:
        return "显著但效应极小"
    return "不显著"


# ---------------- 3. 单因子主检验（pct_exp 轨道，口径与首版完全一致）----------------
single = []
for k in DAILY:
    for h in HORIZONS:
        x = A[k].loc[START:].to_numpy(float)
        y = fwd[h].loc[START:].to_numpy(float)
        ok = np.isfinite(x) & np.isfinite(y)
        x, y = x[ok], y[ok]
        if len(x) < C.AUDIT_MIN_OBS:
            continue
        ic = spearman(x, y)
        lo, hi, p = block_boot_p(x, y, rng, BOOT, BLOCK)
        ir, posrate, nblk = ir_series(x, y)
        bins = np.digitize(x, [20, 40, 60, 80])   # pct_exp 本身是 0-100 分位，固定切档无二次前视
        qret = [round(float(np.mean(y[bins == j]) * 100), 2)
                if (bins == j).sum() >= 20 else None for j in range(5)]
        qwin = [round(float(np.mean(y[bins == j] > 0) * 100), 1)
                if (bins == j).sum() >= 20 else None for j in range(5)]
        valid_idx = [j for j, v in enumerate(qret) if v is not None]
        valid = [qret[j] for j in valid_idx]
        mono = (float(np.corrcoef(pd.Series(valid).rank().values,
                                  np.array(valid_idx, float))[0, 1])
                if len(valid) >= 3 else np.nan)
        single.append({"key": k, "name": F[k]["name"], "h": h, "n": int(len(x)),
                       "n_indep": int(len(x) // h), "ic": round(ic, 4),
                       "ci_lo": lo, "ci_hi": hi, "p": p,
                       "ir": ir, "ic_pos_rate": posrate, "n_blocks": nblk,
                       "q_ret60": qret, "q_win": qwin,
                       "mono": round(mono, 3) if np.isfinite(mono) else None})

# 族内 BH-FDR（主检验，保持首版口径）
ps = np.array([r["p"] for r in single])
q_single = bh_fdr(ps)
for r, q in zip(single, q_single):
    r["q"] = round(float(q), 4)
    r["sig_fdr"] = bool(q < 0.05)
    r["verdict"] = verdict_of(r["ic"], r["sig_fdr"], r["ir"])
R["single"] = single
R["meta"]["n_tests"] = len(ps)
R["meta"]["n_sig_raw"] = int((ps < 0.05).sum())
R["meta"]["n_sig_fdr"] = int(sum(r["sig_fdr"] for r in single))
R["verdict_counts"] = {v: int(sum(r["verdict"] == v for r in single))
                       for v in sorted(set(r["verdict"] for r in single))}
log(f"[L1] 主检验 {len(ps)} 次：raw p<0.05 = {R['meta']['n_sig_raw']}，FDR 后 = {R['meta']['n_sig_fdr']}")


# ---------------- 4. 因子结构：相关 / 聚类 / PCA（pct_exp 轨道）----------------
X = pd.DataFrame({k: A[k].loc[START:] for k in DAILY})
Cc = X.corr(method="spearman", min_periods=250)
dupes = []
for i, a in enumerate(DAILY):
    for b in DAILY[i + 1:]:
        v = Cc.loc[a, b]
        if pd.notna(v) and abs(v) >= 0.98:
            dupes.append({"a": F[a]["name"], "b": F[b]["name"], "r": round(float(v), 3)})
R["duplicates"] = dupes

Cm = np.array(Cc.fillna(0.0).to_numpy(float), copy=True)
np.fill_diagonal(Cm, 1.0)
labels = [F[k]["name"] for k in DAILY]


def cluster(cmat, k=5):
    dist = 1.0 - np.abs(cmat)
    np.fill_diagonal(dist, 0.0)
    cs = [[i] for i in range(len(cmat))]
    while len(cs) > k:
        best = None
        for a in range(len(cs)):
            for b in range(a + 1, len(cs)):
                d = float(np.mean(dist[np.ix_(cs[a], cs[b])]))
                if best is None or d < best[0]:
                    best = (d, a, b)
        _, a, b = best
        merged = cs[a] + cs[b]
        cs = [c for i, c in enumerate(cs) if i not in (a, b)] + [merged]
    return cs


cl = cluster(Cm, 5)
R["clusters"] = [{"members": [labels[i] for i in c], "size": len(c),
                  "avg_abs_corr": round(float(np.mean(np.abs(
                      Cm[np.ix_(c, c)][np.triu_indices(len(c), 1)]))), 3) if len(c) > 1 else 1.0}
                 for c in cl]
Z = (X - X.mean()) / X.std(ddof=0).replace(0, 1)
Z = Z.fillna(0.0).to_numpy(float)
ev = np.clip(np.linalg.eigvalsh(Z.T @ Z / max(1, len(Z)))[::-1], 0, None)
R["pca"] = {"explained": [round(float(v / ev.sum()) * 100, 1) for v in ev[:8]],
            "cum3": round(float(ev[:3].sum() / ev.sum()) * 100, 1),
            "cum5": round(float(ev[:5].sum() / ev.sum()) * 100, 1),
            "n_factors": len(DAILY),
            "n_effective_note": ("共线严重，独立检验数远小于 17；按层次聚类约 5 组，"
                                 "有效检验数应以聚类数估计，固定样本 BH 仍偏乐观。")}
R["corr"] = {"labels": labels, "matrix": [[round(float(v), 2) if pd.notna(v) else None
                                          for v in row] for row in Cc.to_numpy()]}


# ---------------- 5. 状态依赖：条件 IC + FDR ----------------
turn = pd.DataFrame(tp["history"]).assign(date=lambda d: pd.to_datetime(d["date"])).set_index("date")
turn = turn.reindex(DATES).ffill(limit=5)
mom60 = hs / hs.shift(60) - 1
rv20 = hs.pct_change().rolling(20).std()
rvpct = rv20.expanding(min_periods=250).rank(pct=True) * 100
STATES = {
    "上涨(60日动量>0)": (mom60 > 0),
    "下跌(60日动量<=0)": (mom60 <= 0),
    "高波动(RV20分位>=70)": (rvpct >= 70),
    "低波动(RV20分位<30)": (rvpct < 30),
    "底部共振>=60": (turn["bottom"] >= 60),
    "底部共振<60": (turn["bottom"] < 60),
    "confirm>=1": (turn["confirm"] >= 1),
    "confirm=0": (turn["confirm"] == 0),
}
cond = []
for sname, mask in STATES.items():
    for k in DAILY:
        sub = pd.DataFrame({"x": A[k], "y": fwd[60]}).loc[START:]
        sub = sub[mask.reindex(sub.index).fillna(False)]
        xv, yv = sub["x"].to_numpy(float), sub["y"].to_numpy(float)
        ok = np.isfinite(xv) & np.isfinite(yv)
        n = int(ok.sum())
        if n < C.AUDIT_COND_MIN_OBS:
            cond.append({"state": sname, "key": k, "name": F[k]["name"],
                         "ic60": None, "n": n, "p": None, "ci_lo": None, "ci_hi": None,
                         "verdict": "样本不足"})
            continue
        x, y = xv[ok], yv[ok]
        ic = round(spearman(x, y), 3)
        lo, hi, p = block_boot_p(x, y, rng, BOOT_COND, BLOCK)
        cond.append({"state": sname, "key": k, "name": F[k]["name"],
                     "ic60": ic, "n": n, "p": p, "ci_lo": lo, "ci_hi": hi,
                     "verdict": "待校正"})

# 条件族内 BH-FDR（只对有 p 值的格子）
idx = [i for i, c in enumerate(cond) if c["p"] is not None]
pc = np.array([cond[i]["p"] for i in idx])
qc = bh_fdr(pc)
for i, q in zip(idx, qc):
    cond[i]["q_cond"] = round(float(q), 4)
    cond[i]["sig_fdr_cond"] = bool(q < 0.05)
for c in cond:
    c.setdefault("q_cond", None)
    c.setdefault("sig_fdr_cond", False)
    if c["p"] is None:
        c["verdict"] = "样本不足"
    else:
        c["verdict"] = ("条件显著" if c["sig_fdr_cond"] and abs(c["ic60"]) >= 0.10
                        else ("显著但效应小" if c["sig_fdr_cond"] else "不显著"))

# 联合 BH-FDR：单因子主检验 + 条件 IC 一起校正（探索性，最严格）
pj = np.concatenate([ps, pc])
qj = bh_fdr(pj)
for i, q in zip(idx, qj[len(ps):]):
    cond[i]["q_joint"] = round(float(q), 4)
    cond[i]["sig_fdr_joint"] = bool(q < 0.05)
for r, q in zip(single, qj[:len(ps)]):
    r["q_joint"] = round(float(q), 4)
    r["sig_fdr_joint"] = bool(q < 0.05)
for c in cond:
    c.setdefault("q_joint", None)
    c.setdefault("sig_fdr_joint", False)

R["conditional"] = cond
R["conditional_fdr"] = {
    "n_tests": int(len(pc)), "n_low_sample": int(len(cond) - len(pc)),
    "raw_sig": int((pc < 0.05).sum()),
    "fdr_sig": int(sum(c["sig_fdr_cond"] for c in cond)),
    "joint_sig": int(sum(c["sig_fdr_joint"] for c in cond)),
    "min_q": round(float(qc.min()), 4) if len(qc) else None,
    "min_q_joint": round(float(qj.min()), 4),
    "top": [{"state": c["state"], "name": c["name"], "ic": c["ic60"], "n": c["n"],
             "q_cond": c["q_cond"], "q_joint": c["q_joint"]}
            for c in sorted([c for c in cond if c["ic60"] is not None],
                            key=lambda z: -abs(z["ic60"]))[:10]],
    "sig_cells": [{"state": c["state"], "name": c["name"], "ic": c["ic60"],
                   "n": c["n"], "p": c["p"], "q_cond": c["q_cond"], "q_joint": c["q_joint"],
                   "ci": [c["ci_lo"], c["ci_hi"]]}
                  for c in cond if c["sig_fdr_cond"]],
    "note": ("条件 IC 是探索性分层：状态由 T 日可知的动量/波动/共振度定义，无 HMM smoothed 前视；"
             "但 8 状态 × 17 因子 = 136 次额外检验必须校正。p 值用移动块 bootstrap "
             f"{BOOT_COND} 次（主检验 {BOOT} 次），均按 60 日重叠修正。"
             f"注意 p 值分辨率下限为 1/{BOOT_COND}，q=0 应读作「低于分辨率下限」而非精确 0；"
             "条件样本重叠、状态定义含阈值、未经样本外检验，显著格子只作假设生成。"),
}
R["joint_fdr"] = {"n_tests": int(len(pj)),
                  "fdr_sig": int((qj < 0.05).sum()),
                  "single_sig": int(sum(r["sig_fdr_joint"] for r in single)),
                  "conditional_sig": int(sum(c["sig_fdr_joint"] for c in cond)),
                  "note": "把主检验与条件 IC 合并为同一族系做 BH 校正，是三者中最严格的口径。"}

R["state_summary"] = []
for sname in STATES:
    rows = [c for c in cond if c["state"] == sname and c["ic60"] is not None]
    top = sorted(rows, key=lambda r: -abs(r["ic60"]))[:5]
    R["state_summary"].append({
        "state": sname, "n_tested": len(rows),
        "n_sig_cond": int(sum(c["sig_fdr_cond"] for c in rows)),
        "n_sig_joint": int(sum(c["sig_fdr_joint"] for c in rows)),
        "top": [{"name": t["name"], "ic": t["ic60"], "n": t["n"],
                 "q_cond": t["q_cond"]} for t in top]})
log(f"[L1] 条件 IC {len(pc)} 次检验：raw p<0.05 = {R['conditional_fdr']['raw_sig']}，"
    f"族内 FDR 后 = {R['conditional_fdr']['fdr_sig']}，联合 FDR 后 = {R['conditional_fdr']['joint_sig']}")


def avg_abs_corr(keys, mask):
    sub = pd.DataFrame({k: A[k] for k in keys}).loc[START:]
    sub = sub[mask.reindex(sub.index).fillna(False)]
    cc = sub.corr(method="spearman", min_periods=60).to_numpy(float)
    iu = np.triu_indices(len(keys), 1)
    v = cc[iu]
    return round(float(np.nanmean(np.abs(v))), 3), int(len(sub))


R["regime_corr"] = {
    "panic": avg_abs_corr(DAILY, STATES["底部共振>=60"]),
    "normal": avg_abs_corr(DAILY, STATES["底部共振<60"]),
}

# 试验日志：用于估计「有效检验数」，应对序贯检验带来的隐性多重比较
R["experiment_log"] = {
    "families": [
        {"name": "单因子主检验", "n": len(ps), "p_method": f"block bootstrap {BOOT}",
         "fdr": "族内 BH", "sig": R["meta"]["n_sig_fdr"]},
        {"name": "条件 IC（8 状态 × 17 因子）", "n": len(pc),
         "p_method": f"block bootstrap {BOOT_COND}", "fdr": "族内 BH + 联合 BH",
         "sig": R["conditional_fdr"]["fdr_sig"]},
        {"name": "多轨道稳健性（探索性，不计入正式族系）",
         "n": len(DAILY) * len(C.FACTOR_ROBUST_TRACKS), "p_method": "正态近似(ne=n/60)",
         "fdr": "族内 BH", "sig": None},
    ],
    "note": ("本项目采用「跑一批 → 看结果 → 加指标 → 再跑」的迭代方式，属于序贯检验；"
             "即使每次都做 BH，跨轮次的隐性试验次数仍未被计入，固定样本 FDR 偏乐观。"
             "后续每轮新增指标/变换/状态都应在此登记，用聚类数估计有效检验数，"
             "并在样本外确认后才允许改生产口径。"),
}


# ---------------- 6. 多轨道稳健性：换变换后结论是否改变 ----------------
robust = []
main_ic = {}
for r in single:
    if r["h"] == 60:
        main_ic[r["key"]] = r["ic"]
for tn in C.FACTOR_ROBUST_TRACKS:
    rows = []
    for k in DAILY:
        col = f"{k}__{tn}"
        if col not in MAT.columns:
            continue
        x = MAT[col].loc[START:].to_numpy(float)
        y = fwd[60].loc[START:].to_numpy(float)
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < C.AUDIT_MIN_OBS:
            continue
        ic = spearman(x[ok], y[ok])
        rows.append({"key": k, "name": F[k]["name"], "ic": round(ic, 4),
                     "p": round(p_approx(ic, int(ok.sum()), 60), 4), "n": int(ok.sum())})
    qs = bh_fdr([r["p"] for r in rows])
    for r, q in zip(rows, qs):
        r["q"] = round(float(q), 4)
        r["sig_fdr"] = bool(q < 0.05)
    arr_main = np.array([main_ic.get(r["key"], np.nan) for r in rows])
    arr_t = np.array([r["ic"] for r in rows])
    ok = np.isfinite(arr_main) & np.isfinite(arr_t)
    rho = spearman(arr_main[ok], arr_t[ok], min_n=5) if ok.sum() >= 5 else None
    robust.append({
        "track": tn, "n": len(rows),
        "abs_ic": round(float(np.mean([abs(r["ic"]) for r in rows])), 4),
        "raw_sig": int(sum(r["p"] < 0.05 for r in rows)),
        "fdr_sig": int(sum(r["sig_fdr"] for r in rows)),
        "min_q": round(float(min(r["q"] for r in rows)), 4) if rows else None,
        "ic_rank_corr_with_main": round(float(rho), 3) if rho is not None and np.isfinite(rho) else None,
        "sign_flip_rate": round(float(np.mean([np.sign(a) != np.sign(b)
                                               for a, b in zip(arr_main[ok], arr_t[ok])]) * 100), 1)
        if ok.sum() else None,
        "top": [{"name": r["name"], "ic": r["ic"], "q": r["q"]}
                for r in sorted(rows, key=lambda z: -abs(z["ic"]))[:3]],
    })
R["track_robustness"] = {
    "tracks": robust,
    "note": ("同一因子换变换轨道后 60 日 IC 的秩相关与符号翻转率；用正态近似 p（ne=n/60）"
             "以便快速扫全轨道，只作稳健性描述，不与主检验的 bootstrap p 混用。"
             "轨道由经济含义先验指定，不按样本内 IC 挑选。"),
}


# ---------------- 7. 贝叶斯示范（与首版完全一致）----------------
def beta_log(a, b):
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def beta_cdf(x, a, b, n=2000):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    t = np.linspace(1e-9, x, n)
    lp = (a - 1) * np.log(t) + (b - 1) * np.log1p(-t) - beta_log(a, b)
    w = np.full(n, (x - 1e-9) / (n - 1))
    w[0] = w[-1] = w[0] / 2
    mx = lp.max()
    return float(np.sum(w * np.exp(lp - mx)) * math.exp(mx))


def beta_quantile(q, a, b):
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if beta_cdf(mid, a, b) < q: lo = mid
        else: hi = mid
    return round((lo + hi) / 2 * 100, 1)


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    e = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round((c - e) * 100, 1), round((c + e) * 100, 1)


n, k, pbase = 15, 3, 0.240
conj = []
for kap in [5, 10, 25, 50]:
    a0, b0 = kap * pbase, kap * (1 - pbase)
    a1, b1 = a0 + k, b0 + n - k
    conj.append({"kappa": kap, "prior_mean": round(pbase * 100, 1),
                 "post_mean": round(a1 / (a1 + b1) * 100, 1),
                 "ci95": [beta_quantile(0.025, a1, b1), beta_quantile(0.975, a1, b1)],
                 "p_gt_base": round(1 - beta_cdf(pbase, a1, b1), 3)})
R["bayes_conjugate"] = {"case": "共振>=85 且 confirm=0 → 60日不破位且上涨(A口径)",
                        "n": n, "k": k, "raw_pct": round(k / n * 100, 1),
                        "base_pct": pbase * 100, "wilson": wilson(k, n), "rows": conj}
a0, b0 = 10 * pbase, 10 * (1 - pbase)
log_l0 = k * math.log(pbase) + (n - k) * math.log(1 - pbase)
log_l1 = beta_log(a0 + k, b0 + n - k) - beta_log(a0, b0)
R["bayes_factor"] = {"log_bf10": round(log_l1 - log_l0, 3),
                     "bf10": round(math.exp(log_l1 - log_l0), 3),
                     "reading": "BF10<1 表示数据更支持「该档与基准无差别」"}

y60 = fwd[60]
bands = [("共振<30", lambda b: b < 30), ("30<=共振<60", lambda b: (b >= 30) & (b < 60)),
         ("60<=共振<85", lambda b: (b >= 60) & (b < 85)), ("共振>=85", lambda b: b >= 85)]
pool = []
for label, fn in bands:
    mask = fn(turn["bottom"])
    sub = pd.DataFrame({"y": y60}).loc[START:]
    v = sub[mask.reindex(sub.index).fillna(False)]["y"].dropna().iloc[::60]
    pool.append({"band": label, "n": int(len(v)), "k": int((v > 0).sum()),
                 "raw_pct": round(float((v > 0).mean()) * 100, 1) if len(v) else None})
rates = np.array([p["k"] / p["n"] for p in pool if p["n"] > 0], float)
ns = np.array([p["n"] for p in pool if p["n"] > 0], float)
mu = float(np.average(rates, weights=ns))
var_obs = float(np.average((rates - mu) ** 2, weights=ns))
var_binom = float(np.average(mu * (1 - mu) / ns, weights=ns))
var_true = max(var_obs - var_binom, 1e-6)
kappa_eb = max(1.0, mu * (1 - mu) / var_true - 1)
alpha, beta_ = mu * kappa_eb, (1 - mu) * kappa_eb
for p in pool:
    a1, b1 = alpha + p["k"], beta_ + p["n"] - p["k"]
    p["pool_all"] = round(mu * 100, 1)
    p["partial_pct"] = round(a1 / (a1 + b1) * 100, 1)
    p["shrink"] = round((p["raw_pct"] - p["partial_pct"]), 1) if p["raw_pct"] is not None else None
R["bayes_hierarchical"] = {"mu": round(mu * 100, 1), "kappa_eb": round(kappa_eb, 1),
                           "alpha": round(alpha, 2), "beta": round(beta_, 2), "bands": pool}


# ---------------- 8. 摘要 ----------------
best = sorted([r for r in single if r["h"] == 60 and r["sig_fdr"]],
              key=lambda r: -abs(r["ic"]))
R["summary"] = {
    "n_daily": len(DAILY), "n_all": len(F),
    "n_tests": R["meta"]["n_tests"], "n_sig_raw": R["meta"]["n_sig_raw"],
    "n_sig_fdr": R["meta"]["n_sig_fdr"],
    "n_cond_tests": R["conditional_fdr"]["n_tests"],
    "n_cond_sig_fdr": R["conditional_fdr"]["fdr_sig"],
    "n_cond_sig_joint": R["conditional_fdr"]["joint_sig"],
    "cum3": R["pca"]["cum3"], "cum5": R["pca"]["cum5"],
    "top_fdr": [{"name": r["name"], "h": r["h"], "ic": r["ic"], "q": r["q"], "ir": r["ir"]}
                for r in best[:8]],
    "regime_corr": R["regime_corr"], "dupes": len(dupes),
    "verdict_counts": R["verdict_counts"],
}
R["meta"]["elapsed_sec"] = round(time.time() - T0, 1)

write_json_js("efficacy_audit", R, "EFFICACY_AUDIT")

print("=== L1 常驻审计完成 ===")
print(f"因子总数 {len(F)} / 日频入模 {len(DAILY)} / 排除 {len(F)-len(DAILY)}；耗时 {R['meta']['elapsed_sec']}s")
print(f"主检验 {len(ps)} 次：raw p<0.05 = {R['meta']['n_sig_raw']}，BH-FDR 后 = {R['meta']['n_sig_fdr']}")
print(f"条件 IC {len(pc)} 次：raw p<0.05 = {R['conditional_fdr']['raw_sig']}，"
      f"族内 FDR 后 = {R['conditional_fdr']['fdr_sig']}，联合 FDR 后 = {R['conditional_fdr']['joint_sig']}")
print(f"联合族系 {R['joint_fdr']['n_tests']} 次，FDR 后显著 = {R['joint_fdr']['fdr_sig']}")
print(f"PCA 前3解释 {R['pca']['cum3']}% / 前5 {R['pca']['cum5']}% / 完全共线对 {len(dupes)}")
print("判定分布:", R["verdict_counts"])
print("多轨道稳健性:")
for t in robust:
    print(f"  {t['track']:12s} |IC|={t['abs_ic']:.4f} FDR显著={t['fdr_sig']} "
          f"与主轨道秩相关={t['ic_rank_corr_with_main']} 符号翻转={t['sign_flip_rate']}%")
print("状态依赖 top3:")
for s in R["state_summary"]:
    print("  ", s["state"], "显著", s["n_sig_cond"], "/", s["n_tested"], "→",
          " / ".join(f"{t['name'][:8]}({t['ic']:+.2f},q={t['q_cond']})" for t in s["top"][:3]))
print("输出 data/efficacy_audit.json / .js（window.EFFICACY_AUDIT）")
