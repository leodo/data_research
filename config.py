# -*- coding: utf-8 -*-
"""全局口径配置：经验系数、抓取参数与服务端口。

这些数值多为实测标定，散落在各脚本时容易改一处漏一处、也难以复核。
统一放此处；修改任何系数请在注释里写明依据与日期。
注意：中证1000 QVIX 回归系数是运行时用重叠期 np.polyfit 拟合的，
不是固定常量（随数据更新会微调），故不放在这里。
"""
from __future__ import annotations

# ---------------- 服务 ----------------
PORT = 8040

# ---------------- 抓取参数 ----------------
NPROC = 8                       # 新浪 mini_racer 非线程安全，必须多进程；8 为实测吞吐/稳定性平衡点
START_CROWDING = "20150101"     # 交易拥挤度起始
START_BREADTH = "20150101"      # 市场宽度起始
START_TURNOVER = "20150105"     # 全市场换手率起始
START_ETF_RATIO = "20180102"    # 300ETF 占比起始
START_FEAR_GREED = "20210101"   # 恐贪指数起始

# ---------------- 全市场换手率桥接（rebuild_turnover.py）----------------
# 深交所 2021-07 后接口返回损坏 xlsx，改用分段口径还原全市场量级。
# 系数来自重叠样本：全市场/沪市换手率比值实测 1.28~1.42，绝对值有 10~20% 误差，分位走势可靠。
K_SZ_TO_ALL = 0.72              # 2015-2021：全市场 ≈ 深市换手率 × 0.72
K_SH_TO_ALL = 1.36              # 2022 起：全市场 ≈ 沪市换手率 × 1.36

# ---------------- 头部 300ETF 占比（build_etf_ratio.py）----------------
ETF_CODES = ("510300", "510310", "510330")  # 上交所三只；159919 深交所接口不可用
MV_ANCHOR_YI = 340000.0         # 2026-11 沪深300 流通市值约 34 万亿（单位：亿元）
                                # 分母用「指数最新点位」线性代理：系数 = MV_ANCHOR_YI / 最新点位

# ---------------- 恐贪指数（build_fear_greed.py）----------------
FG_WINDOW = 250                 # 滚动分位窗口（交易日）
STRENGTH_MAX_LAG_DAYS = 3       # 成分股强度缓存允许落后的交易日数
QVIX_1000_CUT = "2025-12-31"    # 中证1000 专属 QVIX 真实段截止，之后用 500ETF 回归衔接

# ---------------- 市场宽度（build_breadth.py）----------------
MA_LONG = 200                   # 主口径：站上 200 日均线
MA_SHORT = 60                   # 辅助：站上 60 日均线
BREADTH_MIN_STOCKS = 100        # 当日有效股票数下限，低于此不输出

# ---------------- 两融（build_margin.py）----------------
MARGIN_GROWTH_WIN = 60          # 融资余额增速回看窗口（交易日）

# ---------------- 顶底共振信号板（build_turning.py）----------------
TURNING_FEAR_PCT = 70           # 单维度「底部极值」阈值：fear>=70（0=极热/顶部，100=极冷/底部）
TURNING_TOP_PCT = 20            # 单维度「顶部极值」阈值：fear<=20
TURNING_TRIGGER = 60            # 共振度触发线（满足极值的维度占比 %）
TURNING_MIN_DIMS = 6            # 少于此数量的有效维度不出信号（早期样本不足）
TURNING_FWD_WINS = (20, 60)     # 历史回测的前瞻收益窗口（交易日）
TURNING_TRIGGER_GAP = 20        # 触发日去重的间隔（交易日），避免重叠样本夸大显著性
TURNING_MIN_HIST = 250          # 单维度 expanding 分位的最小历史样本
TURNING_ARMS_PANIC_ADV = 20.0   # 上涨家数占比低于此值(极端普跌日)时剔除 TRIN 维度：
                                # 此时低 TRIN 是资金抱团+全面失血，不是买盘强劲
TURNING_ARMS_EUPHORIA_ADV = 80.0  # 高于此值(极端普涨日)同样剔除：
                                # 家数比爆炸使 TRIN 机械升高，并非恐慌抛售。
                                # 实测全历史 TRIN>2 的 96 日中 67 日为普涨日、仅 1 日为恐慌日

