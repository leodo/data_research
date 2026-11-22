# -*- coding: utf-8 -*-
"""生成《因子周期画像（草案 v3）》：默认折叠微观段 + 展示标准 + R2/R3 + 噪声体检 + 试验日志。"""
import json, os
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load = lambda f: json.load(open(os.path.join(BASE, "data", f), encoding="utf-8"))
d, r2, tl, nz, st = (load("horizon_profile.json"), load("rolling_origin.json"),
                     load("trial_log.json"), load("noise_check.json"), load("state_strat.json"))
cm = load("cross_market.json")
meta, F = d["meta"], d["factors"]
HS = meta["horizons"]
G2 = r2["factors"]
NZ = nz["factors"]
ST = st["factors"]

CAT = {"可用尺度": "ok", "待验证": "warn", "效应可见·统计不可证": "blue",
       "效应过小": "gray", "无信号": "gray"}
SHORT = {"可用尺度": "可用", "待验证": "待验", "效应可见·统计不可证": "不可证",
         "效应过小": "过小", "无信号": "无信号"}
VC = {"全状态一致": "ok", "全状态同号但弱": "warn", "状态依赖（方向反转）": "bad", "样本不足": "gray"}

order = ["composite"] + sorted([k for k in F if k != "composite"], key=lambda k: -F[k]["score_long"])
cnt = {}
for k in F:
    cnt[F[k]["verdict"]] = cnt.get(F[k]["verdict"], 0) + 1
h1 = [abs(next(r["ic"] for r in F[k]["rows"] if r["h"] == 1)) for k in F]
h1_med, h1_max = float(np.median(h1)), float(np.max(h1))
onset_le60 = sum(1 for k in F if F[k]["onset_h"] and F[k]["onset_h"] <= 60)
onset_none = sum(1 for k in F if not F[k]["onset_h"])
n_consist = sum(1 for k in ST if ST[k]["verdict"] == "全状态一致")
n_regime = sum(1 for k in ST if ST[k]["verdict"] == "状态依赖（方向反转）")


def mo(field):
    vals = [G2[k][field] for k in G2 if G2[k]["n_band"] > 0]
    return float(np.mean(vals)) if vals else float("nan")


