/* 可转债栏目前端：总览 / 指数 / 估值 / 双低 / 全市场 / 分布 / 强赎与新债
 * 数据来自 window.CB_DATA（build_cb.py 生成）。
 */
(function(){
  "use strict";

  var D = window.CB_DATA;
  var charts = [], refs = {};
  var state = {
    csiRange: "3",
    longRange: "all",
    page: 1,
    pageSize: 25,
    q: "",
    price: "all",
    premium: "all",
    rating: "all",
    noRedeem: false,
    sortKey: "dblow",
    sortDir: 1
  };
  var theme = "light";

  function $(id){ return document.getElementById(id); }
  function cssVar(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function esc(s){
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
    });
  }
  function fmt(v, n){
    var x = Number(v);
    if(v === null || v === undefined || v === "" || !isFinite(x)) return "—";
    return x.toFixed(n === undefined ? 2 : n);
  }
  function signed(v, n){
    var x = Number(v);
    if(v === null || v === undefined || !isFinite(x)) return "—";
    return (x > 0 ? "+" : "") + x.toFixed(n === undefined ? 2 : n);
  }
  function pctText(v, n){
    return (v === null || v === undefined) ? "—" : signed(v, n) + "%";
  }
  function cls(v){
    var x = Number(v);
    if(!isFinite(x) || x === 0) return "flat";
    return x > 0 ? "up" : "down";
  }
  function rgba(hex, a){
    var h = String(hex || "").replace("#", "");
    if(h.length === 3) h = h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
    var n = parseInt(h, 16);
    if(!isFinite(n)) return "rgba(128,128,128," + a + ")";
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }
  function shortDate(s){ return s ? String(s).slice(5) : "—"; }

  function pctZone(p){
    if(p === null || p === undefined) return "—";
    if(p >= 80) return "高位";
    if(p >= 60) return "偏高";
    if(p <= 20) return "低位";
    if(p <= 40) return "偏低";
    return "中性";
  }
  function tempLabel(t){
    if(t === null || t === undefined) return "—";
    if(t <= 10) return "冰点";
    if(t <= 20) return "低温";
    if(t <= 40) return "偏冷";
    if(t <= 60) return "中性";
    if(t <= 80) return "偏热";
    return "高温";
  }
  function ratingBadge(r){
    var k = String(r || "—");
    if(k === "—") return '<span class="badge gray">—</span>';
    var n = ratingRank(k);
    var c = n >= 10 ? "blue" : (n >= 6 ? "gray" : (n >= 2 ? "orange" : "red"));
    return '<span class="badge ' + c + '">' + esc(k) + '</span>';
  }
  function redeemBadge(b){
    if(b.redeem_kind === "强赎"){
      return '<span class="badge red" title="' + esc(b.redeem_status || "已公告强赎") + '">强赎 ' + shortDate(b.redeem_event) + '</span>';
    }
    if(b.redeem_kind === "不强赎"){
      return '<span class="badge green" title="' + esc(b.redeem_status || "公告不强赎") + '">不强赎 ' + shortDate(b.redeem_event) + '</span>';
    }
    if(b.redeem_counter){
      var n = parseInt(String(b.redeem_counter).split("/")[0], 10);
      if(n >= 5){
        return '<span class="badge gray" title="15 个交易日中已有 ' + esc(b.redeem_counter) + ' 日达标">' + esc(b.redeem_counter) + '</span>';
      }
    }
    return '<span class="badge gray">—</span>';
  }
  function nameHtml(b){
    return '<div class="c-name"><a href="https://data.eastmoney.com/kzz/detail/' + b.code + '.html" target="_blank" rel="noopener">' +
           esc(b.name) + '</a><span>' + esc(b.code) + '</span></div>';
  }
  function stockHtml(b){
    var chg = (b.stock_chg === null || b.stock_chg === undefined) ? "—" : signed(b.stock_chg, 2) + "%";
    return '<div class="c-stock"><span>' + esc(b.stock || "—") + '</span><span>' +
           fmt(b.stock_price, 2) + ' <em class="' + cls(b.stock_chg) + '" style="font-style:normal">' +
           chg + '</em></span></div>';
  }

  // ---------------- 总体布局 ----------------
  function renderAll(){
    if(!D){
      $("app").className = "";
      $("app").innerHTML = '<div class="err">未找到 <b>data/cb_data.js</b>。请先在项目目录运行 <b>python build_cb.py</b> 生成数据，再刷新本页。</div>';
      return;
    }
    var M = D.market, C = M.csi, J = M.jsl, O = M.own, P = M.pcts;
    $("dataStamp").textContent = "数据 " + D.meta.date + " · " + D.meta.count + " 只";
    document.title = "可转债 · 双低与溢价率 · 投研工作台";

    var html = '';
    html += '<div class="page-head"><div><h1>可转债 <span>双低 · 溢价率 · 强赎</span></h1>' +
            '<div class="page-sub" style="text-align:left">全市场 ' + O.count + ' 只（东财有报价）· 成交额 ' + fmt(J.amount, 0) + ' 亿 · 数据截至 ' + D.meta.date + '</div></div>' +
            '<div class="page-sub">中证指数 000832 · 集思录等权口径<br>纯债估值为东方财富口径</div></div>';

    // KPI
    html += '<div class="kpi-grid">';
    html += kpi("中证转债指数", fmt(C.close, 2), "", '<span class="' + cls(C.chg) + '">' + pctText(C.chg, 2) + '</span> · 近1年分位 <b>' + fmt(P.csi_1y, 1) + '%</b>（' + pctZone(P.csi_1y) + '）');
    html += kpi("集思录等权指数", fmt(J.idx, 2), "", '<span class="' + cls(J.idx_chg) + '">' + pctText(J.idx_chg, 2) + '</span> · 换手率 <b>' + fmt(J.turnover, 2) + '%</b>');
    html += kpi("中位价格", fmt(J.mid_price, 2), "元", '&lt;100 元 <b>' + O.below_100 + '</b> 只 · ≥130 元 <b>' + O.above_130 + '</b> 只');
    html += kpi("中位转股溢价率", fmt(J.mid_premium, 1), "%", '近1年分位 <b>' + fmt(P.premium, 1) + '%</b> · ' + pctZone(P.premium));
    html += kpi("平均双低", fmt(J.dblow, 1), "", '近1年分位 <b>' + fmt(P.dblow, 1) + '%</b> · 中位 <b>' + fmt(O.median_dblow, 1) + '</b>');
    html += kpi("市场温度", fmt(J.temp, 1), "°", tempLabel(J.temp) + ' · 近1年分位 <b>' + fmt(P.temp, 1) + '%</b>');
    html += kpi("成交额", fmt(J.amount, 0), "亿", '换手率 <b>' + fmt(J.turnover, 2) + '%</b> · 集思录口径 <b>' + J.count + '</b> 只');
    html += kpi("强赎公告", String(O.redeem_yes), "只", '不强赎公告 <b>' + O.redeem_no + '</b> 只 · 全市场 <b>' + O.count + '</b> 只');
    html += '</div>';

    html += '<div class="insight"><b>当前位置</b><span>中证转债指数 ' + fmt(C.close, 2) + '，近一年分位 ' + fmt(P.csi_1y, 1) + '%（' + pctZone(P.csi_1y) + '）；' +
            '中位转股溢价率 ' + fmt(J.mid_premium, 1) + '%，近一年分位 ' + fmt(P.premium, 1) + '%（' + pctZone(P.premium) + '）。' +
            '市场温度 ' + fmt(J.temp, 1) + '（' + tempLabel(J.temp) + '），平均双低 ' + fmt(J.dblow, 1) + '；' +
            '低价券（&lt;100 元）' + O.below_100 + ' 只，强赎公告 ' + O.redeem_yes + ' 只。</span></div>';

    // 1 指数走势
    html += '<section class="card" data-toc="指数走势" data-toc-group="行情">' +
      '<div class="sec-head"><div><div class="sec-title">中证转债指数</div>' +
      '<div class="sec-sub">000832 · 2015-01-05 起 · 日线 · 中证指数官网</div></div>' +
      '<div class="sec-right"><div class="ranges" id="csiRanges">' +
      '<button data-r="1">1年</button><button data-r="3" class="on">3年</button><button data-r="5">5年</button><button data-r="all">全部</button>' +
      '</div></div></div><div class="chart" id="csiChart"></div>' +
      '<div class="note">中证转债指数以沪深交易所可转债为样本，按市值加权；用于观察转债市场整体 beta。当前点位 <b>' + fmt(C.close, 2) + '</b>，' +
      '近一年分位 <b>' + fmt(P.csi_1y, 1) + '%</b>，近三年分位 <b>' + fmt(C.pct_3y, 1) + '%</b>。</div></section>';

    // 2 长周期估值
    if(D.history.long && D.history.long.length > 100 && D.long_meta){
      html += longSectionHtml();
    }

    // 3 等权指数
    html += '<section class="card" data-toc="等权指数" data-toc-group="行情">' +
      '<div class="sec-head"><div><div class="sec-title">集思录等权指数与成交额</div>' +
      '<div class="sec-sub">近 250 个交易日 · 全部上市转债等权 · 2017-12-29 基准 1000 点</div></div>' +
      '<div class="sec-right"><span class="badge ' + (J.idx_chg >= 0 ? "red" : "green") + '">' + pctText(J.idx_chg, 2) + '</span>' +
      '<span class="badge gray">' + fmt(J.idx, 2) + '</span></div></div>' +
      '<div class="chart sm" id="jslChart"></div>' +
      '<div class="note">等权指数对中小转债更敏感，与市值加权的 000832 互为补充；柱状为全市场日成交额（亿元）。' +
      '当前等权指数 <b>' + fmt(J.idx, 2) + '</b>，近 250 日成交额均值 <b>' + fmt(avgOf(D.history.jsl, "amount"), 0) + '</b> 亿。</div></section>';

    // 4 估值水位
    html += '<section class="card" data-toc="估值水位" data-toc-group="估值">' +
      '<div class="sec-head"><div><div class="sec-title">溢价率 · 双低 · 温度 · YTM</div>' +
      '<div class="sec-sub">集思录等权口径 · 近 250 个交易日 · 中位/平均为全市场转债横截面统计</div></div>' +
      '<div class="sec-right"><span class="badge orange">中位溢价 ' + fmt(J.mid_premium, 1) + '%</span>' +
      '<span class="badge blue">平均双低 ' + fmt(J.dblow, 1) + '</span>' +
      '<span class="badge gray">温度 ' + fmt(J.temp, 1) + '</span></div></div>' +
      '<div class="mini-kpis">' +
      '<div class="mini-kpi"><div class="k">中位转股溢价率</div><div class="v">' + fmt(J.mid_premium, 2) + '%</div><div class="s">近1年分位 ' + fmt(P.premium, 1) + '% · ' + pctZone(P.premium) + '</div></div>' +
      '<div class="mini-kpi"><div class="k">平均双低</div><div class="v">' + fmt(J.dblow, 2) + '</div><div class="s">近1年分位 ' + fmt(P.dblow, 1) + '% · ' + pctZone(P.dblow) + '</div></div>' +
      '<div class="mini-kpi"><div class="k">市场温度</div><div class="v">' + fmt(J.temp, 2) + '°</div><div class="s">' + tempLabel(J.temp) + ' · 近1年分位 ' + fmt(P.temp, 1) + '%</div></div>' +
      '<div class="mini-kpi"><div class="k">平均到期收益率</div><div class="v">' + fmt(J.ytm, 2) + '%</div><div class="s">税前 · 集思录口径</div></div>' +
      '</div>' +
      '<div class="grid2" style="margin-top:14px">' +
      '<div><div class="chart sm" id="premChart"></div></div>' +
      '<div><div class="chart sm" id="tempChart"></div></div>' +
      '</div>' +
      '<div class="note">双低 = 转债价格 + 转股溢价率（百分点），越低表示价格越低、转股溢价越小；温度是集思录按价格、溢价率、到期收益率等综合的市场冷热指标，0–100，越低越冷。' +
      '平均到期收益率为集思录税前口径。当前中位溢价率处于近一年 <b>' + fmt(P.premium, 1) + '%</b> 分位，平均双低处于 <b>' + fmt(P.dblow, 1) + '%</b> 分位。</div></section>';

    // 5 双低榜单
    html += '<section class="card" data-toc="双低榜单" data-toc-group="策略">' +
      '<div class="sec-head"><div><div class="sec-title">双低 Top 20</div>' +
      '<div class="sec-sub">按「价格 + 转股溢价率」升序 · 已剔除无报价/退市券 · 点击名称查看东财详情</div></div>' +
      '<div class="sec-right"><span class="badge green">自算均值 ' + fmt(O.avg_dblow, 1) + '</span>' +
      '<span class="badge gray">中位 ' + fmt(O.median_dblow, 1) + '</span></div></div>' +
      '<div class="chart" id="scatterChart" style="height:330px"></div>' +
      '<div class="tbl-wrap" style="margin-top:10px;max-height:430px"><table class="tbl tbl-top"><thead><tr>' +
      '<th>#</th><th>转债</th><th>价格</th><th>溢价率</th><th>双低</th><th>正股</th><th>评级</th><th>发行(亿)</th><th>剩余(年)</th><th>状态</th>' +
      '</tr></thead><tbody>' + topRows() + '</tbody></table></div>' +
      '<div class="note">双低策略同时压缩「价格」与「溢价率」两个维度，偏债型低价券负责安全垫、低溢价券负责股性。' +
      '散点横轴为价格、纵轴为溢价率、气泡大小为剩余规模、颜色由绿到红表示双低由低到高（仅显示价格≤300 元、溢价率≤150% 的券）。' +
      '榜单仅为横截面排序，不含正股质地、信用风险与强赎条款筛选，不构成组合建议。</div></section>';

    // 6 全市场
    html += '<section class="card" data-toc="全市场列表" data-toc-group="全市场">' +
      '<div class="sec-head"><div><div class="sec-title">全市场转债</div>' +
      '<div class="sec-sub">' + O.count + ' 只 · 价格/溢价率/双低/纯债价值/评级/规模 · 支持搜索与排序</div></div>' +
      '<div class="sec-right"><span class="badge gray">双低越低越偏债</span></div></div>' +
      '<div class="toolbar">' +
      '<input id="fSearch" type="text" placeholder="搜索代码 / 名称 / 正股" autocomplete="off" />' +
      '<select id="fPrice"><option value="all">全部价格</option><option value="lt100">&lt;100 元</option><option value="100120">100–120 元</option><option value="120130">120–130 元</option><option value="ge130">≥130 元</option></select>' +
      '<select id="fPremium"><option value="all">全部溢价率</option><option value="lt10">&lt;10%</option><option value="1030">10–30%</option><option value="3050">30–50%</option><option value="ge50">≥50%</option></select>' +
      '<select id="fRating"><option value="all">全部评级</option>' + ratingOptions() + '</select>' +
      '<label class="chk"><input type="checkbox" id="fNoRedeem"> 剔除强赎</label>' +
      '<button class="btn" id="fReset">重置</button>' +
      '<span class="spacer"></span><span class="cnt" id="fCount"></span></div>' +
      '<div class="tbl-wrap"><table class="tbl tbl-all" id="allTbl"><thead><tr>' +
      '<th>转债</th><th class="sortable" data-k="price">价格</th><th>涨跌</th><th>正股</th>' +
      '<th class="sortable" data-k="conv_price">转股价</th><th class="sortable" data-k="conv_value">转股价值</th>' +
      '<th class="sortable" data-k="premium">溢价率</th><th class="sortable on" data-k="dblow">双低</th>' +
      '<th class="sortable" data-k="pure_bond">纯债价值</th><th class="sortable" data-k="pure_premium">纯债溢价</th>' +
      '<th class="sortable" data-k="rating">评级</th><th class="sortable" data-k="size">发行(亿)</th>' +
      '<th class="sortable" data-k="year_left">剩余(年)</th><th>状态</th>' +
      '</tr></thead><tbody id="allBody"></tbody></table></div>' +
      '<div class="pager" id="pager"></div>' +
      '<div class="note">纯债价值来自东方财富可转债历史估值（逐券最新一期，允许滞后 5 个交易日）；纯债溢价 = 现价 / 纯债价值 − 1。' +
      '规模列为发行规模（非剩余规模）；状态列：强赎/不强赎为公告事件，0/15 为 15 个交易日中触发强赎条件的进度。</div></section>';

    // 7 分布
    html += '<section class="card" data-toc="价格与溢价率分布" data-toc-group="分布">' +
      '<div class="sec-head"><div><div class="sec-title">价格与溢价率分布</div>' +
      '<div class="sec-sub">全市场 ' + O.count + ' 只 · 横截面计数</div></div>' +
      '<div class="sec-right"><span class="badge gray">低价券 ' + O.below_100 + ' 只</span><span class="badge gray">负溢价 ' + O.neg_premium + ' 只</span></div></div>' +
      '<div class="dist-grid"><div><div class="chart xs" id="priceDist"></div></div>' +
      '<div><div class="chart xs" id="premDist"></div></div></div>' +
      '<div class="note">价格分布按 90/100/110/120/130 元分档；溢价率分布按 0/10/20/30/50% 分档。' +
      '当前 110–120 元为最密集价格带（' + distCount(O.price_dist, "110–120") + ' 只），溢价率 ≥50% 的券占 ' +
      pctOf(O.premium_dist) + '%。</div></section>';

    // 8 事件
    html += '<section class="card" data-toc="强赎与新债" data-toc-group="事件">' +
      '<div class="grid2"><div><div class="event-head"><div class="sec-title" style="font-size:14px">强赎 / 不强赎日历</div>' +
      '<span class="badge red">强赎 ' + O.redeem_yes + '</span></div>' +
      '<div class="tbl-wrap short" style="margin-top:8px"><table class="tbl tbl-redeem"><thead><tr>' +
      '<th>类型</th><th>转债</th><th>日期</th><th>最后转股</th><th>赎回价</th><th>现价</th><th>剩余(亿)</th>' +
      '</tr></thead><tbody>' + redeemRows() + '</tbody></table></div>' +
      '<div class="note">日期：强赎为最后交易日，不强赎为重新计数起点；以公司公告为准。</div></div>' +
      '<div><div class="event-head"><div class="sec-title" style="font-size:14px">新债申购</div>' +
      '<span class="badge blue">近期 ' + D.new_bonds.length + ' 只</span></div>' +
      '<div class="tbl-wrap short" style="margin-top:8px"><table class="tbl tbl-new"><thead><tr>' +
      '<th>申购日</th><th>转债</th><th>正股</th><th>计划(亿)</th><th>转股价</th><th>上市日</th>' +
      '</tr></thead><tbody>' + newRows() + '</tbody></table></div>' +
      '<div class="note">新债日历来自同花顺，覆盖申购日前 30 天至未来 45 天；上市日为空表示待定。</div></div></div></section>';

    html += '<div class="foot"><b>数据来源</b>：中证指数（000832 中证转债指数）、东方财富（全市场转债快照 / 纯债估值）、' +
      '集思录（等权指数 / 强赎状态）、同花顺（新债日历）。<br>' +
      '<b>口径</b>：双低 = 价格 + 转股溢价率（百分点）；温度为集思录市场温度；纯债价值为东财估值口径。' +
      '所有指标为公开数据复算，非官方数值，仅用于研究参考，不构成投资建议。</div>';

    $("app").className = "";
    $("app").innerHTML = html;

    disposeCharts();
    renderCharts();
    bindEvents();
    if(window.mountToc){
      window.mountToc({app:"#app", bar:"#tocBar", side:"#tocSide", prefix:"cb"});
    }
    renderAllTable();
  }

  function kpi(k, v, unit, sub){
    return '<div class="kpi"><div class="k"><span>' + k + '</span></div><div class="v">' + v +
           (unit ? '<small>' + unit + '</small>' : '') + '</div><div class="s">' + sub + '</div></div>';
  }

  function longSectionHtml(){
    var LM = D.long_meta || {};
    var last = D.history.long[D.history.long.length - 1] || {};
    var eqRet = LM.eq_ret === null || LM.eq_ret === undefined ? "—" : signed(LM.eq_ret * 100, 1) + "%";
    var dd = LM.max_dd === null || LM.max_dd === undefined ? "—" : (LM.max_dd * 100).toFixed(1) + "%";
    return '<section class="card" data-toc="长周期估值" data-toc-group="估值">' +
      '<div class="sec-head"><div><div class="sec-title">长周期估值（2017-11 起）</div>' +
      '<div class="sec-sub">东方财富逐券历史复算 · 含退市券 · 2017-11-17 至 ' + (LM.end || "—") + ' · ' + (LM.days || 0) + ' 个交易日</div></div>' +
      '<div class="sec-right"><div class="ranges" id="longRanges">' +
      '<button data-r="3">3年</button><button data-r="5">5年</button><button data-r="all" class="on">全部</button>' +
      '</div></div></div>' +
      '<div class="mini-kpis">' +
      '<div class="mini-kpi"><div class="k">中位转股溢价率</div><div class="v">' + fmt(LM.mid_premium, 2) + '%</div><div class="s">长周期分位 ' + fmt(LM.premium_pct, 1) + '%</div></div>' +
      '<div class="mini-kpi"><div class="k">平均双低</div><div class="v">' + fmt(LM.avg_dblow, 2) + '</div><div class="s">长周期分位 ' + fmt(LM.dblow_pct, 1) + '%</div></div>' +
      '<div class="mini-kpi"><div class="k">等权指数（2017-11=1000）</div><div class="v">' + fmt(LM.eq_last, 0) + '</div><div class="s">累计 ' + eqRet + ' · 最大回撤 ' + dd + '</div></div>' +
      '<div class="mini-kpi"><div class="k">温度代理</div><div class="v">' + fmt(LM.temp, 1) + '</div><div class="s">近500日价格 z-score 映射</div></div>' +
      '</div>' +
      '<div class="chart" id="longEqChart" style="height:340px"></div>' +
      '<div class="grid2" style="margin-top:12px">' +
      '<div class="chart sm" id="longPremChart"></div>' +
      '<div class="chart sm" id="longTempChart"></div>' +
      '</div>' +
      '<div class="note">长序列由东财 <b>RPTA_WEB_KZZ_LS</b> 逐券历史按日横截面复算，包含退市券；2017-11 起步样本 ' + (D.history.long[0] ? (D.history.long[0].count || 30) : 30) + ' 只，2018 年后逐步增至 300+（当前 ' + (LM.count || "—") + ' 只）。' +
      '近250日与集思录交叉校验：中位溢价率 <b>' + fmt(LM.corr_premium, 3) + '</b>、平均双低 <b>' + fmt(LM.corr_dblow, 3) + '</b>、温度代理 <b>' + fmt(LM.corr_temp, 3) + '</b>。' +
      '等权指数为价格等权（不含强赎转股收益与交易成本）；温度代理 = 中位价格近 500 日 z-score 的 logistic 映射（0–100），<b>不是集思录官方温度</b>；成交额仍见下方近250日图。</div></section>';
  }

  function ratingOptions(){
    var seen = {}, out = "";
    D.bonds.forEach(function(b){
      var r = b.rating || "—";
      if(!seen[r]){ seen[r] = 1; out += '<option value="' + esc(r) + '">' + esc(r) + '</option>'; }
    });
    return out;
  }
  function distCount(arr, k){
    for(var i = 0; i < arr.length; i++) if(arr[i].k === k) return arr[i].n;
    return 0;
  }
  function avgOf(arr, key){
    var s = 0, n = 0;
    arr.forEach(function(x){ var v = Number(x[key]); if(isFinite(v)){ s += v; n++; } });
    return n ? s / n : null;
  }
  function pctOf(arr){
    var total = 0, n = 0;
    arr.forEach(function(x){ total += x.n; if(x.k === "≥50" || x.k === "50–") n += x.n; });
    return total ? (n / total * 100).toFixed(1) : "0.0";
  }

  // ---------------- 表格 ----------------
  function topRows(){
    return D.bonds.slice(0, 20).map(function(b, i){
      return '<tr><td>' + (i + 1) + '</td><td>' + nameHtml(b) + '</td>' +
        '<td>' + fmt(b.price, 2) + '</td><td>' + fmt(b.premium, 2) + '%</td>' +
        '<td><b>' + fmt(b.dblow, 2) + '</b></td><td>' + esc(b.stock || "—") + '</td>' +
        '<td>' + ratingBadge(b.rating) + '</td><td>' + fmt(b.size, 1) + '</td>' +
        '<td>' + fmt(b.year_left, 2) + '</td><td>' + redeemBadge(b) + '</td></tr>';
    }).join("");
  }

  function redeemRows(){
    if(!D.redeem.length) return '<tr><td colspan="7" style="text-align:center;color:var(--text-3)">暂无公告事件</td></tr>';
    return D.redeem.map(function(x){
      return '<tr><td><span class="badge ' + (x.kind === "强赎" ? "red" : "green") + '">' + esc(x.kind) + '</span></td>' +
        '<td><div class="c-name"><a href="https://data.eastmoney.com/kzz/detail/' + x.code + '.html" target="_blank" rel="noopener">' + esc(x.name) + '</a><span>' + esc(x.code) + '</span></div></td>' +
        '<td>' + shortDate(x.event) + '</td><td>' + shortDate(x.last_convert) + '</td>' +
        '<td>' + fmt(x.redeem_price, 2) + '</td><td>' + fmt(x.price, 2) + '</td><td>' + fmt(x.size, 2) + '</td></tr>';
    }).join("");
  }

  function newRows(){
    if(!D.new_bonds.length) return '<tr><td colspan="6" style="text-align:center;color:var(--text-3)">近期暂无新债</td></tr>';
    return D.new_bonds.map(function(x){
      return '<tr><td>' + shortDate(x.sub_date) + '</td>' +
        '<td><div class="c-name"><span style="font-weight:700;color:var(--ink)">' + esc(x.name) + '</span><span>' + esc(x.code) + '</span></div></td>' +
        '<td>' + esc(x.stock || "—") + '</td><td>' + fmt(x.size, 2) + '</td>' +
        '<td>' + fmt(x.conv_price, 2) + '</td><td>' + (x.listing_date ? shortDate(x.listing_date) : "待定") + '</td></tr>';
    }).join("");
  }

  function allRow(b){
    return '<tr><td>' + nameHtml(b) + '</td>' +
      '<td>' + fmt(b.price, 2) + '</td>' +
      '<td class="' + cls(b.chg) + '">' + (b.chg === null || b.chg === undefined ? "—" : signed(b.chg, 2) + "%") + '</td>' +
      '<td>' + stockHtml(b) + '</td>' +
      '<td>' + fmt(b.conv_price, 2) + '</td>' +
      '<td>' + fmt(b.conv_value, 2) + '</td>' +
      '<td>' + fmt(b.premium, 2) + '%</td>' +
      '<td><b>' + fmt(b.dblow, 2) + '</b></td>' +
      '<td>' + fmt(b.pure_bond, 2) + '</td>' +
      '<td>' + fmt(b.pure_premium, 2) + '%</td>' +
      '<td>' + ratingBadge(b.rating) + '</td>' +
      '<td>' + fmt(b.size, 2) + '</td>' +
      '<td>' + fmt(b.year_left, 2) + '</td>' +
      '<td>' + redeemBadge(b) + '</td></tr>';
  }

  function cmp(a, b){
    var va = a[state.sortKey], vb = b[state.sortKey];
    if(va === null || va === undefined){
      if(vb === null || vb === undefined) return 0;
      return 1;
    }
    if(vb === null || vb === undefined) return -1;
    if(state.sortKey === "rating"){
      return (ratingRank(va) - ratingRank(vb)) * state.sortDir;
    }
    if(typeof va === "string" || typeof vb === "string"){
      return String(va).localeCompare(String(vb)) * state.sortDir;
    }
    return (Number(va) - Number(vb)) * state.sortDir;
  }
  function ratingRank(r){
    var m = {"AAA":12, "AA+":11, "AA":10, "AA-":9, "A+":8, "A":7, "A-":6,
             "BBB+":5, "BBB":4, "BBB-":3, "BB+":2, "BB":1, "BB-":0,
             "B+":-1, "B":-2, "B-":-3, "CCC":-4, "CC":-5, "C":-6, "D":-7};
    return m[r] === undefined ? 0 : m[r];
  }

  function filteredBonds(){
    var q = state.q;
    return D.bonds.filter(function(b){
      if(q && (b.code + " " + b.name + " " + (b.stock || "")).toLowerCase().indexOf(q) < 0) return false;
      var p = Number(b.price);
      if(state.price === "lt100" && !(p < 100)) return false;
      if(state.price === "100120" && !(p >= 100 && p < 120)) return false;
      if(state.price === "120130" && !(p >= 120 && p < 130)) return false;
      if(state.price === "ge130" && !(p >= 130)) return false;
      var pr = Number(b.premium);
      if(state.premium === "lt10" && !(pr < 10)) return false;
      if(state.premium === "1030" && !(pr >= 10 && pr < 30)) return false;
      if(state.premium === "3050" && !(pr >= 30 && pr < 50)) return false;
      if(state.premium === "ge50" && !(pr >= 50)) return false;
      if(state.rating !== "all" && b.rating !== state.rating) return false;
      if(state.noRedeem && b.redeem_kind === "强赎") return false;
      return true;
    }).sort(cmp);
  }

  function renderAllTable(){
    if(!D) return;
    var rows = filteredBonds();
    var pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    if(state.page > pages) state.page = pages;
    if(state.page < 1) state.page = 1;
    var start = (state.page - 1) * state.pageSize;
    var pageRows = rows.slice(start, start + state.pageSize);
    var body = $("allBody");
    if(body) body.innerHTML = pageRows.map(allRow).join("") ||
      '<tr><td colspan="14" style="text-align:center;color:var(--text-3);padding:24px">没有匹配的转债</td></tr>';
    var cnt = $("fCount");
    if(cnt) cnt.textContent = "筛选 " + rows.length + " / " + D.bonds.length + " 只";
    var pager = $("pager");
    if(pager){
      pager.innerHTML = '<button data-page="prev" ' + (state.page <= 1 ? "disabled" : "") + '>上一页</button>' +
        '<span>第 <b>' + state.page + '</b> / ' + pages + ' 页 · 共 ' + rows.length + ' 只</span>' +
        '<button data-page="next" ' + (state.page >= pages ? "disabled" : "") + '>下一页</button>';
    }
    var tbl = $("allTbl");
    if(tbl){
      Array.prototype.forEach.call(tbl.querySelectorAll("th[data-k]"), function(th){
        var on = th.getAttribute("data-k") === state.sortKey;
        th.classList.toggle("on", on);
        if(on) th.setAttribute("data-dir", String(state.sortDir));
        else th.removeAttribute("data-dir");
      });
    }
  }

  function bindEvents(){
    var r = $("csiRanges");
    if(r){
      r.onclick = function(e){
        var b = e.target.closest("button[data-r]");
        if(!b) return;
        state.csiRange = b.getAttribute("data-r");
        Array.prototype.forEach.call(r.querySelectorAll("button"), function(x){ x.classList.toggle("on", x === b); });
        renderCsi();
      };
    }

    var lr = $("longRanges");
    if(lr){
      lr.onclick = function(e){
        var b = e.target.closest("button[data-r]");
        if(!b) return;
        state.longRange = b.getAttribute("data-r");
        Array.prototype.forEach.call(lr.querySelectorAll("button"), function(x){ x.classList.toggle("on", x === b); });
        renderLong();
      };
    }

    var tb = $("allTbl");
    if(tb){
      tb.querySelector("thead").onclick = function(e){
        var th = e.target.closest("th[data-k]");
        if(!th) return;
        var k = th.getAttribute("data-k");
        if(state.sortKey === k) state.sortDir = -state.sortDir;
        else { state.sortKey = k; state.sortDir = (k === "rating" ? 1 : 1); }
        state.page = 1;
        renderAllTable();
      };
    }

    var s = $("fSearch");
    if(s){
      var timer = null;
      s.oninput = function(){
        var v = s.value.trim().toLowerCase();
        clearTimeout(timer);
        timer = setTimeout(function(){ state.q = v; state.page = 1; renderAllTable(); }, 140);
      };
    }
    var fp = $("fPrice");
    if(fp) fp.onchange = function(){ state.price = fp.value; state.page = 1; renderAllTable(); };
    var fpr = $("fPremium");
    if(fpr) fpr.onchange = function(){ state.premium = fpr.value; state.page = 1; renderAllTable(); };
    var fr = $("fRating");
    if(fr) fr.onchange = function(){ state.rating = fr.value; state.page = 1; renderAllTable(); };
    var fn = $("fNoRedeem");
    if(fn) fn.onchange = function(){ state.noRedeem = fn.checked; state.page = 1; renderAllTable(); };
    var reset = $("fReset");
    if(reset){
      reset.onclick = function(){
        state.q = ""; state.price = "all"; state.premium = "all"; state.rating = "all";
        state.noRedeem = false; state.sortKey = "dblow"; state.sortDir = 1; state.page = 1;
        if(s) s.value = "";
        if(fp) fp.value = "all";
        if(fpr) fpr.value = "all";
        if(fr) fr.value = "all";
        if(fn) fn.checked = false;
        renderAllTable();
      };
    }
    var pager = $("pager");
    if(pager){
      pager.onclick = function(e){
        var b = e.target.closest("button[data-page]");
        if(!b) return;
        var d = b.getAttribute("data-page");
        state.page += d === "next" ? 1 : -1;
        renderAllTable();
      };
    }
  }

  // ---------------- 图表 ----------------
  function disposeCharts(){
    charts.forEach(function(c){ try{ c.dispose(); }catch(e){} });
    charts = []; refs = {};
  }
  function mk(id){
    var el = $(id);
    if(!el) return null;
    var c = echarts.init(el);
    charts.push(c);
    return c;
  }
  function axisLine(){ return {lineStyle:{color:cssVar("--line")}}; }
  function axisLabel(){ return {color:cssVar("--text-3"), fontSize:10}; }
  function splitLine(){ return {lineStyle:{color:cssVar("--line"), opacity:.65}}; }
  function legendStyle(){ return {top:0, right:0, itemWidth:10, itemHeight:6, textStyle:{color:cssVar("--text-2"), fontSize:10}}; }
  function tooltipStyle(trigger){
    return {
      trigger: trigger || "axis",
      backgroundColor: cssVar("--chart-tip-bg"),
      borderColor: cssVar("--chart-tip-bd"),
      borderWidth: 1,
      textStyle: {color: cssVar("--chart-tip-fg"), fontSize:11},
      axisPointer: {type:"cross", lineStyle:{color:cssVar("--chart-mark"), type:"dashed"}}
    };
  }
  function area(top, bottom){
    return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      {offset:0, color:rgba(top, .26)},
      {offset:1, color:rgba(bottom, 0)}
    ]);
  }
  function timeAxis(rows, boundary, long){
    return {
      type:"category", boundaryGap:!!boundary, data:rows.map(function(x){return x.d;}),
      axisLine:axisLine(), axisTick:{show:false},
      axisLabel:Object.assign(axisLabel(), {
        formatter:function(v){ return long ? String(v).slice(0, 7) : String(v).slice(5); },
        hideOverlap:true
      })
    };
  }

  function renderCharts(){
    renderCsi();
    renderLong();
    renderJsl();
    renderPremium();
    renderTemp();
    renderScatter();
    renderDist();
  }

  function longRows(){
    var all = (D.history && D.history.long) || [];
    if(!all.length) return [];
    if(state.longRange === "all") return all;
    var days = state.longRange === "3" ? 1095 : 1825;
    var last = new Date(all[all.length - 1].d + "T00:00:00");
    var cut = new Date(last.getTime() - days * 86400000);
    return all.filter(function(x){ return new Date(x.d + "T00:00:00") >= cut; });
  }

  function renderLong(){
    var rows = longRows();
    if(!rows.length) return;
    renderLongEq(rows);
    renderLongPrem(rows);
    renderLongTemp(rows);
  }

  function renderLongEq(rows){
    var el = $("longEqChart");
    if(!el) return;
    var c = refs.longEq || mk("longEqChart");
    refs.longEq = c;
    var brand = cssVar("--brand"), neu = cssVar("--neu");
    c.setOption({
      animation:false,
      grid:{left:52,right:18,top:30,bottom:28},
      legend: legendStyle(),
      tooltip: Object.assign(tooltipStyle("axis"), {
        formatter:function(p){
          var r = rows[p[0].dataIndex]; if(!r) return "";
          var er = r.eq === null ? null : (r.eq / 1000 - 1) * 100;
          var cr = r.csi === null ? null : (r.csi / 1000 - 1) * 100;
          return '<b>' + r.d + '</b><br>全市场等权 ' + fmt(r.eq, 1) + '（' + signed(er, 1) + '%）' +
                 '<br>中证转债 ' + fmt(r.csi, 1) + '（' + signed(cr, 1) + '%）<br>样本 ' + r.count + ' 只';
        }
      }),
      xAxis: timeAxis(rows, false, true),
      yAxis: {type:"value", scale:true, splitLine:splitLine(), axisLabel:Object.assign(axisLabel(), {formatter:function(v){ return Number(v).toFixed(0); }})},
      series:[
        {name:"全市场等权（自算）", type:"line", data:rows.map(function(x){return x.eq;}), symbol:"none", lineStyle:{width:2,color:brand}, areaStyle:{color:area(brand,brand)}},
        {name:"中证转债（同起点）", type:"line", data:rows.map(function(x){return x.csi;}), symbol:"none", lineStyle:{width:1.5,color:neu,type:"dashed"}}
      ]
    }, true);
  }

  function renderLongPrem(rows){
    var el = $("longPremChart");
    if(!el) return;
    var c = refs.longPrem || mk("longPremChart");
    refs.longPrem = c;
    var brand = cssVar("--brand"), neu = cssVar("--neu");
    c.setOption({
      animation:false,
      grid:{left:44,right:46,top:34,bottom:26},
      legend: legendStyle(),
      tooltip: Object.assign(tooltipStyle("axis"), {
        formatter:function(p){
          var r = rows[p[0].dataIndex]; if(!r) return "";
          return '<b>' + r.d + '</b><br>中位溢价率 ' + fmt(r.mid_premium, 2) + '%' +
                 '<br>平均双低 ' + fmt(r.avg_dblow, 2) + '<br>中位纯债溢价 ' + fmt(r.pure_premium, 2) + '%<br>样本 ' + r.count + ' 只';
        }
      }),
      xAxis: timeAxis(rows, false, true),
      yAxis:[
        {type:"value", name:"溢价率%", scale:true, nameTextStyle:{color:cssVar("--text-3"),fontSize:9}, splitLine:splitLine(), axisLabel:Object.assign(axisLabel(), {formatter:function(v){return v + "%";}})},
        {type:"value", scale:true, splitLine:{show:false}, axisLabel:axisLabel()}
      ],
      series:[
        {name:"中位溢价率", type:"line", data:rows.map(function(x){return x.mid_premium;}), symbol:"none", lineStyle:{width:2,color:neu}, areaStyle:{color:area(neu,neu)}},
        {name:"平均双低", type:"line", yAxisIndex:1, data:rows.map(function(x){return x.avg_dblow;}), symbol:"none", lineStyle:{width:1.8,color:brand,type:"dashed"}}
      ]
    }, true);
  }

  function renderLongTemp(rows){
    var el = $("longTempChart");
    if(!el) return;
    var c = refs.longTemp || mk("longTempChart");
    refs.longTemp = c;
    var brand = cssVar("--brand"), neu = cssVar("--neu"), pos = cssVar("--pos");
    var jmap = {};
    (D.history.jsl || []).forEach(function(x){ jmap[x.d] = x.temp; });
    c.setOption({
      animation:false,
      grid:{left:40,right:16,top:34,bottom:26},
      legend: legendStyle(),
      tooltip: Object.assign(tooltipStyle("axis"), {
        formatter:function(p){
          var r = rows[p[0].dataIndex]; if(!r) return "";
          return '<b>' + r.d + '</b><br>自算温度代理 ' + fmt(r.temp, 1) +
                 '<br>集思录温度 ' + fmt(jmap[r.d], 1) + '<br>样本 ' + r.count + ' 只';
        }
      }),
      xAxis: timeAxis(rows, false, true),
      yAxis:{type:"value", min:0, max:100, splitLine:splitLine(), axisLabel:axisLabel()},
      series:[
        {name:"自算温度代理", type:"line", data:rows.map(function(x){return x.temp;}), symbol:"none", smooth:0.2, lineStyle:{width:1.8,color:brand}},
        {name:"集思录温度（近250日）", type:"line", data:rows.map(function(x){return jmap[x.d] === undefined ? null : jmap[x.d];}), symbol:"none", smooth:0.2, connectNulls:false, lineStyle:{width:1.5,color:neu,type:"dashed"}}
      ]
    }, true);
  }

  function csiRows(){
    var all = D.history.csi;
    if(state.csiRange === "all") return all;
    var days = state.csiRange === "1" ? 365 : state.csiRange === "3" ? 1095 : 1825;
    var last = new Date(all[all.length - 1].d + "T00:00:00");
    var cut = new Date(last.getTime() - days * 86400000);
    return all.filter(function(x){ return new Date(x.d + "T00:00:00") >= cut; });
  }

  function renderCsi(){
    var el = $("csiChart");
    if(!el) return;
    var rows = csiRows();
    var c = refs.csi;
    if(!c){ c = mk("csiChart"); refs.csi = c; }
    var brand = cssVar("--brand");
    c.setOption({
      animation: false,
      grid: {left:52, right:18, top:16, bottom:28},
      tooltip: Object.assign(tooltipStyle("axis"), {
        formatter: function(p){
          var r = rows[p[0].dataIndex];
          if(!r) return "";
          return '<b>' + r.d + '</b><br>收盘 ' + fmt(r.c, 2) +
                 '<br>涨跌 <span style="color:' + (r.chg > 0 ? cssVar("--up") : r.chg < 0 ? cssVar("--down") : cssVar("--text-2")) + '">' +
                 pctText(r.chg, 2) + '</span><br>成交额 ' + fmt(r.amt, 0) + ' 亿';
        }
      }),
      xAxis: timeAxis(rows, false),
      yAxis: {type:"value", scale:true, splitLine:splitLine(), axisLabel:Object.assign(axisLabel(), {formatter:function(v){return Number(v).toFixed(0);}})},
      series: [{
        type:"line", name:"中证转债", data:rows.map(function(x){return x.c;}),
        symbol:"none", lineStyle:{width:2, color:brand},
        areaStyle:{color:area(brand, brand)},
        emphasis:{focus:"series"}
      }]
    }, true);
  }

  function renderJsl(){
    var el = $("jslChart");
    if(!el) return;
    var rows = D.history.jsl;
    var c = mk("jslChart"); refs.jsl = c;
    var brand = cssVar("--brand"), neu = cssVar("--neu");
    c.setOption({
      animation:false,
      grid:{left:52,right:56,top:30,bottom:28},
      legend: legendStyle(),
      tooltip: Object.assign(tooltipStyle("axis"), {
        formatter:function(p){
          var r = rows[p[0].dataIndex]; if(!r) return "";
          return '<b>' + r.d + '</b><br>等权指数 ' + fmt(r.idx, 2) + '（' + pctText(r.idx_chg, 2) + '）' +
                 '<br>成交额 ' + fmt(r.amount, 2) + ' 亿<br>转债数 ' + r.count + ' 只';
        }
      }),
      xAxis: timeAxis(rows, true),
      yAxis:[
        {type:"value", name:"指数", scale:true, nameTextStyle:{color:cssVar("--text-3"),fontSize:10}, splitLine:splitLine(), axisLabel:axisLabel()},
        {type:"value", name:"", nameTextStyle:{color:cssVar("--text-3"),fontSize:10}, splitLine:{show:false}, axisLabel:axisLabel()}
      ],
      series:[
        {name:"等权指数", type:"line", data:rows.map(function(x){return x.idx;}), symbol:"none", lineStyle:{width:2,color:brand}, areaStyle:{color:area(brand,brand)}},
        {name:"成交额(亿)", type:"bar", yAxisIndex:1, data:rows.map(function(x){return x.amount;}), barWidth:"55%", itemStyle:{color:rgba(neu,.18)}, emphasis:{itemStyle:{color:rgba(neu,.35)}}}
      ]
    }, true);
  }

  function renderPremium(){
    var el = $("premChart");
    if(!el) return;
    var rows = D.history.jsl;
    var c = mk("premChart"); refs.prem = c;
    var brand = cssVar("--brand"), neu = cssVar("--neu");
    c.setOption({
      animation:false,
      grid:{left:46,right:50,top:34,bottom:26},
      legend: legendStyle(),
      tooltip: Object.assign(tooltipStyle("axis"), {
        formatter:function(p){
          var r = rows[p[0].dataIndex]; if(!r) return "";
          return '<b>' + r.d + '</b><br>中位溢价率 ' + fmt(r.mid_premium, 2) + '%' +
                 '<br>平均双低 ' + fmt(r.dblow, 2) + '<br>平均溢价率 ' + fmt(r.avg_premium, 2) + '%';
        }
      }),
      xAxis: timeAxis(rows, false),
      yAxis:[
        {type:"value", name:"溢价率%", scale:true, nameTextStyle:{color:cssVar("--text-3"),fontSize:10}, splitLine:splitLine(), axisLabel:Object.assign(axisLabel(),{formatter:function(v){return v + "%";}})},
        {type:"value", name:"", scale:true, nameTextStyle:{color:cssVar("--text-3"),fontSize:10}, splitLine:{show:false}, axisLabel:axisLabel()}
      ],
      series:[
        {name:"中位溢价率", type:"line", data:rows.map(function(x){return x.mid_premium;}), symbol:"none", lineStyle:{width:2,color:neu}, areaStyle:{color:area(neu,neu)}},
        {name:"平均双低", type:"line", yAxisIndex:1, data:rows.map(function(x){return x.dblow;}), symbol:"none", lineStyle:{width:1.8,color:brand,type:"dashed"}}
      ]
    }, true);
  }

  function renderTemp(){
    var el = $("tempChart");
    if(!el) return;
    var rows = D.history.jsl;
    var c = mk("tempChart"); refs.temp = c;
    var brand = cssVar("--brand"), pos = cssVar("--pos"), cau = cssVar("--cau"), line = cssVar("--line");
    c.setOption({
      animation:false,
      grid:{left:40,right:16,top:30,bottom:26},
      tooltip: Object.assign(tooltipStyle("axis"), {
        formatter:function(p){
          var r = rows[p[0].dataIndex]; if(!r) return "";
          return '<b>' + r.d + '</b><br>市场温度 ' + fmt(r.temp, 2) + '（' + tempLabel(r.temp) + '）<br>成交额 ' + fmt(r.amount, 0) + ' 亿';
        }
      }),
      xAxis: timeAxis(rows, false),
      yAxis:{type:"value", min:0, max:100, splitLine:splitLine(), axisLabel:axisLabel()},
      series:[{
        name:"市场温度", type:"line", data:rows.map(function(x){return x.temp;}), symbol:"none",
        lineStyle:{width:2,color:brand}, areaStyle:{color:area(brand,brand)},
        markArea:{silent:true, itemStyle:{color:rgba(pos,.055)}, data:[[{yAxis:0},{yAxis:20}],[{yAxis:80},{yAxis:100}]]},
        markLine:{silent:true, symbol:"none", lineStyle:{type:"dashed",color:line}, label:{color:cssVar("--text-3"),fontSize:9,formatter:"{c}"}, data:[{yAxis:20},{yAxis:50},{yAxis:80}]}
      }]
    }, true);
  }

  function renderScatter(){
    var el = $("scatterChart");
    if(!el) return;
    var c = mk("scatterChart"); refs.scatter = c;
    var pts = D.bonds.filter(function(b){
      return b.price !== null && b.premium !== null && b.price <= 300 && b.premium <= 150 && b.premium >= -15;
    });
    var brand = cssVar("--brand");
    c.setOption({
      animation:false,
      grid:{left:50,right:18,top:18,bottom:38},
      tooltip: Object.assign(tooltipStyle("item"), {
        formatter:function(p){
          var v = p.data.value;
          return '<b>' + esc(v[5]) + '</b> ' + esc(v[4]) + '<br>价格 ' + fmt(v[0], 2) + ' 元' +
                 '<br>溢价率 ' + fmt(v[1], 2) + '%<br>双低 ' + fmt(v[3], 2) + '<br>发行规模 ' + fmt(v[2], 1) + ' 亿';
        }
      }),
      xAxis:{type:"value", min:70, max:300, name:"价格(元)", nameLocation:"middle", nameGap:24, nameTextStyle:{color:cssVar("--text-3"),fontSize:10}, splitLine:splitLine(), axisLine:axisLine(), axisLabel:axisLabel()},
      yAxis:{type:"value", min:-15, max:150, name:"溢价率(%)", nameLocation:"middle", nameGap:34, nameTextStyle:{color:cssVar("--text-3"),fontSize:10}, splitLine:splitLine(), axisLine:axisLine(), axisLabel:axisLabel()},
      visualMap:{show:false, min:80, max:280, dimension:3, inRange:{color:["#11b66e", "#f4c430", "#ff4713"]}},
      series:[{
        type:"scatter",
        data: pts.map(function(b){ return {value:[b.price, b.premium, b.size || 0, b.dblow, b.code, b.name]}; }),
        symbolSize:function(v){ return Math.max(6, Math.min(22, 5 + Math.sqrt(Math.max(1, v[2] || 5)) * 1.4)); },
        itemStyle:{opacity:.82, borderColor:"rgba(255,255,255,.55)", borderWidth:.5},
        emphasis:{scale:1.5, itemStyle:{opacity:1, borderColor:brand, borderWidth:1.2}}
      }]
    }, true);
  }

  function renderDist(){
    var colors = ["#11b66e", "#5fb87a", "#f4c430", "#f97316", "#ef4444", "#c62828"];
    var pd = $("priceDist");
    if(pd){
      var c1 = mk("priceDist"); refs.pdist = c1;
      var a1 = D.market.own.price_dist;
      c1.setOption({
        animation:false,
        grid:{left:36,right:12,top:24,bottom:26},
        tooltip:Object.assign(tooltipStyle("axis"), {formatter:function(p){ return p[0].name + " 元<br><b>" + p[0].value + "</b> 只"; }}),
        xAxis:{type:"category", data:a1.map(function(x){return x.k;}), axisLine:axisLine(), axisTick:{show:false}, axisLabel:axisLabel()},
        yAxis:{type:"value", splitLine:splitLine(), axisLabel:axisLabel()},
        series:[{type:"bar", data:a1.map(function(x,i){ return {value:x.n, itemStyle:{color:colors[i % colors.length], borderRadius:[3,3,0,0]}}; }), barWidth:"55%", label:{show:true, position:"top", color:cssVar("--text-2"), fontSize:10}}]
      }, true);
    }
    var pr = $("premDist");
    if(pr){
      var c2 = mk("premDist"); refs.prdist = c2;
      var a2 = D.market.own.premium_dist;
      c2.setOption({
        animation:false,
        grid:{left:36,right:12,top:24,bottom:26},
        tooltip:Object.assign(tooltipStyle("axis"), {formatter:function(p){ return p[0].name + "% 溢价率<br><b>" + p[0].value + "</b> 只"; }}),
        xAxis:{type:"category", data:a2.map(function(x){return x.k + "%";}), axisLine:axisLine(), axisTick:{show:false}, axisLabel:axisLabel()},
        yAxis:{type:"value", splitLine:splitLine(), axisLabel:axisLabel()},
        series:[{type:"bar", data:a2.map(function(x,i){ return {value:x.n, itemStyle:{color:colors[i % colors.length], borderRadius:[3,3,0,0]}}; }), barWidth:"55%", label:{show:true, position:"top", color:cssVar("--text-2"), fontSize:10}}]
      }, true);
    }
  }

  // ---------------- 主题 ----------------
  function applyTheme(t, refresh){
    theme = t === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", theme);
    try{
      localStorage.setItem("cb_theme", theme);
      localStorage.setItem("fg_theme", theme);
    }catch(e){}
    var b = $("themeToggle");
    if(b){
      b.querySelector(".tt-ico").textContent = theme === "dark" ? "☀" : "◐";
      b.querySelector(".tt-label").textContent = theme === "dark" ? "浅色" : "暗色";
    }
    if(refresh){
      disposeCharts();
      renderCharts();
    }
  }

  function init(){
    var saved = "light";
    try{ saved = localStorage.getItem("cb_theme") || localStorage.getItem("fg_theme") || "light"; }catch(e){}
    try{
      var q = new URLSearchParams(location.search).get("theme");
      if(q === "dark" || q === "light") saved = q;
    }catch(e){}
    applyTheme(saved, false);

    if(!D){ renderAll(); return; }
    renderAll();

    var tb = $("themeToggle");
    if(tb) tb.onclick = function(){ applyTheme(theme === "dark" ? "light" : "dark", true); };

    var rt = null;
    window.addEventListener("resize", function(){
      if(rt) return;
      rt = requestAnimationFrame(function(){ rt = null; charts.forEach(function(c){ try{ c.resize(); }catch(e){} }); });
    });
  }

  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
