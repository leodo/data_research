# -*- coding: utf-8 -*-
"""
情绪指标面板构建引擎（公开数据自算版）
=====================================
仿 funddb.cn/indicators?tab=mood 的 8 个情绪指标，全部用公开数据源自行计算，
不取用原站任何专有/会员数据。指标口径依据原站公开释义(paraphrase)还原。

8 个指标：
  1. rp         A股风险溢价      = 全A盈利收益率(1/PE) − 10年国债收益率    [精确自算]
  2. dgz        低估指数行业      = 申万31个一级行业中处于低估(低分位)的个数 [近似:价格分位代理估值]
  3. fx_fe      新基金发行        = 近90日滚动日均发行份额(亿份), 反向指标   [精确自算]
  4. pgjj_zsy   正收益偏股基金占比= 上证基金指数近1年收益率为正占比的代理     [代理]
  5. qschsl     全市场换手率      = 两市成交额 ÷ 全市场总市值 ×100%         [精确自算]
  6. pcr        PCR(认沽/认购持仓)= 上交所ETF期权未平仓认沽/认购比           [精确自算]
  7. zzpgnh     股基滚动年化收益  = 上证基金指数近3年年化收益                [代理:基金指数]
  8. magic      神奇指标          = 沪深300全收益 ÷ 中债综合财富指数 比价     [代理]

每个指标统一输出：
  value  当前原始值
  score  情绪分位(0-100, 越高越"乐观/贪婪"; 反向指标已翻转)
  pct_all / pct_3y / pct_5y / pct_10y  各窗口 expanding 分位
  state  状态(积极/中立/谨慎)
  history [{date, value, pct}] 全历史序列
  source 数据来源说明

方向约定（score 高分=贪婪/亢奋，低分=恐惧/冰点，与恐贪指数一致）：
  rp       风险溢价高=股票便宜=底部区域 => 情绪偏低(恐惧)  [反向]
  dgz      低估行业多=底部区域          => 情绪偏低        [反向]
  fx_fe    新基金发行火=资金亢奋        => 情绪偏高        [正向]
  pgjj_zsy 正收益基金多=市场好          => 情绪偏高        [正向]
  qschsl   换手率高=交易火热            => 情绪偏高        [正向]
  pcr      A股持仓PCR顺周期：低位=市场已在底部 => 情绪偏低(积极) [正向；实测PCR低分位后续收益更好]
  zzpgnh   股基年化高=市场好            => 情绪偏高        [正向]
  magic    沪深300贴近债基=见底         => 情绪偏低        [反向]
"""
from __future__ import annotations

import json
import sys
import time

import akshare as ak
import numpy as np
import pandas as pd

from core import (BASE, DATA, TODAY, get_or_fetch, log, pct_rank,
                  sjoin, window_pct)


# 1) 全A PE（乐咕，含总市值）
def fetch_all_pe():
    d = ak.stock_market_pe_lg(symbol="全部A股")
    d["date"] = pd.to_datetime(d["日期"])
    return sjoin(d.set_index("date")["市盈率"], "all_pe")


# 1b) 全市场总市值（乐咕，换手率分母）
def fetch_total_mv():
    d = ak.stock_market_pe_lg(symbol="全部A股")
    d["date"] = pd.to_datetime(d["日期"])
    return sjoin(d.set_index("date")["总市值"], "total_mv")


# 1c) 10年国债收益率（优先用已缓存的原始文件，避免东财限流）
def fetch_bond10():
    raw = DATA / "mood_bond10_raw.csv"
    if raw.exists():
        d = pd.read_csv(raw)
    else:
        d = ak.bond_zh_us_yield_em(start_date="20100101")
        d.to_csv(raw, index=False)
    d["日期"] = pd.to_datetime(d["日期"])
    d = d[["日期", "中国国债收益率10年"]].dropna()
    return sjoin(d.set_index("日期")["中国国债收益率10年"], "bond10")


# 5) 全市场换手率（交易所权威口径，由 rebuild_turnover.py 生成）
# 真值 = (沪市成交额+深市成交额)/(沪市总市值+深市总市值)*100；
# 上交所官方仅 2022 起有数据，之前用深市换手率×沪深稳定比例桥接。
def fetch_turnover_rate():
    p = DATA / "mood_turnover_rate.csv"
    if not p.exists():
        return None
    d = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
    return sjoin(d, "turnover_rate")