# ---------------- ERP 股债归因（build_mood.py 内）----------------
ERP_ATTR_WIN = 250              # 归因回看窗口（交易日，约 1 年）
ERP_ILLUSION_SHARE = 60         # 债券端贡献占比 ≥ 此值且股票端未变便宜 → 判定「低利率便宜错觉」

# ---------------- 阿姆氏指标与宽广度体系（build_arms.py）----------------
ARMS_MIN_STOCKS = 100           # 当日有效股票数下限
ARMS_TRIN_SMOOTH = 10           # TRIN 平滑窗口（交易日），10 日为常用判极值口径
MCC_FAST = 19                   # McClellan 快线 EMA
MCC_SLOW = 39                   # McClellan 慢线 EMA
BT_SPAN = 10                    # Zweig Breadth Thrust 的 EMA 窗口
BT_LOW = 40.0                   # 推力起点（超卖）
BT_HIGH = 61.5                  # 推力确认线
BT_LOOKBACK = 10                # 由低点升至确认线的最大间隔（交易日）
BREADTH_SCORE_MIN_HIST = 20     # 合成宽度分前各维度 expanding 分位的最小样本

# ---------------- P1 宏观 / 机构 / 产业资本指标 ----------------
M1M2_START = "2008-01-01"       # 东财货币供应量接口起点
REPURCHASE_START = "2015-01-01" # 回购样本 2005 起，但 2015 前样本稀疏，作图从此开始
REPURCHASE_ROLL = 12            # 回购规模滚动窗口（月）

# ---------------- 重要股东增减持（build_holder_change.py）----------------
# 东财 RPT_SHARE_HOLDER_INCREASE 全量 1994 起，但 2015 前公告样本稀疏，
# 与回购卡片起点对齐便于同栏目对照。
HOLDER_CHANGE_START = "2015-01-01"
HOLDER_CHANGE_ROLL = 12         # 滚动窗口（月），与回购一致
HOLDER_CHANGE_LOOKBACK = 15     # 增量回看天数，覆盖盘后补录/修订的公告

# ---------------- 拐点确认层（build_confirm.py，2026-11-21 新增）----------------
# 三项 P0 指标全部挂在「确认层」，不进 8 指标等权综合分、不改 13 维共振板。
# 集中放阈值是为了做三档稳健性检验时不必翻脚本；改动请同步更新卡片口径说明。
CONFIRM_START = "2015-01-01"    # 日频序列起点，与逐股价格缓存 / 宽度体系对齐
CONFIRM_FWD_WINS = (20, 60)     # 日频前瞻收益窗口（交易日）
CONFIRM_TRIGGER_GAP = 20        # 触发日去重间隔（交易日 / 周，按各自序列频率）
CONFIRM_MIN_HIST = 250          # expanding 分位最小样本

# P0-1 情绪-价格底背离（周频，composite.history + 沪深300 周收盘）
# 定义：近 8 周情绪分曾进悲观区(<=35)，随后 4 周回升 >=3 分，同期价格仍未上涨。
DIV_PESSIMIST = 35.0            # 悲观区阈值（综合情绪分绝对水平）
DIV_LOOKBACK_W = 8              # 悲观区回看窗口（周）
DIV_REBOUND_W = 4               # 情绪分回升窗口（周）
DIV_REBOUND_MIN = 3.0           # 4 周回升幅度阈值（分）
DIV_PRICE_MAX = 0.0             # 同期沪深300 涨幅上限(%)，<=0 表示价格滞后
DIV_FWD_W = (4, 12)             # 前瞻窗口（周），12 周≈60 交易日
DIV_ROBUST = ((2.0, 35.0), (3.0, 35.0), (4.0, 35.0), (3.0, 30.0), (3.0, 40.0))
                                # 稳健性档位：(回升阈值, 悲观阈值)

# P0-2 涨跌停家数 / 缩量止跌（逐股未复权收盘价 + 逐股成交额）
LIMIT_MAIN = 10.0               # 主板涨跌停幅度(%)
LIMIT_GEM = 20.0                # 创业板 / 科创板
LIMIT_BJ = 30.0                 # 北交所
LIMIT_TOL = 1.5                 # 判定容差(百分点)：收盘价取整 + 四舍五入会偏离理论价
LIMIT_EXFAT_MAX = -35.0         # 单日跌幅小于此值判为除权/重组异常，不计入跌停
LIMIT_MIN_PRICE = 2.0           # 低价股取整误差过大，不参与涨跌停判定
LIMIT_MIN_STOCKS = 100          # 当日有效股票数下限
VOL_RATIO_WIN = 20              # 20 日量能比窗口（成交额 / 过去20日均额）
VOL_HIGH_WIN = 250              # 成交额相对前高的回看窗口
VOL_SHRINK_MAX = 0.80           # 量能比 <= 此值视为缩量
LIMIT_DOWN_EASE = 1.0           # 跌停占比较 5 日前回落 >= 此值(pp) 视为恐慌缓解

