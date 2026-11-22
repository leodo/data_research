# -*- coding: utf-8 -*-
"""可转债大栏目数据构建。

数据源（均为免登录公开接口，2026-11-21 实测可用）：
1. 中证指数官网 000832 中证转债指数日线（2015 起，含成交额/成分数）
2. 东方财富 datacenter RPT_BOND_CB_LIST + quoteColumns：全市场转债快照
   （现价/涨跌/正股价/转股价/转股价值/转股溢价率/评级/规模/强赎与回售触发价）
3. 东方财富 RPTA_WEB_KZZ_LS：逐券最新纯债价值、纯债溢价率（并发 + 本地缓存）
4. 东方财富 RPTA_WEB_KZZ_LS：逐券长历史（2017 起），自算全市场横截面、等权指数、温度代理
5. 集思录 webapi/cb/index_history/：等权指数、中位价格、中位溢价率、平均双低、温度、成交额（近 250 交易日）
6. 集思录 webapi/cb/redeem/：强赎 / 回售状态
7. 同花顺 data.10jqka.com.cn/ipo/kzz/：新债发行日历

输出：data/cb_data.json / data/cb_data.js，缓存 data/cb_cache/。
只写本栏目自己的文件，不改动现有恐贪 / 情绪数据。
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import requests
from requests.adapters import HTTPAdapter

from workbench.paths import ROOT as BASE, DATA
CACHE = DATA / "cb_cache"
CACHE.mkdir(parents=True, exist_ok=True)

FORCE = "--force" in sys.argv
SKIP_VAL = "--skip-val" in sys.argv
TODAY = date.today()

# 长历史：东财逐券历史，含退市券；2017 起才有足够的转债样本
LONG_START = "2017-01-01"
LONG_START_D = date(2017, 1, 1)
LONG_MIN_COUNT = 30
HIST_CACHE = CACHE / "history.csv"
HIST_COLS = ["code", "date", "price", "premium", "pure_bond", "pure_prem"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/json, text/javascript, */*; q=0.01"}

SESSION = requests.Session()
SESSION.trust_env = False          # 本机 127.0.0.1:7897 代理对 push2 等域名不稳定，统一直连
SESSION.mount("https://", HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=0))
SESSION.mount("http://", HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=0))


# ---------------- 基础工具 ----------------
def log(msg: str):
    print(msg, flush=True)


def num(v):
    """宽松转 float：'—' / '-' / '' / None 一律 None。"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def parse_day(v):
    """兼容 '2026-11-20 00:00:00' / '20261120' / '2026-11-20'。"""
    if not v:
        return None
    s = str(v).strip()[:10]
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def cache_path(name: str) -> Path:
    return CACHE / name


def cache_load(name: str):
    p = cache_path(name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def cache_save(name: str, obj):
    p = cache_path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def get(url, params=None, timeout=30, retries=3, referer=None):
    """带重试的 GET；任何异常最后抛出，由上层决定降级。"""
    last = None
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, headers=h, timeout=timeout)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(0.6 * (i + 1))
    raise last


def fetch_cached(name: str, fetcher, ttl_hours=6):
    """优先取当日缓存；抓取失败回退旧缓存。返回 (obj, fresh)。"""
    p = cache_path(name)
    if not FORCE and p.exists():
        age = (time.time() - p.stat().st_mtime) / 3600
        if age < ttl_hours:
            obj = cache_load(name)
            if obj is not None:
                return obj, True
    try:
        obj = fetcher()
        cache_save(name, obj)
        return obj, True
    except Exception as e:
        old = cache_load(name)
        if old is None:
            raise
        log(f"    [降级] {name} 抓取失败（{type(e).__name__}: {e}），使用旧缓存")
        return old, False


# ---------------- 中证转债指数 ----------------
def fetch_csi_csindex():
    url = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
    params = {
        "indexCode": "000832",
        "startDate": "20150105",
        "endDate": TODAY.strftime("%Y%m%d"),
    }
    j = get(url, params=params, timeout=40, referer="https://www.csindex.com.cn/").json()
    if j.get("code") not in (200, "200"):
        raise RuntimeError(f"中证指数返回 code={j.get('code')}")
    rows, seen = [], set()
    for r in j.get("data") or []:
        d = parse_day(r.get("tradeDate"))
        c = num(r.get("close"))
        if not d or c is None or c <= 0 or d.weekday() >= 5 or d in seen:
            continue
        # 中证接口偶尔带节假日占位行：无成交量且开高低收全等视为占位
        o, h, l = num(r.get("open")), num(r.get("high")), num(r.get("low"))
        vol = num(r.get("tradingVol"))
        if vol is not None and vol <= 0:
            continue
        seen.add(d)
        rows.append({
            "d": d.isoformat(),
            "o": round(o, 3) if o is not None else None,
            "h": round(h, 3) if h is not None else None,
            "l": round(l, 3) if l is not None else None,
            "c": round(c, 3),
            "chg": round(num(r.get("changePct")), 2) if num(r.get("changePct")) is not None else None,
            "amt": round(num(r.get("tradingValue")), 2) if num(r.get("tradingValue")) is not None else None,
            "n": int(num(r.get("consNumber"))) if num(r.get("consNumber")) is not None else None,
        })
    rows.sort(key=lambda x: x["d"])
    if len(rows) < 200:
        raise RuntimeError(f"中证转债指数样本过少：{len(rows)}")
    return rows