# 5b) 对比用：全程上交所官方换手率（2022 起，原始口径，不做沪深桥接/系数换算）
def fetch_turnover_sse():
    p = DATA / "mood_turnover_sse.csv"
    if not p.exists():
        return None
    d = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
    return sjoin(d, "turnover_sse")


# 5c) 头部300ETF占沪深300流通市值比例（由 build_etf_ratio.py 生成）
def fetch_etf_ratio():
    p = DATA / "mood_etf_ratio.csv"
    if not p.exists():
        return None
    d = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
    return sjoin(d, "etf_ratio")


# 5d) 交易拥挤度：前5%个股成交额占比（由 build_crowding.py 生成）
def fetch_crowding():
    p = DATA / "mood_crowding.csv"
    if not p.exists():
        return None
    d = pd.read_csv(p, parse_dates=True)
    d["date"] = pd.to_datetime(d["date"])
    return d.set_index("date")["top5_ratio"]


# 6) PCR：上交所ETF期权未平仓认沽/认购比（真增量：只补缓存最后日期之后的交易日）
def fetch_pcr():
    # 基线：已有缓存则从其最后日期之后继续补；--pcr-full 时丢弃基线做全量重建
    p = DATA / "mood_pcr.csv"
    old = None
    if "--pcr-full" not in sys.argv and p.exists():
        try:
            o = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
            o = o.dropna()
            if len(o) >= 20:
                old = o
        except Exception:
            pass

    # 官方完整交易日历（含当日），避免新浪指数日历滞后导致漏补
    from core import trade_dates
    cal = pd.to_datetime(trade_dates("20210101", TODAY.strftime("%Y%m%d")))
    if old is not None:
        last = old.index.max()
        # 多回溯3天重叠，防止上游数据回填修正
        dates = cal[cal >= last - pd.Timedelta(days=3)]
        log(f"    PCR增量：缓存末日 {last.date()}，待补 {len(dates)} 个交易日")
    else:
        dates = cal
        log(f"    PCR全量：{len(dates)} 个交易日")

    rows = []
    consec_fail = 0
    for i, dt in enumerate(dates):
        ds = dt.strftime("%Y%m%d")
        try:
            d = ak.option_daily_stats_sse(date=ds)
            if d is None or len(d) == 0:
                consec_fail += 1
            else:
                call = pd.to_numeric(d["未平仓认购合约数"], errors="coerce").sum()
                put = pd.to_numeric(d["未平仓认沽合约数"], errors="coerce").sum()
                if call > 0 and put > 0:
                    rows.append((dt, put / call))
                    consec_fail = 0
                else:
                    consec_fail += 1
        except Exception:
            consec_fail += 1
        if i % 250 == 0 and len(dates) > 300:
            log(f"    PCR抓取进度 {i}/{len(dates)} (已获取{len(rows)})")
        # 连续失败30次说明被限流或数据尚未发布，提前退出用已抓数据
        if consec_fail >= 30:
            log(f"    PCR连续失败{consec_fail}次，提前停止")
            break

    s = pd.Series(dict(rows)).sort_index()
    if old is not None and len(s):
        # 新抓数据覆盖同日旧值，其余保持不动
        s = pd.concat([old[old.index < s.index.min()], s])
    elif old is not None:
        s = old
    return sjoin(s, "pcr")


# 3) 新基金发行：按成立日期聚合募集份额，近90日滚动日均
def fetch_fund_issue():
    d = ak.fund_new_found_em()
    d["成立日期"] = pd.to_datetime(d["成立日期"], errors="coerce")
    d["募集份额"] = pd.to_numeric(d["募集份额"], errors="coerce")
    g = d.dropna(subset=["成立日期", "募集份额"]).groupby("成立日期")["募集份额"].sum()
    g = g[g > 0].sort_index()
    g = g[g.index <= TODAY]
    return sjoin(g, "fund_issue")


# 基金指数（代理 pgjj_zsy / zzpgnh）
def fetch_fund_index():
    d = ak.index_zh_daily_sina(symbol="sh000011")  # 上证基金指数
    d["date"] = pd.to_datetime(d["date"])
    return sjoin(d.set_index("date")["close"], "fund_index")


