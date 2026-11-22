# -*- coding: utf-8 -*-
"""
两融余额增速与平均维持担保比例（公开数据自算）
==============================================
数据源：akshare.stock_margin_account_info()（沪深两融账户统计，2012-09 起日更，
        接口一次返回全历史，属于"B 类"，整抓即吸收最新数据，无需增量）

输出：data/mood_margin.csv
  date                日期
  margin_bal          融资余额（亿元）
  growth_60d          融资余额相对 60 个交易日前的增速（%）
  maintenance_ratio   平均维持担保比例（%）

口径说明：融资余额增速反映杠杆资金进出速度，快速上行通常对应情绪亢奋、
         快速下行对应去杠杆/风险偏好收缩；维持担保比例是杠杆安全垫，
         越低越接近强平压力区。两者均为结构观察指标，不计入综合情绪分。
"""
import pandas as pd

from workbench.config import MARGIN_GROWTH_WIN
from workbench.core import DATA, TODAY, log

OUT = DATA / "mood_margin.csv"


def fetch():
    import akshare as ak
    d = ak.stock_margin_account_info()
    d["date"] = pd.to_datetime(d["日期"])
    d = d.sort_values("date").set_index("date")

    bal = pd.to_numeric(d["融资余额"], errors="coerce")
    mnt = pd.to_numeric(d["平均维持担保比例"], errors="coerce")
    growth = (bal / bal.shift(MARGIN_GROWTH_WIN) - 1.0) * 100

    out = pd.DataFrame({
        "margin_bal": bal,
        "growth_60d": growth,
        "maintenance_ratio": mnt,
    })
    out = out[out.index <= TODAY].dropna(subset=["margin_bal"])
    return out


def main():
    df = fetch()
    df.to_csv(OUT, date_format="%Y-%m-%d")
    log(f"输出 {OUT.name}：{len(df)} 行，{df.index.min().date()} ~ {df.index.max().date()}")
    log(f"融资余额 {df['margin_bal'].iloc[-1]:.2f} 亿；"
        f"{MARGIN_GROWTH_WIN}日增速 {df['growth_60d'].iloc[-1]:.2f}%；"
        f"平均维持担保比例 {df['maintenance_ratio'].iloc[-1]:.2f}%")


if __name__ == "__main__":
    main()
