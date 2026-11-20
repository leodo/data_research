# -*- coding: utf-8 -*-
"""
顶底共振信号板：多维度极值 + 拐点确认 + 前瞻收益回测
=====================================================
解决的问题：现有 13 张卡片回答的都是「当前处在历史什么分位」（左侧赔率），
没有一张回答「极值之后有没有转向」。本脚本把多个不同来源的序列统一成
fear 分位（0=极热/顶部，100=极冷/底部），构造：

  bottom_score = fear>=70 的维度数 ÷ 有效维度数 ×100   （底部共振度）
  top_score    = fear<=20 的维度数 ÷ 有效维度数 ×100   （顶部共振度）
  confirm      = 独立的「价格/宽度已转向」确认条件数(0~3)
  divergence   = 指数创60日新低但市场宽度未同步创新低（底部动能背离）

13 个维度：股债风险溢价、破净率、200日宽度、新高新低差、换手率、PCR、
两融60日增速、全A PB(月频)、股基3年年化、沪深300 BIAS20、RSI14，
以及 2026-11-20 新增的阿姆氏指标 TRIN10、麦克莱伦摆动值（后两者由
build_arms.py 产出，缺失时自动跳过，不影响其余 11 维）。
分位用全历史 expanding(min_periods=250)，与情绪面板口径一致；
月频序列最多前向填充 45 个交易日、日频最多 10 日，避免停更数据无限前推。

**TRIN 极端单边日双向屏蔽（2026-11-20 复核补充）**：TRIN 的分子是
涨跌家数比，在 A 股齐涨齐跌时会失去上界，产生两种镜像失真：
  普跌日 涨占比 <TURNING_ARMS_PANIC_ADV    → 家数比趋近0 → TRIN 机械偏低（不是买盘强劲）
  普涨日 涨占比 >TURNING_ARMS_EUPHORIA_ADV → 家数比爆炸  → TRIN 机械偏高（不是恐慌抛售）
实测全历史 TRIN>2 共 96 日，其中 67 日为普涨日、仅 1 日为恐慌日，故
「TRIN 高位 = 恐慌超卖/底部机会」在 A 股不成立。两种单边日都把该日
TRIN 维度置空，不计入有效维度数；维度明细仍列出但标注剔除原因。

历史回测：以沪深300 收盘价计算未来 20 / 60 交易日收益，分「底部拐点触发 /
底部极值未确认 / 顶部警戒 / 全样本基准」四组统计胜率与平均收益；
连续触发段按 20 交易日间隔去重，避免重叠样本夸大显著性。
**这是赔率统计，不是择时信号**：重叠样本、未计交易成本、阈值由本脚本设定，
存在过拟合风险；极值可长期钝化，政策与盈利才决定反弹还是反转。

输出：data/mood_turning.csv      (date, bottom_score, top_score, confirm, ...)
      data/mood_turning_meta.json(当前状态 / 维度明细 / 回测 / 触发日)
      data/mood_tech.csv         (沪深300 技术位置，供独立卡片)
用法：python build_turning.py    （纯本地计算，零网络请求）
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from config import (TURNING_ARMS_EUPHORIA_ADV, TURNING_ARMS_PANIC_ADV,
                    TURNING_FEAR_PCT, TURNING_FWD_WINS, TURNING_MIN_DIMS,
                    TURNING_MIN_HIST, TURNING_TOP_PCT, TURNING_TRIGGER,
                    TURNING_TRIGGER_GAP)
from core import DATA, TODAY, log

OUT_CSV = DATA / "mood_turning.csv"
OUT_META = DATA / "mood_turning_meta.json"
OUT_TECH = DATA / "mood_tech.csv"
START = "2015-01-01"     # 市场宽度缓存起点，此前有效维度不足


def _series(fname: str, col: str) -> pd.Series:
    """读单列时间序列；兼容 data/*.csv 两种首列名(date / 日期 / Unnamed: 0)。"""
    f = DATA / fname
    if not f.exists():
        raise FileNotFoundError(f"缺少 {fname}；请先运行 python start_site.py（或 python build_mood.py）生成源缓存")
    d = pd.read_csv(f)
    first = d.columns[0]
    d[first] = pd.to_datetime(d[first], errors="coerce")
    s = pd.to_numeric(d.set_index(first)[col], errors="coerce")
    s = s.dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()   # 部分缓存含重复日期，reindex 前必须去重
    return s


def _opt_series(fname: str, col: str):
    """可选维度：上游脚本未运行（如首次克隆）时跳过该维度，不让信号板整体失败。"""
    try:
        return _series(fname, col)
    except (FileNotFoundError, KeyError) as e:
        log(f"    [turning] 维度 {col} 跳过：{e}")
        return None


def build_tech():
    """沪深300 技术位置：BIAS20、RSI14、MA20/MA60。"""
    h = _series("mood_hs300.csv", "hs300")
    ma20 = h.rolling(20, min_periods=20).mean()
    ma60 = h.rolling(60, min_periods=60).mean()
    bias20 = (h / ma20 - 1.0) * 100
    delta = h.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi14 = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    tech = pd.DataFrame({"hs300": h, "ma20": ma20, "ma60": ma60,
                         "bias20": bias20, "rsi14": rsi14}).dropna(subset=["bias20"])
    tech = tech[tech.index <= TODAY]
    tech.to_csv(OUT_TECH, index_label="date")
    return h, tech


def build_dimensions(tech):
    """返回 [(key, 名称, 原始序列, 单位, 方向, 最大ffill)]。

    方向 +1 = 原始值越高越接近底部(恐惧)；-1 = 原始值越低越接近底部。
    """
    pe = _series("mood_all_pe_lg.csv", "all_pe_lg")
    b10 = _series("mood_bond10.csv", "bond10")
    idx_u = pe.index.union(b10.index)
    erp = ((1.0 / pe.reindex(idx_u).ffill()) * 100 - b10.reindex(idx_u).ffill()).dropna()

    fi = _series("mood_fund_index.csv", "fund_index")
    fund_ann = ((fi / fi.shift(3 * 244)) ** (1 / 3) - 1.0).dropna() * 100

    return [
        ("erp",       "股债风险溢价",   erp,                                    "%",   +1, 10),
        ("below_net", "破净率",        _series("mood_below_net.csv", "below_net_ratio"), "%", +1, 10),
        ("breadth",   "站上200日线占比", _series("mood_breadth.csv", "pct_above_ma200"), "%", -1, 10),
        ("nhnl",      "新高新低差",     _series("mood_breadth_extra.csv", "nh_nl"),     "%", -1, 10),
        ("turnover",  "全市场换手率",   _series("mood_turnover_rate.csv", "turnover_rate"), "%", -1, 10),
        ("pcr",       "PCR",           _series("mood_pcr.csv", "pcr"),                  "",  -1, 10),
        ("margin",    "两融60日增速",   _series("mood_margin.csv", "growth_60d"),       "%", -1, 10),
        ("pb",        "全A PB中位数",   _series("mood_a_pb.csv", "pb_median"),          "倍", -1, 45),
        ("fund_ann",  "股基3年年化",    fund_ann,                               "%",   -1, 10),
        ("bias20",    "沪深300 BIAS20", tech["bias20"],                         "%",   -1, 10),
        ("rsi14",     "沪深300 RSI14",  tech["rsi14"],                          "",    -1, 10),
        # 2026-11-20 新增：成交量分布与宽度动能，与既有估值/宽度维度互补。
        # TRIN10 高=抛压重/恐慌→底部(+1)；摆动值高=买盘强/偏热→顶部(-1)。
        ("arms",      "阿姆氏指标 TRIN10", _opt_series("mood_arms.csv", "trin10"), "",  +1, 10),
        ("mcc",       "麦克莱伦摆动值",  _opt_series("mood_breadth_advance.csv", "mcc_osc"), "pp", -1, 10),
    ]


def build():
    """执行全部计算；返回可直接写入 mood_data.json 的 turning_panel。

    由 build_mood.main() 在 load_sources() 之后调用（此时所有源缓存已落盘），
    也可 `python build_turning.py` 单独重算。
    """
    log("=" * 60)
    log("顶底共振信号板（本地计算，零网络请求）")
    log("=" * 60)
    h, tech = build_tech()
    idx = h.index[(h.index >= START) & (h.index <= TODAY)]
    dims = [x for x in build_dimensions(tech) if x[2] is not None]
    log(f"基准交易日历 {len(idx)} 日：{idx[0].date()} ~ {idx[-1].date()}，维度 {len(dims)} 个")
    fear, detail = {}, []
    for key, name, raw, unit, direction, max_ff in dims:
        s = raw.reindex(idx).ffill(limit=max_ff)
        pct = s.expanding(min_periods=TURNING_MIN_HIST).rank(pct=True) * 100
        f = pct if direction > 0 else 100 - pct
        fear[key] = f
        last_pos = s.last_valid_index()
        cur_raw = float(s.loc[last_pos]) if last_pos is not None else None
        cur_f = (float(f.loc[last_pos])
                 if last_pos is not None and pd.notna(f.loc[last_pos]) else None)
        prev = s.iloc[-6] if len(s) >= 6 else np.nan
        trend = "↑" if (cur_raw is not None and pd.notna(prev) and cur_raw > prev) else (
                "↓" if (cur_raw is not None and pd.notna(prev) and cur_raw < prev) else "→")
        detail.append({
            "key": key, "name": name, "unit": unit, "direction": direction,
            "value": round(cur_raw, 3) if cur_raw is not None else None,
            "pct": round(float(pct.loc[last_pos]), 1) if last_pos is not None and pd.notna(pct.loc[last_pos]) else None,
            "fear": round(cur_f, 1) if cur_f is not None else None,
            "trend": trend,
            "is_bottom": bool(cur_f is not None and cur_f >= TURNING_FEAR_PCT),
            "is_top": bool(cur_f is not None and cur_f <= TURNING_TOP_PCT),
            "last_date": last_pos.strftime("%Y-%m-%d") if last_pos is not None else None,
        })

    # ---------- 极端单边日的 TRIN 双向屏蔽 ----------
    # 分子「涨跌家数比」在单边市失去上界，两种镜像失真都已被阿姆氏卡片标注：
    #   普跌日 涨占比 <20% → 家数比趋近 0 → TRIN 机械偏低（不是买盘强劲）
    #   普涨日 涨占比 >80% → 家数比爆炸  → TRIN 机械偏高（不是恐慌抛售）
    # 实测全历史 TRIN>2 的 96 日中 67 日为普涨日、仅 1 日为恐慌日，
    # 故 TRIN10 高位不能当作「恐慌/底部极值」计入共振板。
    side_mask = None
    side_reason = None
    if "arms" in fear:
        try:
            adv = _series("mood_breadth_extra.csv", "adv_ratio").reindex(idx).ffill(limit=10)
            panic = (adv < TURNING_ARMS_PANIC_ADV).fillna(False)
            euphoria = (adv > TURNING_ARMS_EUPHORIA_ADV).fillna(False)
            side_mask = panic | euphoria
            side_reason = pd.Series("已剔除·单边日", index=idx)
            side_reason[panic] = "已剔除·普跌日"
            side_reason[euphoria] = "已剔除·普涨日"
            fear["arms"] = fear["arms"].where(~side_mask)
            log(f"    [turning] TRIN 维度在 {int(panic.sum())} 个极端普跌日、"
                f"{int(euphoria.sum())} 个极端普涨日被剔除，不参与极值统计")
        except (FileNotFoundError, KeyError) as e:
            log(f"    [turning] 单边日屏蔽未生效：{e}")
            side_mask, side_reason = None, None
    if side_mask is not None and len(side_mask) and bool(side_mask.iloc[-1]):
        label = str(side_reason.iloc[-1])
        for d in detail:
            if d["key"] == "arms":
                d["masked"] = True
                d["masked_label"] = label
                d["is_bottom"] = False
                d["is_top"] = False

    F = pd.DataFrame(fear, index=idx)
    n_valid = F.notna().sum(axis=1)
    bottom_score = ((F >= TURNING_FEAR_PCT).sum(axis=1) / n_valid * 100).where(n_valid >= TURNING_MIN_DIMS)
    top_score = ((F <= TURNING_TOP_PCT).sum(axis=1) / n_valid * 100).where(n_valid >= TURNING_MIN_DIMS)

    # 拐点确认：独立于估值分位的价格/宽度条件，避免"用情绪分自己确认自己"
    breadth = _series("mood_breadth.csv", "pct_above_ma200").reindex(idx).ffill(limit=10)
    nhnl = _series("mood_breadth_extra.csv", "nh_nl").reindex(idx).ffill(limit=10)
    hh = h.reindex(idx)
    c1 = (hh > hh.shift(20)).astype(int)
    c2 = (breadth.diff(5) > 0).astype(int)
    c3 = (nhnl.diff(5) > 0).astype(int)
    confirm = c1 + c2 + c3

    # 背离：价格创60日新低（新高）而宽度未同步创新低（新高）
    price_low = hh < hh.shift(1).rolling(60, min_periods=60).min()
    price_high = hh > hh.shift(1).rolling(60, min_periods=60).max()
    br_low = nhnl < nhnl.shift(1).rolling(60, min_periods=60).min()
    br_high = nhnl > nhnl.shift(1).rolling(60, min_periods=60).max()
    div_b = (price_low & ~br_low).fillna(False)
    div_t = (price_high & ~br_high).fillna(False)

    out = pd.DataFrame({
        "bottom_score": bottom_score.round(2), "top_score": top_score.round(2),
        "confirm": confirm, "n_valid": n_valid,
        "divergence_bottom": div_b.astype(int), "divergence_top": div_t.astype(int),
        "hs300": hh,
    }, index=idx)
    out = out.dropna(subset=["bottom_score"])
    out.to_csv(OUT_CSV, index_label="date")
    log(f"共振度序列 {len(out)} 日：{out.index[0].date()} ~ {out.index[-1].date()}")

    # ---------- 历史回测 ----------
    px = hh
    fwd = {}
    for w in TURNING_FWD_WINS:
        fwd[w] = (px.shift(-w) / px - 1.0) * 100

    def thin(mask, gap=TURNING_TRIGGER_GAP):
        # 与完整交易日历对齐，避免 out 丢弃前段 NaN 后位置索引错位
        mask = mask.reindex(idx).fillna(False)
        last, res = -10 ** 9, []
        for i, dt in enumerate(idx):
            if bool(mask.iloc[i]) and i - last >= gap:
                res.append(dt)
                last = i
        return pd.DatetimeIndex(res)

    groups = {
        "bottom_confirm": thin((out["bottom_score"] >= TURNING_TRIGGER) & (out["confirm"] >= 1)),
        "bottom_extreme": thin((out["bottom_score"] >= TURNING_FEAR_PCT) & (out["confirm"] == 0)),
        "top_warning": thin(out["top_score"] >= TURNING_TRIGGER),
        "baseline": pd.DatetimeIndex(out.index[::TURNING_TRIGGER_GAP]),
    }
    labels = {"bottom_confirm": "底部拐点触发", "bottom_extreme": "底部极值(未确认)",
              "top_warning": "顶部警戒", "baseline": "全样本基准(每20日)"}

    def stats(dates):
        d = {"n": len(dates)}
        for w in TURNING_FWD_WINS:
            r = fwd[w].reindex(dates).dropna()
            d[f"n{w}"] = len(r)
            d[f"win{w}"] = round(float((r > 0).mean() * 100), 1) if len(r) else None
            d[f"avg{w}"] = round(float(r.mean()), 2) if len(r) else None
            d[f"med{w}"] = round(float(r.median()), 2) if len(r) else None
        return d

    backtest = [dict(key=k, label=labels[k], **stats(v)) for k, v in groups.items()]

    # 最近触发日（合并三类，按日期倒序）
    trig = []
    for k in ["bottom_confirm", "bottom_extreme", "top_warning"]:
        for dt in groups[k]:
            row = out.loc[dt]
            trig.append({
                "date": dt.strftime("%Y-%m-%d"), "kind": labels[k],
                "bottom_score": round(float(row["bottom_score"]), 1),
                "top_score": round(float(row["top_score"]), 1),
                "confirm": int(row["confirm"]),
                "ret20": round(float(fwd[20].get(dt)), 2) if pd.notna(fwd[20].get(dt)) else None,
                "ret60": round(float(fwd[60].get(dt)), 2) if pd.notna(fwd[60].get(dt)) else None,
            })
    trig.sort(key=lambda x: x["date"], reverse=True)

    cur = out.iloc[-1]
    bs, ts = float(cur["bottom_score"]), float(cur["top_score"])
    cf = int(cur["confirm"])
    if bs >= TURNING_TRIGGER and cf >= 1:
        state, level = "底部拐点确认", "bottom"
    elif bs >= TURNING_TRIGGER:
        state, level = "底部共振 · 待拐点确认", "bottom"
    elif bs >= 40:
        state, level = "偏底部区域", "bottom"
    elif ts >= TURNING_TRIGGER:
        state, level = "顶部警戒", "top"
    elif ts >= 40:
        state, level = "偏顶部区域", "top"
    else:
        state, level = "中性", "mid"

    meta = {
        "data_date": out.index[-1].strftime("%Y-%m-%d"),
        "current": {
            "bottom_score": round(bs, 1), "top_score": round(ts, 1),
            "confirm": cf, "n_valid": int(cur["n_valid"]), "n_dims": len(dims),
            "state": state, "level": level,
            "divergence_bottom": bool(cur["divergence_bottom"]),
            "divergence_top": bool(cur["divergence_top"]),
            "hs300": round(float(cur["hs300"]), 2),
        },
        "dims": detail,
        "masked_dims": [d["name"] for d in detail if d.get("masked")],
        "masked_reason": (str(side_reason.iloc[-1])
                          if side_mask is not None and len(side_mask) and bool(side_mask.iloc[-1])
                          else ""),
        "backtest": backtest,
        "triggers": trig[:12],
        "thresholds": {
            "fear_pct": TURNING_FEAR_PCT, "top_pct": TURNING_TOP_PCT,
            "trigger": TURNING_TRIGGER, "min_dims": TURNING_MIN_DIMS,
            "min_hist": TURNING_MIN_HIST, "gap": TURNING_TRIGGER_GAP,
            "fwd_wins": list(TURNING_FWD_WINS),
        },
        "disclaimer": ("共振度是赔率统计而非择时信号。阈值为本脚本设定、未做样本外检验，"
                       "触发日按20交易日去重但收益窗口仍重叠，未计交易成本与滑点；"
                       "低利率环境下 ERP 抬升可能来自债券收益率下行而非股票便宜。"
                       "各维度统一对齐 2015 年起交易日历、分位用 expanding(min_periods=250)，"
                       "与单卡「自身全历史 + min_periods=20」的分位略有差异属口径不同、并非计算错误；"
                       "极端单边日（上涨家数占比<20% 或 >80%）TRIN 会因家数比失真而机械偏低或偏高，"
                       "该情形下的 TRIN 维度已按阿姆氏卡片的警示剔除。"
                       "极值可长期钝化，政策与盈利才决定反弹还是反转。仅供研究参考，不构成投资建议。"),
    }
    OUT_META.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    log("-" * 60)
    log(f"当前：底部共振度 {bs:.1f}  顶部共振度 {ts:.1f}  拐点确认 {cf}/3  "
        f"有效维度 {int(cur['n_valid'])}/{len(dims)}  状态「{state}」")
    log(f"底部动能背离: {bool(cur['divergence_bottom'])}  顶部动能背离: {bool(cur['divergence_top'])}")
    for b in backtest:
        log(f"  {b['label']:18s} n={b['n']:>3d}  20日胜率={b['win20']}% 均值={b['avg20']}%  "
            f"60日胜率={b['win60']}% 均值={b['avg60']}%")
    log(f"输出：{OUT_CSV.name} / {OUT_META.name} / {OUT_TECH.name}")

    hist = [{"date": dt.strftime("%Y-%m-%d"),
             "bottom": round(float(r["bottom_score"]), 1),
             "top": round(float(r["top_score"]), 1),
             "confirm": int(r["confirm"]),
             "hs300": round(float(r["hs300"]), 1)}
            for dt, r in out.iterrows()]
    panel = dict(meta)
    panel["history"] = hist
    return panel


def main():
    p = build()
    c = p["current"]
    log(f"当前状态：{c['state']}（底部共振 {c['bottom_score']} / 顶部共振 {c['top_score']}）")


if __name__ == "__main__":
    main()
