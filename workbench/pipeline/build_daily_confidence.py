# -*- coding: utf-8 -*-
"""L3 每日可信度评分（元评分：今天这套读数「值不值得信」，不是涨跌预测）。

三层：
  D 数据层（这份读数本身干不干净）       权重 0.40
  E 证据层（这套指标在历史上有没有证据）  权重 0.60
  S 尺度匹配（今天的解读是否用在正确周期）作为乘子 0.7~1.0

只读上游，只写 data/confidence_data.json / .js；不改 mood_data.json / 综合分 / 共振板。
"""
import json, os, time
import numpy as np
import pandas as pd

from workbench.paths import ROOT, DATA as _DATA_ROOT
BASE = str(ROOT)
DATA = str(_DATA_ROOT)
FACTORS = ["rp", "dgz", "pgjj_zsy", "qschsl", "zzpgnh", "magic", "etf300_ratio",
           "margin_growth", "breadth_ma200", "erp_attrib", "below_net", "nhnl",
           "adv_ratio", "hs300_rsi", "arms", "breadth_score", "mcclellan"]
NAME = {"rp": "A股风险溢价", "dgz": "低估指数行业", "pgjj_zsy": "正收益偏股基金占比",
        "qschsl": "全市场换手率", "zzpgnh": "股基滚动年化收益", "magic": "神奇指标",
        "etf300_ratio": "头部300ETF占流通市值", "margin_growth": "两融余额60日增速",
        "breadth_ma200": "全A站上200日均线占比", "erp_attrib": "ERP股债归因",
        "below_net": "破净率", "nhnl": "新高新低差NH-NL", "adv_ratio": "上涨家数占比与动能广度",
        "hs300_rsi": "沪深300 RSI14", "arms": "阿姆氏指标TRIN",
        "breadth_score": "市场宽广度综合", "mcclellan": "麦克莱伦摆动指标"}

VERDICT_W = {"可用尺度": 85, "待验证": 40, "效应可见·统计不可证": 20, "效应过小": 10, "无信号": 0}


def log(m):
    print(f"[L3] {m}", flush=True)


def onset_score(h):
    if not h:
        return 0
    if h <= 20:
        return 100
    if h <= 60:
        return 80
    if h <= 120:
        return 50
    if h <= 250:
        return 30
    return 10


