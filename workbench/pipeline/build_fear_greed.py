# -*- coding: utf-8 -*-
"""
恐贪指数构建引擎（多指数版）
============================
7 个指数各自独立计算恐贪指数：
  allmarket  全市场      : 行情=上证综指(代理全市场) ; 隐波=50/300/500ETF QVIX 等权合成
  hs300      沪深300     : 行情=sh000300             ; 隐波=300ETF QVIX
  zz500      中证500     : 行情=sh000905             ; 隐波=500ETF QVIX(专属)+IC升贴水
  zz1000     中证1000    : 行情=sh000852             ; 隐波=专属1000股指QVIX(至2026-03)+500ETF QVIX校准衔接
  zz2000     中证2000    : 行情=512220中证2000ETF(代理); 隐波=100ETF QVIX(代理)
  cyb        创业板指    : 行情=sz399006             ; 隐波=创业板QVIX
  kcb        科创50      : 行情=sh000688             ; 隐波=科创板QVIX

每个指数通用因子：
  volatility 隐含波动率(QVIX)         : 权重30%，反向(高隐波=恐惧)
  bias       150日乖离率(价格/MA150-1): 权重30%，正向(深跌=恐惧)
  bx         股债回报差(指数20日收益-国债20日收益): 正向
  premium    股指期货年化升贴水10日均 : 仅沪深300(IF)/中证1000(IM)有，正向
  wz         两市成交量偏离20日均线    : 正向，市场共用
  strength   成分股创250日新高占比     : 正向

权重：隐波45% + 150日乖离45%（核心恐慌指标合计90%），其余因子平分剩余10%。
     取90%是因为实测要让"隐波极值+价格新低"行情正确反映为历史极低，核心权重须>85%。
沪深300 同时保留官方 API 快照(hs300_official)作为对照，页面默认展示复刻版并标注。

打分：因子原始值在全历史 expanding 窗口内的分位(0-100)，方向统一"高分=贪婪"。
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import akshare as ak

from workbench.config import (START_FEAR_GREED as START, FG_WINDOW as WIN,
                    STRENGTH_MAX_LAG_DAYS, QVIX_1000_CUT)
from workbench.core import BASE, DATA, TODAY_TAG, pct_score

END = TODAY_TAG  # 动态：抓取当日为止的最新可得数据

STATES = [
    (0, 10, "极度恐惧", "#00b070"),
    (10, 30, "恐惧", "#5fb87a"),
    (30, 70, "中立", "#f4c430"),
    (70, 90, "贪婪", "#f97316"),
    (90, 100.0001, "极度贪婪", "#ef4444"),
]


def state_of(v: float):
    for lo, hi, name, color in STATES:
        if lo <= v < hi:
            return name, color
    return "中立", "#f4c430"


# ---------------------------------------------------------------- 指数配置
INDEX_CFG = {
    "allmarket": {
        "name": "全市场", "code": "上证综指(代理)", "spot": "sh000001", "spot_type": "index",
        "qvix": "market", "fut": None, "cons": None,
        "note": "全市场无单一隐波，隐波由50/300/500ETF期权波指等权合成；行情以上证综指代理全市场。",
    },
    "hs300": {
        "name": "沪深300", "code": "000300", "spot": "sh000300", "spot_type": "index",
        "qvix": "300etf", "fut": "IF0", "cons": "000300",
        "note": "隐波取300ETF期权波指；升贴水取IF股指期货。",
    },
    "zz500": {
        "name": "中证500", "code": "000905", "spot": "sh000905", "spot_type": "index",
        "qvix": "500etf", "fut": "IC0", "cons": "000905",
        "note": "隐波取500ETF期权波指（与中证500成分高度重合，为专属代理）；升贴水取IC股指期货。",
    },
    "zz1000": {
        "name": "中证1000", "code": "000852", "spot": "sh000852", "spot_type": "index",
        "qvix": "1000hybrid", "fut": "IM0", "cons": "000852",
        "note": "隐波优先用中证1000股指期权波指（专属，数据至2026-03），停更后以500ETF期权波指按重叠期比例校准衔接；A股无中证1000ETF期权，故无ETF波指。升贴水取IM股指期货。",
    },
    "zz2000": {
        "name": "中证2000", "code": "932000", "spot": "sh512220", "spot_type": "etf",
        "qvix": "100etf", "fut": None, "cons": "932000",
        "note": "中证2000股指行情接口不可用，价格以512220中证2000ETF复权价代理；无专属隐波，以100ETF期权波指代理；无对应股指期货。",
    },
    "cyb": {
        "name": "创业板指", "code": "399006", "spot": "sz399006", "spot_type": "index",
        "qvix": "cyb", "fut": None, "cons": "399006",
        "note": "隐波取创业板ETF期权波指(已做异常值清洗)；创业板无对应股指期货。",
    },
    "kcb": {
        "name": "科创50", "code": "000688", "spot": "sh000688", "spot_type": "index",
        "qvix": "kcb", "fut": None, "cons": "000688",
        "note": "隐波取科创50ETF期权波指(已做异常值清洗)；科创50无对应股指期货。",
    },
}

# 因子方向：True=原始值越高越贪婪；False=越高越恐惧
FACTOR_DIRECTION = {
    "volatility": False,  # 隐波越高=恐惧
    "bias": True,         # 乖离率越高=贪婪(深度负乖离=恐惧)
    "bx": True,
    "premium": True,
    "wz": True,
    "strength": True,
}
FACTOR_NAME = {
    "volatility": "隐含波动率：期权波指(QVIX)",
    "bias": "中期趋势乖离：价格相对150日均线",
    "bx": "避险天堂：股债回报差",
    "premium": "升贴水率：股指期货升贴水",
    "wz": "两市成交额",
    "strength": "股价强度：创新高个股数量占比",
}
FACTOR_UNIT = {k: "%" for k in FACTOR_NAME}


# ---------------------------------------------------------------- 数据获取
def fetch_index(code: str) -> pd.DataFrame:
    df = ak.index_zh_daily_sina(symbol=code)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df.set_index("date").sort_index()


def fetch_etf(code: str) -> pd.DataFrame:
    df = ak.fund_etf_hist_sina(symbol=code)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    # 使用前复权收盘价
    adj_col = "adj_close" if "adj_close" in df.columns else ("close" if "close" in df.columns else None)
    out = pd.DataFrame(index=df["date"])
    out["close"] = df[adj_col].astype(float).values
    return out.sort_index()


def fetch_futures(symbol: str) -> pd.Series:
    df = ak.futures_main_sina(symbol=symbol, start_date=START, end_date=END)
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    s = df.set_index("日期")["收盘价"]
    s.name = "fut"
    return s


def fetch_bond() -> pd.Series:
    df = ak.bond_treasury_index_cbond()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    s = df.set_index("date")["value"]
    s.name = "bond"
    return s


def fetch_turnover() -> pd.Series:
    sh = fetch_index("sh000001")["volume"]
    sz = fetch_index("sz399001")["volume"]
    s = (sh + sz).dropna()
    s.name = "turnover"
    return s


def clean_qvix(s: pd.Series) -> pd.Series:
    """3日中值滤波：去除期权流动性差时出现的孤立异常值(如创业板18.2夹在36/56之间)"""
    med = s.rolling(3, center=True, min_periods=1).median()
    return s.where((s / med - 1).abs() < 0.35, med)


# ---------------- QVIX 抓取：重试 + 多级降级 ----------------
# 2026-11-22 实测 optbbs 全站 502（1/2/0.optbbs.com 与 www 均不可用），
# akshare 的 4 个 QVIX 接口共用同一份 k.csv，单点故障会让整条恐贪链中断。
# 因此按「重试 → 本地缓存 → mood_qvix.csv → 上一版 index_data.json 的隐波序列」
# 逐级降级，并在卡片 source_note 与 JSON 里写明降级来源，不静默造数。
QVIX_KIND_TO_INDEX = {
    "market": "allmarket", "300etf": "hs300", "500etf": "zz500",
    "1000hybrid": "zz1000", "100etf": "zz2000", "cyb": "cyb", "kcb": "kcb",
}
QVIX_FALLBACK = {}   # kind -> (来源说明, 数据截止日)


def _qvix_cache_path(kind: str) -> str:
    return os.path.join(DATA, f"fear_greed_qvix_{kind}.csv")


def _write_qvix_cache(kind: str, s: pd.Series):
    try:
        s.to_csv(_qvix_cache_path(kind))
    except Exception:
        pass


def _read_qvix_cache(kind: str):
    p = _qvix_cache_path(kind)
    if not os.path.exists(p):
        return None
    try:
        s = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0].dropna()
        s.index = pd.to_datetime(s.index)
        s.name = "qvix"
        return s.sort_index()
    except Exception:
        return None


def _qvix_from_mood(kind: str):
    """mood_qvix.csv 由 build_confirm.py --qvix-full 生成，含 50/300/500ETF 三个序列。"""
    p = os.path.join(DATA, "mood_qvix.csv")
    if not os.path.exists(p):
        return None
    try:
        d = pd.read_csv(p, index_col=0, parse_dates=True)
    except Exception:
        return None
    if kind == "market":
        cols = [c for c in ("qvix_50etf", "qvix_300etf", "qvix_500etf") if c in d.columns]
        if len(cols) != 3:
            return None
        sub = d[cols].dropna()
        if sub.empty:
            return None
        s = sub.mean(axis=1)
    else:
        col = {"300etf": "qvix_300etf", "500etf": "qvix_500etf"}.get(kind)
        if not col or col not in d.columns:
            return None
        s = d[col].dropna()
    if s.empty:
        return None
    s.index = pd.to_datetime(s.index)
    s.name = "qvix"
    return s.sort_index()


def _qvix_from_prev_index(kind: str):
    """上一版 index_data.json 里每个指数的 volatility 因子保存了逐日隐波序列。"""
    key = QVIX_KIND_TO_INDEX.get(kind)
    p = os.path.join(DATA, "index_data.json")
    if not key or not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        for fac in (d.get("indices", {}).get(key, {}) or {}).get("factors", []) or []:
            if fac.get("key") != "volatility":
                continue
            rows = fac.get("series") or []
            s = pd.Series({r["date"]: r["value"] for r in rows if r.get("value") is not None})
            if s.empty:
                return None
            s.index = pd.to_datetime(s.index)
            s = s.sort_index()
            s.name = "qvix"
            return s
    except Exception:
        return None
    return None


def _qvix_live(n: str) -> pd.Series:
    """取单一 QVIX 序列并做中值滤波清洗；3 次重试应对源站偶发 502。"""
    last = None
    for attempt in range(3):
        try:
            d = getattr(ak, "index_option_" + n + "_qvix")()
            d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
            d = d[d["close"].fillna(0) > 0].drop_duplicates("date", keep="last")
            return clean_qvix(d.set_index("date")["close"].sort_index())
        except Exception as e:  # noqa: BLE001 - 源站异常种类不固定
            last = e
            if attempt < 2:
                time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}")


def fetch_qvix_1000hybrid() -> pd.Series:
    """中证1000混合隐波。

    专属中证1000股指期权波指(index_option_1000index_qvix) 2022-07~2026-03 有真实数据，
    但数据源 2026-03-13 后停更（之后全是重复死值），且 2025-12 起稀疏失真。
    因此：专属真实段用至 2025-12-31；此后用 500ETF QVIX 按重叠期回归系数校准衔接，
    使量纲与专属序列一致，同时保留 2026 年隐波飙升的信息。
    """
    own = _qvix_live("1000index")   # 专属中证1000股指期权波指
    proxy = _qvix_live("500etf")    # 500ETF 期权波指代理
    # 用 2022-07-22 起的重叠期拟合 own = a * proxy + b
    idx = own.index.intersection(proxy.index)
    idx = idx[idx >= "2022-07-22"]
    a, b = np.polyfit(proxy.loc[idx].values.astype(float), own.loc[idx].values.astype(float), 1)
    print(f"  1000hybrid 校准: own={a:.4f}*500etf+{b:.3f} (n={len(idx)}, R²={np.corrcoef(proxy.loc[idx], own.loc[idx])[0,1]**2:.3f})", flush=True)
    CUT = QVIX_1000_CUT
    own_part = own[own.index <= CUT]
    proxy_part = (proxy * a + b)
    proxy_part = proxy_part[proxy_part.index > CUT]
    s = pd.concat([own_part, proxy_part]).sort_index()
    s.name = "qvix"
    return s


def _fetch_qvix_live(kind: str) -> pd.Series:
    """按配置实时获取隐波。kind=market 用50/300/500ETF等权合成；1000hybrid 为专属+校准拼接。"""
    if kind == "1000hybrid":
        return fetch_qvix_1000hybrid()
    if kind == "market":
        q50, q300, q500 = _qvix_live("50etf"), _qvix_live("300etf"), _qvix_live("500etf")
        idx = q300.index.intersection(q500.index).intersection(q50.index)
        s = (q50.loc[idx] + q300.loc[idx] + q500.loc[idx]) / 3
        s.name = "qvix"
        return s
    s = _qvix_live(kind)
    s.name = "qvix"
    return s


def _norm_qvix(s: pd.Series) -> pd.Series:
    """统一成 YYYY-MM-DD 字符串索引：实时接口与本地 CSV/JSON 降级源索引类型不同，
    不统一会让 df.join 静默落空，最终 dropna 后得到空结果。"""
    s = s.copy()
    s.index = pd.to_datetime(s.index).strftime("%Y-%m-%d")
    return s


def fetch_qvix(kind: str) -> pd.Series:
    """先实时抓取并写缓存；源站不可用时按本地缓存 → mood_qvix.csv → 上一版指数数据逐级降级。"""
    try:
        s = _norm_qvix(_fetch_qvix_live(kind))
        _write_qvix_cache(kind, s)
        return s
    except Exception as e:  # noqa: BLE001
        print(f"  [警告] 隐波源不可用（{kind}）：{type(e).__name__} {str(e)[:80]}", flush=True)
        for label, loader in (("本地缓存", _read_qvix_cache),
                              ("mood_qvix.csv", _qvix_from_mood),
                              ("上一版 index_data.json", _qvix_from_prev_index)):
            s = loader(kind)
            if s is not None and len(s):
                s = _norm_qvix(s)
                QVIX_FALLBACK[kind] = (label, str(s.index.max()))
                _write_qvix_cache(kind, s)   # 把降级序列固化，避免旧数据文件被覆盖后彻底丢失
                print(f"  [降级] 使用{label}，隐波截至 {s.index.max()}", flush=True)
                return s
        raise RuntimeError(f"隐波 {kind} 无可用数据（源不可用且无本地缓存）") from e


def fetch_constituents(index_code: str) -> list[str]:
    if index_code == "399006":  # 创业板指用深证接口
        df = ak.index_stock_cons(symbol=index_code)
        codes = []
        for c in df["品种代码"].astype(str).str.zfill(6).tolist():
            prefix = "sh" if c.startswith(("5", "6", "9")) else "sz"
            codes.append(prefix + c)
        return sorted(set(codes))
    df = ak.index_stock_cons_csindex(symbol=index_code)
    codes = []
    for c in df["成分券代码"].astype(str).tolist():
        c = c.zfill(6)
        prefix = "sh" if c.startswith(("5", "6", "9")) else "sz"
        codes.append(prefix + c)
    return sorted(set(codes))


def fetch_one_stock(args):
    code, start = args
    for attempt in range(3):  # 新浪偶发 ReadTimeout，重试
        try:
            df = ak.stock_zh_a_hist_sina(symbol=code)
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df = df[df["date"] >= start]
            if df.empty:
                return code, None
            hi = df["high"].astype(float)
            roll_max = hi.shift(1).rolling(250, min_periods=120).max()
            newhigh = (hi >= roll_max) & roll_max.notna()
            out = pd.DataFrame({"code": code, "date": df["date"].astype(str), "nh": newhigh.astype(int)})
            return code, out
        except Exception:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
            else:
                return code, None


def _fetch_strength_full(index_code: str, cache_path: str):
    """全量抓取成分股历史行情并计算创新高标记，返回明细表。"""
    codes = fetch_constituents(index_code)
    print(f"  成分股 {len(codes)} 只，开始并发抓取历史行情 ...", flush=True)
    frames, done = [], 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(fetch_one_stock, (c, "2021-01-01")): c for c in codes}
        for fu in as_completed(futs):
            try:
                _, out = fu.result()
                if out is not None:
                    frames.append(out)
            except Exception:
                pass
            done += 1
            if done % 300 == 0:
                print(f"    {done}/{len(codes)} ({time.time()-t0:.0f}s)", flush=True)
    if not frames:
        # 抓取失败时降级：用中证1000缓存(全市场近似)或返回空
        fallback = os.path.join(DATA, "zz1000_newhigh_cache.csv")
        if os.path.exists(fallback) and fallback != cache_path:
            print(f"  {index_code} 成分股抓取失败，降级使用全市场近似缓存", flush=True)
            return pd.read_csv(fallback)
        raise RuntimeError("成分股历史抓取全部失败")
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(cache_path, index=False)
    print(f"  成分股缓存完成：{len(df)} 行，成功 {df['code'].nunique()} 只 ({time.time()-t0:.0f}s)", flush=True)
    return df


def strength_lag_days(cache_last, target_date):
    """缓存末日与主数据末日之间相差多少个交易日。"""
    from workbench.core import trade_dates
    lo, hi = pd.Timestamp(cache_last), pd.Timestamp(target_date)
    try:
        cal = [pd.Timestamp(x) for x in trade_dates("20240101", hi.strftime("%Y%m%d"))]
        return sum(1 for x in cal if lo < x <= hi)
    except Exception:
        return (hi - lo).days


def fetch_strength(index_code: str, cache_path: str, target_date=None) -> pd.Series:
    """成分股创新高占比。

    nh 依赖 250 日滚动窗口，且成分股列表会变动、部分个股近段数据不足，
    实测「只抓近段再拼接」会与全量口径产生偏差，故不做增量，保持全量重抓
    以保证 100% 口径一致。为控制成本（全量约13分钟），仅在缓存落后超过
    STRENGTH_MAX_LAG_DAYS 个交易日时才刷新，并限制同一自然日最多一次。
    该指标为慢变量、权重低，允许最多滞后 3 个交易日。
    """
    stamp_path = cache_path.replace(".csv", "_stamp.txt")
    cache_df = None
    cache_last = None
    if os.path.exists(cache_path):
        try:
            cache_df = pd.read_csv(cache_path)
            if len(cache_df):
                cache_last = pd.to_datetime(cache_df["date"]).max()
        except Exception:
            cache_df = None

    need_refresh = cache_df is None
    if cache_df is not None and target_date is not None and cache_last is not None:
        tgt = pd.Timestamp(target_date)
        if cache_last < tgt:
            lag = strength_lag_days(cache_last, tgt)
            if lag <= STRENGTH_MAX_LAG_DAYS:
                print(f"  成分股缓存末日 {cache_last.date()}，落后{lag}个交易日（≤{STRENGTH_MAX_LAG_DAYS}，容忍）", flush=True)
            else:
                try:
                    with open(stamp_path, encoding="utf-8") as f:
                        stamped = f.read().strip()
                except Exception:
                    stamped = ""
                if stamped == TODAY_TAG:
                    print(f"  成分股缓存落后{lag}个交易日，但今日已全量刷新过，暂沿用", flush=True)
                else:
                    print(f"  成分股缓存落后{lag}个交易日，触发全量刷新", flush=True)
                    need_refresh = True

    if need_refresh:
        try:
            df = _fetch_strength_full(index_code, cache_path)
        except Exception as e:
            if cache_df is not None and len(cache_df):
                print(f"  全量刷新失败({str(e)[:50]})，降级沿用旧缓存", flush=True)
                df = cache_df
            else:
                raise
        try:
            with open(stamp_path, "w", encoding="utf-8") as f:
                f.write(TODAY_TAG)
        except Exception:
            pass
    else:
        df = cache_df

    g = df.groupby("date")["nh"].agg(["sum", "count"])
    g = g[g["count"] >= 30]
    s = g["sum"] / g["count"] * 100
    s.name = "strength"
    return s


# ---------------------------------------------------------------- 打分
# pct_score 已迁至 core（口径一致：min_periods=60，滚动窗口 win=250）


def analysis_text(key: str, v: float, pct: float) -> str:
    window = "极低" if pct < 15 else ("偏低" if pct < 35 else ("偏高" if pct > 70 else "中性"))
    mapping = {
        "volatility": f"期权隐含波动率目前为 {v:.2f}%，情绪分 {pct:.1f}（高分=平静、低分=恐慌）。隐波飙升代表恐慌加剧。",
        "bias": f"价格相对150日均线乖离率为 {v:.2f}%，情绪分 {pct:.1f}（{window}）。深度负乖离代表杀跌惨烈。",
        "bx": f"指数20日收益 − 国债净价20日收益 = {v:.2f}%，情绪分 {pct:.1f}（{window}）。差值越低越避险。",
        "premium": f"股指期货年化升贴水(10日均)为 {v:.2f}%，情绪分 {pct:.1f}（{window}）。深度贴水代表谨慎看空。",
        "wz": f"两市成交量偏离20日均线 {v:.2f}%，情绪分 {pct:.1f}（{window}）。交投萎缩说明情绪低迷。",
        "strength": f"成分股创250日新高占比 {v:.2f}%，情绪分 {pct:.1f}（{window}）。占比越低越弱势。",
    }
    return mapping.get(key, "")


# ---------------------------------------------------------------- 通用构建
def build_index(key: str, cfg: dict, shared: dict) -> dict:
    print(f"\n--- 构建 {cfg['name']} ({key}) ---", flush=True)
    spot = shared["spot"][cfg["spot"]] if cfg["spot"] in shared["spot"] else fetch_etf(cfg["spot"])["close"]
    df = pd.DataFrame({"spot": spot}).sort_index()
    df = df.join(shared["bond"], how="left").join(shared["turnover"], how="left")

    # 隐波
    qv = shared["qvix"][cfg["qvix"]]
    df = df.join(qv, how="left")
    df["qvix"] = df["qvix"].ffill()

    # 升贴水(仅部分指数)
    if cfg["fut"]:
        fut = fetch_futures(cfg["fut"]) if cfg["fut"] not in shared["fut"] else shared["fut"][cfg["fut"]]
        df = df.join(fut, how="left")

    # 股价强度：有成分股配置则抓取，失败则降级为共享的全市场近似；无配置直接用共享近似
    cache = os.path.join(DATA, f"strength_{key}_cache.csv")
    if cfg["cons"]:
        try:
            strength = fetch_strength(cfg["cons"], cache, target_date=df.index.max())
        except Exception as e:
            print(f"  {cfg['name']} 成分股强度抓取失败({str(e)[:60]})，降级用全市场近似", flush=True)
            strength = shared["strength"]
    else:
        strength = shared["strength"]
    if strength is not None:
        df = df.join(strength, how="left")
    else:
        df["strength"] = np.nan

    df = df[df.index >= "2022-07-22"]
    raw = pd.DataFrame(index=df.index)
    raw["volatility"] = df["qvix"]
    raw["bias"] = (df["spot"] / df["spot"].rolling(150).mean() - 1) * 100
    r20s = df["spot"] / df["spot"].shift(20) - 1
    r20b = df["bond"] / df["bond"].shift(20) - 1
    raw["bx"] = (r20s - r20b) * 100
    if cfg["fut"]:
        basis = (df["fut"] / df["spot"] - 1) * (365 / 30) * 100
        raw["premium"] = basis.rolling(10, min_periods=5).mean()
    raw["wz"] = (df["turnover"] / df["turnover"].rolling(20).mean() - 1) * 100
    raw["strength"] = df["strength"]

    # 权重：隐波45% + 150日乖离45% 为核心(合计90%)，其余因子平分剩余10%。
    # 依据：隐波与深跌乖离是恐慌的直接度量；实测要让"隐波极值+价格新低"的行情
    # 正确压过"多因子全面踩踏"的历史低点，核心权重须>85%，故取90%。
    W = {"volatility": 0.45, "bias": 0.45}
    rest = [k for k in ["bx", "premium", "wz", "strength"] if k in raw.columns]
    for k in rest:
        W[k] = round(0.10 / len(rest), 4)

    factors, score_cols = [], []
    for k in list(W.keys()):
        if k not in raw.columns:
            continue
        sc_all, sc_year = pct_score(raw[k], direction=FACTOR_DIRECTION[k])
        score_cols.append(sc_all * W[k])
        series = []
        for dt, val in raw[k].dropna().items():
            if dt in sc_all.index:
                series.append({
                    "date": dt,
                    "value": round(float(val), 4),
                    "pct_year": round(float(sc_year.loc[dt]), 2) if dt in sc_year.index else None,
                    "pct_all": round(float(sc_all.loc[dt]), 2),
                })
        if series:
            last = series[-1]
            factors.append({
                "key": k, "name": FACTOR_NAME[k], "unit": FACTOR_UNIT[k],
                "weight": round(W[k] * 100, 1),
                "analysis": analysis_text(k, last["value"], last["pct_all"]),
                "series": series,
            })

    scores = pd.concat(score_cols, axis=1).dropna()
    final = scores.sum(axis=1)
    print(f"  {cfg['name']}: {final.index.min()} ~ {final.index.max()}, {len(final)} 日, 最新 {final.iloc[-1]:.2f}", flush=True)

    history = [{"date": d, "value": round(float(v), 2)} for d, v in final.items()]
    last = history[-1]
    state, color = state_of(last["value"])
    fb = QVIX_FALLBACK.get(cfg["qvix"])
    fb_note = (f" 本次隐波源不可用，隐波沿用{fb[0]}（截至 {fb[1]}），指数仍按最新价格与其余因子重算。"
               if fb else "")
    return {
        "value": last["value"], "date": last["date"], "desc": state, "color": color,
        "has_data": True, "source": "transparent-reimplementation",
        "source_note": f"{cfg['name']}恐贪指数为透明复刻实现(非官方数值)：以隐波45%+150日乖离45%为核心(合计90%)，其余因子分摊10%，以便敏锐捕捉隐波飙升与价格深跌。{cfg['note']} 数据源 akshare。{fb_note}",
        "history": history, "factors": factors,
    }


def main():
    print("=" * 64)
    print("恐贪指数：7 指数独立计算（全市场/沪深300/中证500/中证1000/中证2000/创业板/科创50）")
    print("=" * 64)

    t0 = time.time()
    print("[1/4] 抓取公共数据（指数行情、隐波、国债、成交额）...", flush=True)
    spot = {}
    for key, cfg in INDEX_CFG.items():
        if cfg["spot_type"] == "index" and cfg["spot"] not in spot:
            spot[cfg["spot"]] = fetch_index(cfg["spot"])["close"]
    qvix, qvix_missing = {}, {}
    for kind in sorted(set(c["qvix"] for c in INDEX_CFG.values())):
        try:
            qvix[kind] = fetch_qvix(kind)
        except Exception as e:  # noqa: BLE001
            qvix_missing[kind] = str(e)[:120]
            print(f"  [跳过] 隐波 {kind} 无可用数据：{e}", flush=True)
    if not qvix:
        # 隐波是核心因子(45%)，一条都没有时绝不写入半成品；保留上一版 index_data.json
        print("[跳过] 所有隐波序列均不可用，保留上一版 index_data.json，本次不重建恐贪指数。", flush=True)
        return
    bond = fetch_bond()
    turnover = fetch_turnover()
    # 全市场近似 strength（用于全市场指数/抓取失败时的降级）
    fb_path = os.path.join(DATA, "zz1000_newhigh_cache.csv")
    strength_fallback = None
    if os.path.exists(fb_path):
        _df = pd.read_csv(fb_path)
        _g = _df.groupby("date")["nh"].agg(["sum", "count"])
        _g = _g[_g["count"] >= 30]
        strength_fallback = (_g["sum"] / _g["count"] * 100)
        strength_fallback.name = "strength"
    shared = {"spot": spot, "qvix": qvix, "bond": bond, "turnover": turnover, "fut": {}, "strength": strength_fallback}
    print(f"  公共数据就绪 ({time.time()-t0:.0f}s)", flush=True)

    print("[2/4] 抓取股指期货(IF/IC/IM)...", flush=True)
    for f in ["IF0", "IC0", "IM0"]:
        shared["fut"][f] = fetch_futures(f)

    print("[3/4] 逐指数计算 ...", flush=True)
    indices = {}
    for key, cfg in INDEX_CFG.items():
        if cfg["qvix"] not in qvix:
            print(f"  [跳过] {cfg['name']}：隐波 {cfg['qvix']} 暂缺", flush=True)
            continue
        try:
            indices[key] = build_index(key, cfg, shared)
        except Exception as e:
            print(f"  [ERROR] {key} 构建失败: {type(e).__name__} {str(e)[:100]}", flush=True)

    if not indices:
        print("[跳过] 没有任何指数构建成功，保留上一版 index_data.json。", flush=True)
        return

    unavailable = [{"key": k, "name": c["name"], "qvix": c["qvix"],
                    "reason": qvix_missing.get(c["qvix"], "构建失败")}
                   for k, c in INDEX_CFG.items() if k not in indices]

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "indices": indices,
        "meta": {k: {"name": c["name"], "code": c["code"], "note": c["note"]} for k, c in INDEX_CFG.items()},
        # 隐波降级记录：页面 source_note 同步写明，便于事后核对哪几天用的是旧隐波
        "qvix_fallback": {k: {"source": v[0], "as_of": v[1]} for k, v in QVIX_FALLBACK.items()},
        # 本次未能重算的指数（隐波源故障 / 构建失败），页面会提示，不静默消失
        "unavailable": unavailable,
    }
    path = os.path.join(DATA, "index_data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(DATA, "index_data.js"), "w", encoding="utf-8") as f:
        f.write("window.FG_DATA=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";")

    print(f"[4/4] 已输出 {path} ({os.path.getsize(path)/1024:.0f} KB), 总耗时 {time.time()-t0:.0f}s", flush=True)
    print("=" * 64)
    for k, v in indices.items():
        print(f"  {INDEX_CFG[k]['name']:8s}: {v['value']:6.2f}  {v['desc']:6s}  ({len(v['history'])} 日)", flush=True)


if __name__ == "__main__":
    main()
