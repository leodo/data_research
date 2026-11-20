# -*- coding: utf-8 -*-
"""
情绪指标面板构建引擎（公开数据自算版）
=====================================
仿 funddb.cn/indicators?tab=mood 的 8 个情绪指标，全部用公开数据源自行计算，
不取用原站任何专有/会员数据。指标口径依据原站公开释义(paraphrase)还原。

8 个指标：
  1. rp         A股风险溢价      = 全A盈利收益率(1/PE) − 10年国债收益率    [月频PE按日对齐]
  2. dgz        低估指数行业      = 申万31个一级行业中处于低估(低分位)的个数 [近似:价格分位代理估值]
  3. fx_fe      新基金发行        = 近90日窗口各发行日平均募集份额(亿份)   [精确自算; 源仅回溯2023-01]
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

from config import ERP_ATTR_WIN, ERP_ILLUSION_SHARE, REPURCHASE_ROLL
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


# 注：data/mood_all_pe.csv 与 data/mood_total_mv.csv 是口径修复前遗留的坏缓存
# (symbol 参数失效误取科创板 PE 90.51)，已无任何消费者，仅为历史留档，勿再读取。


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
def build_erp_attrib(src):
    """把 rp 变动拆成股票端(1/PE)与债券端(国债收益率)贡献，落盘供归因卡片与 rp 警示复用。

    rp = 盈利收益率 EP − 10年国债。两端都会让 rp 抬升：股票变便宜(EP↑)，
    或者无风险利率下行(bond10↓)。后者不代表权益更划算，只是债券收益率更低，
    此时用 ERP 历史分位判断「股票便宜」会系统性高估性价比（便宜错觉）。
    本函数不新增网络请求，直接复用 load_sources() 已加载的两条源序列。
    """
    try:
        pe, bond10 = src.get("pe"), src.get("bond10")
        if pe is None or bond10 is None:
            log("    [erp_attrib] 缺少 PE / 国债源，跳过")
            return None
        pe, bond10 = sjoin(pe, "pe"), sjoin(bond10, "bond10")
        idx = pe.index.union(bond10.index)
        idx = idx[idx <= TODAY]
        ep = 100.0 / pe.reindex(idx).ffill()
        b = bond10.reindex(idx).ffill()
        d = pd.DataFrame({"erp": ep - b, "ep": ep, "bond10": b}).dropna()
        w = ERP_ATTR_WIN
        d["d_erp"] = d["erp"].diff(w)
        d["d_ep"] = d["ep"].diff(w)
        d["d_bond"] = -d["bond10"].diff(w)      # 利率下行 → 正贡献
        den = d["d_ep"].abs() + d["d_bond"].abs()
        d["bond_share"] = d["d_bond"].abs() / den.where(den > 1e-9) * 100
        # 「便宜错觉」：rp 抬升、债券端主导、且股票端并没有同步变便宜
        d["illusion"] = ((d["d_erp"] > 0) & (d["bond_share"] >= ERP_ILLUSION_SHARE)
                         & (d["d_ep"] <= 0)).astype(int)
        d["illusion_gap"] = pct_rank(d["erp"]) - pct_rank(d["ep"])
        d["bond_pct"] = pct_rank(d["bond10"])
        # 落盘保留完整 erp/ep/bond10 历史：派生列 d_erp 等前 w 行为 NaN 属正常。
        # 2026-11-20 复核修复：此前用 dropna(subset=["d_erp"]) 裁掉前 250 行，
        # 导致「ERP 股债归因」卡与「A股风险溢价」主卡对同一个 rp 值给出
        # 97.05 与 97.23 两个不同分位。裁行只应发生在取最新归因结果时。
        out = d.copy()
        out.to_csv(DATA / "mood_erp_attrib.csv", index_label="date", float_format="%.4f")
        r = out.dropna(subset=["d_erp"]).iloc[-1]
        log(f"    [erp_attrib] rp={r['erp']:.3f}% 近{w}日 Δrp={r['d_erp']:+.3f}pp "
            f"= 股票端{r['d_ep']:+.3f} + 债券端{r['d_bond']:+.3f}；"
            f"债券端占 {r['bond_share']:.1f}%，便宜错觉={'是' if r['illusion'] else '否'}")
        return out
    except Exception as e:
        log(f"    [erp_attrib] 计算失败: {e}")
        return None


def calc_erp_attrib_panel():
    """ERP 股债归因：主序列 erp，同轴叠加 1/PE 与 10年国债，一眼看出谁在动。"""
    try:
        d = _read_frame("erp_attrib")
        if d is None or "erp" not in d.columns:
            return None
        p = make_panel("erp_attrib", "ERP 股债归因（便宜是真便宜吗）",
                       d["erp"].dropna(),
                       "全A平均市盈率(乐咕 stock_market_pe_lg 无参，月频)算盈利收益率1/PE，"
                       "10年国债收益率取自东财 bond_zh_us_yield_em(日频)；两序列按交易日并集 ffill 对齐后，"
                       "把近250交易日 rp 变动分解为股票端与债券端贡献。零额外网络请求。",
                       reverse=True, unit="%",
                       desc="rp = 1/PE − 10年国债。分子端(股票变便宜)与分母端(利率下行)都会让 rp 抬升，"
                       "但后者不代表权益更划算，只是无风险利率更低；此时用 ERP 历史分位判断估值便宜会"
                       "系统性高估性价比。图中三条线同轴：红实线=ERP，橙虚线=盈利收益率，蓝虚线=国债收益率，"
                       "可直接看出抬升由谁贡献。PE 为月频 ffill，股票端贡献呈阶梯状属正常。"
                       "注意本卡显示的是 ERP 原始值历史分位（越高=补偿越充分），与上方「A股风险溢价」"
                       "卡片的方向相反（后者是反向情绪分，越低越接近底部）。")
        if p:
            p["score_label"] = "历史分位"
            p["score_hint"] = "（越高=补偿越充分）"
            p["auxiliary"] = _aux_rows(d, ["ep", "bond10"])
            p["aux_lines"] = [
                {"name": "盈利收益率 1/PE", "field": "ep", "color": "#e8633a"},
                {"name": "10年国债收益率", "field": "bond10", "color": "#5b8ff9"},
            ]
            a = d.dropna(subset=["d_erp"])
            r = a.iloc[-1]
            if r["d_erp"] > 0 and r["illusion"] == 1:
                driver = "利率驱动 · 便宜错觉"
            elif r["d_erp"] > 0 and r["bond_share"] >= ERP_ILLUSION_SHARE:
                driver = "利率主导 · 需警惕"
            elif r["d_erp"] > 0:
                driver = "股票驱动 · 真实便宜"
            elif r["d_erp"] < 0:
                driver = "风险补偿收窄"
            else:
                driver = "基本持平"
            n_ill = int(a["illusion"].sum())
            p["aux_text"] = (
                f"近 {ERP_ATTR_WIN} 交易日：Δrp {r['d_erp']:+.3f}pp = 股票端(1/PE) {r['d_ep']:+.3f}pp "
                f"+ 债券端(利率下行) {r['d_bond']:+.3f}pp；债券端占变动 {r['bond_share']:.1f}% → {driver}。"
                f"10年国债 {r['bond10']:.3f}%（历史 {r['bond_pct']:.1f} 分位）；"
                f"ERP 分位与股票端 EP 分位之差 {r['illusion_gap']:+.1f}（越大越可能是利率造成的错觉）。"
                f"全历史「便宜错觉」触发 {n_ill} 天（占 {n_ill/len(a)*100:.1f}%），当前"
                f"{'已触发' if r['illusion'] == 1 else '未触发'}。")
        return p
    except Exception as e:
        log(f"    [erp_attrib] 面板失败: {e}")
        return None


def calc_rp(src):
    try:
        pe, bond10 = src["pe"], src["bond10"]
        ep = (1.0 / pe) * 100.0  # 盈利收益率%
        # PE 为月频、国债为日频：各自前向填充到并集日历再相减，
        # 避免直接取交集导致每月只剩 1 个点（历史分位样本不足）。
        idx = ep.index.union(bond10.index)
        rp = (ep.reindex(idx).ffill() - bond10.reindex(idx).ffill()).dropna()
        rp = rp[rp.index <= TODAY]
        p = make_panel("rp", "A股风险溢价", rp,
                       "全A平均市盈率(乐咕 stock_market_pe_lg 无参，月频1997-12起)算盈利收益率1/PE，减10年国债收益率(东财 bond_zh_us_yield_em 日频2010起)；两序列各自ffill到交易日历并集后相减。2026-11-20 修复此前 symbol 参数失效误用科创板PE(90.51)的失真口径",
                       reverse=True, desc="股票相对债券的预期收益补偿，越高越接近底部区域(反向指标)。PE 为月频数据按日对齐，月内数值不变属正常；分位基于日频填充序列计算")
        # 联动股债归因：低利率可能制造「便宜错觉」，在主卡直接给出警示
        if p is not None:
            try:
                a = _read_frame("erp_attrib")
                if a is not None and len(a):
                    r = a.dropna(subset=["d_erp"]).iloc[-1]
                    ill = int(r["illusion"]) == 1
                    p["erp_warn_level"] = "warn" if ill else "ok"
                    p["erp_warning"] = (
                        f"近 {ERP_ATTR_WIN} 交易日 Δrp {r['d_erp']:+.3f}pp 中，股票端(1/PE)贡献 "
                        f"{r['d_ep']:+.3f}pp、债券端(利率下行)贡献 {r['d_bond']:+.3f}pp（占 {r['bond_share']:.1f}%）。"
                        + ("注意：rp 抬升主要由无风险利率下行造成，股票端并未同步变便宜，"
                           "ERP 的高历史分位可能高估权益性价比，请勿单凭本指标判定底部。"
                           if ill else
                           "当前 rp 抬升主要由股票变便宜驱动，暂不属于低利率造成的「便宜错觉」。"))
            except Exception:
                pass
        return p
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
            # 2026-11-20 复核：东财 fund_new_found_em 实际只回溯到 2023-01，
            # 缓存里 2013/2018 两个孤点是接口残留，会让序列「看起来」有 13 年历史。
            # 剔除孤点后如实以 2023-01 为起点，并在来源说明里写明样本限制。
            fi = fi[fi.index >= "2023-01-01"]
            roll = fi.rolling("90D").mean().dropna()
            roll = roll[roll.index <= TODAY]
            return make_panel("fx_fe", "新基金发行", roll,
                              "按成立日期聚合募集份额(akshare fund_new_found_em)，取近90日窗口内"
                              "各发行日募集份额的滚动平均值(亿份)。"
                              "【数据限制】该接口仅回溯至 2023-01，2014-2022 无公开逐日发行份额数据，"
                              "故本卡历史分位仅基于约 3.8 年样本，跨时代可比性有限；"
                              "缓存中 2013/2018 两个孤立残点已剔除。",
                              reverse=False, unit="亿份",
                              desc="近90日窗口内各发行日的平均募集份额，反映单日新基金发行热度"
                              "(越高越亢奋)。注意这不是「日均发行份额」(合计÷90)，也不是窗口合计；"
                              "东财接口仅回溯至 2023-01，历史分位样本短于本页其他指标。"
                              "综合分历史已相应改为按当日可用指标等权，2023-02 前不含本指标。")
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
                              reverse=False, unit="", desc="认沽/认购持仓比。A股实证为顺周期：PCR与指数同向，处于低位时市场往往已在底部区域（后续收益更好），处高位时需谨慎。{@pcr_start} 以来，低 15% 分位未来 60 日平均 {@pcr_low60}%（{@pcr_low_n} 个样本、胜率 {@pcr_low_win}%），高 15% 分位 {@pcr_high60}%（{@pcr_high_n} 个样本、胜率 {@pcr_high_win}%），全历史基准 {@pcr_base60}%；数字随每次数据更新自动重算（重算于 {@as_of}）")
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
                              "代理指标：上证基金指数(sh000011)相对3年前(3×244=732个交易日)的年化收益率。原站用偏股型基金全样本，此处用基金指数代理",
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
                "上交所官方每日股票换手率字段（成交额÷市价总值×100%），2022-01 起，全程同一口径、不拼接深市、不做系数换算。与上方全市场换手率由同一次抓取同步产出、日期严格对齐。用于与上方『全市场换手率』对比：上方为还原全市场量级的桥接口径，本项为原始沪市口径，数值系统性偏低但无拼接",
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
            "破净率越高=资产折价越极端，按年度峰值自动取历史前 4 大底：{@bn_peaks}（截至 {@bn_as_of}，随数据更新重算）。",
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


# ---------------- 新增观察卡：机构 / 宏观流动性 / 产业资本 ----------------
def calc_fund_position_panel():
    """公募基金股票仓位（周频，2017-12 起）。"""
    try:
        d = _read_frame("fund_position")
        if d is None or "position" not in d.columns:
            return None
        p = make_panel("fund_position", "公募基金股票仓位",
                       d["position"].dropna(),
                       "乐咕乐股 fund_stock_position_lg（akshare），周频，2017-12 起，一次返回全历史。"
                       "仓位为第三方用基金净值回归测算的估计值，并非季报披露的真实持仓比例。",
                       reverse=False, unit="%",
                       desc="机构已经加了多少仓。高仓位=弹药消耗充分、增量买盘有限，历史上常对应情绪偏热与"
                       "阶段性高位；低仓位=现金充裕、潜在买盘足，多对应底部区域。注意这是测算值，"
                       "不同机构口径可差 1~3 个百分点，且历史起点为 2017-12，样本短于本页其他指标。")
        if p:
            p["freq"] = "weekly"
            p["score_label"] = "历史分位"
            p["score_hint"] = "（越高=机构越满仓）"
            p["auxiliary"] = _aux_rows(d, ["ma_12w", "chg_4w"])
            p["aux_lines"] = [{"name": "12周均线", "field": "ma_12w", "color": "#5b8ff9"}]
            r = d.iloc[-1]
            chg = f"{r['chg_4w']:+.2f}pp" if pd.notna(r["chg_4w"]) else "—"
            ma = f"{r['ma_12w']:.2f}%" if pd.notna(r["ma_12w"]) else "—"
            p["aux_text"] = (f"近 4 周仓位变化 {chg}，12 周均线 {ma}；"
                             f"历史区间 {d['position'].min():.2f}~{d['position'].max():.2f}%（{len(d)} 期周频样本）。")
        return p
    except Exception as e:
        log(f"    [fund_position] 计算失败: {e}")
        return None


def calc_m1m2_panel():
    """M1-M2 剪刀差（月频，2008-01 起）。"""
    try:
        d = _read_frame("m1m2")
        if d is None or "gap" not in d.columns:
            return None
        p = make_panel("m1m2", "M1-M2 剪刀差（资金活化）", d["gap"].dropna(),
                       "东方财富 macro_china_money_supply_em（akshare），月频，2008-01 起一次返回全历史；"
                       "gap = M1 同比 − M2 同比（百分点），辅助线为 M1/M2 同比与 3 月均值。",
                       reverse=False, unit="pp",
                       desc="M1 以企业活期存款为主，M2 还含居民储蓄与定期存款。剪刀差走阔或由负转正，"
                       "说明资金「活化」、流入实体与风险资产，历史上常领先或同步于股市行情；深度为负"
                       "说明资金沉淀、风险偏好低，多对应底部区域。央行 2024 年起调整过 M1 统计范围"
                       "（纳入个人活期存款与非银支付备付金），前后绝对值不完全可比，请以历史分位与方向为主。")
        if p:
            p["freq"] = "monthly"
            p["score_label"] = "历史分位"
            p["score_hint"] = "（越高=资金越活化）"
            p["auxiliary"] = _aux_rows(d, ["m1_yoy", "m2_yoy", "gap_ma3"])
            p["aux_lines"] = [
                {"name": "M1同比", "field": "m1_yoy", "color": "#e8633a"},
                {"name": "M2同比", "field": "m2_yoy", "color": "#5b8ff9"},
            ]
            p["mark_lines"] = [{"y": 0, "label": "0 资金活化分界", "color": "#2aa876"}]
            r = d.iloc[-1]
            p["aux_text"] = (f"当前 M1 {r['m1_yoy']:.2f}% / M2 {r['m2_yoy']:.2f}%，剪刀差 {r['gap']:+.2f}pp"
                             f"（3 月均值 {r['gap_ma3']:+.2f}pp）；历史区间 "
                             f"{d['gap'].min():.2f}~{d['gap'].max():.2f}pp，共 {len(d)} 个月点。")
        return p
    except Exception as e:
        log(f"    [m1m2] 计算失败: {e}")
        return None


def calc_repurchase_panel():
    """A股回购规模（月频，2015-01 起）。"""
    try:
        d = _read_frame("repurchase")
        col = f"plan_amt_roll{REPURCHASE_ROLL}"
        if d is None or col not in d.columns:
            return None
        p = make_panel("repurchase", f"A股回购规模（滚动{REPURCHASE_ROLL}月）",
                       d[col].dropna(),
                       "东方财富 stock_repurchase_em（akshare），5811 条回购方案按(代码,起始日)去重后"
                       "按月聚合；主口径为计划回购金额（上下限均值）的 12 个月滚动合计，辅助线为已回购金额。",
                       reverse=False, unit="亿元",
                       desc="回购是产业资本用真金白银表达「自家股票便宜」的行为，规模放量常出现在市场底部"
                       "或政策鼓励期（2018Q4、2024 年回购增持再贷款），是底部/转折的辅助证据。"
                       "局限：计划金额为预案区间中值、未必全部实施；已回购金额按方案起始月归档而非实际实施月；"
                       "金额未经市值归一化，长期有规模膨胀趋势，故以历史分位与相对变化为主，不宜跨时代比绝对值。")
        if p:
            p["freq"] = "monthly"
            p["score_label"] = "历史分位"
            p["score_hint"] = "（越高=产业资本越认可低估）"
            done_col = f"done_amt_roll{REPURCHASE_ROLL}"
            p["auxiliary"] = _aux_rows(d, [done_col])
            p["aux_lines"] = [{"name": f"已回购金额({REPURCHASE_ROLL}月)", "field": done_col, "color": "#5b8ff9"}]
            r = d.iloc[-1]
            p["aux_text"] = (f"当前滚动 {REPURCHASE_ROLL} 月计划回购 {r[col]:.1f} 亿、"
                             f"其中已实施 {r[done_col]:.1f} 亿；当月新披露预案 {int(r['n_plans'])} 家。"
                             f"历史峰值 {d[col].max():.1f} 亿，区间切换按数据点计（月频）。")
        return p
    except Exception as e:
        log(f"    [repurchase] 计算失败: {e}")
        return None


# ---------------- 新增观察卡：阿姆氏指标与市场宽广度体系 ----------------
def calc_arms_panel():
    """阿姆氏指标 TRIN：两套口径 + 10 日平滑 + 极端单边日诊断。"""
    try:
        d = _read_frame("arms")
        if d is None or "trin_vol" not in d.columns:
            return None
        p = make_panel("arms", "阿姆氏指标 TRIN（Arms Index）", d["trin_vol"].dropna(),
                       "复用 data/breadth_prices/(5547 只未复权收盘价) 与 data/crowding_amounts/(成交额) "
                       "按日对齐自算，零个股请求。TRIN=(涨家数/跌家数)÷(涨股总成交量/跌股总成交量)，"
                       "代数上等价于「平均每只下跌股成交量 ÷ 平均每只上涨股成交量」；"
                       "主序列为经典成交量口径(amount/close 反推股数)，辅助线含 10 日平滑与成交额口径。",
                       reverse=False, unit="",
                       desc="Richard Arms 1967 年的量能分配指标：TRIN>1 表示平均每只下跌股的成交量大于上涨股，"
                       "<1 表示上涨股平均放量。它衡量的是「量在涨跌两组之间的分配」，不是全市场抛压总量。"
                       "A 股齐涨齐跌特征明显，家数比在极端单边日失去上界，会让 TRIN 机械失真："
                       "极端普跌日家数比趋近 0 → TRIN 偏低（并非买盘强劲，而是少数上涨股吸走成交叠加全面失血）；"
                       "极端普涨日家数比放大 → TRIN 偏高（并非恐慌抛售，而是上涨家数太多、单只上涨股量能被摊薄）。"
                       "因此教科书「>2 = 恐慌超卖/底部机会、<0.5 = 买盘过热」在 A 股不可机械套用，"
                       "务必同时看上涨家数占比与指数位置；10 日平滑(蓝虚线)能降噪但不能消除该结构性偏差。")
        if p:
            p["score_label"] = "历史分位"
            p["score_hint"] = "（越高=跌股平均量相对越大，极端单边日会失真）"
            p["auxiliary"] = _aux_rows(d, ["trin10", "trin"])
            p["aux_lines"] = [
                {"name": "TRIN 10日平滑", "field": "trin10", "color": "#5b8ff9"},
                {"name": "成交额口径 Dollar TRIN", "field": "trin", "color": "#8b5cf6"},
            ]
            p["mark_lines"] = [
                {"y": 0.5, "label": "0.5 低位参考", "color": "#e8633a"},
                {"y": 1.0, "label": "1.0 均衡", "color": "#2aa876"},
                {"y": 2.0, "label": "2.0 高位参考", "color": "#2aa876"},
            ]
            r = d.iloc[-1]
            base = (f"当前成交量口径 {r['trin_vol']:.3f}、10 日平滑 {r['trin10']:.3f}、"
                    f"成交额口径 {r['trin']:.3f}；上涨/下跌量比 {r['adv_vol_ratio']:.3f}、"
                    f"上涨股量占比 {r['up_vol_share']:.2f}%、上涨 {int(r['n_adv'])} 家 / "
                    f"下跌 {int(r['n_dec'])} 家。两套口径中位均为 "
                    f"{d['trin_vol'].median():.3f}，1%/99% 分位 "
                    f"{d['trin_vol'].quantile(.01):.3f}/{d['trin_vol'].quantile(.99):.3f}。")
            # 极端单边日诊断：TRIN 高位究竟是恐慌抛售还是普涨日家数比放大，
            # 用全历史构成与最近一次实例让用户可自行核对图上任意一个尖峰。
            share = d["n_adv"] / (d["n_adv"] + d["n_dec"]) * 100
            hi = d[d["trin_vol"] > 2.0]
            hs = share.reindex(hi.index)
            eup = d.index[(share > 80) & (d["trin_vol"] > 2.0)]
            diag = ""
            if len(hi):
                diag += (f"【极端单边日诊断】全历史 TRIN>2 共 {len(hi)} 日，其中上涨家数占比 >80% 的"
                         f"普涨日 {int((hs > 80).sum())} 日、<20% 的恐慌日 {int((hs < 20).sum())} 日"
                         "——高位极值主要由家数比放大驱动，并非恐慌抛售。")
            if len(eup):
                dt, rr = eup[-1], d.loc[eup[-1]]
                diag += (f"最近一次「普涨却 TRIN>2」：{dt.strftime('%Y-%m-%d')} "
                         f"TRIN={rr['trin_vol']:.3f}、上涨占比 {share.loc[dt]:.1f}%、"
                         f"涨 {int(rr['n_adv'])} / 跌 {int(rr['n_dec'])} 家，"
                         "此类日不可按「超卖底部」解读。")
            p["aux_text"] = base + diag
        return p
    except Exception as e:
        log(f"    [arms] 计算失败: {e}")
        return None


def calc_breadth_score_panel():
    """市场宽广度综合指标：6 维宽度分位等权合成 + Zweig 推力线。"""
    try:
        d = _read_frame("breadth_advance")
        if d is None or "breadth_score" not in d.columns:
            return None
        p = make_panel("breadth_score", "市场宽广度综合指标", d["breadth_score"].dropna(),
                       "复用 mood_breadth.csv 与 mood_breadth_extra.csv：站上MA200/MA60占比、NH-NL、"
                       "上涨家数占比、20日动量广度、多头排列占比，各自取全历史 expanding 分位后等权合成 0~100；"
                       "辅助线为 Zweig Breadth Thrust 的 10 日 EMA。零网络请求。",
                       reverse=False, unit="",
                       desc="把多个宽度维度压成一个可跨时代比较的读数：高=普涨、内部健康、情绪偏热；"
                       "低=普跌、内部动能枯竭，常对应底部区域。相比单一「站上 200 日线占比」，"
                       "综合分不易被某一口径的除权跳空或行业结构带偏，但也因此更平滑、更滞后。"
                       "紫虚线为宽度推力 EMA10：由 ≤40% 在 10 日内快速升破 61.5% 是经典的底部反转确认信号。")
        if p:
            p["score_label"] = "历史分位"
            p["score_hint"] = "（越高=内部越健康）"
            p["auxiliary"] = _aux_rows(d, ["bt_ema"])
            p["aux_lines"] = [{"name": "宽度推力 EMA10", "field": "bt_ema", "color": "#8b5cf6"}]
            p["mark_lines"] = [
                {"y": 20, "label": "20 宽度冰点", "color": "#2aa876"},
                {"y": 80, "label": "80 普涨过热", "color": "#e8633a"},
            ]
            r = d.iloc[-1]
            hits = d.index[d["bt_signal"] == 1]
            recent = f"，最近一次 {hits[-1].date()}" if len(hits) else ""
            p["aux_text"] = (f"当前宽度综合分 {r['breadth_score']:.2f} / 100；"
                             f"宽度推力 EMA10 {r['bt_ema']:.2f}%（确认线 61.5%，起点 40%）；"
                             f"当日净广度 {r['net_pct']:+.2f}pp（涨 {int(r['n_adv'])} / 跌 {int(r['n_dec'])} 家）。"
                             f"Zweig 推力历史触发 {len(hits)} 次{recent}。")
        return p
    except Exception as e:
        log(f"    [breadth_score] 计算失败: {e}")
        return None


def calc_mcclellan_panel():
    """McClellan Oscillator / Summation（归一化净广度的 EMA19−EMA39）。"""
    try:
        d = _read_frame("breadth_advance")
        if d is None or "mcc_osc" not in d.columns:
            return None
        p = make_panel("mcclellan", "麦克莱伦摆动指标（市场宽广度动能）", d["mcc_osc"].dropna(),
                       "由逐股涨跌家数算净广度占比(涨−跌)/有效股票数×100，再取 EMA19 − EMA39 得摆动值；"
                       "Summation Index 为其累计和。数据源同宽度扩展包，零网络请求。",
                       reverse=False, unit="pp",
                       desc="经典 McClellan 用净涨跌「家数」，但 A 股上市公司数由 2015 年约 2800 增至 2026 年"
                       "约 5500，绝对家数量纲随时代膨胀、阈值无法跨时代比较，故此处改用占比(百分点)。"
                       "摆动值上穿 0 = 短期买盘转强，深度负值后收敛常是下跌动能衰竭的早期迹象；"
                       "Summation 是长期趋势，与指数背离时提示内部参与度不足。")
        if p:
            p["score_label"] = "历史分位"
            p["score_hint"] = "（越高=短期买盘越强）"
            p["auxiliary"] = _aux_rows(d, ["net_pct"])
            p["aux_lines"] = [{"name": "当日净广度", "field": "net_pct", "color": "#5b8ff9"}]
            p["mark_lines"] = [{"y": 0, "label": "0 多空分界", "color": "#2aa876"}]
            r = d.iloc[-1]
            chg = r["mcc_sum"] - d["mcc_sum"].iloc[-61] if len(d) > 60 else float("nan")
            p["aux_text"] = (f"当前摆动值 {r['mcc_osc']:+.2f}pp、Summation {r['mcc_sum']:.1f}"
                             f"（近 60 交易日变化 {chg:+.1f}）；当日净广度 {r['net_pct']:+.2f}pp。"
                             f"摆动值历史区间 {d['mcc_osc'].min():.1f} ~ {d['mcc_osc'].max():.1f}pp。")
        return p
    except Exception as e:
        log(f"    [mcclellan] 计算失败: {e}")
        return None


SIGNAL_BUILDERS = [
    ("below_net", calc_below_net_panel),
    ("nhnl", calc_nhnl_panel),
    ("adv_ratio", calc_adv_panel),
    ("hs300_rsi", calc_tech_panel),
    ("arms", calc_arms_panel),
    ("breadth_score", calc_breadth_score_panel),
    ("mcclellan", calc_mcclellan_panel),
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
    ("erp_attrib", calc_erp_attrib_panel),
    ("fund_position", calc_fund_position_panel),
    ("m1m2", calc_m1m2_panel),
    ("repurchase", calc_repurchase_panel),
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
    """当前值 = 各面板 score 等权平均；历史 = 当日可用面板等权平均后周频采样。

    2026-11-20 复核修复：此前用 common_start = max(各面板起点) 裁历史，
    而「新基金发行」的数据源只回溯到 2023-01，把整条综合分历史截到 2023-02，
    白白丢掉其余 7 个指标二十多年的历史。现改为「当日至少 4 个指标有效即出分」，
    早期为少指标口径，并用 composite.meta 明确标注口径变化点，不伪装成 8 指标全程可比。
    """
    composite_parts = {k: p["score"] for k, p in panels.items()}
    composite = round(float(np.mean(list(composite_parts.values()))), 2) if composite_parts else 50.0
    comp_hist = []
    meta = {"n_metrics": len(panels), "history_start": None, "full_start": None,
            "min_metrics": 4,
            "note": "综合分历史按「当日已有数据的指标等权」计算：早期部分指标尚未发布，"
                    "参与指标少于 8 个，与当前 8 指标等权口径不完全可比；"
                    "全指标齐备后才是严格等权。当前值始终为全部有效指标等权。"}
    try:
        score_series = {}
        for k, p in panels.items():
            s = pd.Series(
                [h["pct"] for h in p["history"]],
                index=pd.to_datetime([h["date"] for h in p["history"]]),
            ).dropna()
            if p["reverse"]:
                s = 100 - s
            score_series[k] = s
        df = pd.DataFrame(score_series).sort_index()
        n_avail = df.notna().sum(axis=1)
        comp = df.ffill().mean(axis=1).where(n_avail >= meta["min_metrics"]).dropna()
        full = n_avail[n_avail >= len(panels)]
        meta["full_start"] = full.index.min().strftime("%Y-%m-%d") if len(full) else None
        meta["history_start"] = comp.index.min().strftime("%Y-%m-%d") if len(comp) else None
        comp_w = comp.resample("W-FRI").last().dropna()
        comp_hist = [
            {"date": dt.strftime("%Y-%m-%d"), "score": round(float(v), 2)}
            for dt, v in comp_w.items()
        ]
        log(f"    综合分历史: {comp.index.min().date()} ~ {comp.index.max().date()}, {len(comp_hist)}周点；"
            f"全{len(panels)}指标齐备自 {meta['full_start']}")
    except Exception as e:
        log(f"    综合分历史计算失败: {e}")
    return composite, comp_hist, meta


# ---------------- 输出 ----------------
def hs300_close_series():
    """供前端「同分位历史前瞻收益」使用的沪深300收盘价交易日序列。

    复用 build_fear_greed.py 已落盘的 mood_hs300.csv（2002 起全历史），零网络请求。
    前端按 date 查找该日及之后第 20/60 个交易日的收盘价，用于给每个分项的
    「当前分位在历史上意味着什么」提供可核查的描述性统计。
    """
    try:
        h = pd.read_csv(DATA / "mood_hs300.csv", parse_dates=["date"]).dropna()
        h = h[h["date"] <= pd.Timestamp(TODAY)].sort_values("date")
        h = h[h["date"].notna()]
        return [[d.strftime("%Y-%m-%d"), round(float(v), 3)]
                for d, v in zip(h["date"], h["hs300"])]
    except Exception as e:
        log(f"    [hs300_close] 读取失败，前瞻统计将不可用: {e}")
        return []


# ---------------- 动态历史结论 ----------------
# 原先 desc / 页面说明里写死了「PCR 低15%分位未来60日 +2.6%」「破净率历次大底
# 24.4%/14.1%/...」「TRIN>2 绝大多数是普涨日」等结论，它们会随数据累积失真
# （2026-11-20 复核时 PCR 实际已变成 +1.12% / -1.17%）。改为每次构建重算，
# 以扁平字典写入 D.facts，前端用 {@key} 占位符替换，零额外网络请求。
def _fwd_bucket(points, lo, hi, dates, vals, gap=20, wins=(20, 60)):
    """按 PIT 分位区间取历史观测日，间隔 gap 交易日去重，需完整前瞻窗口。"""
    import bisect
    n = len(vals)
    acc = {w: [] for w in wins}
    if n < max(wins) + 1:
        return {w: {"n": 0, "avg": None, "win": None} for w in wins}
    last = -10 ** 9
    for q in points:
        v = q.get("pct")
        if v is None or not (lo <= v <= hi):
            continue
        i = bisect.bisect_left(dates, q["date"])
        if i >= n or dates[i] != q["date"] or i + max(wins) >= n or i - last < gap:
            continue
        for w in wins:
            acc[w].append((vals[i + w] / vals[i] - 1.0) * 100.0)
        last = i
    return {w: {"n": len(a),
                "avg": round(float(np.mean(a)), 2) if a else None,
                "win": round(float(np.mean([x > 0 for x in a]) * 100), 1) if a else None}
            for w, a in acc.items()}


def _signed(x, digits=2):
    return "—" if x is None else ("+" if x >= 0 else "") + f"{x:.{digits}f}"


def build_facts(panels, signal_panels):
    """随数据自动重算的历史结论，供前端 {@key} 替换。"""
    facts = {"as_of": TODAY.strftime("%Y-%m-%d")}
    try:
        h = pd.read_csv(DATA / "mood_hs300.csv", parse_dates=["date"]).dropna().sort_values("date")
        dates = [d.strftime("%Y-%m-%d") for d in h["date"]]
        vals = [float(v) for v in h["hs300"]]
    except Exception as e:
        log(f"    [facts] 沪深300序列不可用: {e}")
        dates, vals = [], []
    n = len(vals)

    # 全历史基准：每 20 交易日取样
    base = {w: [] for w in (20, 60)}
    for i in range(0, n - 60, 20):
        for w in base:
            base[w].append((vals[i + w] / vals[i] - 1.0) * 100.0)
    facts["pcr_base20"] = _signed(round(float(np.mean(base[20])), 2) if base[20] else None)
    facts["pcr_base60"] = _signed(round(float(np.mean(base[60])), 2) if base[60] else None)
    facts["pcr_base_n"] = len(base[60])

    # ① PCR 低 / 高 15% 分位的未来收益（原写死 +2.6% / -3.5%）
    p = panels.get("pcr")
    if p and n and p.get("history"):
        hp = p["history"]
        lo = _fwd_bucket(hp, 0, 15, dates, vals)
        hi = _fwd_bucket(hp, 85, 100, dates, vals)
        facts.update({
            "pcr_start": hp[0]["date"],
            "pcr_low_n": lo[60]["n"], "pcr_high_n": hi[60]["n"],
            "pcr_low20": _signed(lo[20]["avg"]), "pcr_low60": _signed(lo[60]["avg"]),
            "pcr_high20": _signed(hi[20]["avg"]), "pcr_high60": _signed(hi[60]["avg"]),
            "pcr_low_win": "—" if lo[60]["win"] is None else f"{lo[60]['win']:.1f}",
            "pcr_high_win": "—" if hi[60]["win"] is None else f"{hi[60]['win']:.1f}",
        })

    # ② 破净率历次大底峰值：按年度最大值自动取前 4，不再人工写年份与数值
    bn = signal_panels.get("below_net")
    if bn and bn.get("history"):
        s = pd.Series({pd.Timestamp(q["date"]): float(q["value"])
                       for q in bn["history"] if q.get("value") is not None})
        if len(s):
            yearly = s.groupby(s.index.year).max()
            top = yearly.sort_values(ascending=False).head(4).sort_index()
            facts["bn_peaks"] = " / ".join(f"{int(y)}年 {v:.1f}%" for y, v in top.items())
            facts["bn_as_of"] = bn["history"][-1]["date"]

    # ③ TRIN>2 的成因分布（普涨日 / 普跌日），直接读 build_arms 的零请求产物
    try:
        a = pd.read_csv(DATA / "mood_arms.csv", parse_dates=["date"])
        g = a[a["trin_vol"] > 2]
        eu = int((g["adv_share"] > 80).sum())
        pa = int((g["adv_share"] < 20).sum())
        nn = len(g)
        share = round(eu / nn * 100, 1) if nn else 0.0
        start = a["date"].min().strftime("%Y-%m-%d")
        facts.update({
            "arms_n": nn, "arms_euphoria": eu, "arms_panic": pa,
            "arms_euphoria_share": share, "arms_start": start,
            "arms_as_of": a["date"].max().strftime("%Y-%m-%d"),
            "arms_sentence": (f"本页 {start} 起全历史 TRIN>2 的 {nn} 个交易日中，"
                              f"普涨日 {eu} 个（{share}%）、普跌日 {pa} 个"),
        })
    except Exception as e:
        log(f"    [facts] 阿姆氏分布不可用: {e}")

    log(f"    [facts] 动态结论: PCR低档60日 {facts.get('pcr_low60','—')}% / "
        f"高档 {facts.get('pcr_high60','—')}%；破净峰值 {facts.get('bn_peaks','—')}；"
        f"TRIN>2 共 {facts.get('arms_n','—')} 日")
    return facts


def write_output(panels, compare_panel, macro_panels, crowding_panel,
                 signal_panels, turning_panel, composite, comp_hist, comp_meta, t0):
    out = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": TODAY.strftime("%Y-%m-%d"),
        "composite": {"score": composite, "state": state_of(composite)[0],
                      "history": comp_hist, "meta": comp_meta},
        "panels": panels,
        "compare_panel": compare_panel,
        "macro_panels": macro_panels,
        "macro_order": [k for k, _ in MACRO_BUILDERS if k in macro_panels],
        "crowding_panel": crowding_panel,
        "signal_panels": signal_panels,
        "signal_order": [k for k, _ in SIGNAL_BUILDERS if k in signal_panels],
        "turning_panel": turning_panel,
        "order": METRIC_ORDER,
        "hs300_close": hs300_close_series(),
        "facts": build_facts(panels, signal_panels),
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
    log(f"结构观察卡片 {len(macro_panels)}/{len(MACRO_BUILDERS)}: {', '.join(macro_panels.keys())}")
    log(f"顶底信号卡片 {len(signal_panels)}/{len(SIGNAL_BUILDERS)}: {', '.join(signal_panels.keys())}")
    if turning_panel:
        c = turning_panel["current"]
        log(f"共振信号板: 底部{c['bottom_score']} 顶部{c['top_score']} 确认{c['confirm']}/3 -> {c['state']}")
    log(f"综合情绪分: {composite} ({state_of(composite)[0]})")
    log(f"输出: {jpath.name} ({jpath.stat().st_size/1024:.0f}KB) / {jspath.name}")
    log(f"附沪深300收盘价 {len(out['hs300_close'])} 个交易日（供前端同分位前瞻统计）")


def main():
    t0 = time.time()
    log("=" * 60)
    log("情绪指标面板构建（公开数据自算版）")
    log("=" * 60)

    src = load_sources()
    # 归因卡片与 rp 警示都读 mood_erp_attrib.csv，必须在指标面板之前落盘；
    # 复用上面已加载的 PE / 国债序列，不额外发请求。
    build_erp_attrib(src)
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

    composite, comp_hist, comp_meta = build_composite(panels)
    write_output(panels, compare_panel, macro_panels, crowding_panel,
                 signal_panels, turning_panel, composite, comp_hist, comp_meta, t0)


if __name__ == "__main__":
    main()