def fetch_csi_tencent():
    """备用源：腾讯行情 000832 日线（约 1000 交易日，无成交额）。"""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000832,day,,,1000,qfq"
    j = get(url, timeout=30, referer="https://gu.qq.com/").json()
    data = (j.get("data") or {}).get("sh000832") or {}
    k = data.get("qfqday") or data.get("day") or []
    rows = []
    for x in k:
        if not x or len(x) < 5:
            continue
        d = parse_day(x[0])
        o, c, h, l = num(x[1]), num(x[2]), num(x[3]), num(x[4])
        if not d or c is None or c <= 0:
            continue
        rows.append({"d": d.isoformat(), "o": o, "h": h, "l": l, "c": round(c, 3),
                     "chg": None, "amt": None, "n": None})
    rows.sort(key=lambda x: x["d"])
    if len(rows) < 200:
        raise RuntimeError(f"腾讯备用指数样本过少：{len(rows)}")
    return rows


def fetch_csi():
    try:
        return fetch_csi_csindex()
    except Exception as e:
        log(f"    [降级] 中证指数失败（{type(e).__name__}），改用腾讯备用源")
        return fetch_csi_tencent()


# ---------------- 东财全市场转债快照 ----------------
EM_COLS = ("SECURITY_CODE,SECURITY_NAME_ABBR,CONVERT_STOCK_CODE,SECURITY_SHORT_NAME,"
           "RATING,ACTUAL_ISSUE_SCALE,INITIAL_TRANSFER_PRICE,TRANSFER_START_DATE,"
           "EXPIRE_DATE,LISTING_DATE,PUBLIC_START_DATE,BOND_EXPIRE,DELIST_DATE")
EM_QUOTE = ("f2~01~CONVERT_STOCK_CODE~CONVERT_STOCK_PRICE,"
            "f3~10~SECURITY_CODE~BOND_CHANGE_RT,"
            "f230~10~SECURITY_CODE~CONVERT_STOCK_CHANGE_RT,"
            "f235~10~SECURITY_CODE~TRANSFER_PRICE,"
            "f236~10~SECURITY_CODE~TRANSFER_VALUE,"
            "f2~10~SECURITY_CODE~CURRENT_BOND_PRICE,"
            "f237~10~SECURITY_CODE~TRANSFER_PREMIUM_RATIO,"
            "f239~10~SECURITY_CODE~RESALE_TRIG_PRICE,"
            "f240~10~SECURITY_CODE~REDEEM_TRIG_PRICE,"
            "f23~01~CONVERT_STOCK_CODE~PBV_RATIO")


def fetch_em_list():
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    rows, total = [], None
    for page in range(1, 8):
        params = {
            "reportName": "RPT_BOND_CB_LIST",
            "columns": EM_COLS,
            "quoteColumns": EM_QUOTE,
            "quoteType": "0",
            "source": "WEB", "client": "WEB",
            "pageNumber": str(page), "pageSize": "500",
            "sortColumns": "PUBLIC_START_DATE", "sortTypes": "-1",
        }
        j = get(url, params=params, timeout=50, referer="https://data.eastmoney.com/").json()
        res = j.get("result") or {}
        total = res.get("count") or total
        page_rows = res.get("data") or []
        rows.extend(page_rows)
        if len(page_rows) < 500:
            break
    if len(rows) < 50:
        raise RuntimeError(f"东财转债列表样本过少：{len(rows)}")
    if total and len(rows) < total - 50:
        raise RuntimeError(f"东财转债列表不完整：{len(rows)}/{total}")
    return rows


# ---------------- 逐券纯债价值（东财历史估值） ----------------
def fetch_valuation(code: str):
    url = "https://datacenter-web.eastmoney.com/api/data/get"
    params = {
        "sty": "ALL", "token": "894050c76af8597a853f5b408b759f5d",
        "st": "date", "sr": "-1", "source": "WEB", "type": "RPTA_WEB_KZZ_LS",
        "filter": f'(zcode="{code}")', "p": "1", "ps": "1",
    }
    j = get(url, params=params, timeout=20, retries=2,
            referer="https://data.eastmoney.com/").json()
    rows = ((j.get("result") or {}).get("data") or [])
    if not rows:
        return None
    r = rows[0]
    return {
        "date": (parse_day(r.get("DATE")) or date.min).isoformat(),
        "pure_bond": num(r.get("PUREBONDVALUE")),
        "pure_premium": num(r.get("PUREBONDOR")),
        "close": num(r.get("FCLOSE")),
        "swap_value": num(r.get("SWAPVALUE")),
        "premium": num(r.get("SWAPOR")),
    }


def load_valuations(codes, trade_date):
    cache = cache_load("valuation.json") or {}
    data = cache.get("data") or {}
    need = []
    for c in codes:
        v = data.get(c)
        d = parse_day(v.get("date")) if v else None
        # 每个新交易日至少刷新一次；若源尚未更新，下次运行会继续尝试
        if FORCE or not v or d is None or d < trade_date:
            need.append(c)
    if SKIP_VAL:
        return data, 0
    if not need:
        return data, 0
    log(f"    纯债估值：缓存命中 {len(codes) - len(need)} / 需抓 {len(need)}")
    t0 = time.time()
    ok = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_valuation, c): c for c in need}
        for i, fut in enumerate(as_completed(futs), 1):
            c = futs[fut]
            try:
                v = fut.result()
                if v:
                    data[c] = v
                    ok += 1
            except Exception:
                pass
            if i % 60 == 0:
                log(f"      进度 {i}/{len(need)}，成功 {ok}，{time.time()-t0:.0f}s")
    cache_save("valuation.json", {"data": data, "saved": datetime.now().isoformat(timespec="seconds")})
    log(f"    纯债估值完成：成功 {ok}/{len(need)}，用时 {time.time()-t0:.0f}s")
    return data, ok


