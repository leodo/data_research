# -*- coding: utf-8 -*-
"""
两融余额增速、平均维持担保比例与担保比例-指数背离度（公开数据自算）
====================================================================
数据源：akshare.stock_margin_account_info()（沪深两融账户统计，2012-09 起日更，
        接口一次返回全历史，属于"B 类"，整抓即吸收最新数据，无需增量）
        akshare.index_zh_daily_sina('sh000001')（上证综指日线，仅用于背离度对照）

输出：data/mood_margin.csv
  date                日期
  margin_bal          融资余额（亿元）
  growth_60d          融资余额相对 60 个交易日前的增速（%）
  maintenance_ratio   平均维持担保比例（%）
  sh_close            上证综指收盘点位
  mid_750             担保比例 750 交易日滚动中枢（%）
  safety_gap          安全垫缺口（百分点）= mid_750 − maintenance_ratio
  mnt_chg20           维持担保比例 20 交易日变化（百分点）
  idx_chg20           上证综指 20 交易日涨跌（%）
  divergence          [保留供对照，已不作面板主序列] 背离度（百分点）
                      = mnt_chg20 − 滚动回归预测值，预测来自
                      mnt_chg20 ~ a + b*idx_chg20，750 交易日滚动窗口
                      （min_periods=250），仅用当日及以前数据，无前视偏差

口径说明：融资余额增速反映杠杆资金进出速度，快速上行通常对应情绪亢奋、
         快速下行对应去杠杆/风险偏好收缩；维持担保比例是杠杆安全垫，
         越低越接近强平压力区。担保比例本身与指数涨跌高度同向（市值/负债的
         机械联动），主图用双轴叠加观察同步性。

         副图用「安全垫缺口」的水平口径：gap = 近 3 年滚动中枢 − 当前值，
         正值=安全垫偏薄、强平压力上升。之所以不用「20 日变化的回归残差」，
         是因为差分把最有信息量的绝对水平差分掉了：实测该残差在 8 个历史
         大底的平均分位 85、在 5 个顶部平均 98（方向反了），而水平缺口在
         底部 94、顶部 49，区分度 +45。滚动中枢而非全历史均值，是为了避免
         担保比例中枢长期上移造成的口径漂移。三者均为结构观察指标，不计入综合情绪分。
"""
import pandas as pd

from workbench.config import MARGIN_GROWTH_WIN
from workbench.core import DATA, TODAY, log

OUT = DATA / "mood_margin.csv"


CHG_WIN = 20      # 变化窗口（交易日）
REG_WIN = 750     # 滚动回归窗口（约 3 年）
REG_MIN = 250     # 回归最小样本（约 1 年）
GAP_WIN = 750     # 安全垫缺口的滚动中枢窗口（约 3 年）
GAP_MIN = 250     # 中枢最小样本（约 1 年）


def fetch_index_close() -> "pd.Series":
    """上证综指收盘（akshare index_zh_daily_sina），失败返回空 Series 不拖垮两融主链。"""
    import akshare as ak
    try:
        d = ak.index_zh_daily_sina(symbol="sh000001")
        s = pd.to_numeric(d.set_index("date")["close"], errors="coerce")
        s.index = pd.to_datetime(s.index)
        return s[~s.index.duplicated(keep="last")].sort_index()
    except Exception as e:
        log(f"  [sh000001] 抓取失败，背离度将跳过: {type(e).__name__}: {e}")
        return pd.Series(dtype=float)


def calc_divergence(mnt: pd.Series, sh: pd.Series) -> pd.DataFrame:
    """担保比例20日变化 与 指数20日涨跌 的滚动回归残差，只产出有完整历史的日期。"""
    joined = pd.concat(
        [mnt.rename("mnt"), sh.rename("idx")], axis=1, sort=True
    ).sort_index()
    joined["idx"] = joined["idx"].ffill()   # 指数日更、两融 T+1，末日对齐用前值
    dm = joined["mnt"].diff(CHG_WIN)
    di = joined["idx"].pct_change(CHG_WIN) * 100
    cov = di.rolling(REG_WIN, min_periods=REG_MIN).cov(dm)
    var = di.rolling(REG_WIN, min_periods=REG_MIN).var()
    beta = cov / var
    alpha = dm.rolling(REG_WIN, min_periods=REG_MIN).mean() - beta * di.rolling(
        REG_WIN, min_periods=REG_MIN).mean()
    div = dm - (alpha + beta * di)
    return pd.DataFrame({
        "sh_close": joined["idx"],
        "mnt_chg20": dm,
        "idx_chg20": di,
        "divergence": div,
    })


def calc_safety_gap(mnt: pd.Series) -> pd.DataFrame:
    """担保比例相对近 3 年滚动中枢的安全垫缺口（水平口径）。

    强平压力取决于安全垫的绝对水平，而非「变化是否超预期」。用滚动中枢而非
    全历史均值，避免担保比例中枢长期上移带来的口径漂移；只用当日及以前数据，
    无前视偏差。gap = 中枢 − 当前值，正值=安全垫偏薄、压力上升。
    """
    mid = mnt.rolling(GAP_WIN, min_periods=GAP_MIN).mean()
    return pd.DataFrame({
        "mid_750": mid,
        "safety_gap": mid - mnt,
    })


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
    extra = calc_divergence(mnt, fetch_index_close())
    out = out.join(extra, how="left")
    out = out.join(calc_safety_gap(mnt), how="left")
    out = out[out.index <= TODAY].dropna(subset=["margin_bal"])
    return out


def main():
    df = fetch()
    df.to_csv(OUT, date_format="%Y-%m-%d")
    log(f"输出 {OUT.name}：{len(df)} 行，{df.index.min().date()} ~ {df.index.max().date()}")
    log(f"融资余额 {df['margin_bal'].iloc[-1]:.2f} 亿；"
        f"{MARGIN_GROWTH_WIN}日增速 {df['growth_60d'].iloc[-1]:.2f}%；"
        f"平均维持担保比例 {df['maintenance_ratio'].iloc[-1]:.2f}%")
    if "safety_gap" in df and df["safety_gap"].notna().any():
        last = df[df["safety_gap"].notna()].iloc[-1]
        log(f"安全垫缺口：末日 {last.name.date()} = {last['safety_gap']:.2f}pp"
            f"（担保比例 {last['maintenance_ratio']:.2f}% vs 近3年中枢 {last['mid_750']:.2f}%）")
    if "divergence" in df and df["divergence"].notna().any():
        last = df[df["divergence"].notna()].iloc[-1]
        log(f"[对照] 变化回归残差：末日 {last.name.date()} = {last['divergence']:.2f}pp"
            f"（同期担保比例20日变化 {last['mnt_chg20']:.2f}pp、上证综指20日 {last['idx_chg20']:.2f}%）")


if __name__ == "__main__":
    main()
