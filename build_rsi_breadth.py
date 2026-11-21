# -*- coding: utf-8 -*-
"""L4 大小盘 RSI 分化（观察层，不进综合分）

- 大盘：沪深300 RSI14（与 build_turning 同法：Wilder 平滑 n=14）
- 小盘：全市场个股 RSI14 的横截面中位数（等权，天然偏小盘）
- 差值 = 大盘 − 小盘；再给差值的滚动 3 年分位（PIT）

只读 data/breadth_prices/*.csv 与 data/mood_hs300.csv，
只写 data/rsi_breadth.json / .js；不改综合分、共振板、任何现有卡片。
"""
import glob, json, os, time
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
WIN = 750          # 分位窗口（约 3 年）


def log(m):
    print(f"[L4] {m}", flush=True)


def rsi14(close: pd.Series, n=14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def board_of(code: str) -> str:
    c = code.lower()
    if c.startswith("bj"):
        return "北交所"
    num = c[2:]
    if c.startswith("sh"):
        return "科创板" if num.startswith("68") else "沪主板"
    return "创业板" if num.startswith("30") else "深主板"


def main():
    t0 = time.time()
    hs = pd.read_csv(os.path.join(DATA, "mood_hs300.csv"), parse_dates=["date"]).set_index("date")["hs300"]
    hs = hs.astype(float).sort_index()
    cal = hs.index
    pos = {d: i for i, d in enumerate(cal)}
    n = len(cal)
    log(f"交易日历 {cal[0].date()} ~ {cal[-1].date()}（{n} 天）")

    files = sorted(glob.glob(os.path.join(DATA, "breadth_prices", "*.csv")))
    log(f"个股缓存 {len(files)} 个")

    mat = np.full((n, len(files)), np.nan, dtype=np.float32)
    boards = [board_of(os.path.basename(f).split(".")[0]) for f in files]
    bad = 0
    for j, f in enumerate(files):
        try:
            d = pd.read_csv(f, parse_dates=["date"])
        except Exception:
            bad += 1
            continue
        d = d.dropna(subset=["close"]).drop_duplicates("date").sort_values("date")
        if len(d) < 60:
            continue
        idx = d["date"].map(pos)
        ok = idx.notna().values
        if ok.sum() < 60:
            continue
        s = pd.Series(d["close"].values[ok], index=cal[idx[ok].astype(int)])
        s = s[~s.index.duplicated(keep="last")]
        # 除权/送股异常：单日 |涨跌| > 21% 视为价格跳变，污染其后 14 日
        ret = s.pct_change()
        jump = ret.abs() > 0.21
        if jump.any():
            # 跳变当天起 14 日内置空（Wilder 平滑的污染窗口）
            mask = jump.rolling(14, min_periods=1).max().astype(bool)
            s = s.copy()
            s[mask.reindex(s.index).fillna(False)] = np.nan
        r = rsi14(s)
        # Wilder 预热：每只股票自身前 30 个交易日的 RSI 不可用
        # （否则所有 2015 年上市的票在头几天会算出 0/100 的伪极值，把中位数带偏）
        r.iloc[:30] = np.nan
        ii = np.array([pos[x] for x in r.index])
        mat[ii, j] = r.values.astype(np.float32)
        if j % 1000 == 0 and j:
            log(f"  已处理 {j}/{len(files)}")
    log(f"个股矩阵完成，用时 {time.time()-t0:.1f}s，读取失败 {bad} 个")

    cnt = np.sum(~np.isnan(mat), axis=1)
    med = np.nanmedian(mat, axis=1)
    # 覆盖度闸门：样本太少的早期日期不参与（否则少数几只票就能左右中位数）
    med = np.where(cnt >= 500, med, np.nan)
    bset = {}
    for b in ["沪主板", "深主板", "创业板", "科创板", "北交所"]:
        cols = [j for j, x in enumerate(boards) if x == b]
        if len(cols) >= 50:
            bv = np.nanmedian(mat[:, cols], axis=1)
            bcnt = np.sum(~np.isnan(mat[:, cols]), axis=1)
            need = min(50, max(10, len(cols) // 3))
            bset[b] = np.round(np.where(bcnt >= need, bv, np.nan), 1)

    hsr = rsi14(hs).values
    spread = hsr - med
    sp = pd.Series(spread)
    pct = sp.rolling(WIN, min_periods=250).apply(lambda w: float((w <= w[-1]).mean() * 100), raw=True).values

    today = dict(
        date=str(cal[-1].date()),
        hs300=round(float(hsr[-1]), 1), median=round(float(med[-1]), 1),
        spread=round(float(spread[-1]), 1),
        spread_pct=None if not np.isfinite(pct[-1]) else round(float(pct[-1]), 1),
        boards={k: round(float(v[-1]), 1) for k, v in bset.items()},
        n_stocks=int(np.sum(~np.isnan(mat[-1]))),
    )

    hist = []
    for i in range(n):
        if not np.isfinite(hsr[i]) or not np.isfinite(med[i]):
            continue
        hist.append(dict(date=str(cal[i].date()), h=round(float(hsr[i]), 1),
                         m=round(float(med[i]), 1), s=round(float(spread[i]), 1)))
    # 前端体积：2015 起全量约 2600 点，保留全部（与 hs300_close 同量级）
    payload = dict(meta=dict(generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                             pool=len(files), window=WIN,
                             note="个股为未复权收盘价；已剔除单日|涨跌|>21%的除权跳变及其后14日。"
                                  "沪深300为价格指数，同样不含分红——两者都带除权季节性，差值可部分抵消。"),
                   today=today, history=hist)
    json.dump(payload, open(os.path.join(DATA, "rsi_breadth.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(os.path.join(DATA, "rsi_breadth.js"), "w", encoding="utf-8").write(
        "window.RSI_BREADTH=" + json.dumps(payload, ensure_ascii=False) + ";\n")
    log(f"完成 {today['date']}：HS300 {today['hs300']} / 中位数 {today['median']} / "
        f"差 {today['spread']}（{today['spread_pct']} 分位），用时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