# P0-3 波动率风险溢价 VRP（QVIX 隐含波动 - 沪深300 已实现波动）
VRP_RV_WIN = 20                 # 已实现波动窗口（交易日），年化按 244 日
VRP_DOWNSIDE_WIN = 60           # 下行波动贡献占比窗口
VRP_ANNUAL_DAYS = 244           # A股年化交易日数
VRP_EXTREME_LOW = 20            # VRP 历史分位 <=20：隐波相对已实现波动偏便宜
VRP_EXTREME_HIGH = 80           # VRP 历史分位 >=80：恐慌溢价高
VRP_PANIC_EASE_WIN = 5          # VRP 由高位回落的观察窗口（交易日）

# ---------------- L0 因子变换层 / L1 常驻效力审计（build_factor_matrix.py / audit_efficacy.py，2026-11-21 新增）----------------
# 双轨原则：保留 pct_exp（= 卡片 history.pct，全历史 expanding PIT）作为生产主口径，
# 保证综合分 34.65 与已交付三份评估报告的历史可比性；并列生成其它变换进独立因子矩阵，
# 绝不替换、绝不回写 mood_data.json。所有变换只用 t 及之前的信息。
FACTOR_START = "2016-01-11"     # 统一审计起点：与两份诊断报告一致
FACTOR_FFILL_LIMIT = {"日": 5, "周": 12, "月": 45}
                                # 混频对齐的前向填充上限（交易日）；低频因子不靠 ffill 造伪独立观测
FACTOR_MIN_COVERAGE = 0.80      # 日频因子入模的 2016 起覆盖率下限
FACTOR_MAIN_TRACK = "pct_exp"   # L1 主检验轨道（生产口径，保持历史可比）
FACTOR_ROBUST_TRACKS = ("raw", "pct_exp", "pct_r126", "pct_r250", "dual_gap", "chg60")
                                # 多轨道稳健性对照；变换按经济含义先验指定，不按样本内 IC 挑
FACTOR_PRIORITY_KS = (0.30, 0.50)   # 跨年 KS 的中/高漂移分界

# 按经济含义分类（决定变换策略，不是数据驱动挑选的结果）
FACTOR_KINDS = {
    "rp": "比率·有理论锚", "dgz": "计数·趋势", "fx_fe": "金额·规模", "pgjj_zsy": "占比·bounded",
    "qschsl": "比率·体制切换", "pcr": "比率·bounded", "zzpgnh": "收益率·均值回复",
    "magic": "复合价差·体制切换", "etf300_ratio": "占比·结构趋势", "margin_growth": "增速·已去量纲",
    "breadth_ma200": "占比·bounded", "a_pb": "估值·有理论锚", "erp_attrib": "比率·有理论锚",
    "fund_position": "占比·bounded", "m1m2": "宏观·月频", "repurchase": "金额·规模",
    "holder_change": "金额·规模", "below_net": "占比·结构趋势", "nhnl": "占比·bounded",
    "adv_ratio": "占比·bounded", "hs300_rsi": "技术指标·已 0-100", "arms": "比率·厚尾",
    "breadth_score": "复合分·bounded", "mcclellan": "摆动量·均值回复",
    "composite": "复合分·bounded",
}

# L1 审计统计口径（与 audit_efficacy.py 首版保持一致，改动会使历史结论不可比）
AUDIT_HORIZONS = (20, 60, 120)
AUDIT_BLOCK = 60                # 移动块 bootstrap 块长（交易日）
AUDIT_BOOT = 1200               # 单因子主检验重采样次数
AUDIT_BOOT_COND = 300           # 条件 IC 重采样次数（136 个格子，控制 DAG 耗时）
AUDIT_MIN_OBS = 120             # 单因子最小有效样本
AUDIT_COND_MIN_OBS = 60         # 条件 IC 最小有效样本
AUDIT_SEED = 20261121           # 固定随机种子，保证审计结果可复跑
