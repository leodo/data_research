# -*- coding: utf-8 -*-
"""L0/L1 因子研究公共层：PIT 变换、统计检验、json/js 双写。

只放纯函数，不产生副作用（除 write_json_js）。被 build_factor_matrix.py（L0）
与 audit_efficacy.py（L1）共用，避免两个脚本各自复制一份口径而漂移。
与 core.py 的分工：core 面向生产构建（缓存/交易日历/分位），本模块面向研究审计。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


def log(msg):
    print(msg, flush=True)


# ---------------- 基础统计 ----------------
def spearman(x, y, min_n=30):
    """Spearman 秩相关；样本不足或常数列返回 nan。

    min_n 可下调用于「因子间 IC 排名的秩相关」这类短向量（如 17 个因子），
    默认 30 保持与首版审计口径一致。
    """
    ok = np.isfinite(np.asarray(x, float)) & np.isfinite(np.asarray(y, float))
    if ok.sum() < min_n:
        return float("nan")
    a = np.asarray(x, float)[ok]
    b = np.asarray(y, float)[ok]
    ra = pd.Series(a).rank().values
    rb = pd.Series(b).rank().values
    if ra.std() < 1e-9 or rb.std() < 1e-9:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def p_approx(ic, n, h):
    """重叠样本修正后的双侧显著性正态近似（ne = n/h），非精确检验。"""
    ne = max(4.0, n / h)
    if not np.isfinite(ic) or abs(ic) >= 1:
        return 1.0
    t = abs(ic) * math.sqrt(ne - 2) / math.sqrt(max(1e-9, 1 - ic * ic))
    return min(1.0, math.erfc(t / math.sqrt(2)))


def ks_stat(a, b):
    a = np.sort(np.asarray(a, float)[np.isfinite(np.asarray(a, float))])
    b = np.sort(np.asarray(b, float)[np.isfinite(np.asarray(b, float))])
    if len(a) < 20 or len(b) < 20:
        return float("nan")
    allv = np.concatenate([a, b])
    ca = np.searchsorted(a, allv, side="right") / len(a)
    cb = np.searchsorted(b, allv, side="right") / len(b)
    return float(np.max(np.abs(ca - cb)))


def year_ks(s, start_year=2016):
    """相邻年份分布 KS 距离均值；越大表示跨年量纲/分布漂移越严重。"""
    s = s.dropna()
    yrs = sorted(set(s.index.year))
    yrs = [y for y in yrs if y >= start_year]
    vals = [ks_stat(s[s.index.year == y].to_numpy(float),
                    s[s.index.year == y + 1].to_numpy(float))
            for y in yrs[:-1] if (y + 1) in yrs]
    vals = [v for v in vals if np.isfinite(v)]
    return (round(float(np.mean(vals)), 3) if vals else None), len(vals)


def year_medians(s, start_year=2016, min_n=30):
    out = []
    for y in sorted(set(s.dropna().index.year)):
        if y < start_year:
            continue
        v = s[s.index.year == y].to_numpy(float)
        v = v[np.isfinite(v)]
        if len(v) >= min_n:
            out.append((int(y), round(float(np.median(v)), 4)))
    return out


def bh_fdr(ps):
    """Benjamini-Hochberg 校正，返回 q 数组（与输入同序）。"""
    p = np.asarray(ps, float)
    n = len(p)
    if n == 0:
        return np.array([])
    order = np.argsort(p)
    q = np.empty(n, float)
    for rank, i in enumerate(order, 1):
        q[i] = min(1.0, p[i] * n / rank)
    srt = np.minimum.accumulate(q[order][::-1])[::-1]
    q[order] = srt
    return q


def block_boot_p(x, y, rng, n_boot=1200, block=60):
    """移动块 bootstrap 的 Spearman CI 与双侧 p；重叠样本下比 t 检验稳健。"""
    n = len(x)
    nb = math.ceil(n / block)
    blocks = [(i * block, min((i + 1) * block, n)) for i in range(nb)]
    vals = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, nb, nb)
        idx = np.concatenate([np.arange(blocks[j][0], blocks[j][1]) for j in pick])
        vals[b] = spearman(x[idx], y[idx])
    vals = vals[np.isfinite(vals)]
    if not len(vals):
        return None, None, 1.0
    lo, hi = np.percentile(vals, [2.5, 97.5])
    p = 2.0 * min(float((vals <= 0).mean()), float((vals >= 0).mean()))
    return round(float(lo), 4), round(float(hi), 4), round(min(1.0, p), 4)


# ---------------- PIT 变换（t 只用 t 及之前的信息）----------------
TRANSFORM_DEFS = [
    {"key": "raw", "name": "原始水平", "window": None, "unit": "原始单位",
     "formula": "卡片 history.value 原值", "role": "保留经济含义/理论锚"},
    {"key": "pct_exp", "name": "全历史 expanding 分位", "window": None, "unit": "0-100",
     "formula": "卡片 history.pct（expanding(min_periods=20).rank(pct)）", "role": "生产主口径，保证历史可比"},
    {"key": "pct_r126", "name": "滚动 126 日分位", "window": 126, "unit": "0-100",
     "formula": "raw.rolling(126,min_periods=60).rank(pct)", "role": "短窗口，抑制趋势型漂移"},
    {"key": "pct_r250", "name": "滚动 250 日分位", "window": 250, "unit": "0-100",
     "formula": "raw.rolling(250,min_periods=60).rank(pct)", "role": "年度相对位置"},
    {"key": "pct_r500", "name": "滚动 500 日分位", "window": 500, "unit": "0-100",
     "formula": "raw.rolling(500,min_periods=120).rank(pct)", "role": "两年相对位置"},
    {"key": "z_r250", "name": "滚动 250 日 Z-score", "window": 250, "unit": "标准差",
     "formula": "(raw − rolling250 mean)/rolling250 std", "role": "与分位对照"},
    {"key": "robust_r250", "name": "稳健标准化(中位/IQR)", "window": 250, "unit": "IQR",
     "formula": "(raw − rolling250 median)/rolling250 IQR", "role": "厚尾因子抗异常值"},
    {"key": "detrend_r500", "name": "滚动去趋势", "window": 500, "unit": "原始单位",
     "formula": "raw − rolling500 median", "role": "剥离慢变水平"},
    {"key": "chg60", "name": "60 日变化", "window": 60, "unit": "原始单位",
     "formula": "raw − raw.shift(60)", "role": "边际动量，平稳但不代表水平"},
    {"key": "logmod", "name": "符号保留对数", "window": None, "unit": "对数",
     "formula": "sign(raw)·log1p(|raw|)", "role": "单调变换对照组"},
    {"key": "logit_pct", "name": "分位 logit", "window": None, "unit": "logit",
     "formula": "log(p/(1−p)), p=pct_exp/100 clip[0.005,0.995]", "role": "拉开尾部分位"},
    {"key": "dual_gap", "name": "双尺度差", "window": 250, "unit": "分位点",
     "formula": "pct_r250 − pct_exp", "role": "长期位置 vs 年度边际的偏离"},
]
TRANSFORM_KEYS = [t["key"] for t in TRANSFORM_DEFS]


def make_transforms(raw: pd.Series, pct_exp: pd.Series) -> dict[str, pd.Series]:
    """全部为 trailing / expanding，无未来函数。"""
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


def detect_freq(index) -> str:
    """按观测中位间隔自动判频（与 audit_efficacy.py 口径一致）。"""
    gaps = np.diff(index.values).astype("timedelta64[D]").astype(float)
    med = float(np.median(gaps))
    return ("日" if med <= 3 else ("周" if med <= 15 else "月")), round(med, 1)


# ---------------- 输出 ----------------
def write_json_js(name: str, obj: dict, var: str) -> tuple[int, int]:
    """写 data/<name>.json 与 data/<name>.js（window.<var>=...），保证精确字符串同步。

    allow_nan=False 强制合法 JSON；js 与 json 内容逐字节一致，便于用 == 精确校验。
    """
    txt = json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    (DATA / f"{name}.json").write_text(txt, encoding="utf-8")
    (DATA / f"{name}.js").write_text(f"window.{var}={txt};", encoding="utf-8")
    return len(txt), len(txt) + len(var) + len("window.=;")


def load_mood() -> dict:
    return json.loads((DATA / "mood_data.json").read_text(encoding="utf-8"))
