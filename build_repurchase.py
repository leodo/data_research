# -*- coding: utf-8 -*-
"""
A股回购规模（东方财富，公开数据自算）
=====================================
数据源：akshare.stock_repurchase_em() —— 一次返回全部回购方案，「B 类」整抓
即最新。实测 5811 条、2005-06 起，按 (股票代码, 回购起始时间) 去重后 5807 条，
说明接口基本是「一个回购方案一行」，用起始日聚合不会重复计数。

  plan_amt  当月新披露回购方案的计划金额（上下限均值，亿元）
  done_amt  这些方案最终已回购金额（亿元）
  *_roll12  12 个月滚动合计（消除月度稀疏，主口径）

回购是产业资本用真金白银表达「自家股票便宜」的行为，规模放量常出现在
市场底部或政策鼓励期（如 2018Q4、2024 年「回购增持再贷款」），属于
底部/转折的辅助证据；但金额未经市值归一化，长期有规模膨胀趋势，
因此请以历史分位与相对变化为主，不宜跨时代比较绝对值。

输出：data/mood_repurchase.csv
  date, plan_amt, done_amt, plan_amt_roll12, done_amt_roll12, n_plans

局限：① 计划金额是预案区间中值，未必全部实施（含「停止实施/否决」样本）；
② 「已回购金额」按方案起始月归档，不是按实际实施月，滚动值反映的是
「该批方案的最终完成额」而非当月实施额；③ 2015 前样本稀疏，作图从
config.REPURCHASE_START 起。
"""
import pandas as pd

from config import REPURCHASE_ROLL, REPURCHASE_START
from core import DATA, TODAY, log

OUT = DATA / "mood_repurchase.csv"


def fetch():
    import akshare as ak
    d = ak.stock_repurchase_em()
    d = d.drop_duplicates(subset=["股票代码", "回购起始时间"], keep="last").copy()
    d["date"] = pd.to_datetime(d["回购起始时间"], errors="coerce")
    lo = pd.to_numeric(d["计划回购金额区间-下限"], errors="coerce")
    hi = pd.to_numeric(d["计划回购金额区间-上限"], errors="coerce")
    plan = pd.concat([lo, hi], axis=1).mean(axis=1, skipna=True) / 1e8
    done = pd.to_numeric(d["已回购金额"], errors="coerce").fillna(0.0) / 1e8
    d["plan_yi"] = plan
    d["done_yi"] = done
    d = d.dropna(subset=["date", "plan_yi"])
    d = d[d["date"] <= TODAY]

    m = d.set_index("date").resample("MS")
    g = m.agg(plan_amt=("plan_yi", "sum"), done_amt=("done_yi", "sum"),
              n_plans=("plan_yi", "size"))
    # 补齐无预案月份为 0，滚动窗口才不会跳过空月
    full = pd.date_range(g.index.min(), g.index.max(), freq="MS")
    g = g.reindex(full, fill_value=0.0)
    g.index.name = "date"
    g[f"plan_amt_roll{REPURCHASE_ROLL}"] = g["plan_amt"].rolling(REPURCHASE_ROLL, min_periods=REPURCHASE_ROLL).sum()
    g[f"done_amt_roll{REPURCHASE_ROLL}"] = g["done_amt"].rolling(REPURCHASE_ROLL, min_periods=REPURCHASE_ROLL).sum()
    g = g[g.index >= pd.Timestamp(REPURCHASE_START)]
    return g.dropna(subset=[f"plan_amt_roll{REPURCHASE_ROLL}"])


def main():
    try:
        df = fetch()
        if df.empty:
            raise ValueError("接口返回空")
    except Exception as e:
        log(f"[repurchase] 抓取失败({e})，沿用已有缓存")
        if OUT.exists():
            return
        raise
    df.to_csv(OUT, date_format="%Y-%m-%d")
    col = f"plan_amt_roll{REPURCHASE_ROLL}"
    log(f"输出 {OUT.name}：{len(df)} 个月点，{df.index.min().date()} ~ {df.index.max().date()}")
    log(f"当前滚动{REPURCHASE_ROLL}月计划回购 {df[col].iloc[-1]:.1f} 亿 "
        f"（历史 min={df[col].min():.1f} max={df[col].max():.1f}），当月预案 {int(df['n_plans'].iloc[-1])} 家")


if __name__ == "__main__":
    main()
