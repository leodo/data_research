# -*- coding: utf-8 -*-
"""公共基础层：路径 / 日期 / 日志 / 缓存 / 分位 / 状态判定。

所有 build_*.py 复用本模块，避免各脚本重复定义导致的口径漂移。
设计原则：只抽取行为完全一致的公共逻辑；两个 state_of 语义不同
（恐贪=五档带颜色，情绪=三档带等级），各自保留在对应脚本，不强行合并。
"""
from __future__ import annotations

import sys
import time
import warnings
from datetime import date
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

# ---------------- 路径与日期 ----------------
BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

TODAY_TAG = date.today().strftime("%Y%m%d")          # YYYYMMDD，供 akshare 接口
TODAY = pd.Timestamp.today().normalize()             # pd.Timestamp，供序列对齐

FORCE_REFRESH = "--force" in sys.argv                # 强制刷新所有源缓存


# ---------------- 日志 ----------------
def log(msg):
    print(msg, flush=True)


# ---------------- 序列工具 ----------------
def sjoin(s: pd.Series, name: str) -> pd.Series:
    """标准化单列序列：去空、转日期索引、去重取名。"""
    s = s.dropna().copy()
    s.index = pd.to_datetime(s.index)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.name = name
    return s


# ---------------- 缓存（带重试 + 过期降级） ----------------
def cache_path(key: str) -> Path:
    return DATA / f"mood_{key}.csv"


def get_or_fetch(key, fetcher, max_age_hours=20, retries=3):
    """读缓存；无缓存则带重试抓取。任何失败返回 None，绝不让单源拖垮全局。"""
    p = cache_path(key)
    if p.exists() and not FORCE_REFRESH:
        age = (time.time() - p.stat().st_mtime) / 3600
        if age < max_age_hours:
            try:
                return pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
            except Exception:
                pass
    for i in range(retries):
        try:
            s = fetcher()
            if s is not None and len(s) > 0:
                s.to_csv(p)
                return s
        except Exception as e:
            log(f"    [{key}] 第{i+1}/{retries}次失败: {type(e).__name__}")
            if i < retries - 1:
                time.sleep(2 * (i + 1))
    # 重试全失败：即使缓存过期也勉强用旧缓存
    if p.exists():
        try:
            log(f"    [{key}] 用过期缓存降级")
            return pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
        except Exception:
            pass
    return None


# ---------------- 分位（expanding 全历史口径，各脚本统一） ----------------
def pct_rank(series: pd.Series) -> pd.Series:
    """全历史 expanding 分位(0-100)，情绪面板口径 min_periods=20。"""
    return series.expanding(min_periods=20).rank(pct=True) * 100


def pct_score(s: pd.Series, direction: bool = True, win: int = 250):
    """恐贪指数口径：全历史分位 + 滚动250日分位，min_periods=60。"""
    s = s.dropna().astype(float)
    all_pct = s.expanding(min_periods=60).rank(pct=True) * 100
    year_pct = s.rolling(win, min_periods=60).rank(pct=True) * 100
    if not direction:
        all_pct = 100 - all_pct
        year_pct = 100 - year_pct
    return all_pct.dropna(), year_pct.dropna()


def window_pct(series: pd.Series, years: int | None) -> float:
    """当前值在指定窗口内的分位(0-100)。"""
    if years is None:
        sub = series.dropna()
    else:
        cutoff = series.index.max() - pd.DateOffset(years=years)
        sub = series[series.index >= cutoff].dropna()
    if len(sub) < 20:
        return 50.0
    return float((sub <= sub.iloc[-1]).sum() / len(sub) * 100)


# ---------------- 交易日历 ----------------
def trade_dates(start: str, end: str | None = None) -> list[str]:
    """官方完整交易日历（含最新交易日），返回 YYYYMMDD 字符串列表。"""
    import akshare as ak
    end = end or TODAY_TAG
    d = ak.calendar_trade_date_hist_sina()
    return [x.strftime("%Y%m%d") for x in pd.to_datetime(d["trade_date"])
            if start <= x.strftime("%Y%m%d") <= end]
