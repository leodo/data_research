# -*- coding: utf-8 -*-
"""L0 因子变换层：生成统一、双轨、PIT 的因子矩阵（只读 / 零网络 / 不进综合分）。

输入  data/mood_data.json（24 张卡片的 history.value + history.pct、composite、hs300_close）
输出  data/factor_matrix.csv   宽表：交易日 × (入模因子 × 12 变换) + hs300，供 L1 与外部分析
      data/factor_matrix.json / .js（window.FACTOR_MATRIX）  元数据 + 当前读数 + 图表数据

设计纪律
--------
1. 双轨并存、绝不替换：pct_exp 就是卡片现用的全历史 expanding 分位，原样保留，
   保证综合分 34.65(8/8) 与已交付报告的历史可比性；其它 11 种变换并列生成，
   只写进独立因子矩阵，从不回写 mood_data.json。
2. 全部 PIT：expanding / rolling 都只用 t 及之前的数据；对齐用 reindex+ffill(limit)，
   不做 bfill、不做全样本标准化、不做全样本分位切档。启动时会抽样回放验证。
3. 混频不造伪独立观测：月频/周频因子只登记元数据，不进日频矩阵；ffill 有上限。
4. 元数据诚实：卡片没有 publish_date（源端发布日期），写 null 并标注，
   用 as_of / stale_days / history_start / coverage_pct 近似，绝不伪造。

用法：python build_factor_matrix.py   （--force 由 DAG 透传，本脚本只读，忽略该参数）
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from workbench import config as C
from workbench.factor_utils import (DATA, TRANSFORM_DEFS, TRANSFORM_KEYS, bh_fdr, detect_freq,
                          log, make_transforms, spearman, write_json_js, year_ks,
                          year_medians)

START = pd.Timestamp(C.FACTOR_START)
m = json.loads((DATA / "mood_data.json").read_text(encoding="utf-8"))
hs = pd.Series({pd.Timestamp(d): float(v) for d, v in m["hs300_close"]}).sort_index()
DATES = hs.index
BASE_DATE = DATES[-1]
log(f"[L0] 交易日历 {DATES[0].date()} ~ {BASE_DATE.date()}（{len(DATES)} 日），审计起点 {START.date()}")


# ---------------- 1. 收集 25 个因子的原始值 + 生产分位 ----------------
FACTORS = {}
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
        declared = p.get("freq")
        auto, medgap = detect_freq(raw.index)
        freq = {"monthly": "月", "weekly": "周", "daily": "日"}.get(declared, auto)
        FACTORS[k] = {
            "key": k, "name": p["name"], "group": grp, "unit": str(p.get("unit") or "—"),
            "kind": C.FACTOR_KINDS.get(k, "未分类"), "reverse": bool(p.get("reverse")),
            "source": str(p.get("source") or "")[:180],
            "freq": freq, "freq_source": "declared" if declared else "auto",
            "medgap": medgap, "raw": raw, "pct_exp": pct,
            "pct_exp_source": "卡片 history.pct（生产口径）",
            "score": p.get("score"),
        }

# 综合情绪分：周频，只登记元数据与变换，不入日频矩阵
ch = m["composite"]["history"]
cs = pd.Series({pd.Timestamp(x["date"]): float(x["score"])
                for x in ch if x.get("score") is not None}).sort_index()
cs = cs[~cs.index.duplicated(keep="last")]
FACTORS["composite"] = {
    "key": "composite", "name": "综合情绪分(8等权)", "group": "汇总", "unit": "分",
    "kind": C.FACTOR_KINDS["composite"], "reverse": False, "source": "8 指标情绪分等权",
    "freq": "周", "freq_source": "auto", "medgap": detect_freq(cs.index)[1],
    "raw": cs, "pct_exp": cs.expanding(min_periods=60).rank(pct=True) * 100,
    "pct_exp_source": "自算 expanding（卡片未输出 pct）", "score": m["composite"]["score"],
}
log(f"[L0] 收集因子 {len(FACTORS)} 个")


# ---------------- 2. 生成 12 种 PIT 变换并对齐到交易日历 ----------------
ALIGN = {}
for k, f in FACTORS.items():
    t = make_transforms(f["raw"], f["pct_exp"])
    ALIGN[k] = {n: s.reindex(DATES).ffill(limit=C.FACTOR_FFILL_LIMIT[f["freq"]])
                for n, s in t.items()}


# ---------------- 3. PIT 抽样回放（真正的无前视自检）----------------
def pit_check():
    """对代表因子/变换，用 t 及之前的 raw 重算 t 日值，与全量序列比对。"""
    rng = np.random.default_rng(C.AUDIT_SEED)
    checks, maxdiff = [], 0.0
    for k in ["rp", "etf300_ratio", "hs300_rsi", "below_net"]:
        if k not in FACTORS:
            continue
        raw = FACTORS[k]["raw"]
        for tn in ["pct_r126", "pct_r250", "z_r250", "chg60"]:
            full = ALIGN[k][tn]
            cand = full.loc[START:].dropna()
            if len(cand) < 100:
                continue
            for t in rng.choice(cand.index.to_numpy(), size=4, replace=False):
                t = pd.Timestamp(t)
                sub = raw.loc[:t]
                if len(sub) < 130:
                    continue
                re = make_transforms(sub, FACTORS[k]["pct_exp"].loc[:t])[tn]
                if not len(re) or not np.isfinite(re.iloc[-1]):
                    continue
                d = abs(float(re.iloc[-1]) - float(full.loc[t]))
                maxdiff = max(maxdiff, d)
                checks.append({"key": k, "t": tn, "date": str(t.date()), "diff": round(d, 9)})
    return {"n": len(checks), "max_abs_diff": round(maxdiff, 9),
            "passed": bool(checks and maxdiff < 1e-6), "samples": checks[:6],
            "note": "rolling/expanding 变换用截至 t 的子样本重算末值，与全量序列在 t 处比对；"
                    "差值 0 表示无未来函数。"}


PIT = pit_check()
log(f"[L0] PIT 回放 {PIT['n']} 次，最大差 {PIT['max_abs_diff']}，通过={PIT['passed']}")


# ---------------- 4. 元数据、覆盖率、漂移统计 ----------------
def recommend(kind, ks, freq):
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
    if freq != "日":
        b += "；低频因子不进日频矩阵（避免 ffill 造伪独立观测）"
    elif ks is not None and ks >= C.FACTOR_PRIORITY_KS[1]:
        b += "；高漂移，必须并列年内去均值 IC 复核"
    elif ks is not None and ks < C.FACTOR_PRIORITY_KS[0]:
        b += "；低漂移，变换增益有限，可不改"
    return b


def stale_trade_days(as_of):
    """as_of 距基准日的交易日数（按真实交易日历数，不用自然日）。"""
    if as_of >= BASE_DATE:
        return 0
    return int((DATES > as_of).sum())


records, excluded = [], []
for k, f in FACTORS.items():
    a = ALIGN[k]
    raw, pe, r126, r250 = a["raw"], a["pct_exp"], a["pct_r126"], a["pct_r250"]
    sub = pe.loc[START:]
    coverage = round(float(sub.notna().mean()) * 100, 1)
    in_matrix = (f["freq"] == "日" and coverage / 100 >= C.FACTOR_MIN_COVERAGE)
    ks_raw, _ = year_ks(raw.dropna())
    ks_exp, _ = year_ks(pe.dropna())
    ks_r126, _ = year_ks(r126.dropna())
    ks_r250, _ = year_ks(r250.dropna())
    rs = raw.loc[START:].dropna()
    trend_rho = spearman(rs.to_numpy(float), np.arange(len(rs), dtype=float)) if len(rs) >= 100 else None
    as_of = f["raw"].index[-1]
    cur = {}
    for tn in TRANSFORM_KEYS:
        s = a[tn].dropna()
        cur[tn] = round(float(s.iloc[-1]), 4) if len(s) else None
    # 逐年路径：原始值中位数 / expanding 分位中位数 / rolling126 分位中位数
    meds = dict(year_medians(raw))
    pmeds = dict(year_medians(pe, min_n=30))
    qmeds = dict(year_medians(r126, min_n=30))
    path = [{"year": y, "raw_med": v, "pct_exp_med": pmeds.get(y), "pct_r126_med": qmeds.get(y)}
            for y, v in meds.items()]
    rec = {
        "key": k, "name": f["name"], "group": f["group"], "unit": f["unit"],
        "kind": f["kind"], "reverse": f["reverse"], "source": f["source"],
        "freq": f["freq"], "freq_source": f["freq_source"], "medgap": f["medgap"],
        "transform": "12 种 PIT 变换并列（见 transforms）",
        "window": {"pct_exp": "全历史 expanding(min_periods=20)",
                   "pct_r126": 126, "pct_r250": 250, "pct_r500": 500,
                   "z_r250": 250, "robust_r250": 250, "detrend_r500": 500, "chg60": 60},
        "pct_exp_source": f["pct_exp_source"],
        "history_start": str(f["raw"].index[0].date()),
        "as_of": str(as_of.date()),
        "publish_date": None,
        "publish_date_status": "源端未提供发布日期；以 as_of 近似，存在 1~3 日发布滞后",
        "stale_trade_days": stale_trade_days(as_of),
        "stale_calendar_days": int((BASE_DATE - as_of).days),
        "n_obs": int(len(f["raw"])), "n_obs_aligned": int(len(sub)),
        "coverage_pct": coverage,
        "in_matrix": in_matrix,
        "score": f["score"],
        "ks_raw": ks_raw, "ks_pct_exp": ks_exp, "ks_pct_r126": ks_r126, "ks_pct_r250": ks_r250,
        "trend_rho": round(float(trend_rho), 3) if trend_rho is not None and np.isfinite(trend_rho) else None,
        "priority": ("高" if ks_raw is not None and ks_raw >= C.FACTOR_PRIORITY_KS[1]
                     else ("中" if ks_raw is not None and ks_raw >= C.FACTOR_PRIORITY_KS[0] else "低")),
        "recommend": recommend(f["kind"], ks_raw, f["freq"]),
        "current": cur,
        "year_path": path,
    }
    records.append(rec)
    if not in_matrix:
        excluded.append({"key": k, "name": f["name"], "freq": f["freq"],
                         "coverage_pct": coverage,
                         "reason": ("非日频：不进日频矩阵，避免 ffill 造伪独立观测"
                                    if f["freq"] != "日" else "2016 起覆盖率不足 80%")})
# 保持收集顺序（= cards 的 order/macro_order/signal_order，composite 末尾）。
# 不要按 KS / 覆盖率重排：L1 的 block bootstrap 按此顺序消耗随机数，
# 重排会让首版 51 次主检验的 p 值不可复现。展示层需要排序请自行 sort。
L0_ORDER_NOTE = "按生产卡片顺序（panels→macro→signal→composite），勿重排：L1 bootstrap 依赖此顺序"
IN = [r["key"] for r in records if r["in_matrix"]]
log(f"[L0] 入模日频因子 {len(IN)} / 排除 {len(excluded)}")


# ---------------- 5. 写宽表 CSV（L1 与数据分析的唯一因子输入）----------------
cols = {}
for k in IN:
    for tn in TRANSFORM_KEYS:
        cols[f"{k}__{tn}"] = ALIGN[k][tn].loc[START:]
cols["hs300"] = hs.loc[START:]
MAT = pd.DataFrame(cols, index=hs.loc[START:].index)
MAT.index.name = "date"
MAT.to_csv(DATA / "factor_matrix.csv", float_format="%.6f", encoding="utf-8")
log(f"[L0] 宽表 {MAT.shape[0]} 行 × {MAT.shape[1]} 列 -> data/factor_matrix.csv")


# ---------------- 6. 变换层验收：跨年 KS / 当前读数覆盖 ----------------
ks_by_t = []
for tn in TRANSFORM_KEYS:
    vals = []
    for k in IN:
        v, _ = year_ks(ALIGN[k][tn].dropna())
        if v is not None:
            vals.append(v)
    ks_by_t.append({"key": tn, "name": next(t["name"] for t in TRANSFORM_DEFS if t["key"] == tn),
                    "ks_mean": round(float(np.mean(vals)), 3) if vals else None, "n": len(vals)})

REP = [k for k in ["rp", "etf300_ratio", "below_net", "hs300_rsi", "qschsl"] if k in IN]
rep_paths = [{"key": k, "name": FACTORS[k]["name"],
              "path": next(r["year_path"] for r in records if r["key"] == k)} for k in REP]

# 双轨一致性：pct_exp 列必须与卡片 history.pct 的 ffill 完全相同
sync_ok = True
for k in IN:
    direct = FACTORS[k]["pct_exp"].reindex(DATES).ffill(
        limit=C.FACTOR_FFILL_LIMIT[FACTORS[k]["freq"]]).loc[START:]
    if not np.allclose(MAT[f"{k}__pct_exp"].fillna(-999).to_numpy(float),
                       direct.fillna(-999).to_numpy(float), atol=1e-9):
        sync_ok = False
        break

OUT = {
    "meta": {
        "layer": "L0 因子变换层",
        "base_date": str(BASE_DATE.date()), "start": str(START.date()),
        "n_trade_days": int(len(DATES)), "n_days_in_matrix": int(len(MAT)),
        "n_factors_all": len(FACTORS), "n_in_matrix": len(IN),
        "n_transforms": len(TRANSFORM_KEYS), "n_columns": int(MAT.shape[1]),
        "main_track": C.FACTOR_MAIN_TRACK, "robust_tracks": list(C.FACTOR_ROBUST_TRACKS),
        "calendar_source": "data/mood_data.json hs300_close（2002 起真实交易日）",
        "csv_file": "data/factor_matrix.csv",
        "pit_check": PIT, "prod_track_sync": bool(sync_ok),
        "factor_order": L0_ORDER_NOTE,
        "ffill_limit": C.FACTOR_FFILL_LIMIT, "min_coverage": C.FACTOR_MIN_COVERAGE,
        "note": ("pct_exp 是生产主口径、原样保留；其它变换并列生成，绝不替换。"
                 "CSV 只含 2016 起交易日；hs300 列为基准收盘价，不是因子。"
                 "外部分析请勿把 ffill 产生的重复值当独立观测。"),
    },
    "transforms": TRANSFORM_DEFS,
    "factors": records,
    "excluded": excluded,
    "charts": {"ks_by_transform": ks_by_t, "year_paths": rep_paths},
    "caveats": [
        "本层不回答「哪种变换更有效」：跨年 KS 降低是分位压平分布的设计使然，不等于预测信息增加。",
        "变换必须按经济含义先验指定；2026-11-21 诊断中 204 个评价单元 raw p<0.05 = 0、BH-FDR 后 min q = 0.998，"
        "walk-forward 样本内挑最佳变换在样本外的平均排名 6.0/12（随机 6.5），故禁止按样本内 IC 挑变换。",
        "卡片 history 只有 date/value/pct，没有源端 publish_date；本层如实写 null，用 as_of 与 stale_days 近似，"
        "月频宏观数据还存在统计口径修订（M1 2024 年调整），跨时代比绝对值仍有偏差。",
        "单调变换（logmod / logit_pct）对 Spearman IC 严格不变，只影响 Pearson 相关与分布形态；"
        "分位类变换是路径依赖映射，同一原始值在不同时点会落到不同分位。",
        "L0 只做变换与元数据，不做任何有效性判定；判定与多重检验校正属于 L1。",
    ],
}
njson, njs = write_json_js("factor_matrix", OUT, "FACTOR_MATRIX")
log(f"[L0] factor_matrix.json {njson/1024:.1f}KB / .js {njs/1024:.1f}KB，生产轨道同步={sync_ok}")
log("[L0] 完成（未写 mood_data.json / 未改综合分 / 未改共振板）")
