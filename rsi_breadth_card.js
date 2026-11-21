/* L4 大小盘 RSI 分化 · 独立渲染器（2026-11-21）
 * 消费 window.RSI_BREADTH（data/rsi_breadth.js），挂载到 mood.html 的 #rsiRoot。
 * 前缀 rb-，独立图表实例；缺失数据只提示本卡，不影响其它卡片。
 */
(function () {
  "use strict";
  var D = window.RSI_BREADTH;
  var root = document.getElementById("rsiRoot");
  if (!root) return;
  if (!D || !D.history || !D.history.length) {
    root.innerHTML = '<div class="card"><b>大小盘 RSI 分化</b> · 数据未就绪，请运行 <code>python build_rsi_breadth.py</code>。</div>';
    return;
  }
  if (typeof echarts === "undefined") return;

  var css = [
    '.rb-card{padding:18px 20px}',
    '.rb-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px}',
    '.rb-title{font-size:16px;font-weight:800;color:var(--ink)}',
    '.rb-sub{font-size:12px;color:var(--text-3)}',
    '.rb-kpi{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 4px}',
    '.rb-chip{background:var(--bg-soft);border:1px solid var(--line);border-radius:9px;padding:8px 12px;min-width:104px}',
    '.rb-chip .k{font-size:11px;color:var(--text-3)}',
    '.rb-chip .v{font-size:18px;font-weight:800;margin-top:1px;font-variant-numeric:tabular-nums}',
    '.rb-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}',
    '@media(max-width:900px){.rb-grid{grid-template-columns:1fr}}',
    '.rb-chart{width:100%;height:260px}',
    '.rb-note{font-size:11.5px;color:var(--text-3);line-height:1.8;margin-top:8px}'
  ].join("\n");
  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  function cv(name, fb) {
    try {
      var v = getComputedStyle(document.body).getPropertyValue(name);
      return (v && v.trim()) || fb;
    } catch (e) { return fb; }
  }
  var axisLine = cv("--line", "#e3e6ec"), textC = cv("--text-3", "#8a93a0");
  var C1 = "#2f6fd0", C2 = "#ff5b2e";

  var T = D.today, H = D.history;
  var dates = H.map(function (x) { return x.date; });
  var hs = H.map(function (x) { return x.h; });
  var md = H.map(function (x) { return x.m; });
  var sp = H.map(function (x) { return x.s; });

  var signColor = T.spread >= 0 ? "#e23b2e" : "#12864f";
  var pctTxt = (T.spread_pct === null || T.spread_pct === undefined) ? "—" : T.spread_pct.toFixed(0) + "%";
  var html = '';
  html += '<div class="card rb-card" data-toc="大小盘 RSI 分化" data-toc-group="结构观察" id="toc_rsi_split">';
  html += '<div class="rb-head"><span class="rb-title">大小盘 RSI 分化</span>' +
    '<span class="rb-sub">大盘＝沪深300（市值加权）｜小盘＝全市场 ' + (T.n_stocks || 0) +
    ' 只个股 RSI14 的横截面中位数（等权）· 数据 ' + T.date + '</span></div>';
  html += '<div class="rb-kpi">' +
    '<div class="rb-chip"><div class="k">沪深300 RSI</div><div class="v" style="color:' + C1 + '">' + T.hs300.toFixed(1) + '</div></div>' +
    '<div class="rb-chip"><div class="k">全市场中位数</div><div class="v" style="color:' + C2 + '">' + T.median.toFixed(1) + '</div></div>' +
    '<div class="rb-chip"><div class="k">差值（大−小）</div><div class="v" style="color:' + signColor + '">' + (T.spread > 0 ? "+" : "") + T.spread.toFixed(1) + '</div></div>' +
    '<div class="rb-chip"><div class="k">差值 3 年分位</div><div class="v">' + pctTxt + '</div></div>' +
    '</div>';
  html += '<div class="rb-grid"><div id="rbChart1" class="rb-chart"></div><div id="rbChart2" class="rb-chart"></div></div>';
  html += '<div class="rb-note"><b>怎么读：</b>差值为正且处高分位＝大盘相对小盘超买（抱团）；为负且处低分位＝小盘相对活跃。' +
    '差值比两个绝对水平更稳——它天然抵消了全市场的同涨同跌。' +
    '个股为未复权收盘价，已剔除单日 |涨跌| &gt; 21% 的除权跳变及其后 14 日；沪深300 也是价格指数（不含分红），两者都带除权季节性。' +
    '本卡为观察层，<b>不进 8 指标综合分、不改共振板</b>。</div>';
  html += '</div>';
  root.innerHTML = html;

  var zoom = { type: "inside", filterMode: "none", start: 80, end: 100 };
  var slider = { type: "slider", bottom: 6, height: 14, filterMode: "none", start: 80, end: 100,
                 borderColor: axisLine, textStyle: { color: textC, fontSize: 10 } };

  var c1 = echarts.init(document.getElementById("rbChart1"));
  c1.setOption({
    title: { text: "大盘 vs 小盘 RSI14", left: "center", textStyle: { fontSize: 12, color: textC, fontWeight: 600 } },
    tooltip: { trigger: "axis" },
    legend: { data: ["沪深300", "全市场中位数"], top: 22, textStyle: { fontSize: 10, color: textC } },
    grid: { left: 40, right: 14, top: 52, bottom: 46 },
    xAxis: { type: "category", data: dates, boundaryGap: false,
             axisLine: { lineStyle: { color: axisLine } }, axisLabel: { color: textC, fontSize: 10 } },
    yAxis: { type: "value", min: 0, max: 100, interval: 25,
             axisLabel: { color: textC, fontSize: 10 }, splitLine: { lineStyle: { color: axisLine } } },
    dataZoom: [zoom, slider],
    series: [
      { name: "沪深300", type: "line", data: hs, showSymbol: false, smooth: false, sampling: "lttb",
        lineStyle: { width: 1.4, color: C1 }, itemStyle: { color: C1 },
        markLine: { silent: true, symbol: "none", label: { show: false },
                    lineStyle: { color: axisLine, type: "dashed", width: 1 }, data: [{ yAxis: 70 }, { yAxis: 30 }] } },
      { name: "全市场中位数", type: "line", data: md, showSymbol: false, smooth: false, sampling: "lttb",
        lineStyle: { width: 1.4, color: C2 }, itemStyle: { color: C2 } }
    ]
  });

  var c2 = echarts.init(document.getElementById("rbChart2"));
  c2.setOption({
    title: { text: "差值（大盘 − 小盘）", left: "center", textStyle: { fontSize: 12, color: textC, fontWeight: 600 } },
    tooltip: { trigger: "axis" },
    grid: { left: 40, right: 14, top: 52, bottom: 46 },
    xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: axisLine } },
             axisLabel: { color: textC, fontSize: 10 } },
    yAxis: { type: "value", axisLabel: { color: textC, fontSize: 10 }, splitLine: { lineStyle: { color: axisLine } } },
    dataZoom: [zoom, slider],
    series: [{ name: "差值", type: "bar", data: sp, barMaxWidth: 2,
               itemStyle: { color: function (p) { return p.value >= 0 ? "#e23b2e" : "#12864f"; } } }]
  });

  window.addEventListener("resize", function () {
    try { c1.resize(); c2.resize(); } catch (e) {}
  });

  // 卡片是渲染后才插入 DOM 的，重扫一次二级目录，否则导航里看不到这张卡
  if (window.mountToc) {
    try { window.mountToc({ app: "#app", bar: "#tocBar", side: "#tocSide", prefix: "mood" }); } catch (e) {}
  }
})();