# 神奇指标分子：沪深300全收益（用沪深300价格指数代理）
def fetch_hs300():
    d = ak.index_zh_daily_sina(symbol="sh000300")
    d["date"] = pd.to_datetime(d["date"])
    return sjoin(d.set_index("date")["close"], "hs300")


# 神奇指标分母：中债综合财富指数
def fetch_bond_idx():
    d = ak.bond_composite_index_cbond(indicator="财富", period="总值")
    d["date"] = pd.to_datetime(d["date"])
    return sjoin(d.set_index("date")["value"], "bond_idx")


# 2) 申万一级行业历史（用于低估行业个数）
# 缓存目录：每行业一个CSV。index_hist_sw 无日期参数(每次返回全历史)，
# 无法真增量，故采用「抓取成功则更新 / 失败或源回退则用缓存兜底」策略，
# 避免接口偶发少返回若干行业导致低估行业数在 7~9 间跳变。
SW_CACHE = DATA / "sw_industries"


def fetch_sw_industries():
    SW_CACHE.mkdir(exist_ok=True)
    info = ak.index_first_info_sw()
    codes = info["行业代码"].astype(str).str.replace(".SI", "", regex=False).tolist()
    names = dict(zip(info["行业代码"].astype(str).str.replace(".SI", "", regex=False), info["行业名称"]))
    series = {}
    n_ok = n_fallback = n_reject = 0
    for code in codes:
        fp = SW_CACHE / f"{code}.csv"
        cached = None
        if fp.exists():
            try:
                c = pd.read_csv(fp, index_col=0, parse_dates=True).iloc[:, 0].dropna()
                if len(c) >= 250:
                    cached = c
            except Exception:
                pass
        try:
            d = ak.index_hist_sw(symbol=code, period="day")
            d["日期"] = pd.to_datetime(d["日期"])
            fresh = sjoin(d.set_index("日期")["收盘"], code)
            if len(fresh) < 250:
                raise ValueError("有效行数不足")
            if cached is not None and fresh.index.max() < cached.index.max():
                # 上游数据源回退（如整体停在11-09）：拒绝覆盖，沿用缓存
                series[names.get(code, code)] = cached
                n_reject += 1
                continue
            fresh.to_csv(fp)
            series[names.get(code, code)] = fresh
            n_ok += 1
        except Exception:
            if cached is not None:
                series[names.get(code, code)] = cached
                n_fallback += 1
            continue
    log(f"    申万行业：成功{n_ok} 兜底{n_fallback} 拒绝回退{n_reject} 共{len(series)}个")
    return series


# ---------------- 状态判定（情绪面板三档，语义与恐贪五档不同，保留在此） ----------------
def state_of(score: float) -> tuple[str, int]:
    if score <= 30:
        return "积极", 1
    if score <= 70:
        return "中立", 2
    return "谨慎", 3


def make_panel(key, name, raw, source, reverse=False, unit="%", desc=""):
    """把原始序列转成标准面板结构。
    raw: pd.Series 原始指标值
    reverse: True=指标值越高越"恐惧/底部"(如风险溢价), score 取反
    """
    raw = raw.dropna().sort_index()
    if len(raw) < 20:
        return None
    pct_all = pct_rank(raw)
    # 情绪分：正向=分位本身；反向=100-分位
    score = (100 - pct_all) if reverse else pct_all
    cur_raw = float(raw.iloc[-1])
    cur_score = float(score.iloc[-1])
    st, lv = state_of(cur_score)
    hist = []
    for dt, v in raw.items():
        p = pct_all.get(dt)
        hist.append({
            "date": dt.strftime("%Y-%m-%d"),
            "value": round(float(v), 4),
            "pct": round(float(p), 2) if pd.notna(p) else None,
        })
    return {
        "key": key, "name": name, "unit": unit,
        "value": round(cur_raw, 4),
        "score": round(cur_score, 2),
        "state": st, "state_level": lv,
        "pct_all": round(float(pct_all.iloc[-1]), 2),
        "pct_3y": round(window_pct(score, 3), 2),
        "pct_5y": round(window_pct(score, 5), 2),
        "pct_10y": round(window_pct(score, 10), 2),
        "source": source, "desc": desc, "reverse": reverse,
        "history": hist,
    }


