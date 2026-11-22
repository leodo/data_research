# -*- coding: utf-8 -*-
"""
M1-M2 剪刀差（货币供应量，公开数据自算）
========================================
数据源：akshare.macro_china_money_supply_em()（东方财富，一次返回全历史，
「B 类」整抓即最新），月频，实测 2008-01 起 226 期。

  gap = M1 同比增速 − M2 同比增速（百分点）
  M1 为企业活期存款为主，M2 含居民储蓄与定期存款。gap 走阔/由负转正
  说明资金「活化」、进入实体与风险资产，历史上常领先或同步于股市行情；
  gap 深度为负说明资金沉淀、风险偏好低，多对应底部区域。

输出：data/mood_m1m2.csv
  date, m1_yoy, m2_yoy, gap, gap_ma3, m1_amt, m2_amt

局限：月频且央行口径在 2024 年起调整过 M1 统计范围（纳入个人活期存款与
非银支付备付金），前后绝对值不完全可比，故本卡片以历史分位与方向为主，
不要用跨时代的绝对 gap 值直接比较高低。
"""
import pandas as pd

from workbench.config import M1M2_START
from workbench.core import DATA, TODAY, log

OUT = DATA / "mood_m1m2.csv"


def fetch():
    import akshare as ak
    d = ak.macro_china_money_supply_em()
    month = d["月份"].astype(str).str.extract(r"(\d{4})年(\d{1,2})月")
    d["date"] = pd.to_datetime(month[0] + "-" + month[1].str.zfill(2) + "-01", errors="coerce")
    d = d.dropna(subset=["date"]).sort_values("date").set_index("date")
    m1 = pd.to_numeric(d["货币(M1)-同比增长"], errors="coerce")
    m2 = pd.to_numeric(d["货币和准货币(M2)-同比增长"], errors="coerce")
    gap = m1 - m2
    out = pd.DataFrame({
        "m1_yoy": m1, "m2_yoy": m2, "gap": gap,
        "gap_ma3": gap.rolling(3, min_periods=3).mean(),
        "m1_amt": pd.to_numeric(d["货币(M1)-数量(亿元)"], errors="coerce"),
        "m2_amt": pd.to_numeric(d["货币和准货币(M2)-数量(亿元)"], errors="coerce"),
    })
    out = out[out.index >= pd.Timestamp(M1M2_START)]
    return out[out.index <= TODAY].dropna(subset=["gap"])


def main():
    try:
        df = fetch()
        if df.empty:
            raise ValueError("接口返回空")
    except Exception as e:
        log(f"[m1m2] 抓取失败({e})，沿用已有缓存")
        if OUT.exists():
            return
        raise
    df.to_csv(OUT, date_format="%Y-%m-%d")
    log(f"输出 {OUT.name}：{len(df)} 个月点，{df.index.min().date()} ~ {df.index.max().date()}")
    r = df.iloc[-1]
    log(f"当前 M1 {r['m1_yoy']:.2f}%  M2 {r['m2_yoy']:.2f}%  剪刀差 {r['gap']:+.2f}pp "
        f"（3月均值 {r['gap_ma3']:+.2f}pp，历史 min={df['gap'].min():.2f} max={df['gap'].max():.2f}）")


if __name__ == "__main__":
    main()
