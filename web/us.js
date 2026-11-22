/* 暗盘行情栏目前端：Hyperliquid HIP-3 公开行情（美股 + 商品）
 *
 * 数据链：页面 5 秒轮询 /api/usdark/state（同源，后端保持 WebSocket）
 * 刷新门控：只有在「页面可见 + 本栏目在视口内」时才轮询；切到其他栏目 / 标签页
 *          隐藏后立即停止，后端 20 秒无心跳即断开 WS，不产生任何行情请求。
 * 诚实展示：最新成交价只随真实成交变，价格单元格只在 last_trade_ms 变化时闪动；
 *          没有新成交时显示「最后成交」与「报价更新」时间，不做插值。
 */
(function(){
  "use strict";

  var API = "/api/usdark";
  var POLL = 5000, POLL_BOOT = 1200;

  var S = {
    ready: false, data: null, catalog: [], quotes: [],
    focus: null, userFocus: false,
    timer: null, ticker: null, active: false, inView: true,
    srvOffset: 0, nextAt: 0, lastStateAt: 0,
    rows: {}, ageEls: {}, prevPx: {}, prevTradeMs: {},
    sortKey: "vol", sortDir: -1, q: "", mode: "all",
    candles: null, candleCoin: null, candleAt: 0,
    spark: {}, sparkAt: 0, sparkLoading: false, qmap: {},
    prevDay: {}, prevSession: null,
    charts: {}, bootTries: 0, lastError: "", netFail: 0
  };

  // ---------------------------------------------------------------- 工具
  function $(id){ return document.getElementById(id); }
  function esc(s){
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
    });
  }
  function cssVar(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function num(v){ var x = Number(v); return (v === null || v === undefined || v === "" || !isFinite(x)) ? null : x; }
  function srvNow(){ return Date.now() + S.srvOffset; }
  function isActive(){ return S.active; }

  function pxDigits(v){
    var x = Math.abs(num(v) || 0);
    if(x >= 100) return 2;
    if(x >= 10) return 2;
    if(x >= 1) return 3;
    return 4;
  }
  function fmtPx(v){
    var x = num(v);
    if(x === null) return "—";
    return x.toLocaleString("en-US", {minimumFractionDigits: pxDigits(x), maximumFractionDigits: pxDigits(x)});
  }
  // 中间价常常带半分位（130.605），直接四舍五入到两位会看起来等于卖一
  function fmtMid(v){
    var x = num(v);
    if(x === null) return "—";
    var cents = Math.abs(x * 100 - Math.round(x * 100));
    return cents < 1e-6 ? x.toFixed(2) : x.toFixed(3);
  }
  function fmtNum(v, d){
    var x = num(v);
    if(x === null) return "—";
    return x.toLocaleString("en-US", {minimumFractionDigits: d || 0, maximumFractionDigits: d || 0});
  }
  function fmtPct(v){
    var x = num(v);
    if(x === null) return "—";
    return (x > 0 ? "+" : "") + x.toFixed(2) + "%";
  }
  function fmtUsd(v){
    var x = num(v);
    if(x === null) return "—";
    var a = Math.abs(x);
    if(a >= 1e8) return "$" + (x / 1e8).toFixed(2) + " 亿";
    if(a >= 1e4) return "$" + (x / 1e4).toFixed(1) + " 万";
    return "$" + x.toFixed(0);
  }
  function fmtQty(v){
    var x = num(v);
    if(x === null) return "—";
    var a = Math.abs(x);
    if(a >= 1e8) return (x / 1e8).toFixed(2) + " 亿";
    if(a >= 1e4) return (x / 1e4).toFixed(1) + " 万";
    return x.toLocaleString("en-US", {maximumFractionDigits: 0});
  }
  function fmtSz(v){
    var x = num(v);
    if(x === null) return "—";
    if(Math.abs(x) >= 1000) return fmtQty(x);
    if(Math.abs(x) >= 1) return x.toFixed(2);
    return x.toFixed(4);
  }
  function cls(v){
    var x = num(v);
    if(x === null || x === 0) return "flat";
    return x > 0 ? "up" : "down";
  }
  function fmtClock(ms){
    if(!ms) return "—";
    var d = new Date(ms);
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }
  function pad(n){ return (n < 10 ? "0" : "") + n; }
  function agoText(ms){
    if(!ms) return "—";
    var d = srvNow() - ms;
    if(d < -2000) return "刚刚";
    if(d < 0) d = 0;
    var s = Math.round(d / 1000);
    if(s < 5) return "刚刚";
    if(s < 60) return s + " 秒前";
    var m = Math.floor(s / 60);
    if(m < 60) return m + " 分钟前";
    var h = Math.floor(m / 60);
    if(h < 24) return h + " 小时前";
    var dd = Math.floor(h / 24);
    return dd + " 天前";
  }
  function ageMs(ms){ return ms ? Math.max(0, srvNow() - ms) : null; }

  function statusOf(q){
    var ta = ageMs(q.last_trade_ms), ba = ageMs(q.bbo_ms);
    var st = (S.data && S.data.config) || {stale_trade_sec: 60, stale_quote_sec: 60};
    if(ta === null && ba === null) return "none";
    if(ta !== null && ta <= st.stale_trade_sec * 1000) return "live";
    if(ba !== null && ba <= st.stale_quote_sec * 1000) return "book";
    if(ba !== null) return "stale";
    return "none";
  }
  var STATUS = {
    live:  {label: "成交中", cls: "green"},
    book:  {label: "报价中", cls: "blue"},
    stale: {label: "报价停滞", cls: "orange"},
    none:  {label: "无报价", cls: "gray"}
  };
  function statusHtml(q){
    var s = STATUS[statusOf(q)] || STATUS.none;
    return '<span class="badge ' + s.cls + '">' + s.label + '</span>';
  }

  // ---------------------------------------------------------------- 轮询与门控
  function boot(){
    readTheme();
    bindVisibility();
    S.active = document.visibilityState !== "hidden" && S.inView;
    if(S.active){ poll(); startTicker(); }
    else { updateStatus(); showPause(); }
  }

  function bindVisibility(){
    document.addEventListener("visibilitychange", updateActive);
    window.addEventListener("pageshow", updateActive);
    window.addEventListener("pagehide", function(){ S.active = false; stopTimer(); });
    S.inView = computeInView();
    var onScroll = function(){
      if(onScroll._p) return;
      onScroll._p = true;
      requestAnimationFrame(function(){
        onScroll._p = false;
        var v = computeInView();
        if(v !== S.inView){ S.inView = v; updateActive(); }
      });
    };
    window.addEventListener("scroll", onScroll, {passive: true});
    window.addEventListener("resize", onScroll);
  }

  // 栏目是否还在视口附近。留半屏容差：矮视口下 #app 可能恰好从首屏下方开始，
  // 此时用户明明正看着本栏目，不能判成「已离开」而停掉刷新。
  function computeInView(){
    var app = $("app");
    if(!app) return true;
    var r = app.getBoundingClientRect();
    var vh = window.innerHeight || 800;
    var margin = Math.max(240, vh * 0.5);
    return r.bottom > -margin && r.top < vh + margin;
  }

  function updateActive(){
    var on = document.visibilityState !== "hidden" && S.inView;
    if(on === S.active) return;
    S.active = on;
    if(on){
      hidePause();
      poll();
      startTicker();
    }else{
      stopTimer();
      stopTicker();
      showPause();
      updateStatus();
    }
  }

  function showPause(){
    var box = $("pauseBox");
    if(box){
      box.hidden = false;
      box.innerHTML = '<b>已暂停刷新</b>：' +
        (document.visibilityState === "hidden"
          ? "浏览器标签页不可见，已停止请求行情。回到本页会自动恢复。"
          : "本栏目不在可视区域，已停止请求行情。滚动回栏目会自动恢复。") +
        '<span style="margin-left:auto;color:var(--text-3)">后端将在 20 秒内断开行情连接</span>';
    }
    var cd = $("cd");
    if(cd) cd.textContent = "已暂停";
  }

  function hidePause(){
    var box = $("pauseBox");
    if(box) box.hidden = true;
  }

  function schedule(ms){
    clearTimeout(S.timer);
    S.nextAt = Date.now() + ms;
    S.timer = setTimeout(function(){ poll(); }, ms);
  }

  function poll(){
    if(!isActive()) return;
    clearTimeout(S.timer);
    var url = API + "/state?focus=" + encodeURIComponent(S.focus || "") + "&_=" + Date.now();
    var ctrl = ("AbortController" in window) ? new AbortController() : null;
    var to = setTimeout(function(){ if(ctrl) ctrl.abort(); }, 12000);
    fetch(url, {cache: "no-store", signal: ctrl ? ctrl.signal : undefined})
      .then(function(r){
        if(r.status === 404) throw {code: 404};
        if(!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function(j){
        clearTimeout(to);
        if(!j.ok) throw new Error(j.error || "接口返回异常");
        S.netFail = 0;
        onState(j);
      })
      .catch(function(e){
        clearTimeout(to);
        onError(e);
      });
  }

  function onError(e){
    S.netFail++;
    if(e && e.code === 404){ renderNoService(); return; }
    S.lastError = (e && (e.message || e.code)) || "网络错误";
    if(!S.ready){
      $("app").className = "loading";
      $("app").innerHTML = "正在连接行情服务…<br><span style='font-size:12px;color:var(--text-3)'>" +
        esc(S.lastError) + "</span>";
    }else{
      showNetError("行情连接中断：" + S.lastError + "，正在自动重试…");
      updateStatus();
    }
    if(isActive()) schedule(Math.min(3000 * S.netFail, 15000));
  }

  function showNetError(msg){
    var box = $("errBox");
    if(box){ box.hidden = false; box.textContent = msg; }
  }
  function hideNetError(){
    var box = $("errBox");
    if(box) box.hidden = true;
  }

  function onState(j){
    S.data = j;
    var off = num(j.server_time_ms);
    if(off !== null) S.srvOffset = off - Date.now();
    S.lastStateAt = Date.now();
    S.catalog = j.catalog || [];
    S.quotes = j.quotes || [];
    S.qmap = {};
    S.quotes.forEach(function(q){ S.qmap[q.coin] = q; });
    if(!S.focus || !S.quotes.some(function(q){ return q.coin === S.focus; })){
      S.focus = pickDefaultFocus();
      S.focusSync = true;   // 首屏选中标的后立刻重询一次，让后端订阅它的 l2Book
    }
    if(!S.ready && S.catalog.length){
      renderPage();
      S.ready = true;
    }
    if(!S.ready){
      // 目录尚未就绪：稍快重试，让首屏尽快出来
      S.bootTries++;
      $("app").className = "loading";
      var hint = "已注册观察者，正在拉取市场目录与订阅盘口";
      if(S.bootTries > 20){
        hint = "行情目录为空：" + (j.catalog_error || "交易所未返回匹配市场") + "（仍在重试）";
      }
      $("app").innerHTML = "正在建立 Hyperliquid 行情连接…<br><span style='font-size:12px;color:var(--text-3)'>" +
        esc((j.conn && j.conn.status) === "error" ? ("连接失败：" + (j.conn.error || "未知错误")) : hint) + "</span>";
      updateStatus();
      if(isActive()) schedule(POLL_BOOT);
      return;
    }
    hideNetError();
    updateStatus();
    if(Object.keys(S.rows).length !== S.quotes.length) buildRows();
    renderKpis();
    syncTable();
    renderDetail();
    renderSeriesChart();
    if(S.candleCoin !== S.focus || Date.now() - S.candleAt > 60000) loadCandles(false);
    loadSparklines();
    if(isActive()){
      if(S.focusSync){ S.focusSync = false; schedule(500); }
      else schedule(POLL);
    }
  }

  function pickDefaultFocus(){
    if(!S.quotes.length) return S.focus;
    // 默认选中成交额最大的美股，避免 BTC 的主市场成交量把首屏带到加密品种
    var best = null;
    S.quotes.forEach(function(q){
      if(q.group !== "美股") return;
      if(!best || (num(q.day_ntl_vlm) || 0) > (num(best.day_ntl_vlm) || 0)) best = q;
    });
    if(!best) best = S.quotes[0];
    return best.coin;
  }

  function startTicker(){
    stopTicker();
    S.ticker = setInterval(function(){
      if(!isActive()) return;
      tickAges();
      var cd = $("cd");
      if(cd){
        var left = Math.max(0, Math.ceil((S.nextAt - Date.now()) / 1000));
        cd.textContent = left + "s";
      }
    }, 1000);
  }
  function stopTicker(){ if(S.ticker){ clearInterval(S.ticker); S.ticker = null; } }
  function stopTimer(){ clearTimeout(S.timer); S.timer = null; }

  // ---------------------------------------------------------------- 状态徽标
  function updateStatus(){
    var badge = $("liveBadge"), text = $("liveText");
    if(!badge || !text) return;
    var conn = (S.data && S.data.conn) || {};
    var status = conn.status || "idle";
    var mode = "idle", label = "连接中";
    if(!S.active){ mode = "paused"; label = "已暂停"; }
    else if(status === "live"){ mode = "live"; label = "实时行情"; }
    else if(status === "error"){ mode = "err"; label = "连接异常"; }
    else if(status === "idle"){ mode = "paused"; label = "等待连接"; }
    badge.className = "live-badge" + (mode === "live" ? "" : (mode === "err" ? " err" : " paused"));
    text.textContent = label;
    badge.title = (S.data && S.data.conn && S.data.conn.error) || "";
  }

  // ---------------------------------------------------------------- 首次渲染
  function renderPage(){
    var groups = {};
    S.catalog.forEach(function(m){ groups[m.group] = (groups[m.group] || 0) + 1; });
    var groupNames = Object.keys(groups);
    var groupSeg = "";
    if(groupNames.length > 1){
      groupSeg = '<div class="seg" id="groupSeg"><button data-g="all" class="on">全部</button>' +
        groupNames.map(function(g){ return '<button data-g="' + esc(g) + '">' + esc(g) + '</button>'; }).join("") +
        '</div>';
    }

    var html = '';
    html += '<div class="page-head"><div><h1>暗盘行情 <span>美股 · 商品 · 24 小时连续报价</span></h1>' +
      '<div class="page-sub" style="text-align:left">Hyperliquid 的 24 小时暗盘：美股永续 + 黄金 / 白银 / 布伦特油 / WTI 油 + 主交易所比特币，含最新成交、24h 迷你走势与选中标的 K 线；无成交时保留真实成交时间。</div></div>' +
      '<div class="page-sub">数据源 Hyperliquid · 公开读取无需账户<br>页面可见时 5 秒刷新，切走即停</div></div>';

    html += '<div class="kpi-grid" id="kpiGrid"></div>';
    html += '<div class="insight" id="insight"><b>当前</b><span>正在汇总行情…</span></div>';
    html += '<div class="err" id="errBox" hidden></div>';
    html += '<div class="pause-box" id="pauseBox" hidden></div>';

    html += '<div class="trade-layout">';

    html += '<section class="card" data-toc="行情总览" data-toc-group="实时行情">' +
      '<div class="sec-head"><div><div class="sec-title">行情总览</div>' +
      '<div class="sec-sub">按 24h 成交额排序 · 点击任意标的，右侧看 24h 走势与盘口</div></div>' +
      '<div class="sec-right"><span class="refresh-ring">下次刷新 <b id="cd">5s</b></span></div></div>' +
      '<div class="toolbar">' +
        '<input id="qInput" type="text" autocomplete="off" placeholder="搜索代码 / 名称，如 NVDA、黄金" />' +
        groupSeg +
        '<div class="seg" id="modeSeg"><button data-m="all" class="on">全部</button><button data-m="live">仅成交中</button><button data-m="stale">仅停滞/无报价</button></div>' +
        '<span class="spacer"></span><span class="cnt" id="tblCnt">—</span>' +
      '</div>' +
      '<div class="tbl-wrap"><table class="tbl" id="qTbl"><thead><tr>' +
        '<th>标的</th>' +
        '<th class="sortable" data-k="last">最新成交</th>' +
        '<th class="sortable" data-k="chg">24h 涨跌</th>' +
        '<th class="sortable" data-k="prevChg" title="上一美股交易日 16:00 ET 收盘 vs 前一交易日">前一日</th>' +
        '<th>24h 走势</th>' +
        '<th class="sortable" data-k="vol" data-dir="-1">24h 成交额</th>' +
        '<th class="sortable col-oi" data-k="oi">持仓量</th>' +
        '<th class="sortable" data-k="tradeAge">最后成交</th>' +
        '<th>更新状态</th>' +
      '</tr></thead><tbody id="qBody"></tbody></table></div>' +
      '<div class="note"><b>读数规则</b>：最新成交价来自 <b>trades</b> 推送，没有新成交时价格保持不变；' +
        '「更新状态」合并了报价更新时间与状态（成交中 / 报价中 / 报价停滞 / 无报价）。' +
        '买一、卖一、中间价、价差已移到右侧选中标的卡片；「24h 走势」为 1 小时收盘迷你折线，每 5 分钟刷新，红涨绿跌。</div>' +
      '</section>';

    html += '<section class="card side-card" data-toc="选中标的 · 24h 走势" data-toc-group="实时行情">' +
      '<div class="sec-head"><div><div class="sec-title" id="detailTitle">选中标的</div>' +
      '<div class="sec-sub" id="candleSub">5 分钟 K 线 · 交易所成交价</div></div>' +
      '<div class="sec-right"><span class="badge gray" id="detailCoin">—</span></div></div>' +
      '<div id="sideQuote"><div class="skel" style="height:90px;margin:12px 0"></div></div>' +
      '<div class="chart side-chart" id="candleChart"></div>' +
      '<div id="sideMeta"></div>' +
      '</section>';

    html += '</div>';

    html += '<div class="grid2 book-grid">';
    html += '<section class="card" data-toc="盘口深度" data-toc-group="实时行情">' +
      '<div class="sec-head"><div><div class="sec-title">盘口深度</div>' +
      '<div class="sec-sub">买盘 / 卖盘前 8 档 · l2Book 实时推送</div></div></div>' +
      '<div id="depthBody"><div class="note" style="border:0;margin:0;padding:0">等待盘口推送…</div></div></section>';
    html += '<section class="card" data-toc="逐笔成交" data-toc-group="实时行情">' +
      '<div class="sec-head"><div><div class="sec-title">逐笔成交</div>' +
      '<div class="sec-sub">最近 16 笔 · trades 实时推送</div></div></div>' +
      '<div id="tapeBody"><div class="note" style="border:0;margin:0;padding:0">等待成交推送…</div></div></section>';
    html += '</div>';

    html += '<section class="card" data-toc="会话快照" data-toc-group="实时行情">' +
      '<div class="sec-head"><div><div class="sec-title">会话快照</div>' +
      '<div class="sec-sub">页面可见期间每 5 秒记录一次中间价与最新成交价；切走后不再记录</div></div>' +
      '<div class="sec-right"><span class="cnt" id="snapInfo">—</span></div></div>' +
      '<div class="chart sm" id="seriesChart"></div>' +
      '</section>';

    html += '<section class="card" data-toc="数据说明" data-toc-group="说明">' +
      '<div class="sec-head"><div><div class="sec-title">数据说明与口径</div></div></div>' +
      '<div class="note" style="border-top:0;padding-top:0;margin-top:0;font-size:12px">' +
      '<b>数据源</b>：Hyperliquid HIP-3 市场（DEX 前缀 <b>xyz</b>），公开 REST <code>api.hyperliquid.xyz/info</code> 与 ' +
      'WebSocket <code>api.hyperliquid.xyz/ws</code>，无需 API Key、无需连接钱包。HIP-3 标的必须带 DEX 前缀（如 ' +
      '<code>xyz:NVDA</code>），不能只用 <code>NVDA</code>。<br>' +
      '<b>报价口径</b>：最新成交 = <code>trades</code> 推送的成交价与成交时间；买一/卖一 = <code>bbo</code> 推送；' +
      '中间价 = 买卖中值；价差 =（卖一−买一）÷ 中间价，单位 bp；24h 涨跌 = 最新成交 ÷ 交易所 <code>prevDayPx</code> − 1；' +
      '前一日 = 上一美股交易日 16:00 ET 收盘 ÷ 再前一交易日 − 1（周末保持不变，便于看周五收盘）；' +
      '24h 成交额 = 交易所 <code>dayNtlVlm</code>；24h 走势 = <code>candleSnapshot</code> 的 1 小时收盘迷你折线，每 5 分钟刷新。颜色沿用本站「红涨绿跌」。<br>' +
      '<b>刷新机制</b>：页面只在可见时每 5 秒请求一次同源行情快照；切换到其他栏目、标签页隐藏或栏目滚出视口后停止请求。' +
      '后端只有在最近 20 秒收到过页面心跳时才保持 WebSocket，否则主动断开，不在后台常驻采集。<br>' +
      '<b>快照留存</b>：每次快照写入 <code>data/us_dark/snapshots/YYYYMMDD.jsonl</code>，最新全量写入 ' +
      '<code>data/us_dark/latest.json</code>，保留 7 天。<br>' +
      '<b>已知局限</b>：这是永续合约的链上报价，<b>不是正股 / 现货成交价</b>；股票休市时流动性显著变薄、价差走阔，' +
      '商品（黄金 / 白银 / 原油）为 24 小时合约、数量单位随品种（盎司 / 桶），与股票共用同一 DEX 与清算；' +
      '比特币为主交易所加密永续（无 DEX 前缀，代码 <code>BTC</code>），仅作 24 小时风险资产对照。' +
      '标的清单为人工维护（见 <code>config.py</code> 的 <code>US_DARK_MARKETS</code>），' +
      '已下架市场会被自动过滤。「持仓量」是合约张数对应的标的数量，不是交易所库存。<br>' +
      '<b>不做的事</b>：不补点、不插值、不用 markPx 冒充成交价；没有真实成交就明确显示最后成交时间。' +
      '</div></section>';

    html += '<div class="foot"><b>数据来源</b>：Hyperliquid 公开行情接口（HIP-3 <code>xyz</code> DEX）· ' +
      '本地服务按需采集并每 5 秒落快照。本页仅用于研究参考，不构成投资建议。</div>';

    var app = $("app");
    app.className = "";
    app.innerHTML = html;

    bindEvents();
    if(window.mountToc){
      window.mountToc({app: "#app", bar: "#tocBar", side: "#tocSide", prefix: "us"});
    }
    buildRows();
    initCharts();
  }

  function bindEvents(){
    var input = $("qInput");
    if(input){
      input.oninput = function(){ S.q = input.value.trim().toLowerCase(); applyFilter(); };
    }
    var gs = $("groupSeg");
    if(gs){
      gs.onclick = function(e){
        var b = e.target.closest("button[data-g]");
        if(!b) return;
        Array.prototype.forEach.call(gs.querySelectorAll("button"), function(x){ x.classList.toggle("on", x === b); });
        S.group = b.getAttribute("data-g");
        applyFilter();
      };
    }
    var ms = $("modeSeg");
    if(ms){
      ms.onclick = function(e){
        var b = e.target.closest("button[data-m]");
        if(!b) return;
        Array.prototype.forEach.call(ms.querySelectorAll("button"), function(x){ x.classList.toggle("on", x === b); });
        S.mode = b.getAttribute("data-m");
        applyFilter();
      };
    }
    var tbl = $("qTbl");
    if(tbl){
      tbl.querySelector("thead").onclick = function(e){
        var th = e.target.closest("th.sortable");
        if(!th) return;
        var k = th.getAttribute("data-k");
        if(S.sortKey === k){ S.sortDir = -S.sortDir; }
        else{ S.sortKey = k; S.sortDir = (k === "last" || k === "mid" || k === "vol" || k === "oi") ? -1 : 1; }
        syncSortHeaders();
        applyFilter();
      };
      tbl.querySelector("tbody").onclick = function(e){
        var tr = e.target.closest("tr[data-coin]");
        if(!tr) return;
        selectCoin(tr.getAttribute("data-coin"));
      };
    }
    var tb = $("themeToggle");
    if(tb) tb.onclick = function(){ applyTheme(theme === "dark" ? "light" : "dark", true); };
  }

  // ---------------------------------------------------------------- KPI
  function kpi(k, v, unit, sub){
    return '<div class="kpi"><div class="k"><span>' + k + '</span></div><div class="v">' + v +
      (unit ? '<small>' + unit + '</small>' : '') + '</div><div class="s">' + sub + '</div></div>';
  }

  function renderKpis(){
    var grid = $("kpiGrid");
    if(!grid || !S.data) return;
    var conn = S.data.conn || {};
    var active = 0, volSum = 0, liveVol = 0;
    S.quotes.forEach(function(q){
      if(statusOf(q) === "live"){ active++; liveVol += num(q.day_ntl_vlm) || 0; }
      volSum += num(q.day_ntl_vlm) || 0;
    });
    var statusText = S.active ? "实时" : "已暂停";
    var statusColor = S.active ? "var(--pos)" : "var(--text-3)";
    if(S.active && conn.status === "error"){ statusText = "异常"; statusColor = "var(--cau)"; }
    else if(S.active && conn.status !== "live"){ statusText = "连接中"; statusColor = "var(--text-3)"; }

    var h = "";
    h += kpi("行情连接", '<span style="color:' + statusColor + ';font-size:19px">' + statusText + '</span>', "",
      "WS 订阅 <b>" + (conn.subscriptions || 0) + "</b> · 心跳 <b>" + ((S.data.config || {}).ttl_sec || 20) + " 秒</b>");
    h += kpi("覆盖标的", String(S.catalog.length), "个", "美股 + 商品 · Hyperliquid");
    h += kpi("近 60 秒有成交", active + " / " + S.quotes.length, "",
      active ? ("其余 " + (S.quotes.length - active) + " 个无新成交，价格保持上次成交价") : "全部无新成交，价格不跳动");
    h += kpi("24h 成交额合计", fmtUsd(volSum), "", "成交中标的 <b>" + fmtUsd(liveVol) + "</b>");
    h += kpi("快照序号", String((S.data.snapshot || {}).seq || 0), "",
      "每 <b>" + ((S.data.snapshot || {}).interval_sec || 5) + " 秒</b> · 最后 " +
      (S.data.snapshot && S.data.snapshot.at_ms ? fmtClock(S.data.snapshot.at_ms) : "—"));
    grid.innerHTML = h;

    var focusQ = S.quotes.filter(function(q){ return q.coin === S.focus; })[0];
    var ins = $("insight");
    if(ins){
      var txt;
      if(focusQ){
        var st = STATUS[statusOf(focusQ)];
        txt = esc(focusQ.name) + "（" + esc(focusQ.symbol) + "）最新成交 <b>" +
          (focusQ.last_px === null ? "—" : "$" + fmtPx(focusQ.last_px)) + "</b>" +
          (focusQ.chg_pct === null ? "" : "（<span class=\"" + cls(focusQ.chg_pct) + "\">" + fmtPct(focusQ.chg_pct) + "</span>）") +
          "，最后成交 " + agoText(focusQ.last_trade_ms) + "，买一 " +
          (focusQ.bid === null ? "—" : fmtPx(focusQ.bid)) + " / 卖一 " +
          (focusQ.ask === null ? "—" : fmtPx(focusQ.ask)) +
          "，状态「" + st.label + "」。全表 " + active + " 个标的有新成交，" +
          (S.quotes.length - active) + " 个无新成交（价格不变、只显示时间）。";
      }else{
        txt = "等待行情数据…";
      }
      ins.innerHTML = '<b>当前</b><span>' + txt + '</span>';
    }
  }

  // ---------------------------------------------------------------- 表格
  function buildRows(){
    var body = $("qBody");
    if(!body) return;
    body.innerHTML = "";
    S.rows = {}; S.ageEls = {};
    S.quotes.forEach(function(q){
      var tr = document.createElement("tr");
      tr.setAttribute("data-coin", q.coin);
      tr.innerHTML =
        '<td><div class="c-name"><b>' + esc(q.name) + '</b><span>' + esc(q.symbol) + '</span></div></td>' +
        '<td data-f="last"><span class="pxv">—</span></td>' +
        '<td data-f="chg" class="flat">—</td>' +
        '<td data-f="prevChg" class="flat">—</td>' +
        '<td data-f="spark" class="spark-cell"><span class="spark-empty">—</span></td>' +
        '<td data-f="vol">—</td>' +
        '<td data-f="oi" class="col-oi">—</td>' +
        '<td data-f="tradeAge"><div class="c-time"><b>—</b><span>—</span></div></td>' +
        '<td data-f="quote"><div class="c-time"><b class="qbadge"><span class="badge gray">—</span></b><span class="qage">—</span></div></td>';
      body.appendChild(tr);
      S.rows[q.coin] = tr;
      S.ageEls[q.coin] = {
        tradeAgo: tr.querySelector('[data-f="tradeAge"] b'),
        tradeClock: tr.querySelector('[data-f="tradeAge"] span'),
        quoteB: tr.querySelector('[data-f="quote"] .qbadge'),
        quoteS: tr.querySelector('[data-f="quote"] .qage')
      };
    });
    renderSparklines();
    renderPrevDayCells();
    syncSortHeaders();
  }

  function syncSortHeaders(){
    var tbl = $("qTbl");
    if(!tbl) return;
    Array.prototype.forEach.call(tbl.querySelectorAll("th.sortable"), function(th){
      var on = th.getAttribute("data-k") === S.sortKey;
      th.classList.toggle("on", on);
      if(on) th.setAttribute("data-dir", S.sortDir > 0 ? "1" : "-1");
      else th.removeAttribute("data-dir");
    });
  }

  function syncTable(){
    if(!S.rows || !S.quotes.length) return;
    S.quotes.forEach(function(q){
      var tr = S.rows[q.coin];
      if(!tr) return;
      var prevMs = S.prevTradeMs[q.coin];
      var newTrade = q.last_trade_ms && prevMs && q.last_trade_ms !== prevMs;
      var px = q.last_px;
      var pxCell = tr.querySelector('[data-f="last"] .pxv');
      if(pxCell){
        var text = px === null ? "—" : "$" + fmtPx(px);
        if(pxCell.textContent !== text) pxCell.textContent = text;
        if(newTrade && S.prevPx[q.coin] !== undefined && px !== null && S.prevPx[q.coin] !== null){
          var dir = px >= S.prevPx[q.coin] ? "flash-up" : "flash-down";
          pxCell.classList.remove("flash-up", "flash-down");
          void pxCell.offsetWidth;
          pxCell.classList.add(dir);
        }
      }
      tr.querySelector('[data-f="chg"]').innerHTML = q.chg_pct === null ? "—" : fmtPct(q.chg_pct);
      tr.querySelector('[data-f="chg"]').className = cls(q.chg_pct);
      var pc = tr.querySelector('[data-f="prevChg"]');
      if(pc) pc.innerHTML = prevDayHtml(q.coin);
      tr.querySelector('[data-f="vol"]').textContent = fmtUsd(q.day_ntl_vlm);
      tr.querySelector('[data-f="oi"]').textContent = fmtQty(q.open_interest);
      updateAgeCells(q);
      updateQuoteCell(q);
      S.prevTradeMs[q.coin] = q.last_trade_ms;
      if(px !== null) S.prevPx[q.coin] = px;
    });
    trStatusClass();
    applyFilter();
  }

  function updateAgeCells(q){
    var a = S.ageEls[q.coin];
    if(!a) return;
    a.tradeAgo.textContent = agoText(q.last_trade_ms);
    a.tradeClock.textContent = q.last_trade_ms ? fmtClock(q.last_trade_ms) : "无成交";
  }

  // 「更新状态」= 状态徽标 + 报价更新时间，两列合一
  function updateQuoteCell(q){
    var a = S.ageEls[q.coin];
    if(!a || !a.quoteB) return;
    var st = STATUS[statusOf(q)] || STATUS.none;
    var html = '<span class="badge ' + st.cls + '">' + st.label + '</span>';
    if(a.quoteB.innerHTML !== html) a.quoteB.innerHTML = html;
    var text = q.bbo_ms ? (agoText(q.bbo_ms) + " · " + fmtClock(q.bbo_ms)) : "无报价";
    if(a.quoteS.textContent !== text) a.quoteS.textContent = text;
  }

  function tickAges(){
    if(!S.ready) return;
    S.quotes.forEach(function(q){
      updateAgeCells(q);
      updateQuoteCell(q);
    });
    trStatusClass();
  }

  function trStatusClass(){
    S.quotes.forEach(function(q){
      var tr = S.rows[q.coin];
      if(tr) tr.classList.toggle("sel", q.coin === S.focus);
    });
  }

  function rowVisible(q){
    if(S.q){
      var hay = (q.symbol + " " + q.name + " " + q.coin).toLowerCase();
      if(hay.indexOf(S.q) < 0) return false;
    }
    if(S.mode === "live" && statusOf(q) !== "live") return false;
    if(S.mode === "stale" && statusOf(q) !== "stale" && statusOf(q) !== "none") return false;
    if(S.group && S.group !== "all" && q.group !== S.group) return false;
    return true;
  }

  function sortVal(q, k){
    switch(k){
      case "last": return num(q.last_px);
      case "chg": return num(q.chg_pct);
      case "prevChg": var pv = S.prevDay[q.coin]; return pv ? num(pv.chg) : null;
      case "mid": return num(q.mid);
      case "spread": return num(q.spread_bp);
      case "vol": return num(q.day_ntl_vlm);
      case "oi": return num(q.open_interest);
      case "tradeAge": return q.last_trade_ms ? -q.last_trade_ms : -Infinity;
      case "bookAge": return q.bbo_ms ? -q.bbo_ms : -Infinity;
      default: return 0;
    }
  }

  function applyFilter(){
    var body = $("qBody");
    if(!body) return;
    var shown = 0, list = [];
    S.quotes.forEach(function(q){
      var vis = rowVisible(q);
      var tr = S.rows[q.coin];
      if(tr) tr.style.display = vis ? "" : "none";
      if(vis){ shown++; list.push(q); }
    });
    list.sort(function(a, b){
      var va = sortVal(a, S.sortKey), vb = sortVal(b, S.sortKey);
      var na = (va === null || va === undefined || !isFinite(va)), nb = (vb === null || vb === undefined || !isFinite(vb));
      if(na && nb) return 0;
      if(na) return 1;
      if(nb) return -1;
      return (va - vb) * S.sortDir;
    });
    var frag = document.createDocumentFragment();
    list.forEach(function(q){ if(S.rows[q.coin]) frag.appendChild(S.rows[q.coin]); });
    // 隐藏行保持在表尾，避免 DOM 反复搬家
    S.quotes.forEach(function(q){ if(!rowVisible(q) && S.rows[q.coin]) frag.appendChild(S.rows[q.coin]); });
    body.appendChild(frag);
    var cnt = $("tblCnt");
    if(cnt) cnt.textContent = S.q || S.mode !== "all" ? ("筛选 " + shown + " / " + S.quotes.length + " 个") : ("共 " + S.quotes.length + " 个标的");
  }

  function selectCoin(coin){
    if(!coin || coin === S.focus) return;
    S.focus = coin;
    S.userFocus = true;
    S.candles = null;
    S.candleCoin = null;
    trStatusClass();
    renderDetail();
    renderSeriesChart();
    renderCandleChart();
    loadCandles(true);
    poll();
  }

  // ---------------------------------------------------------------- 详情
  function renderDetail(){
    var side = $("sideQuote"), meta = $("sideMeta");
    if(!side) return;
    var f = (S.data && S.data.focus) || {};
    var ownFocus = f.coin === S.focus;
    var q = S.quotes.filter(function(x){ return x.coin === S.focus; })[0] || (ownFocus ? f.quote : null);
    var coinEl = $("detailCoin");
    if(coinEl) coinEl.textContent = S.focus || "—";
    if(!q){
      side.innerHTML = '<div class="note">该标的暂无行情。</div>';
      if(meta) meta.innerHTML = "";
      renderBook(null, []);
      return;
    }
    var title = $("detailTitle");
    if(title) title.innerHTML = esc(q.name) + ' <span style="font-size:11.5px;font-weight:500;color:var(--text-3);letter-spacing:0">' + esc(q.symbol) + ' · ' + esc(q.coin) + '</span>';

    var st = STATUS[statusOf(q)];
    var change = q.chg_pct === null ? "—" : fmtPct(q.chg_pct);
    var h = '';
    h += '<div class="sq-top"><span class="sq-price ' + cls(q.chg_pct) + '">' +
      (q.last_px === null ? "—" : "$" + fmtPx(q.last_px)) +
      '<small class="' + cls(q.chg_pct) + '">' + change + '</small></span>' +
      '<span class="badge ' + st.cls + '">' + st.label + '</span></div>';
    h += '<div class="sq-sub">最后成交 ' +
      (q.last_trade_ms ? fmtClock(q.last_trade_ms) + " · " + agoText(q.last_trade_ms) : "无") +
      (q.last_sz === null ? "" : " · 数量 " + fmtSz(q.last_sz)) + '</div>';
    h += '<div class="sq-grid">' +
      sqItem("买一", q.bid === null ? "—" : fmtPx(q.bid), q.bid_sz === null ? "无买盘" : "数量 " + fmtSz(q.bid_sz)) +
      sqItem("卖一", q.ask === null ? "—" : fmtPx(q.ask), q.ask_sz === null ? "无卖盘" : "数量 " + fmtSz(q.ask_sz)) +
      sqItem("中间价", q.mid === null ? "—" : fmtMid(q.mid),
        q.spread_bp === null ? "价差 —" : "价差 " + fmtNum(q.spread_bp, 1) + " bp") +
      sqItem("24h 成交额", fmtUsd(q.day_ntl_vlm), "持仓 " + fmtQty(q.open_interest)) +
      '</div>';
    side.innerHTML = h;

    if(meta){
      meta.innerHTML = '<div class="side-meta">' +
        '<span>报价更新 <b>' + (q.bbo_ms ? fmtClock(q.bbo_ms) + "（" + agoText(q.bbo_ms) + "）" : "无") + '</b></span>' +
        '<span>盘口深度 <b>' + (ownFocus && f.book && f.book.ms ? fmtClock(f.book.ms) + "（" + agoText(f.book.ms) + "）" : "无") + '</b></span>' +
        '<span>标记价 <b>' + (q.mark_px === null ? "—" : fmtPx(q.mark_px)) + '</b></span>' +
        '<span>预言机 <b>' + (q.oracle_px === null ? "—" : fmtPx(q.oracle_px)) + '</b></span>' +
        '<span>资金费率 <b>' + (q.funding === null ? "—" : (q.funding * 100).toFixed(4) + "%") + '</b></span>' +
        '</div>';
    }
    renderBook(ownFocus ? f.book : null, ownFocus ? (f.trades || []) : []);
  }

  function sqItem(k, v, s){
    return '<div class="sq-item"><div class="k"><span>' + k + '</span></div>' +
      '<div class="v">' + v + '</div><div class="s">' + esc(s || "") + '</div></div>';
  }

  function renderBook(book, trades){
    var d = $("depthBody"), t = $("tapeBody");
    if(d) d.innerHTML = depthHtml(book);
    if(t) t.innerHTML = tapeHtml(trades);
  }

  function depthHtml(book){
    if(!book || (!book.bids.length && !book.asks.length)){
      return '<div class="depth"><div class="depth-col"><h4>买盘</h4><div class="note" style="border:0;margin:0;padding:0">等待盘口推送…</div></div>' +
             '<div class="depth-col"><h4>卖盘</h4><div class="note" style="border:0;margin:0;padding:0">等待盘口推送…</div></div></div>';
    }
    var bids = (book.bids || []).slice(0, 8);
    var asks = (book.asks || []).slice(0, 8);
    var maxB = Math.max.apply(null, bids.map(function(r){ return r[1]; }).concat([1]));
    var maxA = Math.max.apply(null, asks.map(function(r){ return r[1]; }).concat([1]));
    function col(rows, max, kind){
      return rows.map(function(r){
        var w = Math.max(2, Math.min(100, r[1] / max * 100));
        return '<div class="depth-row ' + (kind === "ask" ? "depth-ask" : "depth-bid") + '">' +
          '<span class="bar ' + kind + '" style="width:' + w.toFixed(1) + '%"></span>' +
          '<span class="p">' + fmtPx(r[0]) + '</span><span class="s">' + fmtSz(r[1]) + '</span></div>';
      }).join("");
    }
    return '<div class="depth">' +
      '<div class="depth-col"><h4>买盘 Bid <span>价格 / 数量</span></h4>' + col(bids, maxB, "bid") + '</div>' +
      '<div class="depth-col"><h4>卖盘 Ask <span>价格 / 数量</span></h4>' + col(asks, maxA, "ask") + '</div>' +
      '</div>';
  }

  function tapeHtml(trades){
    if(!trades.length) return '<div class="tape-wrap"><div class="note" style="border:0;margin:0;padding:0">等待成交推送…</div></div>';
    var rows = trades.slice(0, 16).map(function(t){
      var buy = t.side === "B";
      return '<tr><td>' + fmtClock(t.t) + '</td>' +
        '<td class="' + (buy ? "up" : "down") + '">' + (buy ? "买" : "卖") + '</td>' +
        '<td>' + fmtPx(t.px) + '</td><td>' + fmtSz(t.sz) + '</td></tr>';
    }).join("");
    return '<div class="tape-wrap"><table class="tape"><thead><tr>' +
      '<th>时间</th><th>方向</th><th>成交价</th><th>数量</th></tr></thead><tbody>' +
      rows + '</tbody></table></div>';
  }

  // ---------------------------------------------------------------- 图表
  var theme = "light";

  function initCharts(){
    if(!window.echarts) return;
    var c1 = $("candleChart"), c2 = $("seriesChart");
    if(c1 && !S.charts.candle) S.charts.candle = echarts.init(c1);
    if(c2 && !S.charts.series) S.charts.series = echarts.init(c2);
    renderCandleChart();
    renderSeriesChart();
  }

  function disposeCharts(){
    Object.keys(S.charts).forEach(function(k){
      try{ S.charts[k].dispose(); }catch(e){}
      S.charts[k] = null;
    });
    S.charts = {};
  }

  function axisLabel(){ return {color: cssVar("--chart-axis-label"), fontSize: 10}; }
  function axisLine(){ return {lineStyle: {color: cssVar("--chart-axis-line")}}; }
  function splitLine(){ return {lineStyle: {color: cssVar("--chart-split")}}; }

  function renderCandleChart(){
    var ch = S.charts.candle;
    if(!ch) return;
    var cs = (S.candles && S.candles.candles) || [];
    var sub = $("candleSub");
    if(sub && S.candles){
      sub.textContent = "5 分钟 K 线 · 24 小时 · " + cs.length + " 根 · 更新于 " + fmtClock(S.candles.as_of_ms);
    }
    if(!cs.length){
      ch.clear();
      ch.setOption({
        graphic: [{type: "text", left: "center", top: "middle",
          style: {text: "K 线加载中…", fill: cssVar("--text-3"), fontSize: 12}}]
      });
      return;
    }
    var cats = cs.map(function(c){
      var d = new Date(c.t);
      return pad(d.getHours()) + ":" + pad(d.getMinutes());
    });
    var ohlc = cs.map(function(c){ return [c.o, c.c, c.l, c.h]; });
    var vols = cs.map(function(c, i){
      return {value: c.v, itemStyle: {color: c.c >= c.o ? cssVar("--up") : cssVar("--down")}};
    });
    var q = S.quotes.filter(function(x){ return x.coin === S.focus; })[0];
    var markData = [];
    if(q && q.prev_day_px){
      markData.push({yAxis: q.prev_day_px, label: {formatter: "昨收 " + fmtPx(q.prev_day_px), position: "insideEndTop",
        color: cssVar("--text-3"), fontSize: 10}});
    }
    var up = cssVar("--up"), down = cssVar("--down");
    ch.clear();
    ch.setOption({
      animation: false,
      grid: [
        {left: 58, right: 16, top: 18, height: "56%"},
        {left: 58, right: 16, top: "70%", height: "16%"}
      ],
      axisPointer: {link: [{xAxisIndex: "all"}]},
      tooltip: {
        trigger: "axis",
        axisPointer: {type: "cross"},
        backgroundColor: cssVar("--chart-tip-bg"),
        borderColor: cssVar("--chart-tip-bd"),
        textStyle: {color: cssVar("--chart-tip-fg"), fontSize: 11},
        formatter: function(ps){
          var p = ps[0], i = p.dataIndex, c = cs[i];
          return cats[i] + "<br>开 " + fmtPx(c.o) + " 高 " + fmtPx(c.h) +
            "<br>低 " + fmtPx(c.l) + " 收 " + fmtPx(c.c) +
            "<br>量 " + fmtQty(c.v);
        }
      },
      xAxis: [
        {type: "category", data: cats, gridIndex: 0, axisLine: axisLine(), axisTick: {show: false},
          axisLabel: axisLabel(), splitLine: {show: false}},
        {type: "category", data: cats, gridIndex: 1, axisLine: axisLine(), axisTick: {show: false},
          axisLabel: {show: false}, splitLine: {show: false}}
      ],
      yAxis: [
        {scale: true, gridIndex: 0, axisLine: {show: false}, axisLabel: axisLabel(), splitLine: splitLine()},
        {gridIndex: 1, axisLine: {show: false}, axisLabel: {show: false}, splitLine: {show: false}}
      ],
      dataZoom: [{type: "inside", xAxisIndex: [0, 1], start: 0, end: 100}],
      series: [
        {
          type: "candlestick", data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
          itemStyle: {color: up, color0: down, borderColor: up, borderColor0: down},
          markLine: {
            symbol: "none", silent: true, animation: false,
            lineStyle: {color: cssVar("--chart-mark"), type: "dashed", width: 1},
            data: markData
          }
        },
        {type: "bar", data: vols, xAxisIndex: 1, yAxisIndex: 1, barWidth: "60%"}
      ]
    }, true);
  }

  function renderSeriesChart(){
    var ch = S.charts.series;
    if(!ch) return;
    var f = (S.data && S.data.focus) || {};
    var pts = f.series || [];
    var info = $("snapInfo");
    if(info){
      info.textContent = "已记录 " + pts.length + " 个 5 秒快照" +
        (S.data ? " · 总计 " + ((S.data.snapshot || {}).seq || 0) + " 次" : "");
    }
    if(!pts.length){
      ch.clear();
      ch.setOption({
        graphic: [{type: "text", left: "center", top: "middle",
          style: {text: "仍在积累快照：页面可见时每 5 秒记录一次", fill: cssVar("--text-3"), fontSize: 12}}]
      });
      return;
    }
    var mid = [], last = [];
    pts.forEach(function(p){
      mid.push([p[0], p[1]]);
      last.push([p[0], p[2]]);
    });
    ch.clear();
    ch.setOption({
      animation: false,
      grid: {left: 58, right: 16, top: 20, bottom: 28},
      tooltip: {
        trigger: "axis",
        backgroundColor: cssVar("--chart-tip-bg"),
        borderColor: cssVar("--chart-tip-bd"),
        textStyle: {color: cssVar("--chart-tip-fg"), fontSize: 11},
        formatter: function(ps){
          var t = new Date(ps[0].value[0]);
          var s = fmtClock(ps[0].value[0]);
          ps.forEach(function(p){
            if(p.value && p.value[1] !== null) s += "<br>" + p.seriesName + " " + fmtPx(p.value[1]);
          });
          return s;
        }
      },
      legend: {top: 0, right: 0, textStyle: {color: cssVar("--text-3"), fontSize: 10},
        data: ["中间价", "最新成交"]},
      xAxis: {type: "time", axisLine: axisLine(), axisLabel: axisLabel(), splitLine: {show: false}},
      yAxis: {type: "value", scale: true, axisLine: {show: false}, axisLabel: axisLabel(), splitLine: splitLine()},
      series: [
        {name: "中间价", type: "line", data: mid, showSymbol: false, connectNulls: false,
          lineStyle: {width: 1.6, color: cssVar("--neu")}, itemStyle: {color: cssVar("--neu")}},
        {name: "最新成交", type: "line", data: last, showSymbol: false, connectNulls: false, step: "end",
          lineStyle: {width: 1.2, color: cssVar("--brand")}, itemStyle: {color: cssVar("--brand")}}
      ]
    }, true);
  }

  function loadCandles(force){
    if(!S.focus) return;
    var coin = S.focus;
    if(!force && S.candleCoin === coin && Date.now() - S.candleAt < 60000) return;
    fetch(API + "/candles?coin=" + encodeURIComponent(coin) + "&interval=5m&hours=24")
      .then(function(r){ return r.json(); })
      .then(function(j){
        if(!j.ok) throw new Error(j.error || "K 线接口异常");
        if(coin !== S.focus) return;
        S.candles = j; S.candleCoin = coin; S.candleAt = Date.now();
        renderCandleChart();
      })
      .catch(function(e){
        if(coin !== S.focus) return;
        var sub = $("candleSub");
        if(sub) sub.textContent = "K 线加载失败：" + (e.message || e);
      });
  }

  // ---------------------------------------------------------------- 迷你走势
  function sparkSvg(points, tone){
    if(!points || points.length < 2) return '<span class="spark-empty">—</span>';
    var w = 88, h = 26, pad = 2;
    var min = Math.min.apply(null, points), max = Math.max.apply(null, points);
    var span = (max - min) || 1;
    var step = (w - pad * 2) / (points.length - 1);
    var pts = points.map(function(v, i){
      return [pad + i * step, h - pad - (v - min) / span * (h - pad * 2)];
    });
    var d = pts.map(function(p, i){
      return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1);
    }).join(" ");
    var area = d + " L" + (w - pad).toFixed(1) + " " + (h - pad) +
      " L" + pad.toFixed(1) + " " + (h - pad) + " Z";
    var seriesChg = points[0] ? (points[points.length - 1] / points[0] - 1) * 100 : null;
    var hasTone = (tone !== null && tone !== undefined && isFinite(tone));
    var dir = hasTone ? tone : seriesChg;
    var color = (dir === null || Math.abs(dir) < 1e-9) ? "var(--text-3)" :
      (dir > 0 ? "var(--up)" : "var(--down)");
    var last = pts[pts.length - 1];
    var tip = "24h 迷你走势 · 24h 涨跌 " + fmtPct(hasTone ? tone : seriesChg) +
      " · 区间 " + fmtPx(min) + " / " + fmtPx(max);
    return '<svg class="spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" ' +
      'role="img" aria-label="' + esc(tip) + '">' +
      '<title>' + esc(tip) + '</title>' +
      '<path d="' + area + '" style="fill:' + color + '" opacity=".10"/>' +
      '<path d="' + d + '" fill="none" style="stroke:' + color + '" stroke-width="1.4" ' +
      'stroke-linejoin="round" stroke-linecap="round"/>' +
      '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="1.7" style="fill:' + color + '"/>' +
      '</svg>';
  }

  function renderSparklines(){
    if(!S.ready) return;
    Object.keys(S.rows).forEach(function(coin){
      var tr = S.rows[coin];
      if(!tr) return;
      var cell = tr.querySelector('[data-f="spark"]');
      var q = S.qmap && S.qmap[coin];
      if(cell) cell.innerHTML = sparkSvg(S.spark[coin], q ? q.chg_pct : null);
    });
  }

  var WEEK_CN = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  function weekdayCn(iso){
    if(!iso) return "";
    var d = new Date(iso + "T12:00:00");
    return isNaN(d.getTime()) ? "" : WEEK_CN[d.getDay()];
  }

  function prevDayHtml(coin){
    var p = S.prevDay && S.prevDay[coin];
    if(!p || p.chg === null || p.chg === undefined) return '<span class="flat">—</span>';
    var day = p.date ? p.date.slice(5) : "";
    var mark = p.split ? '<sup title="拆股已还原">*</sup>' : "";
    var tip = p.split
      ? ("检测到 " + p.split + ":1 拆股，已按拆股还原（原始 " + fmtPct(p.raw_chg) + "）")
      : (p.date + " 16:00 ET 收盘 vs 前一交易日");
    return '<div class="pc" title="' + esc(tip) + '"><span class="' + cls(p.chg) + '">' +
      fmtPct(p.chg) + mark + '</span>' +
      '<span class="pc-d">' + esc(day) + ' ' + weekdayCn(p.date) + '</span></div>';
  }

  function renderPrevDayCells(){
    if(!S.ready) return;
    Object.keys(S.rows).forEach(function(coin){
      var tr = S.rows[coin];
      if(!tr) return;
      var cell = tr.querySelector('[data-f="prevChg"]');
      if(cell) cell.innerHTML = prevDayHtml(coin);
    });
  }

  function loadSparklines(force){
    if(!isActive() || S.sparkLoading) return;
    if(!force && S.sparkAt && Date.now() - S.sparkAt < 300000) return;
    S.sparkLoading = true;
    var ctrl = ("AbortController" in window) ? new AbortController() : null;
    var to = setTimeout(function(){ if(ctrl) ctrl.abort(); }, 40000);
    fetch(API + "/sparklines?hours=24&_=" + Date.now(),
          {cache: "no-store", signal: ctrl ? ctrl.signal : undefined})
      .then(function(r){ return r.json(); })
      .then(function(j){
        clearTimeout(to);
        if(j.ok){
          S.spark = j.series || {};
          S.prevDay = j.prev_day || {};
          S.prevSession = j.session || null;
          S.sparkAt = Date.now();
          renderSparklines();
          renderPrevDayCells();
        }else{
          S.sparkAt = Date.now() - 240000;
        }
      })
      .catch(function(){
        clearTimeout(to);
        S.sparkAt = Date.now() - 240000;
      })
      .then(function(){ S.sparkLoading = false; });
  }

  // ---------------------------------------------------------------- 主题
  function applyTheme(t, refresh){
    theme = t === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", theme);
    try{
      localStorage.setItem("us_theme", theme);
      localStorage.setItem("fg_theme", theme);
    }catch(e){}
    var b = $("themeToggle");
    if(b){
      b.querySelector(".tt-ico").textContent = theme === "dark" ? "☀" : "◐";
      b.querySelector(".tt-label").textContent = theme === "dark" ? "浅色" : "暗色";
    }
    if(refresh){
      disposeCharts();
      initCharts();
    }
  }

  function readTheme(){
    var saved = "light";
    try{ saved = localStorage.getItem("us_theme") || localStorage.getItem("fg_theme") || "light"; }catch(e){}
    try{
      var q = new URLSearchParams(location.search).get("theme");
      if(q === "dark" || q === "light") saved = q;
    }catch(e){}
    applyTheme(saved, false);
  }

  // ---------------------------------------------------------------- 错误页
  function renderNoService(){
    var app = $("app");
    if(!app || S.ready) return;
    app.className = "";
    app.innerHTML = '<div class="err"><b>行情接口未就绪：/api/usdark/state 返回 404</b><br><br>' +
      '当前运行中的站点进程没有加载 <code>us_dark.py</code>。请关闭旧窗口后重新运行 <code>启动.cmd</code>' +
      '（或 <code>python start_site.py</code> / <code>python serve.py</code>），再刷新本页。<br><br>' +
      '<span style="color:var(--text-3);font-size:12px">本页其余内容无需数据构建；行情服务由站点进程内线程按需连接 Hyperliquid。</span></div>';
  }

  window.addEventListener("resize", function(){
    Object.keys(S.charts).forEach(function(k){ if(S.charts[k]) try{ S.charts[k].resize(); }catch(e){} });
  });

  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
