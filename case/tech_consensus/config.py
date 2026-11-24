# -*- coding: utf-8 -*-
"""宽基指数技术分析多空平日报 · 配置"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
DATA_DIR = os.path.join(HERE, "data")
FONT_PATH = os.path.join(HERE, "fonts", "SimHei.ttf")

# ---------------- 宽基 ETF 池（7 只，含恒生科技） ----------------
ETF_POOL = [
    {"code": "sh510300", "name": "沪深300", "short": "300"},
    {"code": "sh510500", "name": "中证500", "short": "500"},
    {"code": "sh510050", "name": "上证50",  "short": "50"},
    {"code": "sz159915", "name": "创业板",  "short": "创"},
    {"code": "sh588000", "name": "科创50",  "short": "科创"},
    {"code": "sh512100", "name": "中证1000", "short": "1000"},
    {"code": "sh513180", "name": "恒生科技", "short": "恒科"},
]

# 数据范围（留足 EMA200 / MA160 / VMD60 / LAGS252 的预热）
DATA_START = "2021-01-01"
DATA_END = "2026-12-31"

# ---------------- 数据源：多级代理回退体系 ----------------
# 顺序固定：新浪 -> 东方财富 -> 同花顺（AKShare 系） -> Tushare（TS 兜底）
# AK筛在前、TS筛兜底：上一层缺数据时自动落到下一层补齐，全部统一入 data/*.csv 缓存
# sina= dataHub 前复权（主源）、em= 东财 qfq：两者均为前复权，避免口径混用；
# ths（同花顺）与 ts 为未复权，已移出链路，保留函数备查。
SOURCE_ORDER = ["sina", "em"]

# Tushare token 来源：优先环境变量 TUSHARE_TOKEN，其次既有资产中的代理 token
# （token 属密钥，不写入本工程文件，仅记录存在位置与读取方式）
# 候选路径：① 环境变量 TK_CSV 显式指定；② 上一级工作区根 tk.csv；③ 会话目录 tk.csv
TK_CSV = os.environ.get("TK_CSV", "")
TK_CSV_CANDIDATES = [
    TK_CSV,
    os.path.abspath(os.path.join(HERE, "..", "..", "tk.csv")),
    os.path.abspath(os.path.join(HERE, "..", "tk.csv")),
]

# Tushare 代理配置（既有资产，2026-08 实测可用）：
#   - ts_config.py：15k 积分代理 token，必须配 _DataApi__http_url = "https://ttx.dailyfetch.top/" 才能用
#   - ts_official.py：2000 分官方直连 token（沙箱 NAT IP 会轮换，仅固定 IP 环境可用）
# 代理地址可通过环境变量 TUSHARE_HTTP_URL 覆盖
TS_CFG_DIRS = [
    "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5fd6b2558bffeYG74Ts057P2dLR/strategy_repro",
]
TS_DEFAULT_HTTP_URL = os.environ.get("TUSHARE_HTTP_URL", "https://ttx.dailyfetch.top/")

# ---------------- 各方法参数 ----------------
# 动力学MACD：DIF 快慢线已由 12/26 调整为 10/20（2026-09-26 定稿），
# v=DIF/ATR14 速度、a=MACD柱/ATR14 加速度，另加 F 值与 K/U 动能势能判断配合仓位
DX_EMA_FAST, DX_EMA_SLOW, DX_SIG, DX_TREND = 10, 20, 9, 50
MADC_MA_SHORT, MADC_MA_LONG = 40, 160
AC1_WINDOW = 20
AC1_GOOD_MIN = 0.05
VMD_WINDOW = 60
VMD_K, VMD_ALPHA = 4, 2000
VMD_N_LOW = 2
VMD_SLOPE_N = 5
CHAN_VALID_BARS = 15          # 缠论最近买卖点距末尾超过该根数 -> 震荡
MADC_SLOPE_WIN = 5

# ---------------- 汇总投票 ----------------
# 多=+1 空=-1 震荡=0 无信号(池外 etc.)=不计票
SCORE_LONG, SCORE_SHORT, SCORE_FLAT = 1, -1, 0