# ---------------- 集思录指数 / 强赎 ----------------
def fetch_jsl_index():
    url = "https://www.jisilu.cn/webapi/cb/index_history/"
    j = get(url, timeout=30, referer="https://www.jisilu.cn/web/data/cb/index").json()
    d = j.get("data") or {}
    if not isinstance(d, dict) or "price_dt" not in d:
        raise RuntimeError("集思录指数结构异常")
    n = len(d["price_dt"])
    out = []
    for i in range(n):
        row = {k: (v[i] if isinstance(v, list) and i < len(v) else None) for k, v in d.items()}
        out.append(row)
    out.sort(key=lambda x: str(x.get("price_dt") or ""))
    if len(out) < 60:
        raise RuntimeError(f"集思录指数样本过少：{len(out)}")
    return out


def fetch_jsl_redeem():
    url = "https://www.jisilu.cn/webapi/cb/redeem/"
    j = get(url, timeout=40, referer="https://www.jisilu.cn/web/data/cb/redeem").json()
    data = j.get("data") or []
    if not isinstance(data, list) or len(data) < 20:
        raise RuntimeError(f"集思录强赎样本过少：{len(data)}")
    return data


# ---------------- 同花顺新债 ----------------
def fetch_new_bonds():
    url = "https://data.10jqka.com.cn/ipo/kzz/"
    j = get(url, timeout=40, referer="https://data.10jqka.com.cn/ipo/bond/").json()
    data = j.get("list") or []
    if len(data) < 20:
        raise RuntimeError(f"同花顺新债样本过少：{len(data)}")
    return data


# ---------------- 逐券长历史（东财历史估值） ----------------
def history_eligible(r) -> bool:
    """只保留 2017 年后仍在市/退市的券，避免抓 2008-2016 的老债。"""
    exp = parse_day(r.get("EXPIRE_DATE"))
    de = parse_day(r.get("DELIST_DATE"))
    if not exp and not de:
        return True
    return (exp is not None and exp >= LONG_START_D) or (de is not None and de >= LONG_START_D)


def fetch_history_batch(codes, since=None):
    """批量抓逐券历史，每批 100 只；返回 (code,date,price,premium,pure_bond,pure_prem)。"""
    rows = []
    total_batches = (len(codes) + 99) // 100
    for bi in range(total_batches):
        batch = codes[bi * 100:(bi + 1) * 100]
        filt = "(zcode in (" + ",".join(f'"{c}"' for c in batch) + "))"
        if since:
            filt += f"(DATE>='{since}')"
        page = 1
        while True:
            params = {
                "sty": "ALL", "token": "894050c76af8597a853f5b408b759f5d",
                "st": "date", "sr": "1", "source": "WEB", "type": "RPTA_WEB_KZZ_LS",
                "p": str(page), "ps": "50000", "filter": filt,
            }
            try:
                j = get("https://datacenter-web.eastmoney.com/api/data/get", params=params,
                        timeout=120, retries=2, referer="https://data.eastmoney.com/").json()
            except Exception as e:
                log(f"      [警告] 历史批次 {bi+1}/{total_batches} 失败：{type(e).__name__}")
                break
            res = j.get("result") or {}
            rs = res.get("data") or []
            for r in rs:
                d = parse_day(r.get("DATE"))
                if not d or d < LONG_START_D:
                    continue
                close = num(r.get("FCLOSE"))
                prem = num(r.get("SWAPOR"))
                price = close
                if price is None:
                    sv = num(r.get("SWAPVALUE"))
                    if sv is not None and prem is not None:
                        price = sv * (1 + prem / 100)
                if price is None or price <= 0 or prem is None:
                    continue
                rows.append((str(r.get("ZCODE") or ""), d.isoformat(), round(price, 4),
                             round(prem, 4), num(r.get("PUREBONDVALUE")),
                             num(r.get("PUREBONDOR"))))
            pages = int(res.get("pages") or 1)
            if page >= pages or not rs:
                break
            page += 1
        if rows and ((bi + 1) % 5 == 0 or bi == total_batches - 1):
            log(f"      历史批次 {bi+1}/{total_batches}，累计 {len(rows)} 行")
    return rows


def load_history_cache():
    if not HIST_CACHE.exists():
        return pd.DataFrame(columns=HIST_COLS)
    try:
        df = pd.read_csv(HIST_CACHE, dtype={"code": str}, parse_dates=["date"])
    except Exception:
        return pd.DataFrame(columns=HIST_COLS)
    for c in HIST_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[HIST_COLS].copy()
    df["code"] = df["code"].astype(str)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["price", "premium", "pure_bond", "pure_prem"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "price"])
    return df