def main():
    t0 = time.time()
    log("=" * 60)
    log("情绪指标面板构建（公开数据自算版）")
    log("=" * 60)

    log("[1/8] 全A PE / 总市值 / 10年国债 ...")
    pe = get_or_fetch("all_pe", fetch_all_pe)
    mv = get_or_fetch("total_mv", fetch_total_mv)
    bond10 = get_or_fetch("bond10", fetch_bond10)

    log("[2/8] 全市场换手率(交易所权威口径) ...")
    try:
        turnover = fetch_turnover_rate()
        if turnover is None:
            log("    未找到 mood_turnover_rate.csv，请先运行 python rebuild_turnover.py")
    except Exception as e:
        log(f"    换手率读取失败: {e}")
        turnover = None

    log("[3/8] PCR(上交所ETF期权持仓比，需逐个交易日抓取，首次较慢) ...")
    try:
        pcr = get_or_fetch("pcr", fetch_pcr, max_age_hours=12)
    except Exception as e:
        log(f"    PCR抓取失败: {e}")
        pcr = None

    log("[4/8] 新基金发行 ...")
    try:
        fi = get_or_fetch("fund_issue", fetch_fund_issue)
    except Exception as e:
        log(f"    新基金发行失败: {e}")
        fi = None

    log("[5/8] 基金指数(股基年化/正收益占比代理) ...")
    try:
        fi_idx = get_or_fetch("fund_index", fetch_fund_index)
    except Exception as e:
        log(f"    基金指数失败: {e}")
        fi_idx = None

    log("[6/8] 沪深300 + 中债综合指数(神奇指标) ...")
    try:
        hs300 = get_or_fetch("hs300", fetch_hs300)
        bidx = get_or_fetch("bond_idx", fetch_bond_idx)
    except Exception as e:
        log(f"    神奇指标数据失败: {e}")
        hs300 = bidx = None

    log("[7/8] 申万一级行业价格历史(低估行业) ...")
    try:
        sw = fetch_sw_industries()
    except Exception as e:
        log(f"    申万行业失败: {e}")
        sw = {}

    panels = {}

    # ---- 1. 风险溢价：1/PE - 10年国债 ----
    try:
        ep = (1.0 / pe) * 100.0  # 盈利收益率%
        j = ep.index.intersection(bond10.index)
        rp = (ep.loc[j] - bond10.loc[j]).dropna()
        rp = rp[rp.index <= TODAY]
        p = make_panel("rp", "A股风险溢价", rp,
                       "全A市盈率(乐咕 stock_market_pe_lg)算盈利收益率1/PE，减10年国债收益率(东财 bond_zh_us_yield_em)；值越高股票越便宜",
                       reverse=True, desc="股票相对债券的预期收益补偿，越高越接近底部区域(反向指标)")
        if p: panels["rp"] = p
    except Exception as e:
        log(f"    [rp] 计算失败: {e}")

    # ---- 2. 低估行业：申万一级行业价格分位<30%的个数 ----
    try:
        if sw:
            n_low_list = []
            for name, s in sw.items():
                s = s[s.index <= TODAY].dropna()
                if len(s) < 250:
                    continue
                pr = s.expanding(min_periods=250).rank(pct=True) * 100
                n_low_list.append((pr < 30).astype(int).rename(name))
            if n_low_list:
                df = pd.concat(n_low_list, axis=1).sort_index()
                n_low = df.sum(axis=1).dropna()
                n_low.name = "n_low"
                p = make_panel("dgz", "低估指数行业", n_low,
                               "申万31个一级行业指数(akshare index_hist_sw)收盘价的全历史expanding分位，统计处于低分位(<30%)的行业个数",
                               reverse=True, unit="个", desc="处于低估状态的申万一级行业个数越多，市场整体越低估(反向指标)")
                if p: panels["dgz"] = p
    except Exception as e:
        log(f"    [dgz] 计算失败: {e}")

    # ---- 3. 新基金发行：近90日滚动日均 ----
    try:
        if fi is not None:
            roll = fi.rolling("90D").mean().dropna()
            roll = roll[roll.index <= TODAY]
            p = make_panel("fx_fe", "新基金发行", roll,
                           "按成立日期聚合募集份额(akshare fund_new_found_em)，取近90日滚动日均发行份额(亿份)",
                           reverse=False, unit="亿份", desc="近90日日均新基金发行份额，反映资金亢奋程度(越高越贪婪)")
            if p: panels["fx_fe"] = p
    except Exception as e:
        log(f"    [fx_fe] 计算失败: {e}")

    # ---- 4. 正收益偏股基金占比（代理：基金指数近1年收益率为正的滚动占比）----
    try:
        if fi_idx is not None:
            r1y = fi_idx / fi_idx.shift(244) - 1.0  # 近1年收益
            r1y = r1y.dropna()
            # 用近1年收益的"是否为正"做252日滚动占比
            pos = (r1y > 0).astype(float)
            ratio = pos.rolling(252, min_periods=60).mean() * 100
            ratio = ratio.dropna()
            ratio.name = "ratio"
            p = make_panel("pgjj_zsy", "正收益偏股基金占比", ratio,
                           "代理指标：上证基金指数(sh000011)近1年收益率为正的天数占比(252日滚动)。原站用全市场偏股基金全样本，公开源无法精确复刻",
                           reverse=False, desc="近一年收益为正的偏股基金占比，越低越接近底部(代理指标)")
            if p: panels["pgjj_zsy"] = p
    except Exception as e:
        log(f"    [pgjj_zsy] 计算失败: {e}")

    # ---- 5. 全市场换手率：交易所权威口径 ----
    try:
        if turnover is not None:
            t = turnover[turnover.index <= TODAY].dropna()
            p = make_panel("qschsl", "全市场换手率", t,
                           "交易所权威口径：(沪市成交额+深市成交额)÷(沪市总市值+深市总市值)×100%（数据源：上交所每日股票成交概况、深交所每日证券概况）；2022年前沪市无官方数据，用深市换手率×沪深稳定比例桥接",
                           reverse=False, desc="量价维度情绪指标，换手率越高交易越火热、情绪越亢奋，逼近极值可能预示筑顶或磨底")
            if p: panels["qschsl"] = p
    except Exception as e:
        log(f"    [qschsl] 计算失败: {e}")

    # ---- 5b. 对比面板：全程上交所官方换手率（不进综合分，仅作口径对比）----
    compare_panel = None
    try:
        tss = fetch_turnover_sse()
        if tss is not None:
            tss = tss[tss.index <= TODAY].dropna()
            compare_panel = make_panel(
                "qschsl_sse", "换手率·上交所官方口径", tss,
                "上交所官方每日股票换手率字段（成交额÷市价总值×100%），2022-02 起，全程同一口径、不拼接深市、不做系数换算。用于与上方『全市场换手率』对比：上方为还原全市场量级的桥接口径，本项为原始沪市口径，数值系统性偏低但无拼接",
                reverse=False, unit="%",
                desc="【口径对比图】与上方全市场换手率对照：本图只用上交所官方换手率，2022年起口径统一、无深市桥接，因此2024年9月前后不出现拼接差异。数值为沪市单一市场口径，低于全市场属正常，主要用于观察形态与台阶变化，不参与综合情绪分。")
    except Exception as e:
        log(f"    [qschsl_sse] 计算失败: {e}")

    # ---- 5c. 估值与宏观比较栏目（公开数据自算，不进综合分）----
    macro_panels = {}
    try:
        er = fetch_etf_ratio()
        if er is not None:
            er = er[er.index <= TODAY].dropna()
            mp = make_panel(
                "etf300_ratio", "头部300ETF占沪深300流通市值比例", er,
                "分子：三只上交所头部300ETF(510300/510310/510330)份额×单位净值之和(份额取上交所每日ETF规模表，净值取天天基金)；159919因深交所接口不可用未纳入。分母：沪深300流通市值(以指数点位×流通市值锚线性代理)。全程公开数据自算，不抓取乐咕成品数据",
                reverse=False, unit="%",
                desc="被动指数资金对沪深300的定价力量与拥挤度。2024年以来中央汇金等通过宽基ETF持续增持，头部300ETF规模快速膨胀，该比例趋势性抬升，反映被动资金对大盘核心资产边际定价权增强。该指标不含情绪方向判断，仅作市场结构观察，不参与综合情绪分。")
            if mp: macro_panels["etf300_ratio"] = mp
    except Exception as e:
        log(f"    [etf300_ratio] 计算失败: {e}")

    # ---- 5e. 交易拥挤度（前5%个股成交额占比，独立卡片，不进综合分）----
    crowding_panel = None
    try:
        cr = fetch_crowding()
        if cr is not None:
            cr = cr[cr.index <= TODAY].dropna()
            cp = make_panel(
                "crowding", "交易拥挤度（前5%个股成交额占比）", cr,
                "全A股票按每日成交额降序排列，前5%个股成交额之和÷全部A股成交额×100%。"
                "全A列表与个股历史成交额均取新浪公开接口(akshare)，逐日横截面自算，多进程抓取。"
                "股票池为当前在市A股（含北交所），已退市个股不在其中——早期年份存在轻微幸存者偏差，成交额总量偏低、拥挤度略偏高，近年影响可忽略。"
                "辅助指标HHI有效个股数、Top10%占比同表计算（见下方说明）",
                reverse=False, unit="%",
                desc="交易集中度/微观结构拥挤度（中金、广发研报与乐咕通用口径）。"
                "寓意：少数头部个股吃掉了全市场多少成交。越高=抱团越紧、结构越脆弱。"
                "经验阈值：<40%分散健康；40~50%集中度上升需关注；>50%结构风险信号。"
                "历史经验中高拥挤常对应阶段性高低点或风格切换，但不是单独择时信号。"
                "这是反向情绪指标，越高情绪分越高（越谨慎）。本卡片不计入综合情绪分。")
            if cp:
                crowding_panel = cp
                # 挂上辅助指标序列（HHI有效个股数、Top10%）供前端叠加展示
                try:
                    cdf = pd.read_csv(DATA / "mood_crowding.csv", parse_dates=True)
                    cdf["date"] = pd.to_datetime(cdf["date"])
                    cdf = cdf.set_index("date")
                    aux = []
                    for dt, r in cdf.iterrows():
                        if dt <= TODAY:
                            aux.append({"date": dt.strftime("%Y-%m-%d"),
                                        "top10": round(float(r["top10_ratio"]), 4),
                                        "eff_n": round(float(r["effective_n"]), 2)})
                    crowding_panel["auxiliary"] = aux
                except Exception:
                    pass
    except Exception as e:
        log(f"    [crowding] 计算失败: {e}")

    # ---- 6. PCR：认沽/认购持仓比（A股实证为顺周期，正向指标）----
    try:
        if pcr is not None:
            p = make_panel("pcr", "PCR", pcr,
                           "上交所ETF期权未平仓认沽合约数÷未平仓认购合约数(akshare option_daily_stats_sse，逐交易日抓取)，综合50ETF/300ETF/500ETF/科创50ETF共5个标的",
                           reverse=False, unit="", desc="认沽/认购持仓比。A股实证为顺周期：PCR与指数同向，处于低位时市场往往已在底部区域（后续收益更好），处高位时需谨慎。2021年以来PCR低15%分位未来60日平均+2.6%、高15%分位-3.5%")
            if p: panels["pcr"] = p
    except Exception as e:
        log(f"    [pcr] 计算失败: {e}")

    # ---- 7. 股基滚动年化：基金指数近3年年化 ----
    try:
        if fi_idx is not None:
            ann = (fi_idx / fi_idx.shift(3 * 244)) ** (1 / 3) - 1.0
            ann = ann.dropna() * 100
            ann.name = "ann"
            p = make_panel("zzpgnh", "股基滚动年化收益", ann,
                           "代理指标：上证基金指数(sh000011)相对3年前(756交易日)的年化收益率。原站用偏股型基金全样本，此处用基金指数代理",
                           reverse=False, desc="往前3年买入偏股基金并持有的年化收益，向下偏离均值可能是底部")
            if p: panels["zzpgnh"] = p
    except Exception as e:
        log(f"    [zzpgnh] 计算失败: {e}")

    # ---- 8. 神奇指标：沪深300全收益 ÷ 中债综合财富 ----
    try:
        if hs300 is not None and bidx is not None:
            j = hs300.index.intersection(bidx.index)
            h = hs300.loc[j]; b = bidx.loc[j]
            # 归一到起点，取比值；比值越低=股票相对债券越弱=越接近底部
            ratio = (h / h.iloc[0]) / (b / b.iloc[0])
            ratio = ratio.dropna()
            ratio.name = "magic"
            p = make_panel("magic", "神奇指标", ratio,
                           "代理指标：沪深300价格指数 ÷ 中债综合财富指数(财富总值)的归一化比价。原站用沪深300全收益vs偏债基金指数，此处用可公开获取的指数代理",
                           reverse=True, unit="", desc="股票与债券资产回报差，沪深300越贴近债券走势时越可能见底(反向指标)")
            if p: panels["magic"] = p
    except Exception as e:
        log(f"    [magic] 计算失败: {e}")

    # ---- 综合情绪分：各面板 score 等权平均 ----
    composite_parts = {k: p["score"] for k, p in panels.items()}
    composite = round(float(np.mean(list(composite_parts.values()))), 2) if composite_parts else 50.0

    # 综合情绪分历史：各面板按日 score(全历史expanding分位)对齐后等权平均。
    # 只保留所有面板均有数据的共同区间，避免早期只有2-3个指标参与造成误导。
    try:
        score_series = {}
        starts = []
        for k, p in panels.items():
            s = pd.Series(
                [h["pct"] for h in p["history"]],
                index=pd.to_datetime([h["date"] for h in p["history"]]),
            ).dropna()
            if p["reverse"]:
                s = 100 - s
            score_series[k] = s
            if len(s):
                starts.append(s.index.min())
        df = pd.DataFrame(score_series).sort_index()
        common_start = max(starts) if starts else df.index.min()
        # 对齐到交易日网格：用最全的面板日期作基准，其它 ffill
        base_idx = df.index
        comp = df.ffill().mean(axis=1).dropna()
        comp = comp[comp.index >= common_start]
        # 重采样到周频减少点数（保留每周最后一个交易日），曲线更平滑且文件更小
        comp_w = comp.resample("W-FRI").last().dropna()
        comp_hist = [
            {"date": dt.strftime("%Y-%m-%d"), "score": round(float(v), 2)}
            for dt, v in comp_w.items()
        ]
        log(f"    综合分历史: {comp.index.min().date()} ~ {comp.index.max().date()}, {len(comp_hist)}周点")
    except Exception as e:
        log(f"    综合分历史计算失败: {e}")
        comp_hist = []

    out = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": TODAY.strftime("%Y-%m-%d"),
        "composite": {"score": composite, "state": state_of(composite)[0], "history": comp_hist},
        "panels": panels,
        "compare_panel": compare_panel,
        "macro_panels": macro_panels,
        "macro_order": ["etf300_ratio"],
        "crowding_panel": crowding_panel,
        "order": ["rp", "dgz", "fx_fe", "pgjj_zsy", "qschsl", "pcr", "zzpgnh", "magic"],
        "proxy_flags": {
            "rp": "精确", "dgz": "近似", "fx_fe": "精确", "pgjj_zsy": "代理",
            "qschsl": "精确", "pcr": "精确", "zzpgnh": "代理", "magic": "代理",
        },
    }

    jpath = DATA / "mood_data.json"
    jspath = DATA / "mood_data.js"
    jpath.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    jspath.write_text("window.MOOD_DATA=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";", encoding="utf-8")

    log("=" * 60)
    log(f"完成，用时 {time.time() - t0:.1f} 秒")
    log(f"生成面板 {len(panels)}/8: {', '.join(panels.keys())}")
    for k in out["order"]:
        if k in panels:
            p = panels[k]
            log(f"  {p['name']:16s} 值={p['value']:>8.3f} 情绪分={p['score']:>6.2f} {p['state']} [{out['proxy_flags'][k]}] 历史{len(p['history'])}日")
    log(f"综合情绪分: {composite} ({state_of(composite)[0]})")
    log(f"输出: {jpath.name} ({jpath.stat().st_size/1024:.0f}KB) / {jspath.name}")


if __name__ == "__main__":
    main()
