/* L3 每日可信度（元评分）· 独立渲染器（2026-11-21）
 * 消费 data/confidence_data.js 的 window.CONFIDENCE，挂载到 mood.html 的 #confidenceRoot。
 * 与主页面 render() / confirm_cards.js / factor_audit_cards.js 隔离：独立前缀 cf-，不缺数据时不影响任何现有卡片。
 * 定位：衡量「今天这套读数可不可信」，不是涨跌预测。
 */
(function () {
  "use strict";
  var C = window.CONFIDENCE;
  var root = document.getElementById("confidenceRoot");
  if (!root) return;
  if (!C || !C.today) {
    root.innerHTML = '<div class="card cf-card"><div class="cf-head"><b>每日可信度</b></div>' +
      '<div class="cf-note">未找到 data/confidence_data.js，请运行 <code>python build_daily_confidence.py</code>。' +
      '本卡为独立元评分层，缺失不影响综合分与共振板。</div></div>';
    return;
  }

  var css = [
    '.cf-card{padding:18px 20px}',
    '.cf-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:2px}',
    '.cf-title{font-size:16px;font-weight:800;color:var(--ink)}',
    '.cf-sub{font-size:12px;color:var(--text-3)}',
    '.cf-top{display:flex;align-items:center;gap:22px;flex-wrap:wrap;margin:14px 0 6px}',
    '.cf-big{font-size:44px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums}',
    '.cf-lvl{font-size:12px;font-weight:700;padding:4px 12px;border-radius:20px}',
    '.cf-bars{flex:1;min-width:260px;display:grid;gap:7px}',
    '.cf-bar{display:grid;grid-template-columns:64px 1fr 46px;align-items:center;gap:8px;font-size:12px}',
    '.cf-bar .k{color:var(--text-3)}',
    '.cf-bar .t{height:8px;border-radius:5px;background:var(--bg-soft);overflow:hidden}',
    '.cf-bar .f{height:100%;border-radius:5px}',
    '.cf-bar .v{text-align:right;font-weight:700;font-variant-numeric:tabular-nums;color:var(--ink)}',
    '.cf-alert{margin:10px 0 0;padding:8px 12px;border-radius:7px;font-size:12.5px;line-height:1.7;background:#fdeaea;border:1px solid #f3c9c4;color:#8a3029}',
    '.cf-alert.soft{background:#fff8f0;border-color:#f0d9bd;color:#7a5a2a}',
    '.cf-text{background:var(--bg-soft);border-left:3px solid var(--cau);border-radius:7px;padding:9px 12px;font-size:13px;line-height:1.75;color:var(--text-2);margin:12px 0}',
    '.cf-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:6px}',
    '.cf-tb{width:100%;border-collapse:collapse;font-size:12px}',
    '.cf-tb th{text-align:left;color:var(--text-3);font-weight:600;padding:4px 6px;border-bottom:1px solid var(--line)}',
    '.cf-tb td{padding:4px 6px;border-bottom:1px dashed var(--line);color:var(--text-2)}',
    '.cf-tb td.n{text-align:right;font-variant-numeric:tabular-nums;font-weight:700;color:var(--ink)}',
    '.cf-tb .h{color:var(--text-3);font-size:11px}',
    '.cf-ex{margin-top:12px;font-size:12.5px;color:var(--text-2);line-height:1.8}',
    '.cf-chip{display:inline-block;margin:3px 6px 3px 0;padding:2px 8px;border-radius:10px;font-size:11.5px;background:var(--bg-soft);border:1px solid var(--line)}',
    '.cf-spark{margin-top:12px}',
    '.cf-note{font-size:12.5px;color:var(--text-2);line-height:1.8;margin-top:10px}',
    '.cf-foot{font-size:11.5px;color:var(--text-3);margin-top:10px;line-height:1.7}'
  ].join("\n");
  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  function color(v) { return v >= 70 ? "var(--pos)" : (v >= 50 ? "var(--cau)" : "var(--neg)"); }
  function bar(label, v) {
    return '<div class="cf-bar"><span class="k">' + label + '</span>' +
      '<span class="t"><span class="f" style="width:' + Math.max(2, v) + '%;background:' + color(v) + '"></span></span>' +
      '<span class="v">' + v.toFixed(1) + '</span></div>';
  }
  function table(title, items, weight) {
    var h = '<table class="cf-tb"><thead><tr><th>' + title + '（权重 ' + weight + '）</th><th style="text-align:right">得分</th></tr></thead><tbody>';
    items.forEach(function (it) {
      h += '<tr><td>' + it[0] + ' <span class="h">· ' + it[2] + '</span></td><td class="n">' + it[1].toFixed(1) + '</td></tr>';
    });
    return h + '</tbody></table>';
  }
  function spark(hist) {
    if (!hist || hist.length < 3) return "";
    var w = 620, hh = 70, pad = 6;
    var ts = hist.map(function (x) { return x.t; });
    var mn = Math.min.apply(null, ts), mx = Math.max.apply(null, ts);
    var rng = Math.max(6, mx - mn);
    var pts = hist.map(function (x, i) {
      var px = pad + i * (w - 2 * pad) / (hist.length - 1);
      var py = hh - pad - (x.t - mn) / rng * (hh - 2 * pad);
      return px.toFixed(1) + "," + py.toFixed(1);
    }).join(" ");
    return '<div class="cf-spark"><div style="font-size:11.5px;color:var(--text-3);margin-bottom:3px">' +
      '近 3 年可信度走势（每 5 个交易日一个点）</div>' +
      '<svg viewBox="0 0 ' + w + ' ' + hh + '" style="width:100%;height:74px">' +
      '<polyline fill="none" stroke="var(--brand)" stroke-width="1.6" points="' + pts + '"/></svg></div>';
  }

  var T = C.today, D = T.D, E = T.E, S = T.S;
  var lvlColor = T.total >= 70 ? "var(--pos)" : (T.total >= 50 ? "var(--cau)" : "var(--neg)");
  var html = '';
  html += '<div class="card cf-card" data-toc="每日可信度" data-toc-group="总览">';
  html += '<div class="cf-head"><span class="cf-title">每日可信度（元评分）</span>' +
    '<span class="cf-sub">衡量「今天这套读数可不可信」，不是涨跌预测 · 数据日 ' + T.date + '</span></div>';
  html += '<div class="cf-top"><div><div class="cf-big" style="color:' + lvlColor + '">' + T.total.toFixed(1) + '</div>' +
    '<span class="cf-lvl" style="background:' + lvlColor + ';color:#fff">' + T.level + '</span></div>';
  html += '<div class="cf-bars">' + bar("数据层 D", D.score) + bar("证据层 E", E.score) + bar("尺度匹配 S", S.score) + '</div></div>';
  if (T.health && !T.health.ok) {
    html += '<div class="cf-alert"><b>数据过期：</b>数据日为 ' + T.health.data_date +
      '，距今天 ' + T.health.stale_days + ' 天，总分已按 ' + T.health.penalty.toFixed(2) +
      ' 折扣。请先跑一次更新再看结论。</div>';
  } else if (T.health && T.health.stale_factors && T.health.stale_factors.length) {
    html += '<div class="cf-alert soft"><b>部分因子滞后：</b>' +
      T.health.stale_factors.join('、') + ' 已超过 20 个交易日未更新。</div>';
  }
  html += '<div class="cf-text">' + T.text + '</div>';
  html += '<div class="cf-grid">' + table("数据层 D", D.items, "0.40") + table("证据层 E", E.items, "0.60") + '</div>';
  if (T.extreme && T.extreme.n) {
    html += '<div class="cf-ex"><b>当日极值因子 ' + T.extreme.n + ' 个：</b>';
    T.extreme.low.forEach(function (x) { html += '<span class="cf-chip">' + x[0] + ' 低分位 ' + x[1] + '</span>'; });
    T.extreme.high.forEach(function (x) { html += '<span class="cf-chip">' + x[0] + ' 高分位 ' + x[1] + '</span>'; });
    html += '</div>';
  }
  html += '<div class="cf-ex"><b>尺度匹配说明：</b>' + S.items[0][2] + '。' + S.note + '</div>';
  html += spark(C.history);
  html += '<div class="cf-foot">口径：' + C.meta.formula + '；' + C.meta.note +
    '。本卡不参与 8 指标等权综合分、不改共振板、不触发任何交易建议。</div>';
  html += '</div>';
  root.innerHTML = html;
  if (window.mountToc) { try { window.mountToc({ app: "#app", bar: "#tocBar", side: "#tocSide", prefix: "mood" }); } catch (e) {} }
})();