def update_history(em_rows, trade_day):
    """全量补历史 + 活跃券增量；返回合并后的 DataFrame，并写缓存。"""
    cache = load_history_cache()
    cached = set(cache["code"].astype(str)) if len(cache) else set()
    last_map = cache.groupby("code")["date"].max().to_dict() if len(cache) else {}

    eligible = [r for r in em_rows if r.get("SECURITY_CODE") and history_eligible(r)]
    all_codes = list(dict.fromkeys(str(r.get("SECURITY_CODE")) for r in eligible))
    active_codes = [str(r.get("SECURITY_CODE")) for r in eligible
                    if not r.get("DELIST_DATE") and r.get("SECURITY_CODE")]
    active_set = set(active_codes)
    trade_ts = pd.Timestamp(trade_day)
    recent_cut = trade_ts - pd.Timedelta(days=30)

    full_codes, recent_codes = [], []
    for c in all_codes:
        if c not in cached:
            full_codes.append(c)
        elif c in active_set:
            last = last_map.get(c)
            if last is None or pd.isna(last) or last < recent_cut:
                full_codes.append(c)
            else:
                recent_codes.append(c)
    if FORCE:
        full_codes, recent_codes = all_codes, []
    else:
        recent_codes = [c for c in recent_codes
                        if last_map.get(c) is None or pd.isna(last_map.get(c)) or last_map.get(c) < trade_ts]

    log(f"      历史缓存 {len(cache)} 行 / 全量 {len(full_codes)} 只 / 增量 {len(recent_codes)} 只")
    fetched = []
    if full_codes:
        fetched += fetch_history_batch(full_codes, None)
    if recent_codes:
        last_dates = [last_map.get(c) for c in recent_codes
                      if last_map.get(c) is not None and not pd.isna(last_map.get(c))]
        min_last = min(last_dates) if last_dates else (trade_ts - pd.Timedelta(days=30))
        since = (min_last - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        fetched += fetch_history_batch(recent_codes, since)

    if not fetched and len(cache):
        return cache
    new = pd.DataFrame(fetched, columns=["code", "date", "price", "premium", "pure_bond", "pure_prem"])
    if len(new):
        new["date"] = pd.to_datetime(new["date"])
    # 全量券替换旧记录；增量券保留旧历史再追加
    keep = cache[~cache["code"].isin(set(full_codes))] if len(cache) else cache
    if len(new) and len(keep):
        hist = pd.concat([keep, new], ignore_index=True)
    elif len(new):
        hist = new
    else:
        hist = keep
    hist = hist[(hist["date"] >= LONG_START) & (hist["date"] <= pd.Timestamp(trade_day))]
    hist = hist.drop_duplicates(["code", "date"], keep="last")
    hist = hist[hist["price"] > 0].sort_values(["date", "code"])
    if len(hist):
        hist.to_csv(HIST_CACHE, index=False, date_format="%Y-%m-%d")
    return hist


def build_long(hist, csi, jsl, trade_day):
    """用逐券历史构造长周期横截面指标 + 自算等权指数 + 温度代理。"""
    if hist is None or len(hist) < 1000:
        return [], {}
    df = hist.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["price"] > 0) & df["premium"].notna()]
    df["dblow"] = df["price"] + df["premium"]
    g = df.groupby("date")
    agg = pd.DataFrame({
        "count": g["price"].count(),
        "mid_price": g["price"].median(),
        "mid_premium": g["premium"].median(),
        "avg_premium": g["premium"].mean(),
        "mid_dblow": g["dblow"].median(),
        "avg_dblow": g["dblow"].mean(),
        "mid_pure_prem": g["pure_prem"].median(),
    }).sort_index()
    agg = agg[agg["count"] >= LONG_MIN_COUNT]
    if len(agg) < 100:
        return [], {}

    # 等权指数：仅当前一交易日也有价格时计收益，避免停牌跨期
    d = df.sort_values(["code", "date"]).copy()
    d["prev_date"] = d.groupby("code")["date"].shift(1)
    d["prev_price"] = d.groupby("code")["price"].shift(1)
    d["ret"] = d["price"] / d["prev_price"] - 1
    cal = pd.to_datetime([x["d"] for x in csi])
    cal_set = set(cal)
    prev_map = {cal[i]: cal[i - 1] for i in range(1, len(cal))}
    ok = (d["prev_date"].isin(cal_set)) & (d["prev_date"] == d["date"].map(prev_map)) & \
         (d["ret"].abs() < 0.5)
    rr = d[ok].groupby("date")["ret"].agg(["mean", "count"])
    rr = rr[rr["count"] >= LONG_MIN_COUNT]
    eq = pd.Series(index=agg.index, dtype=float)
    eq.iloc[0] = 1000.0
    for i in range(1, len(eq)):
        r = rr["mean"].get(eq.index[i])
        eq.iloc[i] = eq.iloc[i - 1] * (1 + float(r)) if r is not None and pd.notna(r) else eq.iloc[i - 1]
    agg["eq"] = eq

    # 中证转债指数同起点归一，便于对照
    cs = pd.Series({pd.to_datetime(x["d"]): float(x["c"]) for x in csi}).sort_index()
    cs = cs[~cs.index.duplicated(keep="last")]
    cs_al = cs.reindex(agg.index, method="ffill")
    base = cs_al.iloc[0]
    agg["csi"] = (cs_al / base * 1000) if pd.notna(base) and base else None

    # 温度代理：中位价格近 500 日 z-score 的 logistic 映射（与集思录温度近 250 日相关约 0.98）
    ma = agg["mid_price"].rolling(500, min_periods=120).mean()
    sd = agg["mid_price"].rolling(500, min_periods=120).std()
    z = ((agg["mid_price"] - ma) / sd).clip(-8, 8)
    agg["temp"] = 100 / (1 + np.exp(-z))

    rows = []
    for d, r in agg.iterrows():
        rows.append({
            "d": d.strftime("%Y-%m-%d"),
            "mid_price": rnd(r["mid_price"], 3),
            "mid_premium": rnd(r["mid_premium"], 2),
            "avg_premium": rnd(r["avg_premium"], 2),
            "mid_dblow": rnd(r["mid_dblow"], 2),
            "avg_dblow": rnd(r["avg_dblow"], 2),
            "pure_premium": rnd(r["mid_pure_prem"], 2),
            "count": int(r["count"]),
            "eq": rnd(r["eq"], 2),
            "csi": rnd(r["csi"], 2),
            "temp": rnd(r["temp"], 2),
        })

    last = agg.iloc[-1]
    eq_last = float(agg["eq"].iloc[-1])
    max_dd = float((agg["eq"] / agg["eq"].cummax() - 1).min())
    eq_ret = eq_last / 1000 - 1

    # 与集思录近 250 日交叉校验
    corr_prem = corr_dblow = corr_temp = None
    try:
        jj = pd.DataFrame(jsl)
        jj["date"] = pd.to_datetime(jj["price_dt"])
        jj = jj.set_index("date")[["mid_premium_rt", "avg_dblow", "temperature"]]
        jj.columns = ["jsl_premium", "jsl_dblow", "jsl_temp"]
        common = agg.join(jj, how="inner")
        if len(common) >= 30:
            corr_prem = round(float(common["mid_premium"].corr(common["jsl_premium"])), 3)
            corr_dblow = round(float(common["avg_dblow"].corr(common["jsl_dblow"])), 3)
            corr_temp = round(float(common["temp"].corr(common["jsl_temp"])), 3)
    except Exception:
        pass

    meta = {
        "start": agg.index[0].strftime("%Y-%m-%d"),
        "end": agg.index[-1].strftime("%Y-%m-%d"),
        "days": int(len(agg)),
        "count": int(last["count"]),
        "mid_price": rnd(last["mid_price"], 2),
        "mid_premium": rnd(last["mid_premium"], 2),
        "avg_dblow": rnd(last["avg_dblow"], 2),
        "mid_dblow": rnd(last["mid_dblow"], 2),
        "pure_premium": rnd(last["mid_pure_prem"], 2),
        "temp": rnd(last["temp"], 2),
        "premium_pct": pct_of(agg["mid_premium"].tolist(), float(last["mid_premium"])),
        "dblow_pct": pct_of(agg["avg_dblow"].tolist(), float(last["avg_dblow"])),
        "eq_last": rnd(eq_last, 1),
        "eq_ret": rnd(eq_ret, 4),
        "max_dd": rnd(max_dd, 4),
        "corr_premium": corr_prem,
        "corr_dblow": corr_dblow,
        "corr_temp": corr_temp,
    }
    return rows, meta


