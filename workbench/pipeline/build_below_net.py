# -*- coding: utf-8 -*-
"""
破净率：A股顶底判断最硬的单指标之一（公开数据自算）
=====================================================
口径：破净个股数 ÷ 全A上市公司数 × 100%（akshare stock_a_below_net_asset_statistics，
      乐咕乐股，2005-01-05 起日频，接口一次返回全历史，无需增量合并）。
      破净率越高 = 资产折价越极端 = 市场越接近历史底部；峰值与 998/1664/2440/2635
      等历次大底高度重合。顶部预测力弱于底部，属"赔率"而非"时点"指标。

数据类别 B：一次调用返回全历史，每次整抓即可吸收新数据。接口失败时沿用旧缓存。

输出：data/mood_below_net.csv (date, below_net_ratio, below_net_asset, total_company)
用法：python build_below_net.py
"""
import akshare as ak
import pandas as pd

from workbench.core import DATA, TODAY, log

OUT = DATA / "mood_below_net.csv"


def main():
    t0 = pd.Timestamp.now()
    try:
        d = ak.stock_a_below_net_asset_statistics()
        d["date"] = pd.to_datetime(d["date"])
        d["below_net_ratio"] = pd.to_numeric(d["below_net_asset_ratio"], errors="coerce") * 100
        d["below_net_asset"] = pd.to_numeric(d["below_net_asset"], errors="coerce")
        d["total_company"] = pd.to_numeric(d["total_company"], errors="coerce")
        out = (d[["date", "below_net_ratio", "below_net_asset", "total_company"]]
               .dropna(subset=["below_net_ratio"])
               .drop_duplicates("date", keep="last")   # 接口偶发重复日期(实测5条)，必须去重
               .sort_values("date"))
        out = out[out["date"] <= TODAY]
        if len(out) < 100:
            raise ValueError(f"有效行数仅 {len(out)}，疑似接口异常")
        if OUT.exists():
            old = pd.read_csv(OUT, parse_dates=["date"])
            if len(old) and out["date"].max() < old["date"].max():
                log("上游数据源回退，拒绝覆盖，沿用旧缓存")
                return
        OUT.parent.mkdir(exist_ok=True)
        out.to_csv(OUT, index=False)
        log(f"破净率：{len(out)} 行，{out['date'].iloc[0].date()} ~ {out['date'].iloc[-1].date()}，"
            f"当前 {out['below_net_ratio'].iloc[-1]:.2f}%，用时 {(pd.Timestamp.now()-t0).total_seconds():.1f}s")
        # 历史大底对照（仅日志，供口径复核）
        for label, s, e in [("2005 998点", "2005-01-01", "2005-12-31"),
                            ("2008 1664点", "2008-09-01", "2008-12-31"),
                            ("2018 2440点", "2018-10-01", "2019-01-31"),
                            ("2024 2635点", "2024-01-01", "2024-03-31")]:
            x = out[(out["date"] >= s) & (out["date"] <= e)]
            if len(x):
                log(f"  {label:12s} 峰值 {x['below_net_ratio'].max():.2f}%")
    except Exception as e:
        log(f"破净率抓取失败：{type(e).__name__}: {e}")
        if not OUT.exists():
            raise
        log("沿用已有缓存")


if __name__ == "__main__":
    main()
