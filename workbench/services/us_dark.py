# -*- coding: utf-8 -*-
"""暗盘行情（Hyperliquid HIP-3）实时行情服务。

为什么需要后端
--------------
Hyperliquid 的实时行情走 WebSocket（wss://api.hyperliquid.xyz/ws）。浏览器直连会
遇到跨域 / 证书 / 订阅鉴权等一堆问题，而且页面一关连接就断、无法落快照。因此由本
模块在本地站点进程里维持一条 WS 连接，页面只轮询同源的 /api/usdark/state。

三条硬约束（对应需求）
--------------------
1. 按需采集：只有页面可见并持续心跳（默认 5 秒一次）时，后台线程才保持 WS；
   超过 US_DARK_WATCH_TTL 秒没有心跳就主动断开。切到其他栏目 / 标签页后，
   前端停止轮询，后端随即停止请求，不留后台常驻流量。
2. 不制造虚假波动：最新成交价只由 trades 推送更新，并保留交易所成交时间；
   买一/卖一来自 bbo 推送并保留盘口时间；没有新成交时价格原样不动，
   页面显示「最后成交时间」与「报价更新时间」。
3. 每 5 秒落一次快照：data/us_dark/latest.json（最新全量）与
   data/us_dark/snapshots/YYYYMMDD.jsonl（每次快照一行，按天滚动）。

依赖：仅标准库。WebSocket 客户端按 RFC6455 手写（文本帧 / 分片 / ping-pong / close），
不引入 websockets 等第三方包，保证 `pip install -r requirements.txt` 后可直接运行。

HTTP 接口（同源，由 serve.py / start_site.py 转发）
--------------------------------------------------
GET /api/usdark/state?focus=xyz:NVDA               行情快照，同时注册/续约观察者心跳
GET /api/usdark/candles?coin=xyz:NVDA&interval=5m&hours=24   24h K 线代理（30 秒缓存）
GET /api/usdark/sparklines?hours=24                 全部标的的 1h 收盘 + 前一日涨跌（5 分钟缓存）
GET /api/usdark/health                             连接 / 观察者 / 快照计数（不续约心跳）
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import ssl
import struct
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from workbench import config as C

from workbench.paths import ROOT, DATA as _DATA_ROOT
BASE = str(ROOT)
DATA_DIR = str(_DATA_ROOT / "us_dark")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")
INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"

CANDLE_INTERVALS = ("1m", "5m", "15m", "1h")
FOCUS_RE = re.compile(r"^[A-Za-z0-9:_\-]{1,32}$")

NY_TZ = ZoneInfo("America/New_York")


def _last_us_sessions():
    """上一美股交易日的 16:00 ET 与再前一交易日。

    周末看盘时，“前一日”应指周五收盘，而不是 UTC 的周六。节假日未单独剔除，
    取最近的周一至周五；长假期间会落到节假日的 16:00，数据缺失时前端显示 —。
    """
    now = datetime.now(NY_TZ)

    def prev_weekday(d):
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    if now.weekday() < 5 and now.time() >= dtime(16, 0):
        last = now.date()
    else:
        last = prev_weekday(now.date() - timedelta(days=1))
    prior = prev_weekday(last - timedelta(days=1))
    cut_last = int(datetime.combine(last, dtime(16, 0), tzinfo=NY_TZ).timestamp() * 1000)
    cut_prior = int(datetime.combine(prior, dtime(16, 0), tzinfo=NY_TZ).timestamp() * 1000)
    return last, prior, cut_last, cut_prior



# ---------------------------------------------------------------- 基础工具
def now_ms() -> int:
    return int(time.time() * 1000)


def _num(v):
    """字符串数字 → float，异常/非有限值 → None（JSON 里保持 null，不用 0 冒充）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _ms(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def iso_local(ms: int | None = None) -> str:
    dt = datetime.fromtimestamp((ms if ms is not None else now_ms()) / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def post_info(body: dict, timeout: float = 8.0):
    """调用 Hyperliquid 公开 REST 行情接口（无需 API Key）。"""
    req = Request(
        INFO_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "data-research-us-dark/1.0"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- WebSocket
class WebSocketError(Exception):
    pass


class WebSocketClosed(WebSocketError):
    pass


class WsClient:
    """极简 RFC6455 客户端：只处理文本帧、分片、ping/pong 与 close。

    单线程使用（连接、发送、接收都在 feed 线程），不做并发保护。
    """

    def __init__(self, url: str, timeout: float = 10.0):
        u = urlsplit(url)
        self.host = u.hostname or "localhost"
        self.port = u.port or (443 if u.scheme == "wss" else 80)
        self.path = u.path or "/"
        if u.query:
            self.path += "?" + u.query
        self.secure = u.scheme == "wss"
        self.timeout = timeout
        self.sock = None
        self._buf = b""
        self._frag = []
        self._closed = False
        self.last_frame_at = 0.0

    # -- 连接 / 关闭
    def connect(self):
        raw = socket.create_connection((self.host, self.port), self.timeout)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if self.secure:
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: data-research-us-dark/1.0\r\n\r\n"
        ) % (self.path, self.host, key)
        try:
            raw.sendall(req.encode("ascii"))
            head = b""
            deadline = time.time() + self.timeout
            while b"\r\n\r\n" not in head:
                if time.time() > deadline:
                    raise WebSocketError("握手超时")
                chunk = raw.recv(4096)
                if not chunk:
                    raise WebSocketClosed("握手期间连接被关闭")
                head += chunk
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            raise
        header, rest = head.split(b"\r\n\r\n", 1)
        first = header.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 101 " not in (first + " "):
            raw.close()
            raise WebSocketError("握手失败：%s" % first)
        self.sock = raw
        self._buf = rest
        self._frag = []

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.sock is None:
            return
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None

    # -- 发送
    def _send_frame(self, opcode: int, payload: bytes):
        if self.sock is None:
            raise WebSocketClosed("连接未建立")
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_json(self, obj):
        self._send_frame(0x1, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def ping(self):
        self._send_frame(0x9, b"ping")

    # -- 接收
    def _read_exact(self, n: int, allow_timeout: bool = False):
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                # 头部读到一半也算“还没收全”，保留 _buf 下次继续；
                # 旧逻辑会在这里直接抛错，正常的分片帧会被误判成断线。
                if allow_timeout:
                    return None
                raise WebSocketError("读取帧超时")
            if not chunk:
                raise WebSocketClosed("连接已关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self, timeout: float = 1.0):
        """返回一个文本消息；超时返回 None。"""
        if self.sock is None:
            raise WebSocketClosed("连接未建立")
        self.sock.settimeout(timeout)
        head = self._read_exact(2, allow_timeout=True)
        if head is None:
            return None
        b1, b2 = head[0], head[1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.last_frame_at = time.time()
        if opcode == 0x8:
            raise WebSocketClosed("服务端关闭连接")
        if opcode == 0x9:
            self._send_frame(0xA, payload)
            return None
        if opcode == 0xA:
            return None
        if opcode in (0x1, 0x2):
            if fin:
                return payload.decode("utf-8", "replace")
            self._frag = [payload]
            return None
        if opcode == 0x0:
            self._frag.append(payload)
            if fin:
                data = b"".join(self._frag)
                self._frag = []
                return data.decode("utf-8", "replace")
            return None
        return None


# ---------------------------------------------------------------- 行情服务
class UsDarkFeed:
    """持有 WS 连接与内存行情的单例服务。线程安全：所有共享状态走 _lock。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._specs = list(C.US_DARK_MARKETS) + list(C.US_DARK_MAIN) + list(C.US_DARK_PREIPO)
        self._quotes = {}          # coin -> 行情字典
        self._catalog = []         # 目录（顺序与 _specs 一致，仅含交易所确实存在的）
        self._catalog_at = 0.0
        self._catalog_next_try = 0.0
        self._catalog_err = None
        self._catalog_thread = None
        self._book = {}            # focus coin -> 最近一次 l2Book
        self._trades = {}          # coin -> deque[trade dict]
        self._trade_tids = {}      # coin -> deque[tid]
        self._series = {}          # coin -> deque[[ms, mid, last]]
        self._ws = None
        self._subs = set()
        self._status = "idle"      # idle / catalog / connecting / live / error
        self._error = ""
        self._connected_at = 0.0
        self._last_msg = 0.0
        self._last_ping = 0.0
        self._last_snapshot = 0.0
        self._snap_seq = 0
        self._snap_at = None
        self._watch_until = 0.0
        self._focus = None
        self._clock_samples = deque(maxlen=64)
        self._clock_off = 0
        self._backoff = 1.0
        self._next_attempt = 0.0
        self._candle_cache = {}
        self._spark_cache = {}     # hours -> (ts, payload, ttl)
        self._spark_lock = threading.Lock()
        self._started_at = time.time()
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------ 生命周期
    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        os.makedirs(SNAP_DIR, exist_ok=True)
        self._prune_snapshots()
        self._thread = threading.Thread(target=self._run, name="us-dark-feed", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._disconnect("服务停止")

    def _prune_snapshots(self):
        cutoff = time.time() - C.US_DARK_KEEP_DAYS * 86400
        try:
            for name in os.listdir(SNAP_DIR):
                p = os.path.join(SNAP_DIR, name)
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
        except OSError:
            pass

    # ------------------------------------------------------------ 主循环
    def _run(self):
        while not self._stop.is_set():
            try:
                if time.time() >= self._watch_until:
                    if self._ws is not None:
                        self._disconnect("无观察者")
                    if self._status not in ("idle",):
                        self._status = "idle"
                        self._error = ""
                    self._stop.wait(0.5)
                    continue

                # 已有目录时先连 WS 并订阅，再刷新目录。
                # 旧顺序是「先刷新目录、再连 WS」，恢复时目录请求一旦慢/重试，
                # 页面会长时间停在「连接中/报价停滞」（实测可拖 5–20 秒）。
                if self._ws is None and self._catalog and time.time() >= self._next_attempt:
                    self._connect()
                if self._ws is not None:
                    self._sync_subs()

                # 目录：首次必须同步取（还没有标的可订阅）；已有目录时改为后台刷新。
                # 同步刷新会阻塞 recv 数秒，WS 来不及回 pong 会被服务端断开，必须异步。
                stale = (not self._catalog or
                         time.time() - self._catalog_at > C.US_DARK_CATALOG_TTL)
                if stale and time.time() >= self._catalog_next_try:
                    if not self._catalog:
                        self._refresh_catalog()
                        self._catalog_next_try = time.time() + 30
                    elif not (self._catalog_thread and self._catalog_thread.is_alive()):
                        self._catalog_thread = threading.Thread(
                            target=self._refresh_catalog_bg, name="us-dark-catalog", daemon=True)
                        self._catalog_thread.start()

                if not self._catalog:
                    self._status = "catalog"
                    self._stop.wait(1.0)
                    continue

                if self._ws is None:
                    if time.time() < self._next_attempt:
                        self._stop.wait(0.2)
                        continue
                    self._connect()
                    if self._ws is None:
                        continue
                    self._sync_subs()

                raw = self._ws.recv(2.0)
                if raw is not None:
                    self._last_msg = time.time()
                    self._on_message(raw)

                # 心跳：30 秒无任何数据则 ping，90 秒无数据判死重连
                quiet = time.time() - max(self._last_msg, self._ws.last_frame_at)
                if quiet > 30 and time.time() - self._last_ping > 20:
                    self._last_ping = time.time()
                    self._ws.ping()
                if quiet > 90:
                    raise WebSocketError("90 秒无行情推送，重连")

                if time.time() - self._last_snapshot >= C.US_DARK_SNAPSHOT_SEC:
                    self._snapshot()
            except Exception as e:  # noqa: BLE001 - 任何异常都不该终止采集线程
                self._on_error(e)
                self._stop.wait(0.4)

    def _on_error(self, e):
        msg = "%s: %s" % (type(e).__name__, e)
        self._disconnect(msg)
        self._status = "error"
        self._error = msg.replace("\n", " ")[:200]
        self._next_attempt = time.time() + self._backoff
        self._backoff = min(self._backoff * 2, 15.0)

    def _connect(self):
        self._status = "connecting"
        self._error = ""
        ws = WsClient(WS_URL, timeout=10.0)
        try:
            ws.connect()
        except Exception as e:
            self._status = "error"
            self._error = ("连接失败 %s: %s" % (type(e).__name__, e))[:200]
            self._next_attempt = time.time() + self._backoff
            self._backoff = min(self._backoff * 2, 15.0)
            return
        with self._lock:
            self._ws = ws
            self._subs.clear()
            self._clock_samples.clear()
            self._clock_off = 0
            self._connected_at = time.time()
            self._last_msg = time.time()
            self._last_ping = 0.0
            self._last_snapshot = 0.0
            self._status = "live"
            self._error = ""
            self._backoff = 1.0
        print("[暗盘行情] 已连接 Hyperliquid WS，%d 个标的" % len(self._catalog), flush=True)

    def _disconnect(self, reason: str):
        ws = self._ws
        self._ws = None
        self._subs.clear()
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
            print("[暗盘行情] 已断开行情连接（%s）" % reason, flush=True)

    # ------------------------------------------------------------ 目录
    def _refresh_catalog_bg(self):
        """后台刷新目录，避免阻塞 WS 的 recv/pong。"""
        try:
            self._refresh_catalog()
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._catalog_err = "%s: %s" % (type(e).__name__, e)
                self._catalog_next_try = time.time() + 30

    def _refresh_catalog(self):
        """刷新市场目录。

        关键：必须整组成功才替换目录。2026-11-22 实测过一个真实故障：xyz 的
        metaAndAssetCtxs 偶发失败、只拿到主市场 BTC 时，旧实现会用「只有 1 个标的」
        的目录覆盖全量目录，并把 61 个股票的 trades/bbo 全部退订；页面于是永久显示
        「报价停滞」。现在任一分组失败 -> 保留旧目录、30 秒后重试，绝不缩容。
        """
        rows, err, failed = [], None, False
        groups = ((C.US_DARK_DEX, C.US_DARK_MARKETS),
                  ("", C.US_DARK_MAIN),
                  (C.US_DARK_PREIPO_DEX, C.US_DARK_PREIPO))
        for dex, specs in groups:
            if not specs:
                continue
            body = {"type": "metaAndAssetCtxs"}
            if dex:
                body["dex"] = dex
            data = None
            for attempt in range(2):
                try:
                    data = post_info(body, timeout=12.0)
                    break
                except Exception as e:  # noqa: BLE001
                    err = "%s: %s" % (dex or "main", e)
                    if attempt == 0:
                        time.sleep(1.0)
            if not data:
                failed = True
                continue
            try:
                meta, ctxs = data
                universe = meta.get("universe") or []
            except Exception as e:  # noqa: BLE001
                failed = True
                err = "%s: %s" % (dex or "main", e)
                continue
            uni = {u.get("name"): u for u in universe}
            by_coin = {u.get("name"): c for u, c in zip(universe, ctxs)}
            matched = 0
            for coin, name, group in specs:
                u = uni.get(coin)
                c = by_coin.get(coin)
                if not u or not c or u.get("isDelisted"):
                    continue
                matched += 1
                rows.append({"coin": coin, "symbol": coin.split(":", 1)[-1], "name": name,
                             "group": group, "dex": dex or "main", "u": u, "c": c})
            if matched == 0:
                failed = True
                err = "%s: 未匹配到任何标的" % (dex or "main")

        if failed or not rows:
            with self._lock:
                self._catalog_err = err or "交易所未返回任何匹配市场"
                self._catalog_next_try = time.time() + 30
            if self._catalog:
                print("[暗盘行情] 目录刷新失败，沿用旧目录（%s）" % self._catalog_err, flush=True)
            return

        with self._lock:
            self._catalog = [{"coin": r["coin"], "symbol": r["symbol"], "name": r["name"],
                              "group": r["group"], "dex": r["dex"]} for r in rows]
            self._catalog_at = time.time()
            self._catalog_err = None
            ms = now_ms()
            for r in rows:
                q = self._quotes.get(r["coin"])
                if q is None:
                    q = {
                        "coin": r["coin"], "symbol": r["symbol"], "name": r["name"],
                        "group": r["group"], "dex": r["dex"],
                        "last_px": None, "last_sz": None, "last_side": None, "last_trade_ms": None,
                        "bid": None, "bid_sz": None, "ask": None, "ask_sz": None, "bbo_ms": None,
                        "mid": None, "spread_bp": None,
                        "prev_day_px": None, "mark_px": None, "oracle_px": None, "mid_px_ref": None,
                        "day_ntl_vlm": None, "open_interest": None, "funding": None,
                        "sz_decimals": None, "max_leverage": None, "catalog_ms": None,
                    }
                    self._quotes[r["coin"]] = q
                    # 会话快照对所有分组都记录（股票 + 商品）
                    self._series.setdefault(r["coin"], deque(maxlen=C.US_DARK_SERIES_MAX))
                c = r["c"]
                q["prev_day_px"] = _num(c.get("prevDayPx"))
                q["mark_px"] = _num(c.get("markPx"))
                q["oracle_px"] = _num(c.get("oraclePx"))
                q["mid_px_ref"] = _num(c.get("midPx"))
                q["day_ntl_vlm"] = _num(c.get("dayNtlVlm"))
                q["open_interest"] = _num(c.get("openInterest"))
                q["funding"] = _num(c.get("funding"))
                q["sz_decimals"] = r["u"].get("szDecimals")
                q["max_leverage"] = r["u"].get("maxLeverage")
                q["catalog_ms"] = ms

    # ------------------------------------------------------------ 订阅
    def _send_sub(self, typ: str, coin: str, on: bool):
        if self._ws is None:
            return
        sub = {"type": typ, "coin": coin}
        if typ == "l2Book":
            # fast=true：盘口有变动就推（实测约 2 条/秒，普通模式约 0.2 条/秒），
            # 否则深度会明显落后于 bbo 的买一/卖一，同一张卡片里自相矛盾。
            sub["fast"] = True
        self._ws.send_json({
            "method": "subscribe" if on else "unsubscribe",
            "subscription": sub,
        })

    def _sync_subs(self):
        desired = set()
        for row in self._catalog:
            desired.add(("trades", row["coin"]))
            desired.add(("bbo", row["coin"]))
        focus = self._focus
        if focus and any(r["coin"] == focus for r in self._catalog):
            desired.add(("l2Book", focus))
        for typ, coin in list(self._subs - desired):
            self._send_sub(typ, coin, False)
            self._subs.discard((typ, coin))
        for typ, coin in desired - self._subs:
            self._send_sub(typ, coin, True)
            self._subs.add((typ, coin))

    # ------------------------------------------------------------ 消息
    def _on_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        channel = msg.get("channel")
        data = msg.get("data")
        if channel == "trades":
            self._on_trades(data if isinstance(data, list) else [data])
        elif channel == "bbo" and isinstance(data, dict):
            self._on_bbo(data)
        elif channel == "l2Book" and isinstance(data, dict):
            self._on_book(data)
        elif channel == "error":
            with self._lock:
                self._error = str(data)[:200]

    def _on_trades(self, trades):
        ms = now_ms()
        with self._lock:
            for tr in trades:
                if not isinstance(tr, dict):
                    continue
                coin = tr.get("coin")
                q = self._quotes.get(coin)
                if q is None:
                    continue
                px, sz, t = _num(tr.get("px")), _num(tr.get("sz")), _ms(tr.get("time"))
                if px is None or t is None:
                    continue
                tid = tr.get("tid")
                # 订阅成功时交易所会补发最近成交：按 tid 去重，且不允许旧成交覆盖新成交
                tids = self._trade_tids.get(coin)
                if tids is None:
                    tids = self._trade_tids[coin] = deque(maxlen=400)
                if tid is not None and tid in tids:
                    continue
                if tid is not None:
                    tids.append(tid)
                if q["last_trade_ms"] is None or t >= q["last_trade_ms"]:
                    q["last_px"] = px
                    q["last_sz"] = sz
                    q["last_side"] = tr.get("side")
                    q["last_trade_ms"] = t
                tape = self._trades.get(coin)
                if tape is None:
                    tape = self._trades[coin] = deque(maxlen=C.US_DARK_TRADES_MAX)
                tape.appendleft({"t": t, "px": px, "sz": sz, "side": tr.get("side"),
                                 "tid": tid, "recv_ms": ms})

    def _on_bbo(self, data):
        coin = data.get("coin")
        levels = data.get("bbo") or []
        with self._lock:
            q = self._quotes.get(coin)
            if q is None:
                return
            bid = levels[0] if len(levels) > 0 and isinstance(levels[0], dict) else None
            ask = levels[1] if len(levels) > 1 and isinstance(levels[1], dict) else None
            q["bid"] = _num((bid or {}).get("px"))
            q["bid_sz"] = _num((bid or {}).get("sz"))
            q["ask"] = _num((ask or {}).get("px"))
            q["ask_sz"] = _num((ask or {}).get("sz"))
            t = _ms(data.get("time"))
            if t:
                q["bbo_ms"] = t
                self._sample_clock(t)
            self._recalc_mid(q)

    def _on_book(self, data):
        coin = data.get("coin")
        levels = data.get("levels") or []
        if len(levels) < 2:
            return
        def side(rows, n=10):
            out = []
            for r in (rows or [])[:n]:
                if not isinstance(r, dict):
                    continue
                px, sz = _num(r.get("px")), _num(r.get("sz"))
                if px is None or sz is None:
                    continue
                out.append([px, sz, _ms(r.get("n"))])
            return out
        with self._lock:
            if not coin:
                return
            t = _ms(data.get("time"))
            self._book[coin] = {"bids": side(levels[0]), "asks": side(levels[1]), "ms": t}
            q = self._quotes.get(coin)
            if q is not None and q["bbo_ms"] is None and t:
                q["bbo_ms"] = t

    def _sample_clock(self, exchange_ms: int):
        self._clock_samples.append(exchange_ms - now_ms())
        if len(self._clock_samples) >= 3:
            vals = sorted(self._clock_samples)
            self._clock_off = vals[len(vals) // 2]

    @staticmethod
    def _recalc_mid(q):
        bid, ask = q.get("bid"), q.get("ask")
        if bid and ask and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            q["mid"] = mid
            q["spread_bp"] = (ask - bid) / mid * 10000.0 if mid > 0 else None
        else:
            q["mid"] = None
            q["spread_bp"] = None

    def _now_ms(self) -> int:
        return now_ms() + int(self._clock_off)

    # ------------------------------------------------------------ 快照
    def _snapshot(self):
        snap_ms = self._now_ms()
        with self._lock:
            for coin, ser in self._series.items():
                q = self._quotes.get(coin)
                if q is None:
                    continue
                ser.append([snap_ms, q.get("mid"), q.get("last_px")])
            self._snap_seq += 1
            self._snap_at = snap_ms
            seq = self._snap_seq
            compact = {}
            for coin, q in self._quotes.items():
                compact[coin] = [q.get("last_px"), q.get("last_trade_ms"), q.get("bid"), q.get("ask"),
                                 q.get("bbo_ms"), q.get("mid"), q.get("day_ntl_vlm")]
            state = self._state_locked(include_series=False, include_trades=False)
        line = {"ts": snap_ms, "t": iso_local(snap_ms), "seq": seq, "conn": state["conn"]["status"],
                "q": compact}
        try:
            os.makedirs(SNAP_DIR, exist_ok=True)
            day = datetime.fromtimestamp(snap_ms / 1000).strftime("%Y%m%d")
            path = os.path.join(SNAP_DIR, day + ".jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
            tmp = os.path.join(DATA_DIR, "latest.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, os.path.join(DATA_DIR, "latest.json"))
        except OSError as e:
            print("[暗盘行情] 快照写入失败：%s" % e, flush=True)
        self._last_snapshot = time.time()

    # ------------------------------------------------------------ 对外状态
    def touch(self, focus=None):
        with self._lock:
            self._watch_until = time.time() + C.US_DARK_WATCH_TTL
            if focus and FOCUS_RE.match(focus):
                self._focus = focus

    def _state_locked(self, include_series=True, include_trades=True):
        ms = self._now_ms()
        quotes = []
        stale_trade = C.US_DARK_STALE_TRADE * 1000
        stale_quote = C.US_DARK_STALE_QUOTE * 1000
        for coin, q in self._quotes.items():
            d = dict(q)
            d["trade_age_ms"] = (ms - q["last_trade_ms"]) if q.get("last_trade_ms") else None
            d["book_age_ms"] = (ms - q["bbo_ms"]) if q.get("bbo_ms") else None
            prev, last = q.get("prev_day_px"), q.get("last_px")
            d["chg_pct"] = ((last / prev - 1.0) * 100.0) if (prev and last) else None
            if d["trade_age_ms"] is not None and d["trade_age_ms"] <= stale_trade:
                d["status"] = "live"
            elif d["book_age_ms"] is not None and d["book_age_ms"] <= stale_quote:
                d["status"] = "book"
            elif d["book_age_ms"] is not None:
                d["status"] = "stale"
            else:
                d["status"] = "none"
            quotes.append(d)
        order = {row["coin"]: i for i, row in enumerate(self._catalog)}
        quotes.sort(key=lambda d: order.get(d["coin"], 9999))
        focus_payload = None
        if self._focus:
            coin = self._focus
            focus_payload = {"coin": coin}
            if include_series:
                focus_payload["series"] = list(self._series.get(coin, ()))
            book = self._book.get(coin)
            if book:
                focus_payload["book"] = {"bids": book["bids"], "asks": book["asks"], "ms": book["ms"]}
            else:
                focus_payload["book"] = None
            if include_trades:
                focus_payload["trades"] = list(self._trades.get(coin, ()))[:40]
            focus_payload["quote"] = next((d for d in quotes if d["coin"] == coin), None)
        return {
            "ok": True,
            "server_time_ms": ms,
            "clock_offset_ms": int(self._clock_off),
            "conn": {
                "status": self._status,
                "error": self._error,
                "connected_at_ms": _ms(self._connected_at * 1000) if self._connected_at else None,
                "last_msg_age_ms": (now_ms() - int(self._last_msg * 1000)) if self._last_msg else None,
                "watch_left_ms": max(0, int((self._watch_until - time.time()) * 1000)),
                "subscriptions": len(self._subs),
            },
            "catalog": list(self._catalog),
            "catalog_at_ms": _ms(self._catalog_at * 1000) if self._catalog_at else None,
            "catalog_error": self._catalog_err,
            "focus": focus_payload,
            "quotes": quotes,
            "snapshot": {
                "seq": self._snap_seq,
                "at_ms": self._snap_at,
                "interval_sec": C.US_DARK_SNAPSHOT_SEC,
                "keep_days": C.US_DARK_KEEP_DAYS,
                "dir": "data/us_dark/snapshots",
            },
            "config": {
                "poll_ms": C.US_DARK_POLL_MS,
                "stale_trade_sec": C.US_DARK_STALE_TRADE,
                "stale_quote_sec": C.US_DARK_STALE_QUOTE,
                "ttl_sec": C.US_DARK_WATCH_TTL,
            },
        }

    def state(self, focus=None, touch=True):
        if touch:
            self.touch(focus)
        with self._lock:
            return self._state_locked()

    def health(self):
        with self._lock:
            return {
                "ok": True,
                "status": self._status,
                "error": self._error,
                "catalog": len(self._catalog),
                "quotes": len(self._quotes),
                "subscriptions": len(self._subs),
                "watching": time.time() < self._watch_until,
                "watch_left_ms": max(0, int((self._watch_until - time.time()) * 1000)),
                "snapshot_seq": self._snap_seq,
                "snapshot_at": iso_local(self._snap_at) if self._snap_at else None,
                "snapshot_dir": os.path.relpath(SNAP_DIR, BASE).replace("\\", "/"),
                "uptime_sec": int(time.time() - self._started_at),
            }

    # ------------------------------------------------------------ K 线
    def candles(self, coin: str, interval: str = "5m", hours: int = 24):
        if interval not in CANDLE_INTERVALS:
            raise ValueError("interval 只支持 %s" % ", ".join(CANDLE_INTERVALS))
        hours = max(1, min(int(hours or 24), 168))
        with self._lock:
            known = any(row["coin"] == coin for row in self._catalog)
        if not known:
            raise ValueError("未知标的：%s" % coin)
        key = (coin, interval, hours)
        hit = self._candle_cache.get(key)
        if hit and time.time() - hit[0] < C.US_DARK_CANDLE_TTL:
            return hit[1]
        start = now_ms() - hours * 3600 * 1000
        rows = post_info({"type": "candleSnapshot",
                          "req": {"coin": coin, "interval": interval, "startTime": start}})
        out = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            t, o, h, l, c = (_ms(r.get("t")), _num(r.get("o")), _num(r.get("h")),
                             _num(r.get("l")), _num(r.get("c")))
            if None in (t, o, h, l, c):
                continue
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c,
                        "v": _num(r.get("v")), "n": _ms(r.get("n"))})
        payload = {"ok": True, "coin": coin, "interval": interval, "hours": hours,
                   "as_of_ms": now_ms(), "candles": out}
        self._candle_cache[key] = (time.time(), payload)
        return payload

    # ------------------------------------------------------------ 迷你走势
    def sparklines(self, hours: int = 24):
        """批量迷你走势 + 上一美股交易日涨跌幅。

        每个标的取最近 7 天的 1h 收盘（一次请求同时供两列使用）：
          - series：最近 hours 小时收盘，给表格折线；
          - prev_day：上一美股交易日 16:00 ET 收盘 vs 再前一交易日 16:00 ET 收盘，
            周末保持不变，便于看“周五收盘是涨是跌”。
        66 个标的并发（8 线程），缓存 5 分钟；同一时间只允许一个请求在跑。
        """
        hours = max(6, min(int(hours or 24), 48))
        with self._lock:
            hit = self._spark_cache.get(hours)
            if hit and time.time() - hit[0] < hit[2]:
                return hit[1]
            coins = [row["coin"] for row in self._catalog]
        empty = {"ok": True, "as_of_ms": self._now_ms(), "interval": "1h",
                 "hours": hours, "series": {}, "prev_day": {}}
        if not coins:
            return empty
        if not self._spark_lock.acquire(blocking=False):
            with self._lock:
                hit = self._spark_cache.get(hours)
            return hit[1] if hit else empty
        try:
            now = now_ms()
            start = now - 7 * 86400 * 1000
            last, prior, cut_last, cut_prior = _last_us_sessions()

            def one(coin):
                try:
                    rows = post_info({"type": "candleSnapshot",
                                      "req": {"coin": coin, "interval": "1h", "startTime": start}},
                                     timeout=10.0)
                except Exception:  # noqa: BLE001 - 单个标的失败不影响整表
                    return coin, [], None
                pts = []
                for r in rows or []:
                    if not isinstance(r, dict):
                        continue
                    t, o, c = _ms(r.get("t")), _num(r.get("o")), _num(r.get("c"))
                    if t is not None and o is not None and c is not None:
                        pts.append((t, o, c))
                pts.sort()

                def close_before(cut):
                    val = None
                    for t, _o, c in pts:
                        if t < cut:
                            val = c
                        else:
                            break
                    return val

                series = [round(c, 4) for t, _o, c in pts if t >= now - hours * 3600 * 1000]
                cd, cp = close_before(cut_last), close_before(cut_prior)
                prev = None
                if cd is not None and cp:
                    # 拆股/并股会在某一根 1h K 线上留下「open / 上一根 close ≈ 1/N」的
                    # 离散跳变（SNDK 8:1 实测 753.69 → 92.58 ≈ 1/8）。用日收盘反推
                    # 会被当天真实涨跌污染（曾把 8:1 误判成 10:1），所以看跳变本身。
                    splits = []
                    for i in range(1, len(pts)):
                        prev_c, op = pts[i - 1][2], pts[i][1]
                        if not prev_c or not op:
                            continue
                        ratio = op / prev_c
                        if ratio >= 0.5:      # 只处理向下的拆股
                            continue
                        best = None
                        for n in range(2, 21):
                            diff = abs(ratio * n - 1.0)
                            if diff < 0.03 and (best is None or diff < best[0]):
                                best = (diff, n)
                        if best:
                            splits.append((pts[i][0], best[1]))

                    # 只有落在「前一交易日收盘 ~ 上一交易日收盘」之间的拆股才需要还原
                    factor = 1
                    for ts, n in splits:
                        if cut_prior < ts <= cut_last:
                            factor *= n
                    cp_adj = cp / factor if factor else cp
                    prev = {"date": last.isoformat(), "close": round(cd, 4),
                            "chg": round((cd / cp_adj - 1.0) * 100.0, 2)}
                    if factor > 1:
                        prev["split"] = factor
                        prev["raw_chg"] = round((cd / cp - 1.0) * 100.0, 2)
                return coin, series, prev

            series, prev_day = {}, {}
            with ThreadPoolExecutor(max_workers=8) as ex:
                try:
                    for coin, closes, prev in ex.map(one, coins, timeout=45):
                        if len(closes) >= 2:
                            series[coin] = closes
                        if prev:
                            prev_day[coin] = prev
                except TimeoutError:
                    pass
            payload = {"ok": True, "as_of_ms": self._now_ms(), "interval": "1h",
                       "hours": hours, "series": series, "prev_day": prev_day,
                       "session": {"last": last.isoformat(), "prior": prior.isoformat()}}
            ttl = 300 if len(series) >= len(coins) * 0.8 else 60
            with self._lock:
                self._spark_cache[hours] = (time.time(), payload, ttl)
            return payload
        finally:
            self._spark_lock.release()

    # ------------------------------------------------------------ HTTP
    def handle_api(self, handler) -> bool:
        try:
            parsed = urlsplit(handler.path)
        except Exception:
            return False
        path = parsed.path
        if not path.startswith("/api/usdark/"):
            return False
        qs = parse_qs(parsed.query)

        def q1(key, default=None):
            vals = qs.get(key)
            return vals[0] if vals else default

        try:
            if path == "/api/usdark/state":
                payload = self.state(focus=q1("focus"), touch=True)
            elif path == "/api/usdark/candles":
                payload = self.candles(q1("coin", ""), q1("interval", "5m"), int(q1("hours", "24")))
            elif path == "/api/usdark/sparklines":
                payload = self.sparklines(int(q1("hours", "24")))
            elif path == "/api/usdark/health":
                payload = self.health()
            else:
                self._json(handler, 404, {"ok": False, "error": "未知接口"})
                return True
            self._json(handler, 200, payload)
        except ValueError as e:
            self._json(handler, 400, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._json(handler, 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        return True

    @staticmethod
    def _json(handler, code: int, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            handler.send_response(code)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Cache-Control", "no-store, max-age=0")
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.end_headers()
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


_FEED = None


def get_feed() -> UsDarkFeed:
    """取进程内单例（首次调用创建并启动采集线程）。"""
    global _FEED
    if _FEED is None:
        _FEED = UsDarkFeed().start()
    return _FEED


def handle_api(handler) -> bool:
    return get_feed().handle_api(handler)


if __name__ == "__main__":
    # 单独调试：python us_dark.py  然后
    #   curl "http://127.0.0.1:8040/api/usdark/state?focus=xyz:NVDA"
    import http.server
    import socketserver

    feed = get_feed()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if feed.handle_api(self):
                return
            super().do_GET()

        def log_message(self, fmt, *args):
            pass

    class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
        allow_reuse_address = True

    print("暗盘行情调试服务：http://127.0.0.1:%d/api/usdark/health" % C.PORT, flush=True)
    with Server(("127.0.0.1", C.PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
