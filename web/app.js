/* 公共前端 helper：cssVar / crossPointer / mountToc（二级目录）
 * mood.html 与 indicators.html 共用，消除双份维护。
 * 必须在 echarts.min.js 之后、各页面内联脚本之前加载。
 */
function cssVar(name){
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// 十字准星：竖线定位某一天，横线给出该点在纵轴上的历史水位，便于对照
function crossPointer(){
  var c = cssVar("--chart-mark") || "#d4d4d4";
  return {
    type:"cross", snap:true,
    lineStyle:{color:c, type:"dashed", width:1},
    crossStyle:{color:c, type:"dashed", width:1},
    label:{
      backgroundColor:cssVar("--chart-tip-bg") || "#fff",
      borderColor:cssVar("--chart-tip-bd") || "#e8e8e8",
      borderWidth:1,
      color:cssVar("--chart-tip-fg") || "#111",
      fontSize:10
    }
  };
}

/* ---------------- 二级目录：本页卡片快速定位 ----------------
 * 页面渲染完成后调用 mountToc({app:"#app", bar:"#tocBar", side:"#tocSide"})。
 * 卡片只需标注 data-toc="名称" data-toc-group="分组"，目录自动扫描生成，
 * 新增卡片无需改目录代码。
 * 一级目录 = 页面分组，二级目录 = 组内卡片。能力：
 *   - 点击定位（平滑滚动 + 卡片闪烁提示 + hash 可分享）
 *   - 搜索框（按 / 聚焦、↑↓ 选择、Enter 跳转、Esc 关闭）
 *   - 滚动时自动高亮当前分组/卡片（scrollspy）
 *   - 侧边栏同步生成二级树，默认展开当前分组
 *   - 折叠状态记忆在 localStorage
 */
var _tocCleanup = null, _tocInited = false;
var TOC_ALL = "__all__";

function tocEsc(s){
  return String(s == null ? "" : s).replace(/[&<>"']/g, function(c){
    return {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c];
  });
}

function mountToc(opts){
  opts = opts || {};
  var app  = document.querySelector(opts.app  || "#app");
  var bar  = document.querySelector(opts.bar  || "#tocBar");
  var side = document.querySelector(opts.side || "#tocSide");
  if(!app || !bar) return;
  if(_tocCleanup){ try{ _tocCleanup(); }catch(e){} _tocCleanup = null; }

  // 1) 扫描卡片 -> 分组
  var groups = [], gmap = {}, items = [];
  Array.prototype.forEach.call(app.querySelectorAll("[data-toc]"), function(el, i){
    var g = el.getAttribute("data-toc-group") || "其他";
    if(!gmap[g]){ gmap[g] = {name:g, items:[]}; groups.push(gmap[g]); }
    if(!el.id) el.id = "toc_" + (opts.prefix || "card") + "_" + i;
    var it = {id:el.id, label:el.getAttribute("data-toc") || ("卡片 " + (i + 1)), group:g, el:el};
    gmap[g].items.push(it); items.push(it);
    el.classList.add("toc-target");
  });
  if(!items.length){ bar.innerHTML = ""; if(side) side.innerHTML = ""; return; }

  var key = "toc:" + location.pathname.replace(/^\/web/, "");
  var collapsed = false;
  var curGroup = TOC_ALL;          // 默认「全部展开」：二级条目一次性列出全部分组
  var curId = items[0].id;         // 当前滚动位置所在卡片
  var matches = [], sel = 0, manual = {}, timers = [];
  try{ collapsed = localStorage.getItem(key + ":collapsed_v2") === "1"; }catch(e){}

  bar.className = "toc-bar" + (collapsed ? " collapsed" : "");
  bar.innerHTML =
    '<div class="toc-hd">' +
      '<div class="toc-title"><span class="toc-ico">☰</span> 本页目录' +
        '<span class="toc-count">' + items.length + ' 项</span></div>' +
      '<div class="toc-groups" id="tocGroups"></div>' +
      '<div class="toc-tools">' +
        '<div class="toc-search"><input id="tocSearch" type="text" autocomplete="off" ' +
          'placeholder="搜索卡片，回车跳转" aria-label="搜索卡片" />' +
          '<span class="toc-kbd">/</span><div class="toc-results" id="tocResults"></div></div>' +
        '<button type="button" class="toc-collapse" id="tocCollapse">' +
          (collapsed ? "展开" : "收起") + '</button>' +
      '</div>' +
    '</div>' +
    '<div class="toc-l2" id="tocL2"></div>';

  var groupsEl = bar.querySelector("#tocGroups");
  var l2El     = bar.querySelector("#tocL2");
  var resultsEl= bar.querySelector("#tocResults");
  var input    = bar.querySelector("#tocSearch");

  function offset(){
    var tb = document.querySelector(".topbar");
    var h1 = tb ? tb.getBoundingClientRect().height : 56;
    var h2 = collapsed ? 0 : bar.getBoundingClientRect().height;
    return h1 + h2 + 14;
  }
  function byId(id){
    for(var i = 0; i < items.length; i++) if(items[i].id === id) return items[i];
    return null;
  }
  function groupOf(name){
    for(var i = 0; i < groups.length; i++) if(groups[i].name === name) return groups[i];
    return groups[0];
  }
  function flash(el){
    el.classList.remove("toc-flash"); void el.offsetWidth; el.classList.add("toc-flash");
    var t = setTimeout(function(){ el.classList.remove("toc-flash"); }, 1700);
    timers.push(t);
  }
  function renderGroups(){
    var h = '<button type="button" class="toc-g" data-g="' + TOC_ALL + '">全部展开' +
            '<span>' + items.length + '</span></button>';
    groups.forEach(function(g){
      h += '<button type="button" class="toc-g" data-g="' + tocEsc(g.name) + '">' +
           tocEsc(g.name) + '<span>' + g.items.length + '</span></button>';
    });
    groupsEl.innerHTML = h;
  }
  // 默认展示全部分组的全部卡片；点某个分组 = 只看该组
  function renderL2(){
    var h = "", n = 0;
    groups.forEach(function(g){
      if(curGroup !== TOC_ALL && curGroup !== g.name) return;
      h += '<span class="toc-l2-label">' + tocEsc(g.name) + ' ›</span>';
      g.items.forEach(function(it){
        n++;
        h += '<button type="button" class="toc-item" data-id="' + tocEsc(it.id) + '">' +
             '<i>' + (n < 10 ? "0" : "") + n + '</i>' + tocEsc(it.label) + '</button>';
      });
    });
    l2El.className = "toc-l2" + (curGroup === TOC_ALL ? " all" : "");
    l2El.innerHTML = h;
  }
  // 侧栏二级树：默认全部分组展开，用户可手动折叠某一组
  function renderSide(){
    if(!side) return;
    var h = '<div class="toc-side-hd">本页目录 <span>' + items.length + '</span></div>';
    groups.forEach(function(g){
      var open = manual[g.name] === undefined ? true : manual[g.name];
      h += '<div class="toc-side-g' + (open ? " open" : "") + '">' +
        '<button type="button" class="toc-side-gbtn" data-gtoggle="' + tocEsc(g.name) + '">' +
          '<span class="toc-caret">▾</span>' + tocEsc(g.name) +
          '<span class="toc-side-n">' + g.items.length + '</span></button>' +
        '<div class="toc-side-items">' +
          g.items.map(function(it){
            return '<a href="#' + encodeURIComponent(it.id) + '" data-go="' + tocEsc(it.id) +
                   '" title="' + tocEsc(it.label) + '">' + tocEsc(it.label) + '</a>';
          }).join("") + '</div></div>';
    });
    side.innerHTML = h;
  }
  function markActive(){
    Array.prototype.forEach.call(groupsEl.querySelectorAll(".toc-g"), function(b){
      b.classList.toggle("active", b.getAttribute("data-g") === curGroup);
    });
    Array.prototype.forEach.call(l2El.querySelectorAll(".toc-item"), function(b){
      b.classList.toggle("active", b.getAttribute("data-id") === curId);
    });
    if(side) Array.prototype.forEach.call(side.querySelectorAll("[data-go]"), function(a){
      a.classList.toggle("active", a.getAttribute("data-go") === curId);
    });
  }
  function setCollapsed(v){
    collapsed = v;
    bar.classList.toggle("collapsed", v);
    var btn = bar.querySelector("#tocCollapse");
    if(btn) btn.textContent = v ? "展开" : "收起";
    try{ localStorage.setItem(key + ":collapsed_v2", v ? "1" : "0"); }catch(e){}
  }
  function closeResults(){
    resultsEl.classList.remove("show"); matches = []; sel = 0;
  }
  function go(it, keepHash){
    if(!it) return;
    curId = it.id;
    markActive();
    if(!keepHash){ try{ history.replaceState(null, "", "#" + encodeURIComponent(it.id)); }catch(e){} }
    var y = it.el.getBoundingClientRect().top + window.pageYOffset - offset();
    try{ window.scrollTo({top:Math.max(0, y), behavior:"smooth"}); }
    catch(e){ window.scrollTo(0, Math.max(0, y)); }
    flash(it.el);
    if(input){ input.value = ""; input.blur(); }
    closeResults();
    if(collapsed) setCollapsed(false);
  }
  function hl(text, q){
    var i = text.toLowerCase().indexOf(q);
    if(i < 0 || !q) return tocEsc(text);
    return tocEsc(text.slice(0, i)) + "<mark>" + tocEsc(text.slice(i, i + q.length)) +
           "</mark>" + tocEsc(text.slice(i + q.length));
  }
  function renderResults(q){
    q = (q || "").trim().toLowerCase();
    matches = q ? items.filter(function(it){
      return (it.label + " " + it.group).toLowerCase().indexOf(q) >= 0;
    }).slice(0, 12) : [];
    sel = 0;
    resultsEl.innerHTML = matches.length
      ? matches.map(function(it, i){
          return '<button type="button" class="toc-res' + (i === 0 ? " on" : "") +
                 '" data-res="' + i + '"><span>' + tocEsc(it.group) + '</span>' +
                 hl(it.label, q) + '</button>';
        }).join("")
      : (q ? '<div class="toc-empty">没有匹配的卡片</div>' : "");
    resultsEl.classList.toggle("show", !!q);
  }
  function moveSel(d){
    if(!matches.length) return;
    sel = (sel + d + matches.length) % matches.length;
    Array.prototype.forEach.call(resultsEl.querySelectorAll(".toc-res"), function(b, i){
      b.classList.toggle("on", i === sel);
    });
    var on = resultsEl.querySelector(".toc-res.on");
    if(on && on.scrollIntoView) on.scrollIntoView({block:"nearest"});
  }
  // 只高亮当前卡片，不切换二级视图（避免滚动时把「全部展开」收成单组）
  function syncActive(){
    var best = null, off = offset();
    for(var i = 0; i < items.length; i++){
      if(items[i].el.getBoundingClientRect().top <= off) best = items[i];
      else break;
    }
    if(!best) best = items[0];
    if(best.id === curId) return;
    curId = best.id;
    markActive();
  }

  renderGroups(); renderL2(); renderSide(); markActive();

  // 2) 事件（用 on* 属性赋值，重绘不会累积监听）
  bar.onclick = function(e){
    var t = e.target;
    var g = t.closest(".toc-g"), item = t.closest(".toc-item");
    var res = t.closest(".toc-res");
    if(t.closest("#tocCollapse")){ setCollapsed(!collapsed); return; }
    if(g){
      var name = g.getAttribute("data-g");
      if(name === TOC_ALL){ curGroup = TOC_ALL; renderL2(); markActive(); return; }
      var gr = groupOf(name);
      if(gr.items.length === 1){ go(gr.items[0]); return; }  // 单卡片分组：一步直达
      curGroup = gr.name; renderL2(); markActive();
      return;
    }
    if(item){ go(byId(item.getAttribute("data-id"))); return; }
    if(res){ go(matches[Number(res.getAttribute("data-res"))]); return; }
  };
  bar.oninput = function(e){ if(e.target === input) renderResults(input.value); };
  input.onkeydown = function(e){
    if(e.key === "ArrowDown"){ e.preventDefault(); moveSel(1); }
    else if(e.key === "ArrowUp"){ e.preventDefault(); moveSel(-1); }
    else if(e.key === "Enter"){ e.preventDefault(); if(matches.length) go(matches[sel]); }
    else if(e.key === "Escape"){ input.value = ""; closeResults(); input.blur(); }
  };
  input.onblur = function(){ var t = setTimeout(closeResults, 150); timers.push(t); };
  // 阻止搜索结果抢走焦点，避免 blur 先清空结果导致点击失效
  resultsEl.onmousedown = function(e){ e.preventDefault(); };

  if(side){
    side.onclick = function(e){
      var gt = e.target.closest("[data-gtoggle]");
      if(gt){
        var g = gt.parentNode, name = gt.getAttribute("data-gtoggle");
        var open = !g.classList.contains("open");
        g.classList.toggle("open", open); manual[name] = open;
        return;
      }
      var a = e.target.closest("[data-go]");
      if(a){ e.preventDefault(); go(byId(a.getAttribute("data-go"))); }
    };
  }

  var onScroll = function(){
    if(onScroll._p) return;
    onScroll._p = true;
    requestAnimationFrame(function(){ onScroll._p = false; syncActive(); });
  };
  var onKey = function(e){
    var tag = (e.target && e.target.tagName || "").toLowerCase();
    if(tag === "input" || tag === "textarea" || (e.target && e.target.isContentEditable)) return;
    if(e.key === "/"){ e.preventDefault(); if(collapsed) setCollapsed(false); input.focus(); input.select(); }
    else if(e.key === "Escape"){ closeResults(); }
  };
  window.addEventListener("scroll", onScroll, {passive:true});
  window.addEventListener("keydown", onKey);
  _tocCleanup = function(){
    window.removeEventListener("scroll", onScroll);
    window.removeEventListener("keydown", onKey);
    timers.forEach(clearTimeout);
    bar.onclick = bar.oninput = null;
    resultsEl.onmousedown = null;
    if(side) side.onclick = null;
  };

  // 3) 支持 #锚点直达（首次挂载时）
  if(!_tocInited){
    _tocInited = true;
    if(location.hash){
      var id = decodeURIComponent(location.hash.slice(1));
      var it = byId(id);
      if(it){ var t = setTimeout(function(){ go(it, true); }, 260); timers.push(t); }
    }
  }
}
