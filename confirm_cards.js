/* 拐点确认层 · 独立渲染器（2026-11-21）
 * 消费 data/confirm_data.js 的 window.CONFIRM_DATA，挂载到 mood.html 的 #confirmRoot。
 * 与情绪面板主渲染逻辑完全隔离：不复用 render()/renderOneChart()，不碰 MOOD_DATA，
 * 不修改任何现有卡片。图表用页面已加载的 echarts，主题跟随 <html data-theme>。
 */
(function () {
  "use strict";
  var D = window.CONFIRM_DATA;
  var root = document.getElementById("confirmRoot");
  if (!root) return;
  if (!D || !D.cards) {
    root.innerHTML = '<div class="card cfm-card"><div class="cfm-sec-head">拐点确认层 · 数据未就绪</div>' +
      '<div class="cfm-note">未找到 data/confirm_data.js，请运行 <code>python build_confirm.py</code>。' +
      '本层为独立观察层，缺失不影响原有 8 指标与共振板。</div></div>';
    return;
  }

  /* ---------------- 样式：全部走项目主题变量，浅/暗自动适配 ---------------- */
  var css = [
    '.cfm-sec{margin-top:26px}',
    '.cfm-head{display:flex;align-items:baseline;flex-wrap:wrap;gap:10px;margin-bottom:12px}',
    '.cfm-title{font-size:17px;font-weight:800;color:var(--ink)}',
    '.cfm-sub{font-size:12px;color:var(--text-3)}',
    '.cfm-layer{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px}',
    '.cfm-chip{border:1px solid var(--line);background:var(--bg-soft);border-radius:9px;padding:9px 13px;min-width:160px}',
    '.cfm-chip .k{font-size:11px;color:var(--text-3)}',
    '.cfm-chip .v{font-size:15px;font-weight:700;margin-top:2px;color:var(--ink)}',
    '.cfm-chip.on{border-color:var(--cau);background:var(--cau-bg)}',
    '.cfm-chip.on .v{color:var(--cau)}',
    '.cfm-card{padding:18px 20px}',
    '.cfm-sec-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:4px}',
    '.cfm-name{font-size:15px;font-weight:700;color:var(--ink)}',
    '.cfm-badge{font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;border:1px solid var(--line);color:var(--text-2);background:var(--bg-soft)}',
    '.cfm-badge.bottom{color:#fff;background:var(--down);border-color:transparent}',
    '.cfm-badge.caution{color:#fff;background:var(--cau);border-color:transparent}',
    '.cfm-badge.mid{color:var(--text-2)}',
    '.cfm-meta{font-size:11.5px;color:var(--text-3);margin:0 0 12px}',
    '.cfm-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(126px,1fr));gap:9px;margin:12px 0}',
    '.cfm-kv>div{background:var(--bg-soft);border:1px solid var(--line);border-radius:8px;padding:9px 11px}',
    '.cfm-kv .k{font-size:11px;color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.cfm-kv .v{font-size:17px;font-weight:700;margin-top:2px;color:var(--ink);font-variant-numeric:tabular-nums}',
    '.cfm-chart{width:100%;height:252px;margin:8px 0 4px}',
    '.cfm-h3{font-size:12.5px;font-weight:700;color:var(--text-2);margin:16px 0 6px}',
    '.cfm-tbl{width:100%;border-collapse:collapse;font-size:12px;margin:6px 0}',
    '.cfm-tbl th,.cfm-tbl td{border:1px solid var(--line);padding:6px 9px;text-align:left;white-space:nowrap}',
    '.cfm-tbl th{background:var(--bg-soft);color:var(--text-2);font-weight:600}',
    '.cfm-tbl td.num{text-align:right;font-variant-numeric:tabular-nums}',
    '.cfm-tbl tr.hl td{background:var(--brand-soft)}',
    '.cfm-scroll{overflow-x:auto}',
    '.cfm-note{background:var(--srcbg);border:1px solid var(--line);border-radius:8px;padding:11px 14px;font-size:12px;color:var(--text-2);line-height:1.7;margin-top:12px}',
    '.cfm-warn{border-left:3px solid var(--cau);padding-left:10px;margin-top:10px;font-size:12px;color:var(--text-2);line-height:1.7}',
    '.cfm-def{font-size:12px;color:var(--text-2);margin:8px 0 0;line-height:1.7}',
    '.cfm-def b{color:var(--ink)}',
    '.cfm-foot{font-size:11.5px;color:var(--text-3);margin-top:16px;line-height:1.8;border-top:1px solid var(--line);padding-top:12px}',
    'code{background:var(--bg-soft);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:11px}'
  ].join("\n");
  var st = document.createElement("style");
  st.id = "cfm-style"; st.textContent = css;
  document.head.appendChild(st);

  /* ---------------- 工具 ---------------- */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function f(v, suffix) {
    if (v === null || v === undefined || v === "" || (typeof v === "number" && !isFinite(v))) return "—";
    return String(v) + (suffix || "");
  }
  function cls(v) { return v === "bottom" ? "bottom" : v === "caution" ? "caution" : "mid"; }
  function kv(list) {
    return '<div class="cfm-kv">' + list.map(function (x) {
      return '<div><div class="k" title="' + esc(x[0]) + '">' + esc(x[0]) + '</div>' +
        '<div class="v">' + f(x[1], x[2] || "") + '</div></div>';
    }).join("") + "</div>";
  }
  function tbl(cols, rows, hlKeys) {
    var h = '<div class="cfm-scroll"><table class="cfm-tbl"><thead><tr>' +
      cols.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") + "</tr></thead><tbody>";
    rows.forEach(function (r) {
      var on = hlKeys && hlKeys.indexOf(r[0]) >= 0 ? ' class="hl"' : "";
      h += "<tr" + on + ">" + r.map(function (c, i) {
        var isObj = c && typeof c === "object" && Object.prototype.hasOwnProperty.call(c, "html");
        var cell = isObj ? c.html : esc(c);
        return "<td" + (i > 0 && !(isObj && c.left) ? ' class="num"' : "") + ">" + cell + "</td>";
      }).join("") + "</tr>";
    });
    return h + "</tbody></table></div>";
  }
  function cv(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (e) { return fallback; }
  }

  /* ---------------- 卡片 HTML ---------------- */
  var cards = {};
  D.cards.forEach(function (c) { cards[c.key] = c; });
  var L = D.layer || { n_signals: 0, n_total: 3, items: [], state: "未确认" };
  var html = '<div class="cfm-sec" data-toc="拐点确认层（新增）" data-toc-group="拐点确认" id="toc_confirm_layer">';
  html += '<div class="cfm-head"><span class="cfm-title">拐点确认层 · 时点 / 结构热度 / 尾部定价</span>' +
    '<span class="cfm-sub">独立观察层 · 不进 8 指标综合分 · 不改 13 维共振板 · 数据截至 ' + esc(D.data_date) + '</span></div>';
  html += '<div class="cfm-layer">';
  html += '<div class="cfm-chip"><div class="k">确认层汇总</div><div class="v">' + esc(L.state) +
    ' · ' + f(L.n_signals) + '/' + f(L.n_total) + '</div></div>';
  (L.items || []).forEach(function (it) {
    html += '<div class="cfm-chip' + (it.on ? " on" : "") + '"><div class="k">' + esc(it.name) +
      '</div><div class="v">' + (it.on ? "已触发" : "未触发") + '</div></div>';
  });
  html += '</div>';

  // --- 卡片 1：情绪-价格底背离 ---
  var a = cards.divergence;
  if (a) {
    var d = a.detail || {};
    html += '<div class="card cfm-card" data-toc="情绪-价格底背离" data-toc-group="拐点确认" id="toc_cfm_div">';
    html += '<div class="cfm-sec-head"><span class="cfm-name">① 情绪-价格底背离 · 周频</span>' +
      '<span class="cfm-badge ' + cls(a.level) + '">' + esc(a.state) + '</span></div>';
    html += '<p class="cfm-meta">数据 ' + esc(a.last_date) + ' · ' + esc(a.source) + '</p>';
    html += kv([
      ["综合情绪分", d.mood_now], ["近4周情绪变化", d.mood_chg4, " 分"],
      ["近4周沪深300", d.price_ret4, "%"], ["近8周情绪最低", d.pessim_last8_min],
      ["沪深300 收盘", d.hs300], ["历史周点", d.n_weeks]
    ]);
    html += '<div class="cfm-chart" id="cfmChartDiv"></div>';
    html += '<p class="cfm-def"><b>定义：</b>' + esc(a.definition) +
      ' 与共振板「价格新低 vs 宽度未新低」是不同维度：这里对的是<b>综合情绪分</b>先转向。</p>';
    html += '<div class="cfm-h3">历史标定 · 触发后前瞻收益（PIT，20 周去重，t+N ≤ 末日）</div>';
    html += tbl(["分组", "样本 n", "4周胜率", "4周均收益", "12周胜率", "12周均收益", "12周中位"],
      (a.backtest || []).map(function (b) {
        return [b.label, b.n, f(b.win4, "%"), f(b.avg4, "%"), f(b.win12, "%"), f(b.avg12, "%"), f(b.med12, "%")];
      }), ["情绪-价格底背离"]);
    html += '<div class="cfm-h3">阈值稳健性 · 换档位后 12 周表现会不会塌</div>';
    html += tbl(["回升阈值", "悲观阈值", "信号数", "4周胜率", "12周胜率", "12周均收益"],
      (a.robust || []).map(function (r) {
        return ["≥" + r.rebound + " 分", "≤" + r.pessimist, r.n, f(r.win4, "%"), f(r.win12, "%"), f(r.avg12, "%")];
      }), ["≥" + Number(D.thresholds && D.thresholds.div ? D.thresholds.div.rebound_min : 3).toFixed(1) + " 分"]);
    html += '<div class="cfm-scroll" style="margin-top:6px"><div class="cfm-h3" style="margin-top:10px">历史触发日（最近 8 次）</div></div>';
    html += tbl(["日期", "情绪分", "4周情绪Δ", "4周价格", "后4周", "后12周"],
      (a.triggers || []).slice(0, 8).map(function (t) {
        return [t.date, t.mood, t.d_mood4, f(t.price_ret4, "%"), f(t.ret4, "%"), f(t.ret12, "%")];
      }));
    html += '<div class="cfm-warn"><b>反面证据：</b>' + esc(a.caveat) + '</div>';
    html += '</div>';
  }

  // --- 卡片 2：涨跌停 / 缩量止跌 ---
  var b = cards.limits;
  if (b) {
    var q = b.detail || {};
    html += '<div class="card cfm-card" data-toc="涨跌停家数 · 缩量止跌" data-toc-group="拐点确认" id="toc_cfm_lim">';
    html += '<div class="cfm-sec-head"><span class="cfm-name">② 涨跌停家数 · 缩量止跌 · 日频</span>' +
      '<span class="cfm-badge ' + cls(b.level) + '">' + esc(b.state) + '</span></div>';
    html += '<p class="cfm-meta">数据 ' + esc(b.last_date) + ' · ' + esc(b.source) + '</p>';
    html += kv([
      ["有效股票", q.n_valid, " 只"], ["涨停", q.n_limit_up, " 只"], ["跌停", q.n_limit_down, " 只"],
      ["跌停占比", q.down_ratio, "%"], ["5日前跌停占比", q.down_ratio_5d_ago, "%"],
      ["全市场成交额", q.amount_yi, " 亿"], ["20日量能比", q.vol_ratio20],
      ["成交额/250日前高", q.vol_vs_high250]
    ]);
    html += '<div class="cfm-chart" id="cfmChartLim"></div>';
    html += '<p class="cfm-def"><b>定义：</b>' + esc(b.definition) + '</p>';
    html += '<div class="cfm-h3">缩量止跌三条件（当前）</div>';
    html += tbl(["条件", "读数", "是否满足"], [
      ["20日量能比 ≤" + (D.thresholds && D.thresholds.vol ? D.thresholds.vol.shrink_max : 0.8), f(q.vol_ratio20), { html: q.shrink ? "✓ 是" : "✗ 否", left: 1 }],
      ["指数未创20日新低", f(q.hs300), { html: q.no_new_low ? "✓ 是" : "✗ 否", left: 1 }],
      ["跌停占比较5日前回落≥1pp", f(q.down_ratio_5d_ago, "%") + " → " + f(q.down_ratio, "%"), { html: q.panic_ease ? "✓ 是" : "✗ 否", left: 1 }]
    ]);
    html += '<div class="cfm-h3">历史标定 · 20 / 60 交易日前瞻收益</div>';
    html += tbl(["分组", "样本 n", "20日胜率", "20日均收益", "60日胜率", "60日均收益"],
      (b.backtest || []).map(function (x) {
        return [x.label, x.n, f(x.win20, "%"), f(x.avg20, "%"), f(x.win60, "%"), f(x.avg60, "%")];
      }), ["缩量止跌"]);
    html += '<div class="cfm-warn"><b>口径局限：</b>' + esc(b.caveat) + '</div>';
    html += '</div>';
  }

  // --- 卡片 3：VRP ---
  var c = cards.vrp;
  if (c) {
    var v = c.detail || {};
    html += '<div class="card cfm-card" data-toc="波动率风险溢价 VRP" data-toc-group="拐点确认" id="toc_cfm_vrp">';
    html += '<div class="cfm-sec-head"><span class="cfm-name">③ 波动率风险溢价 VRP · 日频</span>' +
      '<span class="cfm-badge ' + cls(c.level) + '">' + esc(c.state) + '</span></div>';
    html += '<p class="cfm-meta">数据 ' + esc(c.last_date) + ' · 隐波源 ' + esc(c.source) + '</p>';
    html += kv([
      ["QVIX 隐波(300ETF)", v.qvix, "%"], ["50ETF QVIX 对照", v.qvix_50etf_ref, "%"],
      ["RV20 已实现波动", v.rv20, "%"], ["VRP 价差", v.vrp, "pp"],
      ["VRP 历史分位", v.vrp_pct], ["下行波动占比", v.downside_share, "%"],
      ["VRP 5日变化", v.vrp_chg5, "pp"]
    ]);
    html += '<div class="cfm-chart" id="cfmChartVrp"></div>';
    html += '<p class="cfm-def"><b>定义：</b>' + esc(c.definition) + '</p>';
    html += '<div class="cfm-h3">同档前瞻收益 · 含方向自证伪</div>';
    html += tbl(["分组", "样本 n", "20日胜率", "20日均收益", "60日胜率", "60日均收益", "判定"],
      (c.backtest || []).map(function (x) {
        return [x.label, x.n, f(x.win20, "%"), f(x.avg20, "%"), f(x.win60, "%"), f(x.avg60, "%"),
          { html: esc(x.verdict || ""), left: 1 }];
      }), ["VRP 高分位≥" + (D.thresholds && D.thresholds.vrp ? D.thresholds.vrp.high : 80)]);
    html += '<div class="cfm-warn"><b>自证伪结论：</b>高 VRP 组 − 低 VRP 组的 60 日均收益价差 ' +
      f(c.spread60, "pp") + '，判定「' + esc(c.verdict) + '」。' +
      (String(c.verdict || "").indexOf("方向相反") >= 0
        ? '方向相反必须保留：高恐慌溢价之后 60 日收益反而更低，说明 VRP 只反映尾部定价、不保证均值回归。'
        : String(c.verdict || "").indexOf("符合预期") >= 0
          ? '方向与「恐慌溢价高→后续修复」的假设一致，但样本量小、窗口重叠，仍不能当作择时胜率。'
          : '价差接近 0 或样本不足，不构成有效择时证据；VRP 只作尾部定价的相对观察。') + '</div>';
    html += '<div class="cfm-warn"><b>口径局限：</b>' + esc(c.caveat) + '</div>';
    html += '</div>';
  }

  html += '<div class="cfm-foot">' + esc(D.disclaimer) +
    '<br>生成 ' + esc(D.generated_at) + ' · 数据源：本地指标库（公开数据自算）· ' +
    '计算脚本 <code>build_confirm.py</code>，阈值见 <code>config.py</code> 拐点确认层段。' +
    '</div></div>';
  root.innerHTML = html;

  /* ---------------- ECharts ---------------- */
  var charts = [];
  function mk(id, opt) {
    var el = document.getElementById(id);
    if (!el || !window.echarts) return;
    var ch = echarts.init(el);
    ch.setOption(opt);
    charts.push({ id: id, ch: ch });
  }
  function theme() {
    return {
      dark: document.documentElement.getAttribute("data-theme") === "dark",
      ink: cv("--ink", "#111"), t2: cv("--text-2", "#4d4d4d"), t3: cv("--text-3", "#8c8c8c"),
      line: cv("--chart-axis-line", "#e8e8e8"), split: cv("--chart-split", "#f3f3f3"),
      up: cv("--up", "#ff4713"), down: cv("--down", "#00875a"),
      neu: cv("--neu", "#1e88ff"), cau: cv("--cau", "#ff2525"),
      brand: cv("--brand", "#ff4713"), mark: cv("--chart-mark", "#d4d4d4"),
      tipBg: cv("--chart-tip-bg", "#fff"), tipFg: cv("--chart-tip-fg", "#111")
    };
  }
  function axis(t, extra) {
    return Object.assign({
      axisLine: { lineStyle: { color: t.line } }, axisTick: { show: false },
      axisLabel: { color: t.t3, fontSize: 10 },
      splitLine: { lineStyle: { color: t.split } }
    }, extra || {});
  }
  function tip(t) {
    return { trigger: "axis", axisPointer: { type: "cross" },
      backgroundColor: t.tipBg, borderColor: t.line, textStyle: { color: t.tipFg, fontSize: 11 } };
  }
  function draw() {
    charts.forEach(function (x) { try { x.ch.dispose(); } catch (e) {} });
    charts = [];
    var t = theme();

    // ① 情绪分（左轴） vs 沪深300（右轴）+ 信号点
    if (cards.divergence) {
      var a = cards.divergence, ch = a.charts || {};
      var m = ch.mood || [], p = ch.price || [];
      var md = m.map(function (x) { return x.date; });
      var mv = m.map(function (x) { return x.value; });
      var pv = p.map(function (x) { return x.value; });
      var sigs = (ch.signals || []);
      mk("cfmChartDiv", {
        grid: { left: 46, right: 58, top: 30, bottom: 26 },
        legend: { data: ["综合情绪分", "沪深300"], textStyle: { color: t.t2, fontSize: 10 }, top: 0 },
        tooltip: tip(t),
        xAxis: axis(t, { type: "category", data: md, boundaryGap: false,
          axisLabel: { color: t.t3, fontSize: 10, interval: Math.max(0, Math.floor(md.length / 6)) },
          splitLine: { show: false } }),
        yAxis: [
          axis(t, { type: "value", min: 0, max: 100, name: "情绪分", nameTextStyle: { color: t.t3, fontSize: 10 } }),
          axis(t, { type: "value", name: "HS300", nameTextStyle: { color: t.t3, fontSize: 10 },
            scale: true, splitLine: { show: false } })
        ],
        series: [
          { name: "综合情绪分", type: "line", data: mv, showSymbol: false, smooth: true,
            lineStyle: { color: t.brand, width: 2 },
            markLine: { silent: true, symbol: "none", lineStyle: { type: "dashed", color: t.mark },
              label: { color: t.t3, fontSize: 9 }, data: [{ yAxis: 35, name: "悲观 35" }] } },
          { name: "沪深300", type: "line", yAxisIndex: 1, data: pv, showSymbol: false, smooth: true,
            lineStyle: { color: t.neu, width: 1.6, opacity: .85 } },
          { name: "底背离信号", type: "scatter", yAxisIndex: 1, symbolSize: 11,
            itemStyle: { color: t.cau },
            data: sigs.map(function (dt) {
              var i = md.indexOf(dt);
              return i < 0 ? null : [i, pv[i]];
            }).filter(function (x) { return x !== null; }) }
        ]
      });
    }

    // ② 涨跌停家数（柱） + 成交额（右轴线）
    if (cards.limits) {
      var b = cards.limits, g = b.charts || {};
      var ds = g.dates || [];
      mk("cfmChartLim", {
        grid: { left: 46, right: 58, top: 30, bottom: 26 },
        legend: { data: ["涨停家数", "跌停家数", "成交额(亿)"], textStyle: { color: t.t2, fontSize: 10 }, top: 0 },
        tooltip: tip(t),
        xAxis: axis(t, { type: "category", data: ds, boundaryGap: true,
          axisLabel: { color: t.t3, fontSize: 10, interval: Math.max(0, Math.floor(ds.length / 6)) },
          splitLine: { show: false } }),
        yAxis: [
          axis(t, { type: "value", name: "家数", nameTextStyle: { color: t.t3, fontSize: 10 } }),
          axis(t, { type: "value", name: "亿元", nameTextStyle: { color: t.t3, fontSize: 10 },
            scale: true, splitLine: { show: false } })
        ],
        series: [
          { name: "涨停家数", type: "bar", stack: "lim", data: g.up || [], itemStyle: { color: t.up } },
          { name: "跌停家数", type: "bar", stack: "lim", data: (g.down || []).map(function (x) { return x === null ? x : -x; }),
            itemStyle: { color: t.down } },
          { name: "成交额(亿)", type: "line", yAxisIndex: 1, data: g.amount || [], showSymbol: false,
            smooth: true, lineStyle: { color: t.neu, width: 1.5, opacity: .8 } }
        ]
      });
    }

    // ③ QVIX vs RV20 + VRP（右轴）
    if (cards.vrp) {
      var c = cards.vrp, v = c.charts || {};
      var qd = (v.qvix || []).map(function (x) { return x.date; });
      var qv = (v.qvix || []).map(function (x) { return x.value; });
      var rv = (v.rv || []).map(function (x) { return x.value; });
      var vp = (v.vrp || []).map(function (x) { return x.value; });
      mk("cfmChartVrp", {
        grid: { left: 46, right: 54, top: 30, bottom: 26 },
        legend: { data: ["QVIX 隐波", "RV20 已实现", "VRP 价差"], textStyle: { color: t.t2, fontSize: 10 }, top: 0 },
        tooltip: tip(t),
        xAxis: axis(t, { type: "category", data: qd, boundaryGap: false,
          axisLabel: { color: t.t3, fontSize: 10, interval: Math.max(0, Math.floor(qd.length / 6)) },
          splitLine: { show: false } }),
        yAxis: [
          axis(t, { type: "value", name: "波动率%", nameTextStyle: { color: t.t3, fontSize: 10 } }),
          axis(t, { type: "value", name: "VRP pp", nameTextStyle: { color: t.t3, fontSize: 10 },
            splitLine: { show: false } })
        ],
        series: [
          { name: "QVIX 隐波", type: "line", data: qv, showSymbol: false, smooth: true,
            lineStyle: { color: t.brand, width: 2 } },
          { name: "RV20 已实现", type: "line", data: rv, showSymbol: false, smooth: true,
            lineStyle: { color: t.neu, width: 1.6 } },
          { name: "VRP 价差", type: "line", yAxisIndex: 1, data: vp, showSymbol: false, smooth: true,
            lineStyle: { color: t.down, width: 1.4, type: "dashed" },
            areaStyle: { color: "rgba(0,135,90,.10)" } }
        ]
      });
    }
  }

  draw();
  // 主题切换时重绘（项目 applyTheme 直接改属性，不派发事件）
  try {
    new MutationObserver(function (ms) {
      ms.forEach(function (m) { if (m.attributeName === "data-theme") draw(); });
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  } catch (e) {}
  window.addEventListener("resize", function () {
    charts.forEach(function (x) { try { x.ch.resize(); } catch (e) {} });
  });

  // 子卡片也纳入页面目录（主脚本 render() 时占位 div 还没内容）
  try {
    if (window.mountToc) window.mountToc({ app: "#app", bar: "#tocBar", side: "#tocSide", prefix: "mood" });
  } catch (e) {}
})();