# ---------------- 统计工具 ----------------
def med(vals):
    x = sorted(v for v in vals if v is not None)
    if not x:
        return None
    n = len(x)
    return x[n // 2] if n % 2 else (x[n // 2 - 1] + x[n // 2]) / 2


def avg(vals):
    x = [v for v in vals if v is not None]
    return sum(x) / len(x) if x else None


def rnd(v, n=2):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, n) if math.isfinite(x) else None


def pct_of(values, v):
    x = [a for a in values if a is not None]
    if v is None or not x:
        return None
    return round(sum(1 for a in x if a <= v) / len(x) * 100, 1)


def bucket(values, edges):
    """edges 形如 [(None,90),(90,100),...,(130,None)]，返回 [{k,n}]。"""
    out = []
    for lo, hi in edges:
        n = 0
        for v in values:
            if v is None:
                continue
            if (lo is None or v >= lo) and (hi is None or v < hi):
                n += 1
        label = (f"≥{lo}" if hi is None else (f"<{hi}" if lo is None else f"{lo}–{hi}"))
        out.append({"k": label, "n": n})
    return out


def norm_rating(v):
    """东财 RATING 偶尔带展望后缀（AAAsti / AA+sti），展示与排序前统一去掉。"""
    s = str(v or "").strip()
    for suf in ("sti", "pos", "neg", "稳定", "正面", "负面"):
        if s.endswith(suf):
            s = s[:-len(suf)]
            break
    return s or "—"


def redeem_info(x):
    """把集思录 redeem_status 归一为：强赎 / 不强赎 / 计数。

    原始 status 有四种形态：
      - '已公告 2026-12-15 强赎'         -> 强赎
      - '不强赎 2026-11-26 起重新计数'   -> 不强赎
      - '0/15 | 30' / '14/15 | 30'       -> 计数（15 日中已有 N 日达标）
      - 空 / 其它                        -> 无事件
    """
    status = str(x.get("redeem_status") or "").strip()
    icon = str(x.get("redeem_icon") or "").strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", status)
    date_in_status = parse_day(m.group(1)) if m else None
    counter = re.match(r"^(\d+)\s*/\s*(\d+)", status)
    is_no = ("不强赎" in status) or icon == "G"
    is_yes = (icon == "R") or ("强赎" in status and "不强赎" not in status)
    if is_no:
        kind = "不强赎"
    elif is_yes:
        kind = "强赎"
    elif counter:
        kind = "计数"
    else:
        kind = ""
    last_trade = parse_day(x.get("delist_dt"))
    last_convert = parse_day(x.get("last_convert_dt"))
    event = last_trade or date_in_status
    return {
        "kind": kind,
        "status": status if kind in ("强赎", "不强赎") else "",
        "counter": f"{counter.group(1)}/{counter.group(2)}" if counter else "",
        "event": event,
        "last_trade": last_trade,
        "last_convert": last_convert,
        "date_in_status": date_in_status,
    }