def main():
    t0 = time.time()
    f = pd.read_csv(os.path.join(DATA, "factor_matrix.csv"), index_col=0, parse_dates=True)
    hp = json.load(open(os.path.join(DATA, "horizon_profile.json"), encoding="utf-8"))["factors"]
    mood = json.load(open(os.path.join(DATA, "mood_data.json"), encoding="utf-8"))
    proxy = mood.get("proxy_flags", {}) or {}

    n = len(f)
    raw = np.column_stack([f[k + "__raw"].astype(float).values for k in FACTORS])
    pct = np.column_stack([f[k + "__pct_exp"].astype(float).values for k in FACTORS])
    p250 = np.column_stack([f[k + "__pct_r250"].astype(float).values for k in FACTORS])
    avail = ~np.isnan(raw)

    # 更新 lag（交易日）
    chg = np.zeros_like(raw, dtype=bool)
    chg[1:] = np.abs(np.diff(raw, axis=0)) > 1e-12
    chg &= avail
    last = np.where(chg, np.arange(n)[:, None], np.nan)
    last = pd.DataFrame(last).ffill().values
    lag = np.arange(n)[:, None] - last
    lag = np.where(np.isnan(lag), 60, lag)

    def fresh(x):
        return np.interp(x, [1, 3, 7, 20, 60], [100, 85, 60, 30, 10],
                         left=100, right=10)

    # D3 PIT 成熟度：按 proxy_flags 静态映射
    def pit_of(k):
        v = str(proxy.get(k, ""))
        if "精确" in v:
            return 100.0
        if "代理" in v:
            return 30.0
        if "近似" in v:
            return 60.0
        return 50.0
    pit = np.array([pit_of(k) for k in FACTORS])

    # 有效样本数（截至当日）
    nobs = np.cumsum(avail, axis=0)

    D1 = avail.mean(axis=1) * 100
    D2 = np.array([fresh(lag[t][avail[t]]).mean() if avail[t].any() else 0.0 for t in range(n)])
    D3 = np.array([pit[avail[t]].mean() if avail[t].any() else 0.0 for t in range(n)])
    dpct = np.abs(pct - p250)
    D4 = np.array([100 - np.nanmean(dpct[t][avail[t]]) * 2 if avail[t].any() else 0.0 for t in range(n)])
    D4 = np.clip(D4, 0, 100)
    D5 = (nobs >= 500).mean(axis=1) * 100
    D = 0.30 * D1 + 0.25 * D2 + 0.20 * D3 + 0.15 * D4 + 0.10 * D5

    ext = np.abs(pct - 50) >= 40
    k = ext.sum(axis=1)
    vw = np.array([VERDICT_W.get(hp[k_]["verdict"], 10) for k_ in FACTORS], float)
    # E1 状态证据：当日可用因子的周期检验结论加权（严格口径）
    E1 = np.array([vw[avail[t]].mean() if avail[t].any() else 0.0 for t in range(n)])
    # E2 极值证据：当日处于极值的因子里，|IC| 达到经济门槛(0.15)的占比
    peak = np.array([abs(hp[k_]["peak_ic"]) for k_ in FACTORS], float)
    E2 = np.array([
        30.0 if not ext[t].any() else float((peak[ext[t] & avail[t]] >= 0.15).mean() * 100)
        for t in range(n)])
    # E3 样本成熟度
    E3 = np.minimum(1.0, nobs.mean(axis=1) / 1250.0) * 100
    # E4 跨期稳定：|单调性| 加权
    mono = np.array([abs(hp[k_]["monotonicity"]) for k_ in FACTORS], float)
    E4 = np.array([mono[avail[t]].mean() * 100 if avail[t].any() else 0.0 for t in range(n)])
    # E5 集中度折价：横截面分位标准差越偏离 25 越差
    sd = np.nanstd(np.where(avail, pct, np.nan), axis=1)
    E5 = np.clip(100 - np.abs(np.nan_to_num(sd, nan=25) - 25) * 2, 0, 100)
    E = 0.35 * E1 + 0.25 * E2 + 0.15 * E3 + 0.15 * E4 + 0.10 * E5

    on = np.array([onset_score(hp[k_]["onset_h"]) for k_ in FACTORS], float)
    S = np.array([on[avail[t]].mean() if avail[t].any() else 0.0 for t in range(n)])

    base = 0.4 * D + 0.6 * E
    total = base * (0.7 + 0.003 * S)

    # 数据时效闸门：读数再干净，如果整条管线没跑，也不能给高分（只作用于当日，不改历史）
    data_date = f.index[-1]
    stale_days = int((pd.Timestamp.now().normalize() - data_date.normalize()).days)
    penalty = 1.0 if stale_days <= 3 else max(0.45, 1 - 0.05 * (stale_days - 3))
    total_today = float(total[-1]) * penalty
    stale_f = [NAME[FACTORS[i]] for i in range(len(FACTORS)) if lag[-1, i] > 20]
    health = dict(data_date=str(data_date.date()), stale_days=stale_days,
                  ok=bool(stale_days <= 3), penalty=round(float(penalty), 2),
                  stale_factors=stale_f)

    t = n - 1
    hi_idx = np.where(ext[t])[0]
    lo_idx = np.where((pct[t] <= 10) & avail[t])[0]
    hix = np.where((pct[t] >= 90) & avail[t])[0]

    def lvl(v):
        return "绿·可参考" if v >= 70 else ("黄·仅供参考" if v >= 50 else "红·不建议据此操作")

    today = dict(
        date=str(f.index[t].date()),
        total=round(total_today, 1), base=round(float(base[t]) * float(penalty), 1),
        level=lvl(total_today),
        health=health,
        D=dict(score=round(float(D[t]), 1),
               items=[["覆盖率", round(float(D1[t]), 1), "17 个因子当日有值的比例"],
                      ["新鲜度", round(float(D2[t]), 1), "距上次数值变化的交易日数"],
                      ["PIT成熟度", round(float(D3[t]), 1), "精确/近似/代理口径加权"],
                      ["分布漂移", round(float(D4[t]), 1), "全历史分位 vs 近250日分位"],
                      ["口径一致性", round(float(D5[t]), 1), "历史样本≥500 的因子占比"]]),
        E=dict(score=round(float(E[t]), 1),
               items=[["状态证据", round(float(E1[t]), 1), "当日可用因子的周期检验结论加权"],
                      ["极值证据", round(float(E2[t]), 1), f"当日 {int(k[t])} 个极值因子中 |IC|≥0.15 的占比"],
                      ["样本成熟度", round(float(E3[t]), 1), "历史长度 / 5 年"],
                      ["跨期稳定", round(float(E4[t]), 1), "各因子周期剖面单调性均值"],
                      ["集中度折价", round(float(E5[t]), 1), f"横截面分位标准差 {sd[t]:.0f}（25 最理想）"]]),
        S=dict(score=round(float(S[t]), 1),
               items=[["尺度匹配", round(float(S[t]), 1), "当日可用因子的起效点加权：日频阅读 × 年频指标"]],
               note="本页是日频阅读节奏，而多数指标起效点在 180 日以上——这是结构性错配，不是缺陷。"),
        extreme=dict(
            n=int(len(hi_idx)),
            low=[[NAME[FACTORS[i]], round(float(pct[t, i]), 0)] for i in lo_idx],
            high=[[NAME[FACTORS[i]], round(float(pct[t, i]), 0)] for i in hix]),
        text="",
    )
    d_, e_, s_, e1_ = today["D"]["score"], today["E"]["score"], today["S"]["score"], float(E1[t])
    if e1_ < 35:
        today["text"] = ("数据很全，但证据很薄：这批指标在当前状态下没有通过检验的历史支撑。"
                         "读数可以看，结论不能据此下。")
    elif e_ >= 55 and e1_ >= 50:
        today["text"] = "数据与证据都不弱，读数可作为位置判断的参考（仍非择时信号）。"
    else:
        today["text"] = "数据与证据都偏弱，今天的读数只适合记录，不适合据此调整仓位。"
    if s_ < 35:
        today["text"] += " 另外：这些指标是年频尺度的，用日频节奏去读会持续放大噪声。"

    hist = [dict(date=str(f.index[i].date()), d=round(float(D[i]), 1), e=round(float(E[i]), 1),
                 s=round(float(S[i]), 1), t=round(float(total[i]), 1))
            for i in range(max(0, n - 750), n, 5)]

    payload = dict(meta=dict(generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                             formula="(0.4D+0.6E)×(0.7+0.003S)",
                             weights=dict(D=0.4, E=0.6, S_mult="0.7~1.0"),
                             note="元评分：衡量今天这套读数可不可信，不预测涨跌"),
                   today=today, history=hist)
    json.dump(payload, open(os.path.join(DATA, "confidence_data.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(os.path.join(DATA, "confidence_data.js"), "w", encoding="utf-8").write(
        "window.CONFIDENCE=" + json.dumps(payload, ensure_ascii=False) + ";\n")
    log(f"完成 {today['date']}：D={d_} E={e_} S={s_} 总分={today['total']} ({today['level']}) "
        f"用时{time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
