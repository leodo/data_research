# -*- coding: utf-8 -*-
"""由只读探针结果生成诊断报告 HTML（不接入 DAG、不改生产文件）。"""
from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
P = json.loads((DATA / "_conditional_probe.json").read_text(encoding="utf-8"))
L = json.loads((DATA / "factor_matrix.json").read_text(encoding="utf-8"))
E = json.loads((DATA / "efficacy_audit.json").read_text(encoding="utf-8"))

S = P["summary"]
SNP = P["snapshot"]
ROB = P["resonance_robust"]


def esc(x):
    return html.escape(str(x))


def num(v, suffix="", sign=False):
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        t = f"{v:+.3f}" if sign else f"{v}"
        return t + suffix
    return esc(v) + suffix


def table(heads, rows, cls=""):
    h = "".join(f"<th>{esc(x)}</th>" for x in heads)
    b = "".join("<tr>" + "".join(f"<td{' class=num' if isinstance(c, str) and c.startswith('@') else ''}>"
                                 f"{(c[1:] if isinstance(c, str) and c.startswith('@') else esc(c))}</td>"
                                 for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


# ---------------- 可信度评分示意（权重先验、非生产口径）----------------
daily = [f for f in L["factors"] if f["in_matrix"]]
stale = [f["stale_trade_days"] for f in daily]
fresh = sum(100 if s == 0 else 70 if s <= 3 else 30 for s in stale) / len(stale)
pri = Counter(f.get("priority") for f in daily)
drift_score = sum({"高": 40, "中": 70, "低": 100}.get(k, 60) * v for k, v in pri.items()) / len(daily)
D_items = [
    ("D1 当日覆盖率", 0.30, SNP["coverage_pct"], "17/17 个日频因子当日均有值"),
    ("D2 新鲜度", 0.25, round(fresh, 1), f"stale=0 共 {stale.count(0)} 个，stale=1 共 {sum(1 for s in stale if s==1)} 个"),
    ("D3 PIT 成熟度", 0.20, 100.0, "全部因子历史起点均早于 250 交易日下限"),
    ("D4 分布漂移", 0.15, round(drift_score, 1), f"跨年 KS 优先级 高{pri['高']} / 中{pri['中']} / 低{pri['低']}"),
    ("D5 口径一致性", 0.10, 70.0, "PIT 回放通过(+)，但 25 个因子无真实 publish_date(−30)"),
]
D = round(sum(w * v for _, w, v, _ in D_items), 1)

cond_states = ["底部共振>=60", "confirm=0"]
n_state_sig = sum(1 for c in E["conditional"]
                  if c["state"] in cond_states and c.get("sig_fdr_joint"))
n_event_sig = sum(1 for t in P["event_tests"] if t.get("sig_fdr"))
seg_consistent = S["n_seg_consistent"]
top2_share = S["top_day_2018_2024_share"]
S_items = [
    ("S1 当前状态的统计证据", 0.30, 0.0 if n_state_sig == 0 else min(100, n_state_sig * 40),
     f"当前属「共振≥60 / confirm=0」档，L1 联合 FDR 显著格子 {n_state_sig} 个"),
    ("S2 极值方向的证据", 0.25, 10.0,
     f"当日 {SNP['n_low']} 低分位 + {SNP['n_high']} 高分位；41 次极值事件仅 {n_event_sig} 次过 FDR，"
     "且与当日主导方向不同"),
    ("S3 事件样本成熟度", 0.20, 35.0, "同类事件 n 中位约 30，Wilson 半宽约 ±17pp"),
    ("S4 跨期稳定性", 0.15, round(seg_consistent / len(daily) * 100, 1),
     f"4 段 IC 符号全一致的因子 {seg_consistent}/{len(daily)}"),
    ("S5 行情集中度折价", 0.10, round(max(0, 100 - top2_share), 1),
     f"高贡献日 2018+2024 占 {top2_share}%"),
]
E_SCORE = round(sum(w * v for _, w, v, _ in S_items), 1)
TOTAL = round(0.4 * D + 0.6 * E_SCORE, 1)
BAND = "绿：可正常解读" if TOTAL >= 70 else ("黄：仅供参考，不据此加减仓" if TOTAL >= 40 else "红：今日读数不宜用于决策")
BANDCLS = "ok" if TOTAL >= 70 else ("warn" if TOTAL >= 40 else "bad")


CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1f2430;--sub:#5b6472;--line:#e3e6ec;
--red:#e23b2e;--green:#12864f;--brand:#ff5b2e;--blue:#2f6fd0;--amber:#d98a00;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1160px;margin:0 auto;padding:26px 20px 60px}
h1{font-size:27px;margin:0 0 6px}h2{font-size:19px;margin:34px 0 12px;padding-left:11px;border-left:4px solid var(--brand)}
h3{font-size:15px;margin:20px 0 8px}.lead{color:var(--sub);margin:0 0 18px}
.sub{color:var(--sub);font-size:13px;margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:14px 0;
box-shadow:0 1px 3px rgba(0,0,0,.03)}
.badge{display:inline-block;padding:3px 11px;border-radius:20px;font-size:12px;font-weight:600;color:#fff;margin-right:6px}
.b-red{background:var(--red)}.b-green{background:var(--green)}.b-amber{background:var(--amber)}
.b-blue{background:var(--blue)}.b-gray{background:#7b8492}
.verdict{font-size:19px;font-weight:700;margin:10px 0 6px;line-height:1.6}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;margin:14px 0}
.kv div{background:#fafbfc;border:1px solid var(--line);border-radius:9px;padding:12px}
.kv .k{color:var(--sub);font-size:12px}.kv .v{font-size:21px;font-weight:700;margin-top:3px}
.kv .n{font-size:11px;color:#8a93a0;margin-top:2px}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:#f0f2f5;font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}
tr.hl td{background:#fff4ec}
.ok{color:var(--green);font-weight:600}.warn{color:var(--amber);font-weight:600}.bad{color:var(--red);font-weight:600}
.chart{width:100%;height:320px}.chart.sm{height:280px}
.note{background:#fff8f0;border:1px solid #f0d9bd;border-radius:9px;padding:12px 15px;color:#7a5a2a;font-size:13px;margin:14px 0}
.crit{background:#fef2f1;border:1px solid #f3c9c4;border-radius:9px;padding:12px 15px;color:#8a3029;font-size:13px;margin:14px 0}
.good{background:#f0f8f3;border:1px solid #c6e5d2;border-radius:9px;padding:12px 15px;color:#1e6b45;font-size:13px;margin:14px 0}
ul{margin:8px 0;padding-left:22px}li{margin:6px 0}
.foot{color:#8a93a0;font-size:12px;margin-top:34px;border-top:1px solid var(--line);padding-top:14px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.scroll{overflow-x:auto}code{background:#eef1f5;padding:1px 5px;border-radius:4px;font-size:12px}
.score{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:12px 0}
.score .big{font-size:42px;font-weight:800;line-height:1}
.score .box{flex:1;min-width:200px;background:#fafbfc;border:1px solid var(--line);border-radius:9px;padding:12px}
@media(max-width:900px){.two{grid-template-columns:1fr}}
"""

HEAD = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>条件有效性与每日可信度评分 · 2026-11-21</title>'
        '<script src="vendor/echarts.min.js"></script><style>' + CSS + '</style></head><body><div class="wrap">')

BODY = []
BODY.append(f"""
<h1>条件有效性与每日可信度评分</h1>
<p class="lead">数据基准：<b>{esc(P['meta']['base_date'])} 收盘</b>｜诊断日 2026-11-21｜
17 个日频因子 × {esc(P['meta']['n_days'])} 交易日｜只读探针，<b>未接入 DAG、未改任何生产文件</b></p>
<p class="sub">输入仅为 <code>data/factor_matrix.csv</code>（L0 统一矩阵）与 <code>mood_data.json</code> 的沪深300 收盘；
零网络请求。前瞻收益按 T 日收盘理想化，未计交易成本、发布滞后与停牌。所有事件定义用 T 日可知的 PIT 分位。</p>

<div class="card">
<span class="badge b-red">有效因子确实极少</span><span class="badge b-amber">「只在极值有效」未被证实</span>
<span class="badge b-blue">真问题是漂移稀释</span><span class="badge b-green">可信度评分可以做</span>
<div class="verdict">你的直觉方向对，但原因要换一个：<b>不是「极值日才有信号」，而是「局部有结构、跨期水平漂移把全局 IC 抵消成近似 0」</b>。
17 个因子段内去均值后 |IC| 有 {S['n_drift_gain_pos']}/17 提升（中位 +{S['median_drift_gain']}），
但全样本 51 次主检验经 FDR 后仍是 0 个显著；41 次极值事件检验 FDR 后仅 1 个存活，且方向是「高位延续」而非「极值反转」。</div>
<div class="kv">
<div><div class="k">L1 主检验（全样本）</div><div class="v">0 / 51</div><div class="n">BH-FDR 后显著因子×窗口数</div></div>
<div><div class="k">极值事件检验</div><div class="v">{S['n_event_fdr_sig']} / {S['n_event_tests']}</div><div class="n">raw p&lt;0.05 仅 {S['n_event_raw_sig']} 个</div></div>
<div><div class="k">前 10% 日的 |贡献| 占比</div><div class="v">{S['median_abs_share_top10']}%</div><div class="n">17 因子中位数，远高于 10% 基准</div></div>
<div><div class="k">高贡献日 2018+2024</div><div class="v">{top2_share}%</div><div class="n">信号高度集中在两段行情</div></div>
<div><div class="k">4 段 IC 符号全一致</div><div class="v">{seg_consistent} / 17</div><div class="n">跨期可复现性差</div></div>
<div><div class="k">高分位共振（严格对照）</div><div class="v">p={[r for r in P['resonance'] if r['label']=='高分位共振'][0]['p']}</div><div class="n">不重叠事件后 p={ROB['non_overlap']['p']}</div></div>
</div>
</div>

<h2>一、先直接回答三个问题</h2>
<div class="card">
<h3>1）「目前可能有效的因子很少？」——是的，但要看口径</h3>
<p>全样本单因子 51 次检验 raw p&lt;0.05 只有 2 个、FDR 后 0 个；条件 IC 187 次联合族系里也只活下来 2 个
（高波动档的宽度类负 IC）。所以<b>能称为「已被证明有效」的因子：0 个；可称为「有条件的假设」：2 个格子</b>。
这不代表指标没用，而是「用日频全样本 IC 这种检验方式」几乎没有功效：8 指标等权本来是为<b>位置判断</b>设计的，
不是为<b>逐日收益预测</b>设计的。</p>
<h3>2）「是不是只在极值 / 少数交易日有效？」——证据不支持，但现象确实存在</h3>
<p>极值事件检验（分位首次进入 ≤10 / ≥90，20 日去重，循环平移对照）：41 次里 raw p&lt;0.05 只有 1 个、
FDR 后 1 个（上涨家数占比高分位 / 20 日 / 超额 +1.54pp / 胜率 59.6% / q=0.041）。
把 17 因子合并成「极值共振」后，低分位共振 p={[r for r in P['resonance'] if r['label']=='低分位共振'][0]['p']}、
高分位共振 p={[r for r in P['resonance'] if r['label']=='高分位共振'][0]['p']}，扫描 15 格 <b>FDR 后 0 个显著</b>。
低分位事件平均超额 +{P['tail_bias']['低分位<=10']['mean_excess_pp']}pp，但 8 格里只有 4 格为正，符号检验 p=1.0。</p>
<div class="crit"><b>最关键的反证：</b>高分位共振看似 n=42、超额 +3.32pp，
但 60 日前瞻窗口彼此重叠，有效独立样本远小于 42。改成<b>间隔≥60 日的不重叠事件</b>后：n={ROB['non_overlap']['n']}、
超额掉到 +{ROB['non_overlap']['excess_pp']}pp、胜率 {ROB['non_overlap']['win_pct']}%、p={ROB['non_overlap']['p']}。
所谓「少数交易日有效」很大一部分是<b>重叠样本制造的伪独立观测</b>。</div>
<h3>3）「长期一起看就是噪音？」——更准确的说法是「信号被漂移抵消」</h3>
<p>块级（不重叠 60 日）IC 的均值 |t|≥2 的有 14/17，块间 lag1 中位 {S['median_block_lag1']}（近似独立，
不是自相关夸大）；可全样本 IC 经移动块 bootstrap 后全不显著。两者并不矛盾：
<b>块内关系稳定，块与块之间的水平漂移把合并后的秩相关拉回 0</b>（Simpson 型抵消）。
佐证：4 段内各自去均值再合并，{S['n_drift_gain_pos']}/17 因子 |IC| 提升；高贡献日 {top2_share}% 落在 2018 与 2024。</p>
<div class="note">注意：段内去均值用了整段均值，<b>含未来信息，只能当诊断</b>，绝不能当生产口径。
它证明的是「漂移在稀释信号」，不是「去均值就能赚钱」。</div>
</div>
""")

# ---- 二、四组检验
conc = sorted(P["concentration"], key=lambda r: r["ic"])
t1 = table(["因子", "全样本 IC", "前10%日贡献占比", "删尾后 IC", "删尾保留率", "|贡献|前10%占比"],
           [[c["name"][:18], "@" + num(c["ic"], sign=True), "@" + num(c["top10"], "%"),
             "@" + num(c["ic_trim10"], sign=True),
             "@" + ("—" if c["trim_retain_pct"] is None else num(c["trim_retain_pct"], "%")),
             "@" + num(next(a["abs_share_top10_pct"] for a in P["abs_share"] if a["key"] == c["key"]), "%")]
            for c in conc])
ev_top = sorted([t for t in P["event_tests"] if t["p"] is not None], key=lambda z: z["p"])[:10]
t2 = table(["因子", "尾部", "窗口", "事件数", "平均收益", "超额", "胜率", "p", "q", "FDR"],
           [[t["name"][:16], t["tail"], "@" + str(t["h"]), "@" + str(t["n"]),
             "@" + num(t["mean_ret_pct"], "%", True), "@" + num(t["excess_pp"], "pp", True),
             "@" + num(t["win_pct"], "%"), "@" + str(t["p"]), "@" + str(t["q"]),
             "存活" if t.get("sig_fdr") else "未存活"] for t in ev_top])
seg_names = ["2016-2018", "2019-2021", "2022-2024", "2025-2026"]
t3 = table(["因子", *seg_names, "符号翻转", "4段一致"],
           [[r["name"][:18], *["@" + num(r[s], sign=True) for s in seg_names],
             "@" + str(r["sign_flips"]), "是" if r["sign_consistent"] else "否"]
            for r in sorted(P["segments"], key=lambda z: z["sign_flips"])])
t4 = table(["共振阈值", "窗口", "事件数", "超额", "胜率", "p", "q", "FDR"],
           [["@" + str(r["thr"]), "@" + str(r["h"]), "@" + str(r["n"]),
             "@" + num(r["excess_pp"], "pp", True), "@" + num(r["win_pct"], "%"),
             "@" + str(r["p"]), "@" + str(r["q"]), "存活" if r["sig_fdr"] else "未存活"]
            for r in sorted(ROB["grid"], key=lambda z: z["p"])])

BODY.append(f"""
<h2>二、四组检验的原始数据</h2>
<div class="card"><h3>A. 贡献集中度与删尾（h=60，pct_exp 轨道）</h3>
<p>逐日贡献 <code>c_t = rank(x)·rank(y)</code>（各映射到 −1..1）。「前10%日贡献占比」在 IC≈0 时分母会反号放大，
所以同时给出更稳健的「|贡献| 占比」与「删尾保留率」。中位数：|贡献| 前10%占 {S['median_abs_share_top10']}%（基准 10%），
删掉影响最大的 10% 日后 IC 保留 {S['median_trim_retain']}%，且有 {S['n_trim_sign_flip']}/17 因子<b>符号翻转</b> —— 
极值日更多是干扰源，不是稳定信号源。</p>
{t1}
<div id="c3" class="chart sm"></div></div>

<div class="card"><h3>B. 时间集中度：哪些年份在贡献信号</h3>
<div id="c1" class="chart"></div>
<p>17 个因子各自贡献最大的 10% 交易日合计 {sum(P['top_day_years'].values())} 个（可重复），
其中 2018 年 {P['top_day_years'].get('2018', 0)} 个、2024 年 {P['top_day_years'].get('2024', 0)} 个，
两段合计 {top2_share}%。这解释了大一统 IC 为什么不稳定：它实际上由少数几段行情的符号决定。</p></div>

<div class="card"><h3>C. 极值事件研究（41 次检验，BH-FDR）</h3>
{t2}
<p>唯一存活格子：上涨家数占比与动能广度「高分位≥90 / 20 日」，超额 +1.54pp、胜率 59.6%、q=0.041。
方向是<b>高位延续（动量）</b>而非极值反转，且与当日「8 个低分位」的主导方向相反；
单次存活、未经样本外，只能登记为待验证假设。</p></div>

<div class="card"><h3>D. 分段 IC（4 段样本外切片）</h3>
{t3}
<p>只有 {seg_consistent}/17 因子四段符号全一致，符号翻转次数中位数 {S['median_seg_flips']}。
但注意几个因子段内 IC 高达 −0.4~−0.5、合并后却接近 0，正是漂移稀释的直接证据。</p>
<div id="c2" class="chart"></div></div>

<div class="card"><h3>E. 「高分位共振」显著性是怎样消失的</h3>
{t4}
<div class="two">
<div><h3>留一年（剔除某年后剩余超额）</h3>
{table(["剔除年份", "剩余事件数", "超额 pp"], [["@" + str(x["drop_year"]), "@" + str(x["n"]),
       "@" + num(x["excess_pp"], sign=True)] for x in ROB["leave_one_year_out"]])}</div>
<div><h3>分段 / 不重叠</h3>
{table(["切片", "事件数", "超额 pp", "胜率"], [[x["segment"], "@" + str(x["n"]),
       "@" + num(x["excess_pp"], sign=True), "@" + num(x["win_pct"], "%")]
      for x in ROB["segments"]] + [["不重叠(≥60日)", "@" + str(ROB["non_overlap"]["n"]),
       "@" + num(ROB["non_overlap"]["excess_pp"], sign=True),
       "@" + num(ROB["non_overlap"]["win_pct"], "%")]])}
<p class="sub">事件年份分布：{esc(json.dumps(ROB['year_dist'], ensure_ascii=False))}</p></div></div>
<div class="crit">留一年与分段看起来都稳（各段超额 +2.7~+5.7pp），这恰恰说明问题不在「某一年份」，
而在<b>同一段行情内 60 日窗口高度重叠</b>：42 个事件约等于 14 个独立观测。
把间隔拉到 60 日后，超额减半、胜率跌破 50%、p 从 0.059 变 0.568。
任何「极值日有效」的结论，必须先过<b>不重叠事件</b>这一关。</div></div>
""")

# ---- 三、方法论
BODY.append(f"""
<h2>三、这类假设今后应该怎么正式检验</h2>
<div class="card">
<p>把「只在极值 / 少数日有效」拆成四个可证伪的子假设，每个都有对应检验与必须做的校正：</p>
{table(["#", "子假设", "检验方法", "必须做的校正 / 陷阱"], [
 ["H1", "极值日才有信号", "尾部事件研究：PIT 分位首次进入 ≤10 / ≥90，20 日去重，循环平移对照给 p", "BH-FDR 覆盖 因子×尾×窗口；事件数<8 不出结论；方向必须事前定，不能用全样本 IC 反推"],
 ["H2", "少数交易日驱动 IC", "逐日贡献集中度、删尾 IC、|贡献| top10 占比、块级 IC 分布", "IC≈0 时占比分母会反号，改用绝对占比；删尾后要重算秩；块均值 t 与全样本 bootstrap 结论不一致时应判为漂移抵消"],
 ["H3", "信号被跨期漂移稀释", "分段 IC、段内去均值合并 IC、跨年 KS、rolling 分位对照", "段内去均值含未来信息，只能诊断；生产口径必须用 rolling/expanding PIT"],
 ["H4", "多个因子同时极值才有效", "共振事件（同日极值因子数≥阈值），把 41 次检验合并成 1 次以提高功效", "阈值×窗口扫描本身也是多重检验；前瞻窗口重叠必须做「间隔≥h」的不重叠事件复检"],
])}
<h3>通用的四道闸门</h3>
<ul>
<li><b>不重叠优先：</b>事件间隔 ≥ 前瞻窗口 h。本报告里这一条直接把 p=0.059 打成 p=0.568。</li>
<li><b>零分布要保留结构：</b>循环平移整条前瞻收益序列，比随机抽日期更能保留收益自相关与事件聚类；随机抽日期若再按 gap 过滤，会改变样本数、使零分布失真。</li>
<li><b>试验日志：</b>本轮新增 41 + 15 + 4（分段）+ 8（留一年）等检验，必须与 L1 的 187 次登记在同一族系，否则序贯检验会让 FDR 形同虚设。</li>
<li><b>样本外终点：</b>任何活下来的格子都要走 walk-forward（训练段定阈值 → 下一段只用该阈值），并报告「换段后是否还活着」。在通过之前绝不改 8 指标等权。</li>
</ul>
<div class="note">还有一个被低估的检验：<b>换被解释变量</b>。8 指标等权本来判「市场位置」而非「次日/60 日收益」，
把目标换成「未来 120 日是否处于底部区域 / 是否出现 20% 级别回撤」可能更有功效。
位置型指标用收益型 IC 评估，本身就可能问错了问题。</div>
</div>
""")

# ---- 四、可信度评分
t5 = table(["层级", "分项", "权重", "得分", "打分依据（2026-11-20）"],
           [["数据", n, f"{int(w*100)}%", "@" + str(v), why] for n, w, v, why in D_items] +
           [["证据", n, f"{int(w*100)}%", "@" + str(v), why] for n, w, v, why in S_items])

BODY.append(f"""
<h2>四、每日可信度评分：可以做，而且最有用的是「证据分」</h2>
<div class="card">
<p>关键设计取舍：<b>可信度 ≠ 看多看空的分数</b>。它是对「今天这套系统的读数值不值得信」的<b>元评分</b>，
分两层——数据层回答「数全不全、口径稳不稳」，证据层回答「今天这种读数，历史上被证明过吗」。
两者必须分开输出，否则「数据很全」会掩盖「证据很弱」。</p>
<div class="score">
<div><div class="k" style="color:var(--sub);font-size:12px">示意综合分</div>
<div class="big {BANDCLS}">{TOTAL}</div><div class="sub">{esc(BAND)}</div></div>
<div class="box"><b>数据层 D = {D}</b><br><span class="sub">覆盖 / 新鲜度 / PIT 成熟度 / 漂移 / 口径一致性</span></div>
<div class="box"><b>证据层 E = {E_SCORE}</b><br><span class="sub">状态证据 / 极值证据 / 样本成熟度 / 跨期稳定性 / 集中度折价</span></div>
<div class="box"><b>综合 = 0.4·D + 0.6·E</b><br><span class="sub">证据层权重更高，因为数据齐全不代表结论可靠</span></div>
</div>
<div class="crit">当日结论：<b>数据 {D} 分、证据 {E_SCORE} 分</b>。17/17 因子齐全、几乎无滞后，
但当日 {SNP['n_low']} 个低分位共振（历史 p90 仅 {SNP['low_cnt_hist_p90']} 个）落在
「L1 无任何 FDR 显著格子」的状态里；历史上同类极值事件 41 次只有 1 次通过校正且方向不同。
翻译成人话：<b>今天数字很全，但恰恰是最不该照着下结论的一天</b>。</div>
{t5}
<div class="note">上表为<b>示意计算，非正式产物</b>：权重按先验设定、未经标定，部分分项用代理量（如 S3 用事件数中位、
S5 用年份集中度）。正式落地时需要逐项写死定义、冻结权重，并在试验日志登记。</div>

<h3>落地形态（确认后实施，本轮未动生产）</h3>
<ul>
<li>新增 <code>build_daily_confidence.py</code>：逐日 PIT，输入 L0 矩阵 + L1 审计结果 + 共振板状态，
输出 <code>data/daily_confidence.csv / .json / .js</code>（含全历史逐日 D / E / 总分 / 档位 / 各分项）。</li>
<li>所有输入在 t 日可知：<code>coverage / stale / history_start</code> 用截至 t 的观测，
<code>status</code> 用 T 日可知的动量·波动·共振档，<b>禁止使用段内去均值、HMM smoothed 等含未来信息的量</b>。</li>
<li>历史回看要用「当时的 L1 结论」而非今天的 L1 结论，否则会把未来统计结果泄漏进历史分数（walk-forward 式重放）。</li>
<li>前端新增独立「每日可信度」卡片与趋势图，<b>不进 8 指标等权综合分、不改 13 维共振板、不触发任何交易建议</b>；
DAG 排在 <code>audit_efficacy.py</code> 之后。</li>
<li>红线：权重不得按样本内表现调参；档位阈值冻结；新增检验一律登记试验日志；
评分只用于「要不要打折看待今天的读数」，不用于择时。</li>
</ul>
</div>

<h2>五、结论与下一步</h2>
<div class="card">
<div class="good"><b>结论：</b>① 已被统计证明有效的日频因子目前是 0 个，条件格子 2 个，属假设级别；
② 「只在极值有效」没有得到支持，41 次检验 FDR 后 1 个且方向反直觉；
③ 真正的问题是跨期水平漂移与行情集中（2018+2024 占 {top2_share}%）把局部结构稀释成全样本噪音；
④ 少数日「有效」的观感，很大程度来自 60 日重叠窗口造成的伪独立样本；
⑤ 每日可信度评分值得做，且应当以证据层为主、数据层为辅。</div>
<p><b>下一步建议（按优先级）：</b></p>
<ul>
<li><b>P0 换目标变量：</b>用「未来 120 日是否处于底部区域 / 最大回撤」替代纯收益 IC，重跑 17 因子主检验——位置型指标可能被现有评估方式系统性低估。</li>
<li><b>P1 落地每日可信度评分：</b>按上文两层结构生成全历史序列 + 独立卡片，先观察不打折扣时的分布。</li>
<li><b>P2 滚动分位轨道做正式 OOS：</b>已诊断 <code>pct_r126</code> 把跨年 KS 从 0.435 压到 0.282，用 walk-forward 检验它在段外是否稳定，再谈是否影响生产口径。</li>
<li><b>P3 共振事件重构：</b>把 41 次分散检验合并为少数几个事先定义的复合事件，强制不重叠 + 样本外复检。</li>
</ul>
<p class="sub">以上均为研究层建议。在 walk-forward 样本外证明有稳定增量之前，
<b>8 指标等权综合分 34.65、13 维共振板、确认层 0/3 均保持不变</b>。</p>
</div>

<p class="foot">本报告由只读探针 <code>_conditional_probe.py</code> 生成，数据源 <code>data/_conditional_probe.json</code>、
<code>factor_matrix.json</code>、<code>efficacy_audit.json</code>；未接入 <code>start_site.py</code> DAG、
未修改 <code>mood.html</code> 与任何生产口径。所有统计为历史描述与假设生成，不构成投资建议；
历史表现不代表未来，样本外未验证的结论不得用于实盘决策。</p>
""")

# ---- 图表脚本
years = sorted(int(k) for k in P["top_day_years"])
year_vals = [P["top_day_years"].get(str(y), P["top_day_years"].get(y, 0)) for y in years]
names_c = [c["name"][:10] for c in P["concentration"]]
ic_full = [c["ic"] for c in P["concentration"]]
ic_dm = [next(d["ic_demeaned"] for d in P["drift"] if d["key"] == c["key"]) for c in P["concentration"]]
abs_sh = [next(a["abs_share_top10_pct"] for a in P["abs_share"] if a["key"] == c["key"])
          for c in P["concentration"]]
trim = [c["trim_retain_pct"] for c in P["concentration"]]

CHARTS = f"""
<script>
var AX={{axisLabel:{{color:'#5b6472',fontSize:11}},axisLine:{{lineStyle:{{color:'#e3e6ec'}}}},
splitLine:{{lineStyle:{{color:'#eef1f5'}}}}}};
function mk(id,opt){{var el=document.getElementById(id);if(!el||!window.echarts)return;
var ch=echarts.init(el);ch.setOption(opt);window.addEventListener('resize',function(){{ch.resize()}});}}
mk('c1',{{title:{{text:'各因子的高贡献日落在哪一年（次数）',left:'center',textStyle:{{fontSize:14}}}},
tooltip:{{trigger:'axis'}},grid:{{left:55,right:25,bottom:40,top:50}},
xAxis:{{type:'category',data:{json.dumps([str(y) for y in years])},...AX}},
yAxis:{{type:'value',...AX}},series:[{{type:'bar',data:{json.dumps(year_vals)},
itemStyle:{{color:function(p){{return (p.name==='2018'||p.name==='2024')?'#e23b2e':'#7fa6db'}},borderRadius:[4,4,0,0]}},
label:{{show:true,position:'top',fontSize:10}}}}]}});
mk('c2',{{title:{{text:'全样本 IC vs 段内去均值后 IC（诊断性）',left:'center',textStyle:{{fontSize:14}}}},
tooltip:{{trigger:'axis'}},legend:{{data:['全样本 IC','段内去均值 IC'],top:26,textStyle:{{fontSize:11}}}},
grid:{{left:55,right:25,bottom:90,top:70}},
xAxis:{{type:'category',data:{json.dumps(names_c, ensure_ascii=False)},
axisLabel:{{color:'#5b6472',fontSize:10,rotate:45}},axisLine:{{lineStyle:{{color:'#e3e6ec'}}}}}},
yAxis:{{type:'value',...AX}},
series:[{{name:'全样本 IC',type:'bar',data:{json.dumps(ic_full)},itemStyle:{{color:'#9aa7b8'}}}},
{{name:'段内去均值 IC',type:'bar',data:{json.dumps(ic_dm)},itemStyle:{{color:'#ff5b2e'}}}}]}});
mk('c3',{{title:{{text:'|贡献| 前10%占比 与 删尾后 IC 保留率',left:'center',textStyle:{{fontSize:14}}}},
tooltip:{{trigger:'axis'}},legend:{{data:['|贡献|前10%占比','删尾IC保留率'],top:26,textStyle:{{fontSize:11}}}},
grid:{{left:55,right:55,bottom:90,top:70}},
xAxis:{{type:'category',data:{json.dumps(names_c, ensure_ascii=False)},
axisLabel:{{color:'#5b6472',fontSize:10,rotate:45}},axisLine:{{lineStyle:{{color:'#e3e6ec'}}}}}},
yAxis:[{{type:'value',name:'%',...AX}},{{type:'value',name:'保留率%',...AX}}],
series:[{{name:'|贡献|前10%占比',type:'bar',data:{json.dumps(abs_sh)},itemStyle:{{color:'#2f6fd0'}}}},
{{name:'删尾IC保留率',type:'line',yAxisIndex:1,data:{json.dumps(trim)},
itemStyle:{{color:'#d98a00'}},lineStyle:{{width:2}},symbolSize:6}}]}});
</script>
"""

OUT = HEAD + "".join(BODY) + CHARTS + "</div></body></html>"
path = BASE / "条件有效性与每日可信度评分-20261121.html"
path.write_text(OUT, encoding="utf-8")
print(f"生成 {path.name}  {len(OUT):,} 字符")
print(f"示意评分：D={D}  E={E_SCORE}  综合={TOTAL}  {BAND}")
