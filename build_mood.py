# -*- coding: utf-8 -*-
"""
情绪指标面板构建引擎（公开数据自算版）
=====================================
仿 funddb.cn/indicators?tab=mood 的 8 个情绪指标，全部用公开数据源自行计算，
不取用原站任何专有/会员数据。指标口径依据原站公开释义(paraphrase)还原。

8 个指标：
  1. rp         A股风险溢价      = 全A盈利收益率(1/PE) − 10年国债收益率    [月频PE按日对齐]
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


# 1) 全A平均市盈率（乐咕乐股，月频全历史）
# 2026-11-20 口径修复：ak.stock_market_pe_lg(symbol="全部A股"/"沪深300") 的
# symbol 参数在 akshare 1.20.65 已失效，三个取值都返回同一份科创板日频数据
# (1778行、最新PE 90.51)。以此为分子会把 1/PE 压到 1.1%，ERP 算出负值，
# 占综合分 1/8 权重却严重失真。改用无参接口的全A「平均市盈率」(月频、
# 1997-12 起、最新 24.09)，由 calc_rp 按交易日 ffill 对齐国债。
# 缓存 key 用 all_pe_lg，确保不会复用旧的坏缓存 mood_all_pe.csv。
def fetch_all_pe():
    d = ak.stock_market_pe_lg()
    d["date"] = pd.to_datetime(d["日期"])
    return sjoin(d.set_index("date")["平均市盈率"], "all_pe_lg")


# 1b) 保留：全市场总市值（当前无指标消费，仅供换手率分母复核）
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


# ---------------- 扩展指标数据读取（CSV 由独立构建脚本产出）----------------
def _read_frame(name):
    p = DATA / f"mood_{name}.csv"
    if not p.exists():
        log(f"    [{name}] 尚无数据文件，跳过（请先运行对应构建脚本）")
        return None
    try:
        d = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
        d = d[~d.index.duplicated(keep="last")]   # 上游偶发重复日期，统一去重
        return d[d.index <= TODAY]
    except Exception as e:
        log(f"    [{name}] 读取失败: {e}")
        return None


def fetch_margin():
    return _read_frame("margin")


def fetch_breadth():
    return _read_frame("breadth")


def fetch_pb():
    return _read_frame("a_pb")


# ---------------- 原始数据源加载 ----------------
def load_sources():
    """取全部原始序列。任一源失败置 None，由对应指标自行跳过，不拖垮全局。"""
    src = {}
    log("[src 1/7] 全A PE(月频) / 10年国债 ...")
    src["pe"] = get_or_fetch("all_pe_lg", fetch_all_pe)
    src["bond10"] = get_or_fetch("bond10", fetch_bond10)

    log("[src 2/7] 全市场换手率(交易所权威口径) ...")
    try:
        src["turnover"] = fetch_turnover_rate()
        if src["turnover"] is None:
            log("    未找到 mood_turnover_rate.csv，请先运行 python rebuild_turnover.py")
    except Exception as e:
        log(f"    换手率读取失败: {e}")
        src["turnover"] = None

    log("[src 3/7] PCR(上交所ETF期权持仓比，真增量) ...")
    try:
        src["pcr"] = get_or_fetch("pcr", fetch_pcr, max_age_hours=12)
    except Exception as e:
        log(f"    PCR抓取失败: {e}")
        src["pcr"] = None

    log("[src 4/7] 新基金发行 ...")
    try:
        src["fi"] = get_or_fetch("fund_issue", fetch_fund_issue)
    except Exception as e:
        log(f"    新基金发行失败: {e}")
        src["fi"] = None

    log("[src 5/7] 基金指数(股基年化/正收益占比代理) ...")
    try:
        src["fi_idx"] = get_or_fetch("fund_index", fetch_fund_index)
    except Exception as e:
        log(f"    基金指数失败: {e}")
        src["fi_idx"] = None

    log("[src 6/7] 沪深300 + 中债综合指数(神奇指标) ...")
    try:
        src["hs300"] = get_or_fetch("hs300", fetch_hs300)
        src["bidx"] = get_or_fetch("bond_idx", fetch_bond_idx)
    except Exception as e:
        log(f"    神奇指标数据失败: {e}")
        src["hs300"] = src["bidx"] = None

    log("[src 7/7] 申万一级行业价格历史(低估行业) ...")
    try:
        src["sw"] = fetch_sw_industries()
    except Exception as e:
        log(f"    申万行业失败: {e}")
        src["sw"] = {}
    return src


# ---------------- 综合分指标：一个指标一个函数，逻辑与原 main 逐字一致 ----------------
def calc_rp(src):
    try:
        pe, bond10 = src["pe"], src["bond10"]
        ep = (1.0 / pe) * 100.0  # 盈利收益率%
        # PE 为月频、国债为日频：各自前向填充到并集日历再相减，
        # 避免直接取交集导致每月只剩 1 个点（历史分位样本不足）。
        idx = ep.index.union(bond10.index)
        rp = (ep.reindex(idx).ffill() - bond10.reindex(idx).ffill()).dropna()
        rp = rp[rp.index <= TODAY]
        return make_panel("rp", "A股风险溢价", rp,
                          "全A平均市盈率(乐咕 stock_market_pe_lg 无参，月频1997-12起)算盈利收益率1/PE，减10年国债收益率(东财 bond_zh_us_yield_em 日频2010起)；两序列各自ffill到交易日历并集后相减。2026-11-20 修复此前 symbol 参数失效误用科创板PE(90.51)的失真口径",
                          reverse=True, desc="股票相对债券的预期收益补偿，越高越接近底部区域(反向指标)。PE 为月频数据按日对齐，月内数值不变属正常；分位基于日频填充序列计算")
    except Exception as e:
        log(f"    [rp] 计算失败: {e}")
        return None


def calc_dgz(src):
    try:
        sw = src["sw"]
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
                return make_panel("dgz", "低估指数行业", n_low,
                                  "申万31个一级行业指数(akshare index_hist_sw)收盘价的全历史expanding分位，统计处于低分位(<30%)的行业个数",
                                  reverse=True, unit="个", desc="处于低估状态的申万一级行业个数越多，市场整体越低估(反向指标)")
        return None
    except Exception as e:
        log(f"    [dgz] 计算失败: {e}")
        return None


def calc_fx_fe(src):
    try:
        fi = src["fi"]
        if fi is not None:
            roll = fi.rolling("90D").mean().dropna()
            roll = roll[roll.index <= TODAY]
            return make_panel("fx_fe", "新基金发行", roll,
                              "按成立日期聚合募集份额(akshare fund_new_found_em)，取近90日滚动日均发行份额(亿份)",
                              reverse=False, unit="亿份", desc="近90日日均新基金发行份额，反映资金亢奋程度(越高越贪婪)")
        return None
    except Exception as e:
        log(f"    [fx_fe] 计算失败: {e}")
        return None


def calc_pgjj_zsy(src):
    try:
        fi_idx = src["fi_idx"]
        if fi_idx is not None:
            r1y = fi_idx / fi_idx.shift(244) - 1.0  # 近1年收益
            r1y = r1y.dropna()
            pos = (r1y > 0).astype(float)
            ratio = pos.rolling(252, min_periods=60).mean() * 100
            ratio = ratio.dropna()
            ratio.name = "ratio"
            return make_panel("pgjj_zsy", "正收益偏股基金占比", ratio,
                              "代理指标：上证基金指数(sh000011)近1年收益率为正的天数占比(252日滚动)。原站用全市场偏股基金全样本，公开源无法精确复刻",
                              reverse=False, desc="近一年收益为正的偏股基金占比，越低越接近底部(代理指标)")
        return None
    except Exception as e:
        log(f"    [pgjj_zsy] 计算失败: {e}")
        return None


def calc_qschsl(src):
    try:
        turnover = src["turnover"]
        if turnover is not None:
            t = turnover[turnover.index <= TODAY].dropna()
            return make_panel("qschsl", "全市场换手率", t,
                              "交易所权威口径：(沪市成交额+深市成交额)÷(沪市总市值+深市总市值)×100%（数据源：上交所每日股票成交概况、深交所每日证券概况）；2022年前沪市无官方数据，用深市换手率×沪深稳定比例桥接",
                              reverse=False, desc="量价维度情绪指标，换手率越高交易越火热、情绪越亢奋，逼近极值可能预示筑顶或磨底")
        return None
    except Exception as e:
        log(f"    [qschsl] 计算失败: {e}")
        return None


def calc_pcr(src):
    try:
        pcr = src["pcr"]
        if pcr is not None:
            return make_panel("pcr", "PCR", pcr,
                              "上交所ETF期权未平仓认沽合约数÷未平仓认购合约数(akshare option_daily_stats_sse，逐交易日抓取)，综合50ETF/300ETF/500ETF/科创50ETF共5个标的",
                              reverse=False, unit="", desc="认沽/认购持仓比。A股实证为顺周期：PCR与指数同向，处于低位时市场往往已在底部区域（后续收益更好），处高位时需谨慎。2021年以来PCR低15%分位未来60日平均+2.6%、高15%分位-3.5%")
        return None
    except Exception as e:
        log(f"    [pcr] 计算失败: {e}")
        return None


def calc_zzpgnh(src):
    try:
        fi_idx = src["fi_idx"]
        if fi_idx is not None:
            ann = (fi_idx / fi_idx.shift(3 * 244)) ** (1 / 3) - 1.0
            ann = ann.dropna() * 100
            ann.name = "ann"
            return make_panel("zzpgnh", "股基滚动年化收益", ann,
                              "代理指标：上证基金指数(sh000011)相对3年前(756交易日)的年化收益率。原站用偏股型基金全样本，此处用基金指数代理",
                              reverse=False, desc="往前3年买入偏股基金并持有的年化收益，向下偏离均值可能是底部")
        return None
    except Exception as e:
        log(f"    [zzpgnh] 计算失败: {e}")
        return None


def calc_magic(src):
    try:
        hs300, bidx = src["hs300"], src["bidx"]
        if hs300 is not None and bidx is not None:
            j = hs300.index.intersection(bidx.index)
            h = hs300.loc[j]; b = bidx.loc[j]
            ratio = (h / h.iloc[0]) / (b / b.iloc[0])
            ratio = ratio.dropna()
            ratio.name = "magic"
            return make_panel("magic", "神奇指标", ratio,
                              "代理指标：沪深300价格指数 ÷ 中债综合财富指数(财富总值)的归一化比价。原站用沪深300全收益vs偏债基金指数，此处用可公开获取的指数代理",
                              reverse=True, unit="", desc="股票与债券资产回报差，沪深300越贴近债券走势时越可能见底(反向指标)")
        return None
    except Exception as e:
        log(f"    [magic] 计算失败: {e}")
        return None


# 注册表：新增综合分指标只需写一个 calc_* 并在此登记一行
METRIC_BUILDERS = [
    ("rp", calc_rp), ("dgz", calc_dgz), ("fx_fe", calc_fx_fe),
    ("pgjj_zsy", calc_pgjj_zsy), ("qschsl", calc_qschsl), ("pcr", calc_pcr),
    ("zzpgnh", calc_zzpgnh), ("magic", calc_magic),
]
METRIC_ORDER = [k for k, _ in METRIC_BUILDERS]


# ---------------- 独立观察面板（均不计入综合分）----------------
def calc_compare_panel():
    """换手率·上交所官方口径（仅作口径对比）"""
    try:
        tss = fetch_turnover_sse()
        if tss is not None:
            tss = tss[tss.index <= TODAY].dropna()
            return make_panel(
                "qschsl_sse", "换手率·上交所官方口径", tss,
                "上交所官方每日股票换手率字段（成交额÷市价总值×100%），2022-02 起，全程同一口径、不拼接深市、不做系数换算。用于与上方『全市场换手率』对比：上方为还原全市场量级的桥接口径，本项为原始沪市口径，数值系统性偏低但无拼接",
                reverse=False, unit="%",
                desc="【口径对比图】与上方全市场换手率对照：本图只用上交所官方换手率，2022年起口径统一、无深市桥接，因此2024年9月前后不出现拼接差异。数值为沪市单一市场口径，低于全市场属正常，主要用于观察形态与台阶变化，不参与综合情绪分。")
        return None
    except Exception as e:
        log(f"    [qschsl_sse] 计算失败: {e}")
        return None


def calc_crowding_panel():
    """交易拥挤度（前5%个股成交额占比），独立卡片"""
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
                    cp["auxiliary"] = aux
                except Exception:
                    pass
            return cp
        return None
    except Exception as e:
        log(f"    [crowding] 计算失败: {e}")
        return None


def calc_etf_ratio_panel():
    """头部300ETF占沪深300流通市值比例"""
    try:
        er = fetch_etf_ratio()
        if er is not None:
            er = er[er.index <= TODAY].dropna()
            mp = make_panel(
                "etf300_ratio", "头部300ETF占沪深300流通市值比例", er,
                "分子：三只上交所头部300ETF(510300/510310/510330)份额×单位净值之和(份额取上交所每日ETF规模表，净值取天天基金)；159919因深交所接口不可用未纳入。分母：沪深300流通市值(以指数点位×流通市值锚线性代理)。全程公开数据自算，不抓取乐咕成品数据",
                reverse=False, unit="%",
                desc="被动指数资金对沪深300的定价力量与拥挤度。2024年以来中央汇金等通过宽基ETF持续增持，头部300ETF规模快速膨胀，该比例趋势性抬升，反映被动资金对大盘核心资产边际定价权增强。该指标不含情绪方向判断，仅作市场结构观察，不参与综合情绪分。")
            if mp:
                mp["score_label"] = "历史分位"
                mp["score_hint"] = "（越高=被动资金占比越高）"
            return mp
        return None
    except Exception as e:
        log(f"    [etf300_ratio] 计算失败: {e}")
        return None


def calc_margin_panel():
    """两融余额 60 日增速（新建，独立卡片）"""
    try:
        d = fetch_margin()
        if d is None or "growth_60d" not in d.columns:
            return None
        s = d["growth_60d"].dropna()
        p = make_panel(
            "margin_growth", "两融余额60日增速", s,
            "融资余额相对 60 个交易日前的变动率(%)，取 akshare stock_margin_account_info（沪深两融账户统计，2012-09 起日更，接口一次返回全历史）。"
            "辅助观察：融资余额绝对规模、平均维持担保比例。",
            reverse=False, unit="%",
            desc="杠杆资金进出速度。增速快速上行=融资加杠杆、风险偏好抬升、情绪偏亢奋；增速转负且持续走低=主动去杠杆、常对应调整或底部区域。"
            "维持担保比例是杠杆安全垫，越低越接近强平压力区。本卡片为结构观察指标，不计入综合情绪分。")
        if p:
            bal = d["margin_bal"].dropna()
            mnt = d["maintenance_ratio"].dropna()
            p["score_label"] = "情绪分位"
            p["score_hint"] = "（越高=杠杆资金越亢奋）"
            p["aux_text"] = (f"辅助指标：融资余额 {bal.iloc[-1]:.0f} 亿元；"
                             f"平均维持担保比例 {mnt.iloc[-1]:.2f}%（越高=杠杆安全垫越厚，越低越接近强平压力区）。")
        return p
    except Exception as e:
        log(f"    [margin_growth] 计算失败: {e}")
        return None


def calc_breadth_panel():
    """全A站上 200 日均线个股占比（新建，独立卡片）"""
    try:
        d = fetch_breadth()
        if d is None or "pct_above_ma200" not in d.columns:
            return None
        s = d["pct_above_ma200"].dropna()
        p = make_panel(
            "breadth_ma200", "全A站上200日均线占比", s,
            "逐日横截面：当日收盘价 > 自身200日均线的个股数 ÷ 当日有效股票数 ×100%。"
            "全A列表取新浪实时行情，个股收盘价取新浪 stock_zh_a_hist_sina（未复权，除权跳空对横截面统计影响有限），"
            "多进程逐只缓存到 data/breadth_prices/ 后自算。",
            reverse=False, unit="%",
            desc="市场宽度/趋势参与度。高（>70%）=普涨、趋势强、情绪偏热；低（<20%）=普跌、超卖，历史上常对应底部区域。"
            "宽度背离（指数创新高但占比走低）往往提示涨势由少数权重股支撑、内部动能转弱。本卡片不计入综合情绪分。")
        if p:
            s60 = d["pct_above_ma60"].dropna()
            p["score_label"] = "情绪分位"
            p["score_hint"] = "（越高=市场越普涨）"
            p["aux_text"] = (f"辅助指标：站上 60 日均线占比 {s60.iloc[-1]:.1f}%；"
                             f"当日有效股票 {int(d['n_stocks'].iloc[-1])} 只（需上市满 200 个交易日才纳入统计）。")
        return p
    except Exception as e:
        log(f"    [breadth_ma200] 计算失败: {e}")
        return None


def calc_pb_panel():
    """全A市净率中位数（月频，新建，独立卡片）"""
    try:
        d = fetch_pb()
        if d is None or "pb_median" not in d.columns:
            return None
        s = d["pb_median"].dropna()
        p = make_panel(
            "a_pb", "全A市净率中位数（月频）", s,
            "全A个股 PB 的中位数，取 akshare stock_a_all_pb（乐咕乐股，2005 起月频，约 263 个月点，接口一次返回全历史）。"
            "辅助序列：等权平均 PB。注意与日频指标不同频，区间切换按数据点计。",
            reverse=False, unit="倍",
            desc="全市场估值中枢。PB 处于低分位=整体便宜、长期赔率较好；高分位=估值偏贵。"
            "中位数比整体法 PB 更不易被少数超大市值股拉动，等权平均则对小盘股更敏感，两者背离可观察风格分化。"
            "本卡片为估值结构观察，不含情绪方向判断，不计入综合情绪分。")
        if p:
            eq = d["pb_equal_weight"].dropna()
            p["freq"] = "monthly"
            p["score_label"] = "历史分位"
            p["score_hint"] = "（越高=估值越贵）"
            p["aux_text"] = (f"辅助指标：等权平均 PB {eq.iloc[-1]:.2f} 倍；"
                             f"共 {len(s)} 个月度数据点（月频，非日频）。")
        return p
    except Exception as e:
        log(f"    [a_pb] 计算失败: {e}")
        return None


# ---------------- 顶底信号明细卡（独立观察，均不计入综合分）----------------
def _aux_rows(d, fields):
    """扩展序列 -> 前端 auxiliary 数组（与图表主序列按日期对齐）。"""
    rows = []
    for dt, r in d.iterrows():
        if dt > TODAY:
            continue
        row = {"date": dt.strftime("%Y-%m-%d")}
        for f in fields:
            v = r.get(f)
            row[f] = round(float(v), 3) if pd.notna(v) else None
        rows.append(row)
    return rows


def calc_below_net_panel():
    """破净率：历次大底峰值同步出现的硬指标。"""
    try:
        d = _read_frame("below_net")
        if d is None or "below_net_ratio" not in d.columns:
            return None
        s = d["below_net_ratio"].dropna()
        p = make_panel(
            "below_net", "破净率（全A破净个股占比）", s,
            "破净个股数 ÷ 全A上市公司数 ×100%，取 akshare stock_a_below_net_asset_statistics（乐咕乐股，2005-01 起日频，一次返回全历史）。"
            "破净率越高=资产折价越极端，历史上 2005/2008/2018/2024 大底峰值分别为 24.4%/14.1%/14.0%/16.6%。",
            reverse=True, unit="%",
            desc="A股顶底判断中底部指示最硬的单指标：市场愿意以低于净资产的价格卖出大量公司，通常意味着系统性的极度悲观。"
            "但它给的是赔率不是时点——破净率可以在高位维持数月，且顶部预测力明显弱于底部。"
            "外部口径（如东财财富号）因子公司剔除规则不同可能与本序列有 1~2 个百分点差异，比较时需注意口径。")
        if p:
            p["score_label"] = "底部信号分位"
            p["score_hint"] = "（越高=越接近历史底部）"
            parts = []
            for label, a, b in [("2005年998点", "2005-01-01", "2005-12-31"),
                                ("2008年1664点", "2008-09-01", "2008-12-31"),
                                ("2018年2440点", "2018-10-01", "2019-01-31"),
                                ("2024年2635点", "2024-01-01", "2024-03-31")]:
                x = s[(s.index >= a) & (s.index <= b)]
                if len(x):
                    parts.append(f"{label} {x.max():.1f}%")
            p["aux_text"] = ("历次大底峰值：" + "、".join(parts) +
                             f"；当前 {s.iloc[-1]:.2f}%（破净 {int(d['below_net_asset'].iloc[-1])} 家 / "
                             f"全A {int(d['total_company'].iloc[-1])} 家）。")
        return p
    except Exception as e:
        log(f"    [below_net] 计算失败: {e}")
        return None


def calc_nhnl_panel():
    """新高新低差：创60日新高家数 - 新低家数，观察下跌动能是否衰竭。"""
    try:
        d = _read_frame("breadth_extra")
        if d is None or "nh_nl" not in d.columns:
            return None
        s = d["nh_nl"].dropna()
        p = make_panel(
            "nhnl", "新高新低差（NH-NL）", s,
            "逐日横截面：创 60 日新高个股占比 − 创 60 日新低个股占比（%）。"
            "复用 data/breadth_prices/ 下 5547 只个股未复权收盘价，零网络请求自算；"
            "辅助线为创新高/创新低家数占比。",
            reverse=False, unit="%",
            desc="市场内部动能最直观的宽度指标。大幅为负=普跌、抛压广泛；由极负值收敛往往是下跌动能衰竭的早期迹象。"
            "顶底关键看背离：指数创 60 日新低而 NH-NL 不再创新低，说明下跌只剩少数权重股贡献；"
            "指数创新高而 NH-NL 走低则提示涨势收窄。背离判定见「顶底共振信号板」。")
        if p:
            p["score_label"] = "情绪分位"
            p["score_hint"] = "（越低=抛压越广泛）"
            p["auxiliary"] = _aux_rows(d, ["new_high", "new_low"])
            p["aux_lines"] = [
                {"name": "创新高占比", "field": "new_high", "color": "#e8633a"},
                {"name": "创新低占比", "field": "new_low", "color": "#5b8ff9"},
            ]
            p["aux_text"] = (f"辅助：当前创新高 {d['new_high'].iloc[-1]:.2f}%、"
                             f"创新低 {d['new_low'].iloc[-1]:.2f}%；"
                             f"当日有效股票 {int(d['n_stocks'].iloc[-1])} 只（需上市满 60 个交易日）。")
        return p
    except Exception as e:
        log(f"    [nhnl] 计算失败: {e}")
        return None


def calc_adv_panel():
    """涨跌家数占比 + 20日动量广度 + 均线多头排列 + 腾落线变化。"""
    try:
        d = _read_frame("breadth_extra")
        if d is None or "adv_ratio" not in d.columns:
            return None
        s = d["adv_ratio"].dropna()
        p = make_panel(
            "adv_ratio", "上涨家数占比与动能广度", s,
            "逐日横截面：收盘价高于前一日收盘的个股占比（%）。辅助线：近 20 日收益为正的个股占比、"
            "MA5>MA20>MA60 多头排列个股占比，三条序列同为 0~100% 可同轴比较。数据源同 NH-NL 卡片。",
            reverse=False, unit="%",
            desc="短周期参与度。上涨家数占比持续偏低=普跌；若指数横盘或反弹而该比例与 20 日动量广度同步回升，"
            "说明内部修复是全面的而非少数权重拉动。腾落线(ADL)为每日净广度的累计值，绝对值无意义，"
            "重点看它是否与指数同步创新低——不同步即为底部动能背离。")
        if p:
            p["score_label"] = "情绪分位"
            p["score_hint"] = "（越低=越普跌）"
            p["auxiliary"] = _aux_rows(d, ["mom20_ratio", "ma_bull"])
            p["aux_lines"] = [
                {"name": "20日动量广度", "field": "mom20_ratio", "color": "#8b5cf6"},
                {"name": "多头排列占比", "field": "ma_bull", "color": "#5b8ff9"},
            ]
            ad = d["ad_line"].dropna()
            chg = float(ad.iloc[-1] - ad.iloc[-61]) if len(ad) > 60 else float("nan")
            p["aux_text"] = (f"辅助：20 日动量广度 {d['mom20_ratio'].iloc[-1]:.2f}%、"
                             f"多头排列占比 {d['ma_bull'].iloc[-1]:.2f}%；"
                             f"腾落线近 60 交易日累计变化 {chg:+.1f} 个百分点"
                             f"（当前 {ad.iloc[-1]:.1f}，负值持续扩大=广度仍在恶化）。")
        return p
    except Exception as e:
        log(f"    [adv_ratio] 计算失败: {e}")
        return None


def calc_tech_panel():
    """沪深300 RSI14 + BIAS20：短期超卖/超买与均线乖离。"""
    try:
        d = _read_frame("tech")
        if d is None or "rsi14" not in d.columns:
            return None
        s = d["rsi14"].dropna()
        p = make_panel(
            "hs300_rsi", "沪深300 RSI14（超卖/超买）", s,
            "Wilder RSI(14)，取新浪沪深300价格指数收盘价(sh000300)自算；辅助信息给出 BIAS20=(收盘/MA20−1)×100% "
            "与 MA20/MA60 相对位置。用固定 30/70 阈值作参考线，历史分位用于观察极端程度。",
            reverse=False, unit="",
            desc="短期动量极端值。<30 为超卖、>70 为超买，但强势趋势中 RSI 可长期钝化，"
            "单独用阈值抄底/逃顶容易被趋势碾过；与宽度、破净率等结构性指标共振时参考价值更高。"
            "BIAS20 为负说明指数在 20 日均线下方，负值越深短期跌幅越急。")
        if p:
            p["score_label"] = "情绪分位"
            p["score_hint"] = "（越低=越超卖）"
            p["mark_lines"] = [
                {"y": 30, "label": "30 超卖线", "color": "#2aa876"},
                {"y": 70, "label": "70 超买线", "color": "#e8633a"},
            ]
            last = d.iloc[-1]
            p["aux_text"] = (f"辅助：BIAS20 {last['bias20']:+.2f}%（收盘 {last['hs300']:.2f} / "
                             f"MA20 {last['ma20']:.2f} / MA60 {last['ma60']:.2f}）；"
                             f"收盘{'高于' if last['hs300'] > last['ma20'] else '低于'}MA20，"
                             f"{'高于' if last['hs300'] > last['ma60'] else '低于'}MA60。")
        return p
    except Exception as e:
        log(f"    [hs300_rsi] 计算失败: {e}")
        return None


SIGNAL_BUILDERS = [
    ("below_net", calc_below_net_panel),
    ("nhnl", calc_nhnl_panel),
    ("adv_ratio", calc_adv_panel),
    ("hs300_rsi", calc_tech_panel),
]


def build_signal_panels():
    out = {}
    for key, fn in SIGNAL_BUILDERS:
        p = fn()
        if p:
            out[key] = p
    return out


MACRO_BUILDERS = [
    ("etf300_ratio", calc_etf_ratio_panel),
    ("margin_growth", calc_margin_panel),
    ("breadth_ma200", calc_breadth_panel),
    ("a_pb", calc_pb_panel),
]


def build_macro_panels():
    out = {}
    for key, fn in MACRO_BUILDERS:
        p = fn()
        if p:
            out[key] = p
    return out


# ---------------- 综合情绪分 ----------------
def build_composite(panels):
    """各面板 score 等权平均；历史按共同区间对齐后周频采样。"""
    composite_parts = {k: p["score"] for k, p in panels.items()}
    composite = round(float(np.mean(list(composite_parts.values()))), 2) if composite_parts else 50.0
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
        comp = df.ffill().mean(axis=1).dropna()
        comp = comp[comp.index >= common_start]
        comp_w = comp.resample("W-FRI").last().dropna()
        comp_hist = [
            {"date": dt.strftime("%Y-%m-%d"), "score": round(float(v), 2)}
            for dt, v in comp_w.items()
        ]
        log(f"    综合分历史: {comp.index.min().date()} ~ {comp.index.max().date()}, {len(comp_hist)}周点")
    except Exception as e:
        log(f"    综合分历史计算失败: {e}")
        comp_hist = []
    return composite, comp_hist


# ---------------- 输出 ----------------
def write_output(panels, compare_panel, macro_panels, crowding_panel,
                 signal_panels, turning_panel, composite, comp_hist, t0):
    out = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": TODAY.strftime("%Y-%m-%d"),
        "composite": {"score": composite, "state": state_of(composite)[0], "history": comp_hist},
        "panels": panels,
        "compare_panel": compare_panel,
        "macro_panels": macro_panels,
        "macro_order": [k for k, _ in MACRO_BUILDERS if k in macro_panels],
        "crowding_panel": crowding_panel,
        "signal_panels": signal_panels,
        "signal_order": [k for k, _ in SIGNAL_BUILDERS if k in signal_panels],
        "turning_panel": turning_panel,
        "order": METRIC_ORDER,
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
    log(f"生成综合分面板 {len(panels)}/8: {', '.join(panels.keys())}")
    for k in out["order"]:
        if k in panels:
            p = panels[k]
            log(f"  {p['name']:16s} 值={p['value']:>8.3f} 情绪分={p['score']:>6.2f} {p['state']} [{out['proxy_flags'][k]}] 历史{len(p['history'])}日")
    log(f"结构观察卡片 {len(macro_panels)}/4: {', '.join(macro_panels.keys())}")
    log(f"顶底信号卡片 {len(signal_panels)}/{len(SIGNAL_BUILDERS)}: {', '.join(signal_panels.keys())}")
    if turning_panel:
        c = turning_panel["current"]
        log(f"共振信号板: 底部{c['bottom_score']} 顶部{c['top_score']} 确认{c['confirm']}/3 -> {c['state']}")
    log(f"综合情绪分: {composite} ({state_of(composite)[0]})")
    log(f"输出: {jpath.name} ({jpath.stat().st_size/1024:.0f}KB) / {jspath.name}")


def main():
    t0 = time.time()
    log("=" * 60)
    log("情绪指标面板构建（公开数据自算版）")
    log("=" * 60)

    src = load_sources()
    panels = {}
    for key, fn in METRIC_BUILDERS:
        p = fn(src)
        if p:
            panels[key] = p

    compare_panel = calc_compare_panel()
    macro_panels = build_macro_panels()
    crowding_panel = calc_crowding_panel()

    # 共振板依赖 load_sources() 刚落盘的各源缓存，且会生成 mood_tech.csv 供技术位置卡片读取，
    # 因此必须先于 build_signal_panels()。build_turning.py 亦可独立运行重算。
    try:
        import build_turning
        turning_panel = build_turning.build()
    except Exception as e:
        log(f"    [turning] 共振信号板构建失败: {e}")
        turning_panel = None
    signal_panels = build_signal_panels()

    composite, comp_hist = build_composite(panels)
    write_output(panels, compare_panel, macro_panels, crowding_panel,
                 signal_panels, turning_panel, composite, comp_hist, t0)


if __name__ == "__main__":
    main()