# ---------------- 主构建 ----------------
def main():
    t0 = time.time()
    log("=" * 56)
    log("  可转债栏目 · 数据构建")
    log("=" * 56)

    degraded = []

    log("[1/7] 中证转债指数（000832）...")
    csi, ok = fetch_cached("csi.json", fetch_csi, ttl_hours=6)
    if not ok:
        degraded.append("中证指数")
    log(f"      {len(csi)} 个交易日：{csi[0]['d']} ~ {csi[-1]['d']}")

    log("[2/7] 东财全市场转债快照...")
    em, ok = fetch_cached("em_list.json", fetch_em_list, ttl_hours=3)
    if not ok:
        degraded.append("东财列表")
    log(f"      {len(em)} 条（含退市）")

    log("[3/7] 集思录等权指数 / 强赎...")
    jsl, ok = fetch_cached("jsl_index.json", fetch_jsl_index, ttl_hours=3)
    if not ok:
        degraded.append("集思录指数")
    redeem_raw, ok = fetch_cached("jsl_redeem.json", fetch_jsl_redeem, ttl_hours=3)
    if not ok:
        degraded.append("集思录强赎")
    log(f"      指数 {len(jsl)} 日，强赎 {len(redeem_raw)} 条")

    log("[4/7] 同花顺新债日历...")
    new_raw, ok = fetch_cached("new_bonds.json", fetch_new_bonds, ttl_hours=6)
    if not ok:
        degraded.append("同花顺新债")
    log(f"      {len(new_raw)} 条")

    # 交易日：优先集思录指数末日，其次中证指数
    trade_day = parse_day(jsl[-1].get("price_dt")) or parse_day(csi[-1]["d"]) or TODAY
    trade_s = trade_day.isoformat()

    # 集思录最新一行
    j_last = jsl[-1] if jsl else {}
    prev = jsl[-2] if len(jsl) >= 2 else {}

    # 强赎映射
    redeem_map = {}
    for x in redeem_raw:
        c = str(x.get("bond_id") or "").strip()
        if c:
            redeem_map[c] = x

    # 组装转债列表
    bonds = []
    for r in em:
        code = str(r.get("SECURITY_CODE") or "").strip()
        price = num(r.get("CURRENT_BOND_PRICE"))
        if not code or price is None or price <= 0:
            continue
        if r.get("DELIST_DATE"):
            continue
        premium = num(r.get("TRANSFER_PREMIUM_RATIO"))
        conv_price = num(r.get("TRANSFER_PRICE"))
        conv_value = num(r.get("TRANSFER_VALUE"))
        dblow = (price + premium) if premium is not None else None
        maturity = parse_day(r.get("EXPIRE_DATE"))
        year_left = ((maturity - trade_day).days / 365.25) if maturity else None
        rd = redeem_map.get(code, {})
        ri = redeem_info(rd) if rd else {"kind": "", "status": "", "counter": "",
                                         "event": None, "last_convert": None, "last_trade": None}
        b = {
            "code": code,
            "name": str(r.get("SECURITY_NAME_ABBR") or ""),
            "price": rnd(price, 3),
            "chg": rnd(num(r.get("BOND_CHANGE_RT")), 2),
            "stock_code": str(r.get("CONVERT_STOCK_CODE") or ""),
            "stock": str(r.get("SECURITY_SHORT_NAME") or ""),
            "stock_price": rnd(num(r.get("CONVERT_STOCK_PRICE")), 2),
            "stock_chg": rnd(num(r.get("CONVERT_STOCK_CHANGE_RT")), 2),
            "conv_price": rnd(conv_price, 3),
            "conv_value": rnd(conv_value, 2),
            "premium": rnd(premium, 2),
            "dblow": rnd(dblow, 2),
            "rating": norm_rating(r.get("RATING")),
            "size": rnd(num(r.get("ACTUAL_ISSUE_SCALE")), 2),
            "year_left": rnd(year_left, 2),
            "maturity": maturity.isoformat() if maturity else None,
            "pb": rnd(num(r.get("PBV_RATIO")), 2),
            "redeem_price": rnd(num(r.get("REDEEM_TRIG_PRICE")), 2),
            "put_price": rnd(num(r.get("RESALE_TRIG_PRICE")), 2),
            "conv_start": (parse_day(r.get("TRANSFER_START_DATE")) or date.min).isoformat()
                          if r.get("TRANSFER_START_DATE") else None,
            "list_date": (parse_day(r.get("LISTING_DATE")) or date.min).isoformat()
                         if r.get("LISTING_DATE") else None,
            "redeem_kind": ri["kind"],
            "redeem_status": ri["status"],
            "redeem_counter": ri["counter"],
            "redeem_event": ri["event"].isoformat() if ri["event"] else None,
            "last_convert_dt": ri["last_convert"].isoformat() if ri["last_convert"] else None,
            "delist_dt": ri["last_trade"].isoformat() if ri["last_trade"] else None,
        }
        if b["conv_start"] == date.min.isoformat():
            b["conv_start"] = None
        if b["list_date"] == date.min.isoformat():
            b["list_date"] = None
        bonds.append(b)

    if len(bonds) < 100:
        raise RuntimeError(f"有效转债过少：{len(bonds)}")

    log("[5/7] 逐券纯债价值（并发）...")
    vals, _ = load_valuations([b["code"] for b in bonds], trade_day)
    for b in bonds:
        v = vals.get(b["code"]) or {}
        d = parse_day(v.get("date"))
        if d and (trade_day - d).days <= 5:
            b["pure_bond"] = rnd(v.get("pure_bond"), 2)
            if b["pure_bond"] and b["price"]:
                b["pure_premium"] = rnd((b["price"] / b["pure_bond"] - 1) * 100, 2)
            else:
                b["pure_premium"] = None
        else:
            b["pure_bond"] = None
            b["pure_premium"] = None

    log("[6/7] 逐券长历史 / 2017 起横截面...")
    try:
        hist = update_history(em, trade_day)
        long_rows, long_meta = build_long(hist, csi, jsl, trade_day)
    except Exception as e:
        log(f"    [降级] 长历史构建失败：{type(e).__name__}: {e}")
        long_rows, long_meta = [], {}
        degraded.append("长历史")
    if long_rows:
        log(f"      {len(long_rows)} 日：{long_meta['start']} ~ {long_meta['end']}，"
            f"样本 {long_meta['count']}，等权 {long_meta['eq_last']}（{long_meta['eq_ret']*100:+.1f}%）")
        log(f"      与集思录近250日相关：溢价率 {long_meta['corr_premium']} / "
            f"双低 {long_meta['corr_dblow']} / 温度代理 {long_meta['corr_temp']}")
    else:
        log("      [提示] 长历史为空，页面将只展示近 250 日序列")

    bonds.sort(key=lambda x: (x["dblow"] is None, x["dblow"] if x["dblow"] is not None else 9e9, x["code"]))

    # 市场统计
    prices = [b["price"] for b in bonds]
    premiums = [b["premium"] for b in bonds]
    dblows = [b["dblow"] for b in bonds]
    own = {
        "count": len(bonds),
        "median_price": rnd(med(prices), 2),
        "avg_price": rnd(avg(prices), 2),
        "median_premium": rnd(med(premiums), 2),
        "avg_premium": rnd(avg(premiums), 2),
        "median_dblow": rnd(med(dblows), 2),
        "avg_dblow": rnd(avg(dblows), 2),
        "total_size": rnd(sum(b["size"] for b in bonds if b["size"] is not None), 1),
        "below_100": sum(1 for p in prices if p < 100),
        "above_130": sum(1 for p in prices if p >= 130),
        "neg_premium": sum(1 for p in premiums if p is not None and p < 0),
        "redeem_yes": sum(1 for b in bonds if b["redeem_kind"] == "强赎"),
        "redeem_no": sum(1 for b in bonds if b["redeem_kind"] == "不强赎"),
    }
    own["price_dist"] = bucket(prices, [(None, 90), (90, 100), (100, 110), (110, 120),
                                        (120, 130), (130, None)])
    own["premium_dist"] = bucket(premiums, [(None, 0), (0, 10), (10, 20), (20, 30),
                                            (30, 50), (50, None)])
    own["rating_dist"] = [
        {"k": r, "n": sum(1 for b in bonds if b["rating"] == r)}
        for r in ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "—"]
        if any(b["rating"] == r for b in bonds)
    ]

    # 集思录历史
    jhist = []
    for x in jsl:
        d = parse_day(x.get("price_dt"))
        if not d:
            continue
        jhist.append({
            "d": d.isoformat(),
            "idx": rnd(num(x.get("idx_price")), 2),
            "idx_chg": rnd(num(x.get("idx_increase_rt")), 2),
            "price": rnd(num(x.get("price")), 2),
            "mid_price": rnd(num(x.get("mid_price")), 2),
            "mid_premium": rnd(num(x.get("mid_premium_rt")), 2),
            "avg_premium": rnd(num(x.get("avg_premium_rt")), 2),
            "dblow": rnd(num(x.get("avg_dblow")), 2),
            "temp": rnd(num(x.get("temperature")), 2),
            "amount": rnd(num(x.get("amount")), 2),
            "count": int(num(x.get("count")) or 0),
            "ytm": rnd(num(x.get("avg_ytm_rt")), 2),
            "turnover": rnd(num(x.get("turnover_rt")), 2),
            "buckets": [
                int(num(x.get("price_90")) or 0),
                int(num(x.get("price_90_100")) or 0),
                int(num(x.get("price_100_110")) or 0),
                int(num(x.get("price_110_120")) or 0),
                int(num(x.get("price_120_130")) or 0),
                int(num(x.get("price_130")) or 0),
            ],
        })
    jhist = jhist[-260:]

    csi_hist = csi[-3000:]
    csi_last = csi_hist[-1]
    csi_1y = [x["c"] for x in csi_hist if x["d"] >= (trade_day - timedelta(days=365)).isoformat()]
    csi_3y = [x["c"] for x in csi_hist if x["d"] >= (trade_day - timedelta(days=365 * 3)).isoformat()]
    csi_all = [x["c"] for x in csi_hist]

    market = {
        "date": trade_s,
        "csi": {
            "close": csi_last["c"],
            "chg": csi_last["chg"],
            "date": csi_last["d"],
            "pct_1y": pct_of(csi_1y, csi_last["c"]),
            "pct_3y": pct_of(csi_3y, csi_last["c"]),
            "pct_all": pct_of(csi_all, csi_last["c"]),
        },
        "jsl": {
            "idx": rnd(num(j_last.get("idx_price")), 2),
            "idx_chg": rnd(num(j_last.get("idx_increase_rt")), 2),
            "price": rnd(num(j_last.get("price")), 2),
            "mid_price": rnd(num(j_last.get("mid_price")), 2),
            "mid_premium": rnd(num(j_last.get("mid_premium_rt")), 2),
            "avg_premium": rnd(num(j_last.get("avg_premium_rt")), 2),
            "dblow": rnd(num(j_last.get("avg_dblow")), 2),
            "temp": rnd(num(j_last.get("temperature")), 2),
            "amount": rnd(num(j_last.get("amount")), 2),
            "count": int(num(j_last.get("count")) or 0),
            "ytm": rnd(num(j_last.get("avg_ytm_rt")), 2),
            "turnover": rnd(num(j_last.get("turnover_rt")), 2),
        },
        "prev": {
            "idx": rnd(num(prev.get("idx_price")), 2),
            "dblow": rnd(num(prev.get("avg_dblow")), 2),
            "mid_premium": rnd(num(prev.get("mid_premium_rt")), 2),
        },
        "own": own,
        "pcts": {
            "premium": pct_of([x["mid_premium"] for x in jhist], num(j_last.get("mid_premium_rt"))),
            "dblow": pct_of([x["dblow"] for x in jhist], num(j_last.get("avg_dblow"))),
            "temp": pct_of([x["temp"] for x in jhist], num(j_last.get("temperature"))),
            "csi_1y": pct_of(csi_1y, csi_last["c"]),
        },
    }

    # 强赎 / 不强赎日历（只保留已公告事件；0/15 计数行只进个股表，不占日历）
    redeem = []
    for x in redeem_raw:
        ri = redeem_info(x)
        if ri["kind"] not in ("强赎", "不强赎"):
            continue
        ev = ri["event"]
        if ev and (ev < trade_day - timedelta(days=60) or ev > trade_day + timedelta(days=200)):
            continue
        redeem.append({
            "code": str(x.get("bond_id") or ""),
            "name": str(x.get("bond_nm") or ""),
            "kind": ri["kind"],
            "status": ri["status"],
            "price": rnd(num(x.get("price")), 3),
            "conv_price": rnd(num(x.get("convert_price")), 3),
            "redeem_price": rnd(num(x.get("redeem_price")), 2),
            "event": ev.isoformat() if ev else None,
            "last_trade": ri["last_trade"].isoformat() if ri["last_trade"] else None,
            "last_convert": ri["last_convert"].isoformat() if ri["last_convert"] else None,
            "size": rnd(num(x.get("curr_iss_amt")), 2),
            "year_left": rnd(num(x.get("year_left")), 3),
            "tip": str(x.get("force_redeem") or "").replace("\n", "；").strip(),
        })
    redeem.sort(key=lambda x: (x["event"] or "9999", x["code"]))
    redeem = redeem[:80]

    # 新债日历（近 30 天 ~ 未来 45 天）
    new_bonds = []
    for x in new_raw:
        d = parse_day(x.get("sub_date"))
        if not d or d < TODAY - timedelta(days=30) or d > TODAY + timedelta(days=45):
            continue
        new_bonds.append({
            "code": str(x.get("bond_code") or ""),
            "name": str(x.get("bond_name") or ""),
            "stock": str(x.get("name") or ""),
            "sub_date": d.isoformat(),
            "size": rnd(num(x.get("plan_total")), 2),
            "conv_price": rnd(num(x.get("price")), 3),
            "listing_date": (parse_day(x.get("listing_date")) or date.min).isoformat()
                            if x.get("listing_date") not in (None, "", "0") else None,
            "expire": (parse_day(x.get("expire_date")) or date.min).isoformat()
                      if x.get("expire_date") else None,
        })
    new_bonds.sort(key=lambda x: x["sub_date"], reverse=True)
    new_bonds = new_bonds[:40]

    out = {
        "meta": {
            "date": trade_s,
            "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count": len(bonds),
            "degraded": degraded,
            "sources": {
                "csi": "中证指数 000832",
                "list": "东方财富 RPT_BOND_CB_LIST",
                "valuation": "东方财富 RPTA_WEB_KZZ_LS",
                "history": "东方财富 RPTA_WEB_KZZ_LS（逐券长历史）",
                "jsl": "集思录等权指数",
                "redeem": "集思录强赎",
                "new": "同花顺新债",
            },
        },
        "market": market,
        "history": {"csi": csi_hist, "jsl": jhist, "long": long_rows},
        "long_meta": long_meta,
        "bonds": bonds,
        "redeem": redeem,
        "new_bonds": new_bonds,
    }

    js = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    (DATA / "cb_data.json").write_text(js, encoding="utf-8")
    (DATA / "cb_data.js").write_text("window.CB_DATA = " + js + ";\n", encoding="utf-8")

    log("[7/7] 输出完成")
    log(f"      交易日 {trade_s}，有效转债 {len(bonds)} 只")
    log(f"      中证转债 {market['csi']['close']} ({market['csi']['chg']}%)，"
        f"集思录等权 {market['jsl']['idx']} ({market['jsl']['idx_chg']}%)")
    log(f"      中位价格 {own['median_price']}，中位溢价 {own['median_premium']}%，"
        f"平均双低 {own['avg_dblow']}，温度 {market['jsl']['temp']}")
    log(f"      强赎 {len(redeem)} 条，新债 {len(new_bonds)} 条，用时 {time.time()-t0:.1f}s")
    if degraded:
        log(f"      [提示] 降级源：{', '.join(degraded)}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