r2_edge, r2_core, r2_fix = mo("pass_rate"), mo("core_pass_rate"), mo("fixed_pass_rate")
n_band_f = sum(1 for k in G2 if G2[k]["n_band"] > 0)

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1f2430;--sub:#5b6472;--line:#e3e6ec;
--red:#e23b2e;--green:#12864f;--brand:#ff5b2e;--blue:#2f6fd0;--amber:#d98a00;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1340px;margin:0 auto;padding:26px 20px 60px}
h1{font-size:27px;margin:0 0 6px}h2{font-size:19px;margin:34px 0 12px;padding-left:11px;border-left:4px solid var(--brand)}
h3{font-size:15px;margin:20px 0 8px}.lead{color:var(--sub);margin:0 0 6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:14px 0;
box-shadow:0 1px 3px rgba(0,0,0,.03)}
.badge{display:inline-block;padding:3px 11px;border-radius:20px;font-size:12px;font-weight:600;color:#fff;margin:6px 6px 0 0}
.b-red{background:var(--red)}.b-green{background:var(--green)}.b-amber{background:var(--amber)}
.b-blue{background:var(--blue)}.b-gray{background:#7b8492}
.verdict{font-size:17px;font-weight:700;margin:12px 0 6px;line-height:1.65}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:14px 0}
.kv div{background:#fafbfc;border:1px solid var(--line);border-radius:9px;padding:12px}
.kv .k{color:var(--sub);font-size:12px}.kv .v{font-size:21px;font-weight:700;margin-top:3px}
.kv .n{font-size:11px;color:#8a93a0;margin-top:2px}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left}
th{background:#f0f2f5;font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}
.note{background:#fff8f0;border:1px solid #f0d9bd;border-radius:9px;padding:12px 15px;color:#7a5a2a;font-size:13px;margin:14px 0}
.crit{background:#fef2f1;border:1px solid #f3c9c4;border-radius:9px;padding:12px 15px;color:#8a3029;font-size:13px;margin:14px 0}
.good{background:#f0f8f3;border:1px solid #c6e5d2;border-radius:9px;padding:12px 15px;color:#1e6b45;font-size:13px;margin:14px 0}
ul{margin:8px 0;padding-left:22px}li{margin:6px 0}
.scroll{overflow-x:auto}
.heat{min-width:1180px;display:grid;grid-template-columns:158px repeat(14,1fr) 78px 74px;gap:2px;align-items:center;font-size:11px}
body.research .heat{grid-template-columns:158px repeat(18,1fr) 78px 70px}
.heat .hd{color:var(--sub);text-align:center;font-weight:600;padding:4px 0;font-size:10.5px}
.heat .fname{font-size:12px;line-height:1.2;padding:3px 4px 3px 0}
.heat .fname i{display:block;font-style:normal;color:#8a93a0;font-size:10px}
.cell{border-radius:4px;text-align:center;padding:7px 0;font-variant-numeric:tabular-nums;font-weight:600;font-size:10.5px}
.band{font-size:10.5px;color:var(--sub);text-align:center}
.micro{display:none}
body.research .micro{display:block}
.chip{display:inline-block;padding:2px 7px;border-radius:11px;font-size:11px;font-weight:600;white-space:nowrap}
.chip.ok{background:#e3f3e9;color:#12864f}.chip.warn{background:#fdf1de;color:#a56a00}
.chip.blue{background:#e8f0fb;color:#2f6fd0}.chip.gray{background:#eef0f3;color:#6b7480}
.chip.bad{background:#fdeaea;color:#b3342a}
.pill{display:inline-block;min-width:32px;padding:1px 7px;border-radius:9px;font-weight:700;font-size:12px}
.s-hi{background:#e3f3e9;color:#12864f}.s-mid{background:#fdf1de;color:#a56a00}.s-lo{background:#eef0f3;color:#6b7480}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--sub);margin:8px 0 2px}
.legend i{display:inline-block;width:26px;height:11px;border-radius:3px;vertical-align:-1px;margin-right:5px}
.tog{display:inline-flex;align-items:center;gap:8px;background:#fff;border:1px solid var(--line);
border-radius:20px;padding:5px 14px;font-size:12.5px;color:var(--sub);cursor:pointer;margin:10px 0 2px}
.tog input{accent-color:#ff5b2e}
.foot{color:#8a93a0;font-size:12px;margin-top:34px;border-top:1px solid var(--line);padding-top:14px}
code{background:#eef1f5;padding:1px 5px;border-radius:4px;font-size:12px}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
.rule{display:flex;gap:10px;margin:10px 0;align-items:flex-start}
.rule b{flex:0 0 74px;background:#fff1ea;color:#b3491f;border-radius:6px;padding:2px 8px;font-size:12px;text-align:center}
"""


def clamp(x, a, b):
    return max(a, min(b, x))


def fmt_ic(ic):
    return "0.00" if abs(ic) < 0.005 else f"{ic:+.2f}"


def cell_style(ic, score):
    a = 0.10 + 0.55 * clamp(score / 100.0, 0, 1)
    rgb = "217,53,42" if ic >= 0 else "47,111,208"
    return f"background:rgba({rgb},{a:.2f})", ("#fff" if a > 0.44 else "#1f2430")


rows_html = []
for k in order:
    f = F[k]
    band = f"{f['band'][0]}–{f['band'][1]}" if f["band"] else "—"
    on = f"起效 {f['onset_h']} 日" if f["onset_h"] else "未出现起效点"
    tds = []
    for r in f["rows"]:
        bg, fg = cell_style(r["ic"], r["score"])
        ring = ";box-shadow:inset 0 0 0 2px #12864f" if r["q"] < 0.10 else ""
        cls = "cell micro" if r["h"] <= 5 else "cell"
        tds.append(f'<div class="{cls}" style="{bg};color:{fg}{ring}" '
                   f'title="h={r["h"]}  IC={r["ic"]:+.3f}  q={r["q"]:.3f}  '
                   f'三段同号={r["seg_cons"]:.0%}  有效n={r["n_eff"]:.1f}  可信度={r["score"]:.0f}">'
                   f'{fmt_ic(r["ic"])}</div>')
    rows_html.append(f'<div class="fname">{f["name"]}<i>{on}</i></div>' + "".join(tds) +
                     f'<div class="band">{band}</div>'
                     f'<div class="band"><span class="chip {CAT[f["verdict"]]}">{SHORT[f["verdict"]]}</span></div>')

tbl = []
for k in order:
    f = F[k]


    def sc(v):
        cls = "s-hi" if v >= 60 else ("s-mid" if v >= 35 else "s-lo")
        return f'<td class="num"><span class="pill {cls}">{v:.0f}</span></td>'
    band = f"{f['band'][0]}–{f['band'][1]}" if f["band"] else "—"
    tbl.append(f'<tr><td>{f["name"]}</td>{sc(f["score_micro"])}{sc(f["score_short"])}'
               f'{sc(f["score_mid"])}{sc(f["score_long"])}'
               f'<td class="num">{"%d 日" % f["onset_h"] if f["onset_h"] else "未出现"}</td>'
               f'<td class="num">{band}</td><td class="num">{f["chg_rate"]:.2f}</td>'
               f'<td><span class="chip {CAT[f["verdict"]]}">{SHORT[f["verdict"]]}</span></td></tr>')

r2rows = []
for k in sorted(G2, key=lambda k: -((G2[k]["core_pass_rate"] or 0) - (G2[k]["fixed_pass_rate"] or 0))):
    g = G2[k]
    nb = g["n_band"]
    e = f'{g["pass_rate"]:.2f}' if nb else "—"
    c = f'{g["core_pass_rate"]:.2f}' if g["n_core"] else "—"
    delta = (g["core_pass_rate"] or 0) - (g["fixed_pass_rate"] or 0)
    mark = "↑" if delta > 0.15 else ("↓" if delta < -0.15 else "＝")
    r2rows.append(f'<tr><td>{g["name"]}</td><td class="num">{nb}</td><td class="num">{e}</td>'
                  f'<td class="num">{c}</td><td class="num">{g["fixed_pass_rate"]:.2f}</td>'
                  f'<td class="num">{delta:+.2f} {mark}</td>'
                  f'<td class="num">{g["jaccard"]:.2f}</td></tr>' if g["jaccard"] is not None else
                  f'<tr><td>{g["name"]}</td><td class="num">{nb}</td><td class="num">{e}</td>'
                  f'<td class="num">{c}</td><td class="num">{g["fixed_pass_rate"]:.2f}</td>'
                  f'<td class="num">{delta:+.2f} {mark}</td><td class="num">—</td></tr>')

strows = []
for k in order:
    s = ST[k]
    c = s["cells"]
    strows.append(
        f'<tr><td>{s["name"]}</td><td class="num">{s["h"]}</td>'
        f'<td class="num">{c["高波动"]["ic"]:+.3f}</td><td class="num">{c["低波动"]["ic"]:+.3f}</td>'
        f'<td class="num">{c["均线上"]["ic"]:+.3f}</td><td class="num">{c["均线下"]["ic"]:+.3f}</td>'
        f'<td class="num">{s["spread"]:+.3f}</td>'
        f'<td><span class="chip {VC[s["verdict"]]}">{s["verdict"]}</span></td></tr>')

nzrows = []
for k in order:
    g = NZ[k]
    if not g.get("ic_base"):
        continue
    dchg = abs((g["ic_chg"] or 0) - g["ic_base"])
    dex = abs((g["ic_ex_extreme"] or 0) - g["ic_base"])
    tag = "更新日敏感" if dchg > 0.05 else ("极端日敏感" if dex > 0.05 else "稳健")
    tc = "warn" if tag != "稳健" else "gray"
    nzrows.append(f'<tr><td>{g["name"]}</td><td class="num">{g["h"]}</td>'
                  f'<td class="num">{g["ic_base"]:+.3f}</td><td class="num">{g["ic_chg"]:+.3f}</td>'
                  f'<td class="num">{g["ic_ex_extreme"]:+.3f}</td><td class="num">{g["ic_winsor"]:+.3f}</td>'
                  f'<td class="num">{g["chg_rate"]:.2f}</td>'
                  f'<td><span class="chip {tc}">{tag}</span></td></tr>')

cat_rows = "".join(
    f'<tr><td><span class="chip {CAT[v]}">{v}</span></td><td class="num">{len([k for k in order if F[k]["verdict"]==v])}</td>'
    f'<td>{"、".join(F[k]["name"] for k in order if F[k]["verdict"]==v)}</td></tr>'
    for v in ["可用尺度", "待验证", "效应可见·统计不可证", "效应过小", "无信号"]
    if any(F[k]["verdict"] == v for k in order))

tl_rows = "".join(
    f'<tr><td class="mono">{e["id"]}</td><td>{e["round"]}</td><td>{e["desc"]}</td>'
    f'<td class="num">{e["n_tests"]}</td><td>{e["result"]}</td><td>{e["decision"]}</td></tr>'
    for e in tl["entries"])

CM_HS = cm["meta"]["horizons"]
CM_IND = [("rsi14", "RSI14"), ("bias200", "200日均线偏离"), ("mom60", "60日动量"), ("pct250", "250日分位")]
CMK = list(cm["markets"])
cm_head = "".join(f'<th class="num">{h}日</th>' for h in CM_HS)
cm_rows = ""
for key, label in CM_IND:
    tds = []
    for h in CM_HS:
        s = cm["summary"].get(f"{key}@{h}")
        if not s:
            tds.append('<td class="num">—</td>')
            continue
        cls = "ok" if s["agreement"] >= s["n"] else ("warn" if s["agreement"] >= s["n"] - 1 else "gray")
        tds.append(f'<td class="num"><span class="chip {cls}">{s["agreement"]}/{s["n"]}</span>'
                   f'<br><span style="font-size:11px;color:#8a93a0">{s["mean_ic"]:+.3f}</span></td>')
    up = sum(1 for p in cm["longer"]["pairs"] if p["ind"] == key and p["up"])
    n = sum(1 for p in cm["longer"]["pairs"] if p["ind"] == key)
    cm_rows += f'<tr><td>{label}</td>{"".join(tds)}<td class="num">{up}/{n}</td></tr>'

cm500 = "".join(
    f'<tr><td>{mk}</td>' + "".join(
        f'<td class="num">{next((r["ic"] for r in cm["markets"][mk]["ind"][k] if r["h"] == 500), None):+.3f}</td>'
        if next((r["ic"] for r in cm["markets"][mk]["ind"][k] if r["h"] == 500), None) is not None
        else '<td class="num">—</td>' for k, _ in CM_IND) + "</tr>"
    for mk in CMK)

body = f"""
<h1>因子周期画像</h1>
<p class="lead">数据基准 <b>2026-11-20</b>｜17 日频因子 + 8 指标综合分 × 18 个前瞻期 = 324 格｜
默认视图已按「展示标准 v1」折叠微观段｜由 <code>build_horizon_profile.py</code> 生成（DAG · L2），<b>不回写 mood.html / 综合分 / 共振板</b></p>
<span class="badge b-amber">展示标准 v1</span><span class="badge b-green">T008 已复核</span><span class="badge b-blue">R1–R4 已跑</span>
<span class="badge b-gray">未上线</span>

<div class="card">
<div class="verdict">微观段（1–5 日）<b>18/18 无效</b>，按标准默认折叠、不再进界面；
R2 证明「精确有效带」不可移植、R3 显示「宽度类」指标方向随状态反转——
所以界面上能给的最强标签只有<b>粗粒度档位 + 起效点</b>。</div>
<div class="kv">
<div><div class="k">扫描格数</div><div class="v">324</div><div class="n">18 标的 × 18 周期</div></div>
<div><div class="k">微观段有效</div><div class="v">0 / 18</div><div class="n">|IC| 中位 {h1_med:.3f}、最大 {h1_max:.3f}</div></div>
<div><div class="k">可用尺度</div><div class="v">{cnt.get('可用尺度',0)}</div><div class="n">过效应+稳定+显著</div></div>
<div><div class="k">待验证</div><div class="v">{cnt.get('待验证',0)}</div><div class="n">形状成立、未过显著</div></div>
<div><div class="k">R3 全状态一致</div><div class="v">{n_consist} / 18</div><div class="n">宽度类 {n_regime} 个方向反转</div></div>
<div><div class="k">起效点 ≤60 日</div><div class="v">{onset_le60}</div><div class="n">{onset_none} 个从未起效</div></div>
</div>
</div>

<h2>一、周期剖面热力图</h2>
<div class="card">
<p>行=标的，列=前瞻期。格内是秩 IC，<b>颜色深浅同时编码效应大小与可信度</b>。
绿色边框 = q&lt;0.10。每行名称下方标注<b>起效点</b>（第一个 |IC|≥0.15 且此后不再跌回的周期）。</p>
<div class="legend">
<span><i style="background:rgba(217,53,42,.55)"></i>IC &gt; 0</span>
<span><i style="background:rgba(47,111,208,.55)"></i>IC &lt; 0</span>
<span><i style="background:#fff;box-shadow:inset 0 0 0 2px #12864f"></i>q &lt; 0.10</span>
</div>
<label class="tog"><input type="checkbox" id="rv"
 onchange="document.body.classList.toggle('research',this.checked)">
<span>研究视图：展开 1–5 日微观段（默认折叠）</span></label>
<div class="scroll"><div class="heat">
<div class="hd"></div>{"".join(f'<div class="hd{" micro" if h<=5 else ""}">{h}日</div>' for h in HS)}
<div class="hd">有效带</div><div class="hd">结论</div>
{"".join(rows_html)}
</div></div>
<div class="note"><b>折叠说明（不是没测）：</b>1/2/3/5 日已全部检验，18 个标的 |IC| 中位 {h1_med:.3f}、最大 {h1_max:.3f}，
没有一个达到 0.15 的起效门槛，因此默认不展示。这是<b>展示口径</b>，不是数据缺失；
勾选上方开关可看到完整剖面。</div>
</div>

<h2>二、展示标准 v1（未来开发口径）</h2>
<div class="card">
<p>你提的「页面不显示 1 日 / 只显示可能有效的尺度」是对的，但直接「只留赢家」会造成幸存者偏差。
所以落成下面五条，默认视图与隐藏逻辑都由它们决定：</p>
<div class="rule"><b>标准 1</b><div><b>默认折叠微观段。</b>1–5 日不进入默认视图；结论固化为「已检验：18/18 无效」，
并在页面上写明「折叠 ≠ 未测」。研究视图保留全量。</div></div>
<div class="rule"><b>标准 2</b><div><b>只展示候选段。</b>每行从「起效点」开始才有意义；
起效点之前的周期在卡片/图上一律标为「无效区」，不参与解读。</div></div>
<div class="rule"><b>标准 3</b><div><b>不展示精确带宽。</b>只给粗粒度档位（微观 / 短 / 中 / 长）+ 起效点。
像「250–375 日」这种数字只留在 JSON，不进界面——R2 已证明它不可移植。</div></div>
<div class="rule"><b>标准 4</b><div><b>「有效」必须三证齐全。</b>q 值、有效样本口径、R2/R3/R4 通过情况，
缺一项只能标「待验证」。目前 18 个标的里只有 2 个够得上「可用」。</div></div>
<div class="rule"><b>标准 5</b><div><b>不许只留赢家。</b>未通过的指标仍须列出并注明原因
（无信号 / 效应过小 / 大效应不可证）。否则读者会把「没显示」误读成「没测过」。</div></div>
</div>

<h2>三、四档可信度 + 起效点</h2>
<div class="card">
<p>微观（1–5）/ 短（8–30）/ 中（40–120）/ 长（180–750），每档取该档最高一格。微观档按标准 1 仅作研究参考。</p>
<div class="scroll"><table>
<thead><tr><th>标的</th><th>微观*</th><th>短</th><th>中</th><th>长</th><th>起效点</th><th>有效带</th><th>更新率</th><th>结论</th></tr></thead>
<tbody>{"".join(tbl)}</tbody></table></div>
<p style="color:var(--sub);font-size:12.5px">* 微观档仅研究用。「更新率」= 有变化的交易日占比，
反映该序列是日频真值还是阶梯/ffill；更新率越低，有效独立观测越少。</p>
</div>

<h2>四、R2 样本外：精确「有效带」被证伪</h2>
<div class="card">
<p>10 个半年起点，每个起点只用此前数据挑带，再用其后 4 年验证（同号且 |IC|≥0.10 记为通过），并跑两个对照。</p>
<div class="kv">
<div><div class="k">边缘带</div><div class="v">{r2_edge:.2f}</div><div class="n">|IC|≥0.15 起</div></div>
<div><div class="k">核心带</div><div class="v">{r2_core:.2f}</div><div class="n">|IC|≥0.7×峰值</div></div>
<div><div class="k">固定长带 基线</div><div class="v">{r2_fix:.2f}</div><div class="n">不用历史挑带</div></div>
<div><div class="k">核心 − 基线</div><div class="v">{r2_core-r2_fix:+.2f}</div><div class="n">无增益</div></div>
</div>
<div class="crit"><b>负面结果：</b>核心带 {r2_core:.2f} 与固定长带 {r2_fix:.2f} 基本打平，边缘带 {r2_edge:.2f} 更差
（同一批 {n_band_f} 个标的、同一批起点）。逐因子看：换手率 +0.50、NH-NL +0.30 优于基线，
但正收益偏股 −0.50、破净率 −0.40、全A站上200日 −0.30 更差——<b>有时对、有时错，净效果为零</b>。
带的位置不可移植，因此标准 3 禁止在界面写精确带宽。</div>
<div class="scroll"><table>
<thead><tr><th>标的</th><th>给出带次数</th><th>边缘带</th><th>核心带</th><th>固定长带</th><th>核心−固定</th><th>带重叠</th></tr></thead>
<tbody>{"".join(r2rows)}</tbody></table></div>
</div>

<h2>五、R3 状态分层：宽度类指标方向会反转</h2>
<div class="card">
<p>按「高/低波动 × 均线上/下」四种状态各算一次 IC（状态判定全 PIT：波动用 expanding 中位数、趋势用滚动 200 日均线）。
高波动占 {st['meta']['share']['hi_vol']:.0%} 交易日、均线上方占 {st['meta']['share']['up']:.0%}。</p>
<div class="scroll"><table>
<thead><tr><th>标的</th><th>h</th><th>高波动</th><th>低波动</th><th>均线上</th><th>均线下</th><th>极差</th><th>判定</th></tr></thead>
<tbody>{"".join(strows)}</tbody></table></div>
<div class="good"><b>跨状态站得住的是慢变量：</b>神奇指标（−0.61/−0.45/−0.59/−0.53）、综合分（−0.76/−0.46/−0.59/−0.58）、
两融（−0.40/−0.35/−0.15/−0.46）四态同号且都够大。综合分在<b>高波动期更强</b>（−0.76 vs −0.46）。</div>
<div class="crit"><b>站不住的是宽度类：</b>全A站上200日均线 均线上 <b>+0.268</b> / 均线下 <b>−0.570</b>（极差 0.838）、
市场宽广度综合 +0.13 / −0.33、换手率 +0.16 / −0.30——<b>牛市里是动量、熊市里是反转动量</b>，
给它们贴任何单一方向标签都是错的。这类只能标「仅状态内可用」。</div>
</div>

<h2>六、R4 跨市场复现：方向复制了，幅度没有</h2>
<div class="card">
<p>把 4 个<b>纯价格类</b>指标（RSI14 / 200日均线偏离 / 60日动量 / 250日分位）在 6 个市场各算一遍。
这 6 个市场独立性强于 A 股内部各指数，是检验「结论是不是本土特例」的唯一硬证据。
（估值/两融/发行类指标是 A 股专有数据，无法跨市场复现，这是本次 R4 的边界。）</p>
<div class="kv">
<div><div class="k">市场数</div><div class="v">{len(CMK)}</div><div class="n">5 海外 + A股本库</div></div>
<div><div class="k">方向一致（三项 250/500）</div><div class="v">6 / 6</div><div class="n">bias200、mom60、pct250 全为负</div></div>
<div><div class="k">长端更强</div><div class="v">{cm['longer']['n_up']} / {cm['longer']['n']}</div><div class="n">RSI 仅 3/6，不是普适规律</div></div>
<div><div class="k">价格类长端天花板</div><div class="v">0.21</div><div class="n">bias200@500 = −0.212</div></div>
</div>
<div class="scroll"><table>
<thead><tr><th>指标</th>{cm_head}<th>长&gt;短</th></tr></thead>
<tbody>{cm_rows}</tbody></table></div>
<p style="color:var(--sub);font-size:12.5px">格内上行 = 同号市场数（满分 6），下行 = 跨市场平均 IC。</p>
<div class="good"><b>被复现的部分（可以当结构性先验）：</b>「越贵/越偏离均线 → 未来收益越低」在 6 个市场、250 与 500 日上
<b>全部同号</b>，且强度随周期上升（bias200 从 −0.081 升到 −0.212）。这是均值回复，不是 A 股特性。</div>
<div class="crit"><b>没被复现的部分（不能当规律）：</b>「长端更强」只有 {cm['longer']['n_up']}/{cm['longer']['n']} 对成立，
RSI 仅 3/6、恒生甚至是反向的（短 0.029 / 长 0.007）。
更关键的是<b>量级</b>：价格类指标到 500 日也只有 |IC|≈0.21，而本库估值类因子在 500 日报出 0.42–0.67。
同一段 A 股数据里，纯价格指标给的是 0.11，估值类给的是 0.5+——这个 3–5 倍的差距，
更像「慢变量与两轮行情共线」而不是「估值因子真的强 3 倍」。</div>
<div class="note">因此 R4 的结论是：<b>方向可用、幅度打折</b>。界面文案可以写「长期越贵越危险」这种方向性判断，
但不能引用「IC=0.5」这种量级去暗示预测力。</div>
</div>

<h2>七、有效统计需要哪些前提：噪声体检</h2>
<div class="card">
<p>同一因子、同一前瞻期（取有效带中点），比较四种处理方式：</p>
<div class="scroll"><table>
<thead><tr><th>标的</th><th>h</th><th>基准</th><th>仅更新日</th><th>剔极端日</th><th>1–99% 缩尾</th><th>更新率</th><th>判定</th></tr></thead>
<tbody>{"".join(nzrows)}</tbody></table></div>
<div class="good"><b>不用做的两件事：</b>①<b>缩尾</b>——18/18 与基准完全一致，因为秩 IC 本身就把极端值换成了排名，
再加 1–99% 缩尾是对同一件事做两遍；②<b>剔极端行情日</b>——最大变化只有 0.032（RSI），
说明结论不是靠几根大阴线撑起来的。</div>
<div class="crit"><b>必须做的一件事：换估计量，不是折算样本量。</b>低估指数行业把「没变的重复日」去掉后，
IC 从 +0.256 变成 <b>+0.533</b>；正收益偏股从 −0.334 变成 −0.133；综合分却几乎不动（−0.604 → −0.606）。
差别在于：<b>当日频等权把同一个值重复几十遍时，IC 会被「这个值持续了多久」主导，而不是「值本身」</b>。
但这<b>不会</b>让有效样本变小（见下方 T008），所以正确做法是把两种采样下的 IC 当成区间，
而不是去乘一个折算系数。</div>
<div class="crit"><b>T007 的折算被 T008 推翻（已撤回）。</b>「把有效 n 乘上更新率」是错的：
<code>n_eff = 观测数/(h/更新间隔)</code> 与采样频率基本无关。实测综合分 500 日的有效 n：日频 4.3 / 原生 4.5，
几乎相等；IC 也几乎不变（−0.604 vs −0.606）。T007 预测的「60.8 → 27.8」不成立。</div>
<div class="note">真正受重采样影响的是另外两个：<b>低估指数行业 +0.256 → +0.533</b>（不稳定，量级不可信）、
<b>正收益偏股 −0.317 → −0.125</b>（可能被高估）。两个对照组 <b>RSI（−0.100/−0.101）</b>、
<b>神奇指标（−0.506/−0.510）</b>完全一致，说明方法本身没跑偏，差异来自真实的采样敏感性。<br>
因此正确做法不是「折算 n」，而是<b>把两种采样下的 IC 作为区间报出来</b>——
日频给下界（重复值衰减），变点给上界（只取活跃日）。综合分落在区间极窄处，结论稳健。</div>
</div>

<h2>八、结论分类</h2>
<div class="card">
<table><thead><tr><th>分类</th><th>数量</th><th>标的</th></tr></thead><tbody>{cat_rows}</tbody></table>
<ul>
<li><b>可用尺度</b>：三道关都过，但仍需跨市场复现。</li>
<li><b>待验证</b>：剖面形状成立、未过严格显著。</li>
<li><b>大效应·不可证</b>：长端 |IC| 0.39–0.54，但循环平移零分布同样宽——慢变量与趋势共线，单市场分不开。</li>
<li><b>效应过小 / 无信号</b>：|IC|&lt;0.15 或方向反复。</li>
</ul>
</div>

<h2>九、方法、口径与硬约束</h2>
<div class="card">
<h3>可信度分</h3>
<p><code>100 × 效应^0.8 × 显著^0.5 × 稳定 × 样本</code>，四项相乘：效应=min(1,|IC|/0.35)；显著=(1−q)^0.5；
稳定=0.5+0.5×三段同号率；样本=√(min(1,有效n/10))。乘法保证效应接近 0 时总分必然接近 0。</p>
<h3>禁止 argmax</h3>
<div class="crit">18 个周期高度自相关，取 IC 最大格等于在 18 个相关候选里挑最大值，必然向上偏。
R2 已证明连「带」都不可移植，因此界面只保留粗粒度档位 + 起效点。</div>
<h3>族系变大 = 门槛变严</h3>
<div class="note">网格从 12 扩到 18，BH 族系从 12 变 18，综合分 500 日的 q 从 <b>0.060 升到 0.135</b>，
跌破 0.10 门槛，从「可用尺度」掉到「待验证」。数据没变，变的只是检验次数——
所以网格必须<b>先冻结、再登记</b>。</div>
<h3>三个已知偏差</h3>
<ul>
<li><b>重叠样本：</b>500 日窗口下有效独立观测只有 ~4–5 个。</li>
<li><b>重复观测：</b>综合分仅 {F['composite']['chg_rate']:.0%} 的交易日数值变化，但 h≫更新间隔时
<code>n_eff≈n/h</code> 不受影响（T008 实测 4.3 vs 4.5）；风险集中在更新间隔与 h 可比的序列（如低估指数行业）。</li>
<li><b>单一市场：</b>2016 年至今约 2 个完整周期。</li>
</ul>
</div>

<h2>十、试验日志（append-only）</h2>
<div class="card">
<div class="scroll"><table>
<thead><tr><th>ID</th><th>轮次</th><th>口径</th><th>检验数</th><th>结果</th><th>决定</th></tr></thead>
<tbody>{tl_rows}</tbody></table></div>
<p>下一轮只剩 <b>R4 跨市场 / 跨指数复现</b>——唯一能真正提高可信度的一轮。</p>
</div>

<h2>十一、届时需要改的页面说明（现在不动）</h2>
<div class="card">
<ul>
<li><b>8 指标卡片</b>：加粗粒度标签 <code>尺度：长周期（≥180 日）· 起效 90 日 · 微观/短周期无效</code>，不写精确天数。</li>
<li><b>综合分卡</b>：改为「1–2 年位置指标，不能周级择时」；并注明<b>高波动期更强</b>（R3 结论）。</li>
<li><b>宽度类卡片（站上200日、宽广度综合、换手率）</b>：必须标「状态依赖：牛市动量 / 熊市反转」，不能给单一方向。</li>
<li><b>共振板 / 极值事件</b>：注明事件级口径，与长周期 IC 不能互相印证。</li>
<li><b>可信度评分卡</b>：在 D/E 之外加第三维「尺度匹配度」，并按更新率折算有效样本。</li>
<li><b>免责声明</b>：任何胜率都标注对应前瞻窗口，否则 60 日胜率会被读成明天。</li>
</ul>
</div>

<div class="foot">
由 <code>_gen_horizon_preview.py</code> 从 <code>horizon_profile.json</code>、<code>rolling_origin.json</code>、
<code>state_strat.json</code>、<code>noise_check.json</code>、<code>trial_log.json</code> 生成｜
输入 <code>factor_matrix.csv</code> + <code>mood_data.json</code>｜零网络请求｜
前瞻收益按 T 日收盘理想化，未计交易成本、发布滞后与停牌｜本页数据由 L2 模块自动生成，页面本身只读。
</div>
"""

html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>因子周期画像 · 2026-11-21</title><style>' + CSS +
        '</style></head><body><div class="wrap">' + body + '</div></body></html>')

out = os.path.join(BASE, "reports", "因子周期画像-20261121.html")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "w", encoding="utf-8").write(html)
print("wrote", out, len(html))
