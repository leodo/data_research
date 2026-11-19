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
