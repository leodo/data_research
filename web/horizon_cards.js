/* L2 周期画像 + L3 每日可信度 · 独立渲染器（2026-11-21）
 * 消费 window.HORIZON_PROFILE 与 window.CONFIDENCE，挂载到 mood.html 的 #horizonRoot。
 * 与 factor_audit_cards.js / confidence_card.js 隔离：前缀 hz-，缺失数据只提示本层。
 */
(function () {
  "use strict";
  var H = window.HORIZON_PROFILE, C = window.CONFIDENCE;
  var root = document.getElementById("horizonRoot");
  if (!root) return;
  if (!H) {
    root.innerHTML = '<div class="card"><b>L2 周期画像</b> · 数据未就绪，请运行 <code>python build_horizon_profile.py</code>。</div>';
    return;
  }

  var css = [
    '.hz-sec{margin-top:22px}',
    '.hz-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:10px}',
    '.hz-title{font-size:17px;font-weight:800;color:var(--ink)}',
    '.hz-sub{font-size:12px;color:var(--text-3)}',
    '.hz-kpi{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:12px}',
    '.hz-chip{border:1px solid var(--line);background:var(--bg-soft);border-radius:9px;padding:8px 12px;min-width:112px}',
    '.hz-chip .k{font-size:11px;color:var(--text-3)}',
    '.hz-chip .v{font-size:16px;font-weight:800;margin-top:1px;color:var(--ink);font-variant-numeric:tabular-nums}',
    '.hz-tb{width:100%;border-collapse:collapse;font-size:12.5px}',
    '.hz-tb th{text-align:left;font-weight:600;color:var(--text-3);padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}',
    '.hz-tb td{padding:6px 8px;border-bottom:1px dashed var(--line);color:var(--text-2)}',
    '.hz-tb td.n{text-align:right;font-variant-numeric:tabular-nums}',
    '.hz-badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:700;white-space:nowrap}',
    '.hz-ok{background:#e3f3e9;color:#12864f}.hz-warn{background:#fdf1de;color:#a56a00}',
    '.hz-blue{background:#e8f0fb;color:#2f6fd0}.hz-gray{background:#eef0f3;color:#6b7480}',
    '.hz-foot{font-size:11.5px;color:var(--text-3);margin-top:10px;line-height:1.8}',
    '.hz-bar{display:grid;grid-template-columns:70px 1fr 44px;gap:8px;align-items:center;font-size:12px;margin:4px 0}',
    '.hz-bar .t{height:7px;border-radius:4px;background:var(--bg-soft);overflow:hidden}',
    '.hz-bar .f{height:100%;border-radius:4px;background:var(--brand)}'
  ].join("\n");
  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  var CLASS = { "可用尺度": "hz-ok", "待验证": "hz-warn", "效应可见·统计不可证": "hz-blue" };
  var order = Object.keys(H).sort(function (a, b) {
    return (H[b].s_long || 0) - (H[a].s_long || 0);
  });
  var nOK = 0, nWarn = 0, nBlue = 0, nGray = 0;
  order.forEach(function (k) {
    var v = H[k].verdict;
    if (v === "可用尺度") nOK++; else if (v === "待验证") nWarn++;
    else if (v === "效应可见·统计不可证") nBlue++; else nGray++;
  });

  var html = '<div class="hz-sec" data-toc="L2 周期画像 / L3 可信度" data-toc-group="因子研究" id="toc_hz">';
  html += '<div class="hz-head"><span class="hz-title">L2 周期画像 · L3 每日可信度</span>' +
    '<span class="hz-sub">每个指标「在哪个时间尺度上说话」· 18 周期 × 18 标的 = 324 格</span></div>';
  html += '<div class="hz-kpi">' +
    '<div class="hz-chip"><div class="k">可用尺度</div><div class="v">' + nOK + '</div></div>' +
    '<div class="hz-chip"><div class="k">待验证</div><div class="v">' + nWarn + '</div></div>' +
    '<div class="hz-chip"><div class="k">大效应·不可证</div><div class="v">' + nBlue + '</div></div>' +
    '<div class="hz-chip"><div class="k">效应过小 / 无信号</div><div class="v">' + nGray + '</div></div>';
  if (C && C.today) {
    html += '<div class="hz-chip"><div class="k">今日可信度</div><div class="v">' + C.today.total.toFixed(1) + '</div></div>';
  }
  html += '</div>';

  html += '<table class="hz-tb"><thead><tr><th>标的</th><th>结论</th><th style="text-align:right">起效点</th>' +
    '<th style="text-align:right">有效带</th><th>状态依赖</th><th style="text-align:right">长端分</th></tr></thead><tbody>';
  order.forEach(function (k) {
    var s = H[k], cls = CLASS[s.verdict] || "hz-gray";
    var band = s.band ? (s.band[0] + "–" + s.band[1] + " 日") : "—";
    var onset = s.onset ? (s.onset + " 日") : "未出现";
    var reg = (s.regime && s.regime.indexOf("状态依赖") === 0) ? '<span class="hz-badge hz-warn">牛熊反向</span>' : '<span style="color:var(--text-3)">—</span>';
    html += '<tr><td>' + s.name + '</td>' +
      '<td><span class="hz-badge ' + cls + '">' + s.verdict + '</span></td>' +
      '<td class="n">' + onset + '</td><td class="n">' + band + '</td>' +
      '<td>' + reg + '</td><td class="n">' + (s.s_long || 0).toFixed(0) + '</td></tr>';
  });
  html += '</tbody></table>';

  html += '<div class="hz-foot">口径：秩 IC（Spearman）+ 循环平移零分布 + 族内 BH-FDR；' +
    '有效带以「|IC|≥0.15 且三段同号≥2/3 且可信度≥55」为闸门。' +
    '<b>不展示精确带宽</b>——R2 已证明历史挑出的带在样本外不优于固定长带。' +
    '完整方法与四轮检验见 <a href="../reports/因子周期画像-20261121.html" target="_blank">因子周期画像</a>。' +
    '本层只读，不回写综合分与共振板。</div>';
  html += '</div>';
  root.innerHTML = html;
})();
