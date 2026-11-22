# -*- coding: utf-8 -*-
"""
全A 市净率历史序列（月频，公开数据自算）
======================================
数据源：akshare.stock_a_all_pb()（乐咕乐股全A PB，月频，一次返回全历史）
输出：data/mood_a_pb.csv
  date              月末日期
  pb_median         全A 市净率中位数
  pb_equal_weight   全A 等权平均市净率

注意：仅月频（约 263 个月点），与日频情绪指标不同频。
      页面作为「估值与宏观比较」栏目的独立卡片，不计入综合情绪分，
      变化值按月频口径由前端特判展示。
"""
import pandas as pd

from workbench.core import DATA, TODAY, log

OUT = DATA / "mood_a_pb.csv"


def fetch():
    import akshare as ak
    d = ak.stock_a_all_pb()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").set_index("date")
    out = pd.DataFrame({
        "pb_median": pd.to_numeric(d["middlePB"], errors="coerce"),
        "pb_equal_weight": pd.to_numeric(d["equalWeightAveragePB"], errors="coerce"),
    })
    out = out[out.index <= TODAY].dropna(subset=["pb_median"])
    return out


def main():
    df = fetch()
    df.to_csv(OUT, date_format="%Y-%m-%d")
    log(f"输出 {OUT.name}：{len(df)} 月，{df.index.min().date()} ~ {df.index.max().date()}")
    log(f"最新 PB 中位数 {df['pb_median'].iloc[-1]:.2f}，"
        f"等权平均 {df['pb_equal_weight'].iloc[-1]:.2f}")


if __name__ == "__main__":
    main()
