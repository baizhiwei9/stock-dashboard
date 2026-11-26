"""
涨跌预测系统 - 日报生成（五算法版本）
算法: XGBoost + TCN 时序卷积网络 + TxDIF (MACD DIF方向策略) + 缠论多级别分析 + LSTM+XGBoost
支持品种：
  股指：沪深300、中证500、中证1000、创业板指、科创50
  国债期货：5年国债(TF)、30年国债(TL)
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()

from trading_day_check import check_trading_day
check_trading_day()

import xgboost as xgb
import numpy as np
import pandas as pd
import akshare as ak

# ── DataHub 统一数据入口（禁止直连 akshare 取数） ──
import os as _os, sys as _sys  # noqa: E402
os, sys = _os, _sys
def _hub_candidates(_start):
    """DataHub 根候选（唯一口径优先）。

    先用仓库根的 ``datahub_path.py``（build.py / scheduler.py 用的同一份代码），
    它不在时退回「同级 checkout」再退老云盘路径。刻意**不**从任意上层目录找
    ``datahub-multi-tier`` —— 机器上可能有别人留下的同名旧副本（/tmp 下就有），
    按目录存在性猜会静默连错库。
    """
    out = []
    cur = os.path.abspath(_start)
    for _ in range(8):
        p = os.path.join(cur, 'datahub_path.py')
        if os.path.isfile(p):
            ns = {'__file__': p, '__name__': 'datahub_path'}
            try:
                with open(p, encoding='utf-8') as f:
                    exec(compile(f.read(), p, 'exec'), ns)
                out.append(ns['datahub_root']())
            except Exception:                              # noqa: BLE001
                pass
            out.append(os.path.join(cur, 'datahub-multi-tier'))
            out.append(os.path.join(os.path.dirname(cur), 'datahub-multi-tier'))
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    out += [os.environ.get('DATA_HUB_PATH') or os.environ.get('DATAHUB_DIR')
            or os.environ.get('EDB_REPO') or '',
            _cp('/opt/coze/.fuse_drive/dataHub_三级体系')]
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq
_HUB_PATHS = _hub_candidates(_os.path.dirname(_os.path.abspath(__file__)))
for _hp in _HUB_PATHS:
    if os.path.isdir(os.path.join(_hp, 'data_hub')) and _hp not in sys.path:
        sys.path.insert(0, _hp)
        break
try:
    import data_hub as dh
except Exception as _e:  # noqa: BLE001
    dh = None
    print(f"[DataHub] 导入失败，回退直连: {_e}")
from datetime import datetime, timedelta
from sklearn.metrics import accuracy_score
import warnings
import json
import requests
import os
import re
from pytdx.hq import TdxHq_API
warnings.filterwarnings('ignore')

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

# ============================================================
# 缠论核心代码（从 strategy_chanlun.py 复制，用于多级别分析）
# ============================================================

# 缠论参数配置
CL_DELTA_A = 0.010       # 抵达缓冲 1.0%
CL_DELTA_E = 0.0005      # 实体容差 0.05%
CL_THETA = 0.0050        # 粘合带宽 0.50%


def cl_calc_ema(data, period):
    """计算EMA（缠论用）"""
    if len(data) < period:
        return []
    ema = [sum(data[:period]) / period]
    multiplier = 2.0 / (period + 1)
    for i in range(period, len(data)):
        ema.append((data[i] - ema[-1]) * multiplier + ema[-1])
    return ema


def cl_calc_macd(closes, fast=10, slow=20, signal=5):
    """计算MACD(10,20,5)（缠论用）"""
    if len(closes) < slow + signal:
        return None, None, None

    ema_fast = cl_calc_ema(closes, fast)
    ema_slow = cl_calc_ema(closes, slow)

    offset = slow - fast
    dif = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]

    dea = cl_calc_ema(dif, signal)

    dea_offset = signal - 1
    dif_aligned = dif[dea_offset:]
    dea_aligned = dea

    macd_hist = [(dif_aligned[i] - dea_aligned[i]) * 2 for i in range(len(dea_aligned))]

    return dif_aligned, dea_aligned, macd_hist


class ChanlunStrategy:
    """缠论策略（趋势浪子修正版）"""

    def __init__(self):
        self.green_status = "ALIVE"
        self.green_dead_count = 0
        self.cycle_active = False
        self.ma40_broken = False
        self.pullback_success_bar = -1
        self.pullback_success_triggered = False
        self.three_buy_triggered = False
        self.central_hub_complete = False
        self.position = 0

    def update_green_status(self, bars, t):
        """更新绿级状态"""
        if t < 80:
            return

        closes = [b["close"] for b in bars[:t+1]]
        ma80 = cl_calc_ema(closes, 80)

        if len(ma80) < 2:
            return

        c_t = bars[t]["close"]
        ma80_t = ma80[-1]
        ma80_prev = ma80[-2]

        ma80_up = ma80_t > ma80_prev * (1 + 0.001)
        ma80_down = ma80_t < ma80_prev * (1 - 0.001)

        if ma80_up and c_t > ma80_t:
            self.green_status = "ALIVE"
            self.green_dead_count = 0
        elif ma80_down or c_t < ma80_t:
            self.green_status = "DEAD"
            self.green_dead_count += 1
        else:
            self.green_status = "INCUBATION"

    def check_standing_above(self, ma, bars, t, n=2):
        """检查是否站稳均线上方n根"""
        if t < n or len(ma) < n:
            return False
        for i in range(n):
            idx = t - i
            if idx >= len(bars) or idx >= len(ma):
                return False
            if bars[idx]["close"] < ma[-(i+1)]:
                return False
        return True

    def check_pullback(self, bars, ma20, ma40, ma80, t):
        """检查回踩"""
        if t < 5 or len(ma20) < 5 or len(ma40) < 5 or len(ma80) < 5:
            return

        bar_t = bars[t]
        c_t = bar_t["close"]
        l_t = bar_t["low"]

        ma20_t = ma20[-1]
        ma40_t = ma40[-1]
        ma80_t = ma80[-1]

        prev_above_ma20 = bars[t-1]["close"] > ma20[-2] if len(ma20) >= 2 else False

        touch_ma20 = abs(l_t - ma20_t) / ma20_t < CL_DELTA_A
        touch_ma40 = abs(l_t - ma40_t) / ma40_t < CL_DELTA_A
        touch = touch_ma20 or touch_ma40

        above_ma80 = l_t > ma80_t * (1 - CL_DELTA_A)

        green_ok = self.green_status == "ALIVE" or self.green_dead_count <= 2

        cycle_ok = self.cycle_active

        if prev_above_ma20 and touch and above_ma80 and green_ok and cycle_ok:
            if t > 0 and c_t > ma20_t:
                self.pullback_success_bar = t

    def check_converging_signs(self, ma20, ma40):
        """检查粘合迹象"""
        if len(ma20) < 1 or len(ma40) < 1:
            return False
        ma20_t = ma20[-1]
        ma40_t = ma40[-1]
        if ma40_t == 0:
            return False
        d = abs(ma20_t - ma40_t) / ma40_t
        return d <= 2 * CL_THETA

    def check_buy_signals(self, bars, ma_dict, t):
        """检查买入信号"""
        ma20 = ma_dict["ma20"]
        ma40 = ma_dict["ma40"]
        ma80 = ma_dict["ma80"]

        if t < 5:
            return False, "数据不足"

        bar_t = bars[t]
        c_t = bar_t["close"]
        buy_long = False
        signal_desc = "无信号"

        if self.green_status != "ALIVE" and self.green_dead_count > 2:
            return False, "绿级死亡"

        self.check_pullback(bars, ma20, ma40, ma80, t)

        # 二买检查
        if self.pullback_success_bar >= 0:
            bars_since = t - self.pullback_success_bar
            if (self.cycle_active and not self.pullback_success_triggered and
                    bars_since >= 1 and bars_since <= 10):
                has_converging = self.check_converging_signs(ma20, ma40)
                standing_ma20 = self.check_standing_above(ma20, bars, t, n=2)
                if has_converging and standing_ma20:
                    buy_long = True
                    signal_desc = "二买：回踩成功+粘合+站稳MA20"
                    self.pullback_success_bar = -1
                    self.pullback_success_triggered = True

        # 三买检查
        if not buy_long and self.cycle_active and not self.three_buy_triggered:
            if self.central_hub_complete:
                ma20_t = ma20[-1]
                ma40_t = ma40[-1]
                if ma40_t > 0:
                    d_t = (ma20_t - ma40_t) / ma40_t
                    leaving = d_t > 2 * CL_THETA
                else:
                    leaving = False

                recent_lows = [bars[k]["low"] for k in range(max(0, t-5), t+1)]
                recent_low = min(recent_lows)
                pullback_not_touch = recent_low > ma40_t * (1 + CL_DELTA_A)
                standing_ma20 = self.check_standing_above(ma20, bars, t, n=2)

                if leaving and pullback_not_touch and standing_ma20:
                    buy_long = True
                    signal_desc = "三买：中枢完成+离开+站稳MA20"
                    self.three_buy_triggered = True
                    self.central_hub_complete = False

        return buy_long, signal_desc

    def check_stop_loss(self, bars, ma_dict, t):
        """检查止损条件"""
        ma20 = ma_dict["ma20"]
        ma40 = ma_dict["ma40"]
        ma80 = ma_dict["ma80"]

        if t < 2 or not ma80:
            return False, ""

        c_t = bars[t]["close"]

        if self.green_status == "DEAD" and self.green_dead_count >= 3:
            return True, "绿级死亡止损"

        if c_t < ma40[-1] * (1 - CL_DELTA_A):
            return True, "跌破MA40止损"

        if c_t < ma80[-1] * (1 - CL_DELTA_A):
            return True, "跌破MA80止损"

        return False, ""

    def check_sell_signals(self, bars, ma_dict, t):
        """检查卖出/做空信号"""
        ma5 = ma_dict["ma5"]
        ma20 = ma_dict["ma20"]
        ma40 = ma_dict["ma40"]
        ma80 = ma_dict["ma80"]

        if t < 5:
            return False, ""

        c_t = bars[t]["close"]
        sell_signal = False
        signal_desc = ""

        # 卖出条件1：绿级死亡 + 短期均线死叉长期均线
        if self.green_status == "DEAD" and self.green_dead_count >= 2:
            if ma5 and ma20 and ma40 and len(ma5) >= 1 and len(ma20) >= 1 and len(ma40) >= 1:
                if ma5[-1] < ma40[-1]:
                    sell_signal = True
                    signal_desc = "绿级死亡+均线空头"

        # 卖出条件2：价格跌破MA40且MA40拐头向下
        if not sell_signal and len(ma40) >= 3:
            ma40_turning_down = ma40[-1] < ma40[-2] < ma40[-3]
            if c_t < ma40[-1] and ma40_turning_down:
                sell_signal = True
                signal_desc = "跌破MA40+MA40拐头"

        # 卖出条件3：MACD死叉 + 价格在MA80下方
        if not sell_signal:
            closes = [b["close"] for b in bars[:t+1]]
            dif, dea, macd_hist = cl_calc_macd(closes)
            if dif is not None and len(dif) >= 2:
                macd_death = dif[-1] < dea[-1] and dif[-2] >= dea[-2]
                if macd_death and c_t < ma80[-1]:
                    sell_signal = True
                    signal_desc = "MACD死叉+价格<MA80"

        return sell_signal, signal_desc

    def analyze(self, bars):
        """
        分析缠论信号
        返回: {
            "signal": "买入"/"卖出"/"平仓"/"观望",
            "signal_type": "二买"/"三买"/"止损"/"",
            "details": {...}
        }
        """
        result = {
            "strategy": "缠论",
            "signal": "观望",
            "signal_type": "",
            "details": {}
        }

        if len(bars) < 100:
            return result

        closes = [b["close"] for b in bars]
        ma5 = cl_calc_ema(closes, 5)
        ma10 = cl_calc_ema(closes, 10)
        ma20 = cl_calc_ema(closes, 20)
        ma40 = cl_calc_ema(closes, 40)
        ma80 = cl_calc_ema(closes, 80)

        t = len(bars) - 1
        self.update_green_status(bars, t)

        # 周期激活
        if not self.cycle_active and not self.ma40_broken:
            if self.green_status == "ALIVE":
                self.cycle_active = True

        ma_dict = {"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma40": ma40, "ma80": ma80}

        result["details"] = {
            "price": closes[-1],
            "green_status": self.green_status,
            "cycle_active": self.cycle_active,
            "ma20": ma20[-1] if ma20 else 0,
            "ma40": ma40[-1] if ma40 else 0,
            "ma80": ma80[-1] if ma80 else 0,
        }

        # 如果有持仓，先检查止损
        if self.position > 0:
            stop_loss, stop_reason = self.check_stop_loss(bars, ma_dict, t)
            if stop_loss:
                result["signal"] = "平仓"
                result["signal_type"] = "止损"
                result["details"]["stop_reason"] = stop_reason
                return result

            # 检查卖出信号
            sell_signal, sell_desc = self.check_sell_signals(bars, ma_dict, t)
            if sell_signal:
                result["signal"] = "卖出"
                result["signal_type"] = sell_desc
                return result

        # 检查买入信号
        buy_long, signal_desc = self.check_buy_signals(bars, ma_dict, t)

        if buy_long:
            result["signal"] = "买入"
            if "二买" in signal_desc:
                result["signal_type"] = "二买"
            elif "三买" in signal_desc:
                result["signal_type"] = "三买"

        # ── 无明确信号时，根据均线趋势推断方向 ──
        if result["signal"] == "观望":
            if ma20 and ma40 and ma80 and len(ma20) >= 2 and len(ma40) >= 2:
                # 空头排列: MA5 < MA20 < MA40 < MA80 且 MA40 下行
                if ma5 and ma5[-1] < ma20[-1] < ma40[-1] < ma80[-1]:
                    result["direction"] = -1
                # 价格在 MA80 下方 + MA40 下行（即使不是完美空头排列）
                elif closes[-1] < ma80[-1] and ma40[-1] < ma40[-3]:
                    result["direction"] = -1
                # 多头排列: MA5 > MA20 > MA40 > MA80 且 MA40 上行
                elif ma5 and ma5[-1] > ma20[-1] > ma40[-1] > ma80[-1]:
                    result["direction"] = 1
                # 价格在 MA80 上方 + MA40 上行
                elif closes[-1] > ma80[-1] and ma40[-1] > ma40[-3]:
                    result["direction"] = 1

        return result


# ============================================================
# 飞书 Webhook 配置（敏感值统一走 app_secrets，代码里不留明文）
# ============================================================
for _sp in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'case', '_lib'),
            _cp('/Coze/Drive/股市监控画布/stock-dashboard/case/_lib')):
    if os.path.isdir(_sp) and _sp not in sys.path:
        sys.path.insert(0, _sp)
try:
    import app_secrets as _app_secrets
except Exception:  # noqa: BLE001
    _app_secrets = None


def _secret(dotted, *env_names, default=''):
    """敏感配置读取：环境变量 > ~/.config/stock-dashboard/secrets.json，缺失返回 default。"""
    for _n in env_names:
        _v = os.environ.get(_n)
        if _v:
            return _v
    if _app_secrets is None:
        return default
    try:
        return _app_secrets.get(dotted) or default
    except Exception:  # noqa: BLE001
        return default


FEISHU_WEBHOOK_URL = _secret('feishu.webhook', 'FEISHU_WEBHOOK_URL')

# ============================================================
# 品种代码映射
# ============================================================
# 股指指数
INDEX_MAP = {
    '沪深300': 'sh000300',
    '中证500': 'sh000905',
    '中证1000': 'sh000852',
    '创业板指': 'sz399006',
    '科创50': 'sh000688'
}

# 国债期货（主力连续）
FUTURES_MAP = {
    '5年国债': 'TF0',
    '30年国债': 'TL0',
}

# 港股指数
HK_INDEX_MAP = {
    '恒生指数': 'HSI',
    '恒生科技': 'HSTECH',
}

WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


# ============================================================
# TCN 模型定义
# ============================================================

class TCNBlock(nn.Module):
    """TCN 残差块"""
    def __init__(self, in_ch, out_ch, kernel_size=3, dilation=1, dropout=0.15):
        super().__init__()
        self.pad = (kernel_size - 1) * dilation
        self.conv1 = nn.Conv1d(in_ch, out_ch, kernel_size, padding=self.pad, dilation=dilation)
        self.bn1 = nn.BatchNorm1d(out_ch)
        self.conv2 = nn.Conv1d(out_ch, out_ch, kernel_size, padding=self.pad, dilation=dilation)
        self.bn2 = nn.BatchNorm1d(out_ch)
        self.dropout = nn.Dropout(dropout)
        self.downsample = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else None
        self.relu = nn.ReLU()

    def forward(self, x):
        out = self.conv1(x)
        out = out[:, :, :x.size(2)]
        out = self.bn1(out)
        out = self.relu(out)
        out = self.dropout(out)
        out = self.conv2(out)
        out = out[:, :, :x.size(2)]
        out = self.bn2(out)
        out = self.relu(out)
        out = self.dropout(out)
        res = x if self.downsample is None else self.downsample(x)
        return self.relu(out + res)


class TCNModel(nn.Module):
    """TCN 完整模型"""
    def __init__(self, input_dim=23, nb_filters=32, kernel_size=3,
                 dilations=None, dropout=0.15):
        super().__init__()
        if dilations is None:
            dilations = [1, 2, 4, 8]
        layers = []
        in_ch = input_dim
        for d in dilations:
            layers.append(TCNBlock(in_ch, nb_filters, kernel_size, d, dropout))
            in_ch = nb_filters
        self.tcn = nn.Sequential(*layers)
        self.conv1x1 = nn.Conv1d(nb_filters, 1, kernel_size=1)
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.fc = nn.Sequential(
            nn.Linear(1, 16), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(16, 1), nn.Sigmoid()
        )

    def forward(self, x):
        out = self.tcn(x)
        out = self.conv1x1(out)
        out = self.pool(out).squeeze(-1)
        out = self.fc(out)
        return out


# ============================================================
# TCN 特征工程（23维）
# ============================================================

def compute_tcn_features(df):
    """计算 23 维 TCN 特征"""
    df = df.copy()
    df['return'] = df['close'].pct_change()
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma40'] = df['close'].rolling(40).mean()
    df['ma_ratio'] = df['ma5'] / df['ma20']
    df['mom_5'] = df['close'].pct_change(5)
    df['mom_20'] = df['close'].pct_change(20)
    df['ma20_dev'] = df['close'] / df['ma20']
    df['vol5'] = df['return'].rolling(5).std() * np.sqrt(252)
    df['vol20'] = df['return'].rolling(20).std() * np.sqrt(252)
    df['vol_ratio'] = df['vol5'] / df['vol20']
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    df['volume_ma5'] = df['volume'].rolling(5).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma5'].replace(0, np.nan)
    df['volume_ma20'] = df['volume'].rolling(20).mean()
    df['volume_trend'] = df['volume_ma5'] / df['volume_ma20'].replace(0, np.nan)
    df['high_low'] = (df['high'] - df['low']) / df['close']
    df['upper_shadow'] = (df['high'] - df[['open', 'close']].max(axis=1)) / df['close']
    df['lower_shadow'] = (df[['open', 'close']].min(axis=1) - df['low']) / df['close']
    df['body'] = abs(df['close'] - df['open']) / df['close']
    df = df.fillna(0)
    df = df.replace([np.inf, -np.inf], 0)
    return df


TCN_FEATURE_COLS = [
    'return', 'ma5', 'ma10', 'ma20', 'ma40', 'ma_ratio', 'mom_5', 'mom_20', 'ma20_dev',
    'vol5', 'vol20', 'vol_ratio',
    'rsi', 'macd', 'macd_signal', 'macd_hist',
    'volume_ratio', 'volume_ma20', 'volume_trend',
    'high_low', 'upper_shadow', 'lower_shadow', 'body'
]



# ============================================================
# 流动性特征
# ============================================================

def get_shibor_data():
    """获取 Shibor 1周利率"""
    try:
        df = dh.get_shibor_all() if dh else None
        if df is None or len(df) == 0:
            raise RuntimeError('DataHub Shibor 为空')
        df = df.rename(columns={'日期': 'date', '1W-定价': 'shibor_1w'})
        df['date'] = pd.to_datetime(df['date'])
        df['shibor_1w'] = df['shibor_1w'].astype(float)
        df = df[['date', 'shibor_1w']].sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"获取 Shibor 数据失败: {e}")
        return None


def get_hibor_data():
    """获取 Hibor 1周利率"""
    try:
        _r = dh.get_hibor() if dh else None
        df = _r.get('history') if isinstance(_r, dict) else None
        if df is None or len(df) == 0:
            raise RuntimeError('DataHub HIBOR 为空')
        df = df.rename(columns={'trade_date': 'date', 'report_date': 'date', '1W': 'hibor_1w', '利率': 'hibor_1w'})
        if 'hibor_1w' not in df.columns:
            _c = [c for c in df.columns if 'W' in str(c) or 'hibor' in str(c).lower()]
            if not _c:
                raise RuntimeError(f'HIBOR 列未识别: {list(df.columns)[:8]}')
            df = df.rename(columns={_c[0]: 'hibor_1w'})
        if 'date' not in df.columns:
            df = df.rename(columns={df.columns[0]: 'date'})
        df['date'] = pd.to_datetime(df['date'])
        df['hibor_1w'] = df['hibor_1w'].astype(float)
        df = df[['date', 'hibor_1w']].sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"获取 Hibor 数据失败: {e}")
        return None


def add_liquidity_features_a(df, shibor_df):
    """为A股指数添加流动性特征"""
    df = df.copy()
    if 'amount' not in df.columns:
        df['amount'] = df['close'] * df['volume']
    df['amount_ratio'] = df['amount'] / df['amount'].rolling(20).mean().replace(0, np.nan)
    df['amount_trend'] = df['amount'].rolling(5).mean() / df['amount'].rolling(20).mean().replace(0, np.nan)
    df['daily_ret'] = df['close'].pct_change()
    df['amihud_illiq'] = df['daily_ret'].abs() / (df['amount'] / 1e8).replace(0, np.nan)
    if shibor_df is not None:
        df['_date_key'] = df['date'].astype('datetime64[ns]')
        shibor_tmp = shibor_df.copy()
        shibor_tmp['_date_key'] = shibor_tmp['date'].astype('datetime64[ns]')
        df = pd.merge_asof(df.sort_values('_date_key'), shibor_tmp.sort_values('_date_key'),
                            on='_date_key', direction='backward')
        df = df.drop(columns=['_date_key'], errors='ignore')
        df['shibor_1w_chg'] = df['shibor_1w'] - df['shibor_1w'].shift(5)
    else:
        df['shibor_1w'] = 0.0
        df['shibor_1w_chg'] = 0.0
    df = df.drop(columns=['daily_ret'], errors='ignore')
    df = df.fillna(0)
    df = df.replace([np.inf, -np.inf], 0)
    return df


def add_liquidity_features_hk(df, hibor_df):
    """为港股指数添加流动性特征"""
    df = df.copy()
    df['daily_ret'] = df['close'].pct_change()
    df['amihud_illiq_hk'] = df['daily_ret'].abs() / (df['volume'] / 1e6).replace(0, np.nan)
    if hibor_df is not None:
        df['_date_key'] = df['date'].astype('datetime64[ns]')
        hibor_tmp = hibor_df.copy()
        hibor_tmp['_date_key'] = hibor_tmp['date'].astype('datetime64[ns]')
        df = pd.merge_asof(df.sort_values('_date_key'), hibor_tmp.sort_values('_date_key'),
                            on='_date_key', direction='backward')
        df = df.drop(columns=['_date_key'], errors='ignore')
        df['hibor_1w_chg'] = df['hibor_1w'] - df['hibor_1w'].shift(5)
    else:
        df['hibor_1w'] = 0.0
        df['hibor_1w_chg'] = 0.0
    df = df.drop(columns=['daily_ret'], errors='ignore')
    df = df.fillna(0)
    df = df.replace([np.inf, -np.inf], 0)
    return df


LIQ_COLS_A = ['amount_ratio', 'amount_trend', 'amihud_illiq', 'shibor_1w', 'shibor_1w_chg']
LIQ_COLS_HK = ['amihud_illiq_hk', 'hibor_1w', 'hibor_1w_chg']

# 增强版特征列（基础23维 + 流动性）
TCN_FEATURE_COLS_ENHANCED_A = TCN_FEATURE_COLS + LIQ_COLS_A
TCN_FEATURE_COLS_ENHANCED_HK = TCN_FEATURE_COLS + LIQ_COLS_HK


class TCNDataset(Dataset):
    def __init__(self, X, y):
        self.X = torch.FloatTensor(X)
        self.y = torch.FloatTensor(y)
    def __len__(self):
        return len(self.X)
    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


def create_tcn_sequences(df, feature_cols, seq_len=40):
    """创建 TCN 序列数据"""
    X_list, y_list = [], []
    values = df[feature_cols].values
    for i in range(seq_len, len(df) - 1):
        seq = values[i - seq_len:i].T
        X_list.append(seq)
        ret = (df['close'].iloc[i + 1] - df['close'].iloc[i]) / df['close'].iloc[i]
        y_list.append(1 if ret > 0 else 0)
    return np.array(X_list), np.array(y_list)


def train_tcn_model(X_train, y_train, epochs=30, batch_size=32, lr=0.001, input_dim=23):
    """训练 TCN 模型"""
    device = torch.device('cpu')
    dataset = TCNDataset(X_train, y_train)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    model = TCNModel(input_dim=input_dim).to(device)
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    model.train()
    for epoch in range(epochs):
        total_loss = 0
        for X_batch, y_batch in loader:
            X_batch = X_batch.to(device)
            y_batch = y_batch.to(device).unsqueeze(1)
            optimizer.zero_grad()
            pred = model(X_batch)
            loss = criterion(pred, y_batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += loss.item()
        scheduler.step(total_loss / len(loader))
    return model


def predict_tcn_signal(model, X_pred):
    """TCN 预测"""
    model.eval()
    with torch.no_grad():
        X_tensor = torch.FloatTensor(X_pred)
        if X_tensor.dim() == 2:
            X_tensor = X_tensor.unsqueeze(0)
        proba = model(X_tensor).item()
    return proba


def tcn_generate_signal(proba):
    """TCN 信号规则: proba>=0.55→做多, <=0.45→做空, 中间→空仓"""
    if proba >= 0.55:
        return 1, "🟢 做多"
    elif proba <= 0.45:
        return -1, "🔴 做空"
    else:
        return 0, "⚪ 空仓"


# ============================================================
# LSTM+XGBoost 组合模型
# ============================================================

class LSTMDataset(Dataset):
    def __init__(self, X, y):
        self.X = torch.FloatTensor(X)
        self.y = torch.FloatTensor(y)
    def __len__(self):
        return len(self.X)
    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


class LSTMModel(nn.Module):
    """LSTM 模型：输出预测概率 + 隐藏状态嵌入"""
    def __init__(self, input_dim=23, hidden_dim=32, num_layers=2, dropout=0.2):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers,
                            batch_first=True, dropout=dropout if num_layers > 1 else 0)
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, 16), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(16, 1), nn.Sigmoid()
        )

    def forward(self, x):
        # x: (batch, seq_len, input_dim)
        lstm_out, (h_n, c_n) = self.lstm(x)
        # 使用最后一个时间步的输出
        last_out = lstm_out[:, -1, :]  # (batch, hidden_dim)
        proba = self.fc(last_out)       # (batch, 1)
        return proba, last_out           # 返回预测概率和隐藏状态嵌入


def create_lstm_sequences(df, feature_cols, seq_len=40):
    """创建 LSTM 序列数据 (batch, seq_len, features)"""
    X_list, y_list = [], []
    values = df[feature_cols].values
    for i in range(seq_len, len(df) - 1):
        seq = values[i - seq_len:i]  # (seq_len, features)
        X_list.append(seq)
        ret = (df['close'].iloc[i + 1] - df['close'].iloc[i]) / df['close'].iloc[i]
        y_list.append(1 if ret > 0 else 0)
    return np.array(X_list), np.array(y_list)


def train_lstm_model(X_train, y_train, epochs=40, batch_size=32, lr=0.001, input_dim=23):
    """训练 LSTM 模型"""
    device = torch.device('cpu')
    dataset = LSTMDataset(X_train, y_train)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    model = LSTMModel(input_dim=input_dim, hidden_dim=32, num_layers=2, dropout=0.2).to(device)
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=8, factor=0.5)

    best_loss = float('inf')
    patience_counter = 0
    best_state = None

    model.train()
    for epoch in range(epochs):
        total_loss = 0
        for X_batch, y_batch in loader:
            X_batch = X_batch.to(device)
            y_batch = y_batch.to(device).unsqueeze(1)
            optimizer.zero_grad()
            pred, _ = model(X_batch)
            loss = criterion(pred, y_batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += loss.item()
        avg_loss = total_loss / len(loader)
        scheduler.step(avg_loss)

        # Early stopping
        if avg_loss < best_loss:
            best_loss = avg_loss
            patience_counter = 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            patience_counter += 1
            if patience_counter >= 8:
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model


def predict_lstm_with_embedding(model, X_pred):
    """LSTM 预测，返回 (概率, 隐藏嵌入)"""
    model.eval()
    with torch.no_grad():
        X_tensor = torch.FloatTensor(X_pred)
        if X_tensor.dim() == 2:
            X_tensor = X_tensor.unsqueeze(0)  # (1, seq_len, features)
        proba, last_out = model(X_tensor)
        proba = proba.item()
        # last_out shape: (1, hidden_dim) → squeeze → (hidden_dim,)
        embedding = last_out.squeeze(0).cpu().numpy()
    return proba, embedding


def build_lstm_xgb_meta_features(lstm_proba, embedding, df_latest_row, feature_cols, n_features):
    """构建 LSTM+XGBoost 元学习器的特征"""
    # LSTM 输出: 概率(1) + 嵌入(32) = 33
    lstm_features = [lstm_proba] + embedding.tolist()
    # 最新一天的原始特征
    latest_features = df_latest_row[:n_features].tolist()
    return lstm_features + latest_features


def train_lstm_xgboost(df, feature_cols, seq_len=40):
    """训练 LSTM+XGBoost 组合模型"""
    # Step 1: 准备 LSTM 训练数据
    X_lstm, y_lstm = create_lstm_sequences(df, feature_cols, seq_len)
    if len(X_lstm) < 50:
        return None, None

    # Step 2: 训练 LSTM
    lstm_model = train_lstm_model(X_lstm, y_lstm, epochs=40, input_dim=len(feature_cols))

    # Step 3: 用 LSTM 生成元特征，训练 XGBoost 修正
    lstm_model.eval()
    meta_X_list, meta_y_list = [], []

    with torch.no_grad():
        batch_size = 64
        for i in range(0, len(X_lstm), batch_size):
            batch_X = torch.FloatTensor(X_lstm[i:i+batch_size])
            batch_proba, batch_h = lstm_model(batch_X)
            batch_proba = batch_proba.numpy().flatten()
            # batch_h shape: (batch, hidden_dim) — 模型已返回 last_out
            batch_h = batch_h.numpy()

            for j in range(len(batch_proba)):
                idx = i + j
                sample_idx = seq_len + idx  # 在 df 中的位置
                if sample_idx < len(df):
                    lstm_feat = [batch_proba[j]] + batch_h[j].tolist()
                    latest_feat = df[feature_cols].iloc[sample_idx].tolist()
                    meta_X_list.append(lstm_feat + latest_feat)
                    meta_y_list.append(y_lstm[idx])

    if len(meta_X_list) < 30:
        return lstm_model, None

    meta_X = np.array(meta_X_list)
    meta_y = np.array(meta_y_list)

    # 训练 XGBoost 元学习器
    meta_model = xgb.XGBClassifier(
        n_estimators=100, learning_rate=0.05, max_depth=3,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, use_label_encoder=False, eval_metric='logloss'
    )
    meta_model.fit(meta_X, meta_y)

    return lstm_model, meta_model


def predict_lstm_xgboost(lstm_model, meta_model, df, feature_cols, seq_len=40):
    """用 LSTM+XGBoost 预测"""
    seq = df[feature_cols].iloc[-seq_len:].values  # (seq_len, features)
    seq_batch = seq.reshape(1, seq_len, len(feature_cols))

    # LSTM 预测
    lstm_proba, embedding = predict_lstm_with_embedding(lstm_model, seq_batch)

    if meta_model is None:
        return lstm_proba, lstm_proba

    # 构建元特征
    lstm_feat = [lstm_proba] + embedding.tolist()
    latest_feat = df[feature_cols].iloc[-1].tolist()
    meta_features = lstm_feat + latest_feat
    meta_X = np.array(meta_features).reshape(1, -1)

    # XGBoost 修正预测
    xgb_proba = meta_model.predict_proba(meta_X)[0][1]
    return xgb_proba, lstm_proba


def lstm_xgb_generate_signal(proba):
    """LSTM+XGBoost 信号生成"""
    if proba >= 0.55:
        signal = 1
        signal_str = "🟢 看多"
    elif proba <= 0.45:
        signal = -1
        signal_str = "🔴 看空"
    else:
        signal = 0
        signal_str = "⚪ 中性"
    return signal, signal_str


# ============================================================
# TxDIF 策略（MACD DIF 方向策略）
# ============================================================

def compute_txdif(df, is_futures=False):
    """
    TxDIF 策略：基于 MACD DIF 线的方向判断多空
    股票和期货的逻辑相反：
    - 股票：DIF 上升 → 看多，DIF 下降 → 看空
    - 期货（国债）：DIF 上升 → 看空，DIF 下降 → 看多（反向逻辑）
    """
    df = df.copy()
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['dif'] = ema12 - ema26
    df['dea'] = df['dif'].ewm(span=9, adjust=False).mean()
    df['dif_change'] = df['dif'].diff()
    
    latest_dif = df['dif'].iloc[-1]
    prev_dif = df['dif'].iloc[-2]
    dif_change = latest_dif - prev_dif
    
    latest_close = df['close'].iloc[-1]
    dif_change_norm = dif_change / latest_close * 1000
    
    flat_threshold = 0.01
    
    if abs(dif_change_norm) < flat_threshold:
        signal = 0
        signal_str = "⚪ 中性"
    elif dif_change_norm > 0:
        # DIF 上升
        if is_futures:
            signal = -1
            signal_str = "🔴 看空"
        else:
            signal = 1
            signal_str = "🟢 看多"
    else:
        # DIF 下降
        if is_futures:
            signal = 1
            signal_str = "🟢 看多"
        else:
            signal = -1
            signal_str = "🔴 看空"
    
    return signal, signal_str, latest_dif, dif_change_norm


# ============================================================
# XGBoost 相关函数
# ============================================================

def get_last_trading_day(current_date):
    last_day = current_date - timedelta(days=1)
    if last_day.weekday() == 5:
        last_day -= timedelta(days=1)
    elif last_day.weekday() == 6:
        last_day -= timedelta(days=2)
    return last_day


def get_next_trading_day(current_date):
    next_day = current_date + timedelta(days=1)
    if next_day.weekday() == 5:
        next_day += timedelta(days=2)
    elif next_day.weekday() == 6:
        next_day += timedelta(days=1)
    return next_day


def get_index_data(index_code, start_date, end_date):
    try:
        _pfx = str(index_code)[:2].lower()
        _num = str(index_code)[2:] if _pfx in ('sh', 'sz') else str(index_code)
        _ts = f"{_num}.{'SH' if _pfx == 'sh' else 'SZ'}"
        df = dh.get_index_daily(ts_code=_ts) if dh else None
        if df is None or len(df) == 0:
            raise RuntimeError(f'DataHub 指数日线为空: {_ts}')
        df = df.rename(columns={'trade_date': 'date', 'vol': 'volume'})
        df['date'] = pd.to_datetime(df['date'].astype(str))
        df = df[(df['date'] >= start_date) & (df['date'] <= end_date)]
        df = df.sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"获取 {index_code} 数据失败: {e}")
        return None


def get_futures_data(symbol, start_date, end_date):
    """获取国债期货主力连续数据"""
    try:
        df = dh.get_futures_main_daily(symbol=symbol, start_date=start_date, end_date=end_date) if dh else None
        if df is None or len(df) == 0:
            raise RuntimeError(f'DataHub 期货日线为空: {symbol}')
        df = df.rename(columns={
            '日期': 'date', '开盘价': 'open', '最高价': 'high',
            '最低价': 'low', '收盘价': 'close', '成交量': 'volume',
            '持仓量': 'hold', 'trade_date': 'date', 'vol': 'volume', 'oi': 'hold',
        })
        df['date'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close']:
            df[col] = df[col].astype(float)
        df['volume'] = df['volume'].astype(float)
        df = df.sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"获取期货 {symbol} 数据失败: {e}")
        return None


def get_hk_index_data(symbol, start_date=None, end_date=None):
    """获取港股指数日线数据（恒生指数等），返回与A股指数格式一致的 DataFrame"""
    try:
        df = dh.get_hk_index_daily(symbol, start_date=start_date, end_date=end_date) if dh else None
        if df is None or len(df) == 0:
            raise RuntimeError(f'DataHub 港股指数为空: {symbol}')
        # 列名: date/open/high/low/close/volume/amount — 与A股指数一致
        df['date'] = pd.to_datetime(df['date'])
        if start_date:
            df = df[df['date'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['date'] <= pd.to_datetime(end_date)]
        df = df.sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"获取港股指数 {symbol} 数据失败: {e}")
        return None


def create_xgb_features(df, feature_cols, seq_len=40):
    seq = df[feature_cols].iloc[-seq_len:]
    feat = np.concatenate([
        seq.mean().values, seq.std().values, seq.iloc[-1].values, [seq_len]
    ])
    return feat.reshape(1, -1)


def build_xgb_training(df, feature_cols, seq_len=40):
    X_list, y_list = [], []
    for i in range(seq_len, len(df) - 1):
        seq = df[feature_cols].iloc[i-seq_len:i]
        feat = np.concatenate([
            seq.mean().values, seq.std().values, seq.iloc[-1].values, [seq_len]
        ])
        X_list.append(feat)
        ret = (df['close'].iloc[i+1] - df['close'].iloc[i]) / df['close'].iloc[i]
        y_list.append(1 if ret > 0 else 0)
    return np.array(X_list), np.array(y_list)


def train_xgb_model(X_train, y_train):
    model = xgb.XGBClassifier(
        n_estimators=100, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, eval_metric='logloss',
        random_state=42, verbosity=0
    )
    model.fit(X_train, y_train)
    return model


def predict_xgb(model, X_pred):
    proba = model.predict_proba(X_pred)[0, 1]
    return 1 if proba > 0.5 else 0, proba


# ============================================================
# 多时间级别数据获取（缠论多级别分析用）
# ============================================================

def get_index_minute(symbol, scale=15, datalen=1023):
    """获取指数分钟K线（15/60），返回 list[dict] bars。

    取数顺序：DataHub(get_minute_bar) → pytdx 兜底。
    DataHub 列与 df_to_bars 一致（datetime/open/high/low/close/volume），
    2026-11-26 实测 000300.SH freq=60/15 各返回 200/100 行。
    symbol: 'sh000300' / 'sz399006'
    """
    prefix = str(symbol)[:2].lower()
    code = str(symbol)[2:]
    ts_code = f"{code}.{'SH' if prefix == 'sh' else 'SZ'}"

    # 1) DataHub（唯一取数口径）
    if dh is not None:
        try:
            df = dh.get_minute_bar(ts_code=ts_code, freq=str(scale), n=max(int(datalen), 200))
            if df is not None and len(df) >= 50:
                df = df.copy()
                if 'volume' not in df.columns and 'vol' in df.columns:
                    df = df.rename(columns={'vol': 'volume'})
                if 'datetime' not in df.columns and 'date' in df.columns:
                    df = df.rename(columns={'date': 'datetime'})
                return df_to_bars(df)
            print(f"    指数分钟(DataHub) 不足 {ts_code} scale={scale}，回退 pytdx")
        except Exception as e:  # noqa: BLE001
            print(f"    指数分钟(DataHub)失败 {ts_code} scale={scale}: {e}，回退 pytdx")

    # 2) pytdx 兜底（保留原实现）
    try:
        market = 1 if prefix == 'sh' else 0
        category = 1 if scale == 15 else 3
        servers = [
            ("218.75.126.9", 7709), ("119.97.185.10", 7709),
            ("47.103.48.45", 7709), ("120.79.60.107", 7709),
            ("113.105.73.86", 7709), ("119.147.212.81", 7709),
            ("112.74.214.43", 7727), ("221.231.141.60", 7709),
            ("101.227.73.20", 7709), ("101.227.77.254", 7709),
        ]
        data = None
        for attempt in range(3):
            for host, port in servers:
                try:
                    api = TdxHq_API()
                    api.connect(host, port)
                    data = api.get_index_bars(category, market, code, 0, min(datalen, 800))
                    api.disconnect()
                    if data:
                        break
                except Exception:  # noqa: BLE001
                    try:
                        api.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
                    continue
            if data:
                break
            if attempt < 2:
                import time
                time.sleep(1)
        if not data:
            print(f"    获取指数分钟数据失败 {symbol} scale={scale}: DataHub 与 pytdx 均无数据")
            return None
        return [{'datetime': it['datetime'], 'open': float(it['open']),
                 'high': float(it['high']), 'low': float(it['low']),
                 'close': float(it['close']), 'volume': float(it.get('vol', 0))}
                for it in data]
    except Exception as e:  # noqa: BLE001
        print(f"    获取指数分钟数据失败 {symbol} scale={scale}: {e}")
        return None


def get_futures_minute(symbol, period="15"):
    """获取期货分钟K线数据（15/60分钟），返回 list[dict] bars"""
    try:
        df = dh.get_minute_bar(ts_code=symbol, freq=str(period), n=1000, kind='fut') if dh else None
        if df is None or len(df) == 0:
            raise RuntimeError(f'DataHub 期货分钟为空: {symbol}')
        if df is None or len(df) < 50:
            return None
        return df_to_bars(df)
    except Exception as e:
        print(f"    获取期货分钟数据失败 {symbol} period={period}: {e}")
        return None


def df_to_bars(df):
    """DataFrame → list[dict] bars（缠论需要的格式）"""
    bars = []
    for _, row in df.iterrows():
        bars.append({
            'datetime': str(row.get('date', row.get('datetime', ''))),
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
            'volume': float(row.get('volume', 0)),
        })
    return bars


def run_chanlun_analysis(bars):
    """对一组 bars 运行缠论分析，返回 {signal, signal_text, signal_type, green_status, details}"""
    if not bars or len(bars) < 100:
        return {
            'signal': 'N/A',
            'signal_text': 'N/A (数据不足)',
            'signal_type': '',
            'green_status': 'N/A',
            'details': {},
            'direction': 0,
        }
    strategy = ChanlunStrategy()
    result = strategy.analyze(bars)

    signal = result['signal']
    signal_type = result.get('signal_type', '')
    green_status = result.get('details', {}).get('green_status', 'N/A')

    # 映射到方向（优先用 analyze 里的推断方向）
    if signal == '买入':
        direction = 1
        signal_text = f"🟢 {signal}"
    elif signal in ('卖出', '平仓'):
        direction = -1
        signal_text = f"🔴 {signal}"
    else:
        # 无缠论买卖点时，根据均线推断的趋势方向显示
        direction = result.get('direction', 0)
        if direction == -1:
            signal_text = f"⚪ 偏空"
        elif direction == 1:
            signal_text = f"⚪ 偏多"
        else:
            signal_text = f"⚪ 观望"

    if signal_type:
        signal_text += f"({signal_type})"

    return {
        'signal': signal,
        'signal_text': signal_text,
        'signal_type': signal_type,
        'green_status': green_status,
        'details': result.get('details', {}),
        'direction': direction,
    }


def aggregate_chanlun_signals(cl_15m, cl_60m, cl_daily):
    """汇总3个时间级别的缠论信号，返回综合判断"""
    results = [cl_15m, cl_60m, cl_daily]
    total = sum(r['direction'] for r in results if r['direction'] != 0)
    valid_count = sum(1 for r in results if r['signal'] != 'N/A')

    if valid_count == 0:
        return 'N/A', 0

    if total >= 2:
        consensus = '多级别共振看多 📈'
    elif total <= -2:
        consensus = '多级别共振看空 📉'
    elif total > 0:
        consensus = '偏多'
    elif total < 0:
        consensus = '偏空'
    else:
        consensus = '分歧'

    return consensus, total


# ============================================================
# 单品种五算法预测
# ============================================================

def run_single_symbol(index_name, index_code, category='index', shibor_df=None, hibor_df=None):
    """运行单个品种的五算法预测
    category: 'index'(A股指数) / 'futures'(国债期货) / 'hk_index'(港股指数)
    """
    is_futures = (category == 'futures')
    is_hk = (category == 'hk_index')
    print(f"  处理 {index_name} ({index_code})...")

    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=1500)).strftime('%Y%m%d')

    if is_futures:
        df = get_futures_data(index_code, start_date, end_date)
    elif is_hk:
        df = get_hk_index_data(index_code, start_date, end_date)
    else:
        df = get_index_data(index_code, start_date, end_date)

    if df is None or len(df) < 300:
        print(f"    数据不足: {len(df) if df is not None else 0}")
        return None

    latest_close = df['close'].iloc[-1]
    latest_date = df['date'].iloc[-1]

    # ---- 添加流动性特征 ----
    has_liquidity = False
    if category == 'index' and (shibor_df is not None):
        df = add_liquidity_features_a(df, shibor_df)
        has_liquidity = True
    elif category == 'hk_index' and (hibor_df is not None):
        df = add_liquidity_features_hk(df, hibor_df)
        has_liquidity = True

    # ---- XGBoost 预测 ----
    print(f"    XGBoost 预测中...")
    xgb_feature_cols = ['open', 'high', 'low', 'close', 'volume']
    if has_liquidity and category == 'index':
        xgb_feature_cols = xgb_feature_cols + LIQ_COLS_A
    elif has_liquidity and category == 'hk_index':
        xgb_feature_cols = xgb_feature_cols + LIQ_COLS_HK
    train_window = 200
    seq_len = 40

    if len(df) >= seq_len + train_window:
        train_data = df.iloc[-train_window-seq_len:-1]
        X_train_xgb, y_train_xgb = build_xgb_training(train_data, xgb_feature_cols, seq_len)
        if len(X_train_xgb) > 0:
            xgb_model = train_xgb_model(X_train_xgb, y_train_xgb)
            X_pred_xgb = create_xgb_features(df.iloc[:-1], xgb_feature_cols, seq_len)
            xgb_signal, xgb_proba = predict_xgb(xgb_model, X_pred_xgb)
        else:
            xgb_signal, xgb_proba = None, None
    else:
        xgb_signal, xgb_proba = None, None

    # ---- TCN 预测 ----
    print(f"    TCN 预测中...")
    df_tcn = compute_tcn_features(df)
    tcn_seq_len = 40

    # 选择特征列：有流动性数据时用增强版
    if has_liquidity and category == 'index':
        tcn_feat_cols = TCN_FEATURE_COLS_ENHANCED_A
    elif has_liquidity and category == 'hk_index':
        tcn_feat_cols = TCN_FEATURE_COLS_ENHANCED_HK
    else:
        tcn_feat_cols = TCN_FEATURE_COLS

    if len(df_tcn) >= tcn_seq_len + 50:
        X_train_tcn, y_train_tcn = create_tcn_sequences(df_tcn, tcn_feat_cols, tcn_seq_len)
        if len(X_train_tcn) > 50:
            tcn_model = train_tcn_model(X_train_tcn, y_train_tcn, epochs=30, input_dim=len(tcn_feat_cols))
            seq = df_tcn[tcn_feat_cols].iloc[-tcn_seq_len:].values.T.reshape(1, len(tcn_feat_cols), tcn_seq_len)
            tcn_proba = predict_tcn_signal(tcn_model, seq)
            tcn_signal, tcn_signal_str = tcn_generate_signal(tcn_proba)
        else:
            tcn_signal, tcn_proba, tcn_signal_str = None, None, "N/A"
    else:
        tcn_signal, tcn_proba, tcn_signal_str = None, None, "N/A"

    # ---- LSTM+XGBoost 预测 ----
    print(f"    LSTM+XGBoost 预测中...")
    lstm_seq_len = 40
    try:
        if len(df_tcn) >= lstm_seq_len + 50:
            lstm_model, meta_model = train_lstm_xgboost(df_tcn, tcn_feat_cols, lstm_seq_len)
            if lstm_model is not None:
                lstm_xgb_proba, lstm_raw_proba = predict_lstm_xgboost(
                    lstm_model, meta_model, df_tcn, tcn_feat_cols, lstm_seq_len)
                lstm_xgb_signal, lstm_xgb_signal_str = lstm_xgb_generate_signal(lstm_xgb_proba)
            else:
                lstm_xgb_proba, lstm_raw_proba = None, None
                lstm_xgb_signal, lstm_xgb_signal_str = None, "N/A"
        else:
            lstm_xgb_proba, lstm_raw_proba = None, None
            lstm_xgb_signal, lstm_xgb_signal_str = None, "N/A"
    except Exception as e:
        print(f"    LSTM+XGBoost 预测失败: {e}")
        lstm_xgb_proba, lstm_raw_proba = None, None
        lstm_xgb_signal, lstm_xgb_signal_str = None, "N/A"

    # ---- TxDIF 预测 ----
    print(f"    TxDIF 预测中...")
    if len(df) >= 30:
        txdif_signal, txdif_signal_str, txdif_dif, txdif_change = compute_txdif(df, is_futures=is_futures)
    else:
        txdif_signal, txdif_signal_str, txdif_dif, txdif_change = None, "N/A", None, None

    # ---- 缠论多级别分析 ----
    print(f"    缠论多级别分析中...")
    # 日线 bars
    daily_bars = df_to_bars(df)
    # 港股指数无分钟线数据，仅运行日线缠论
    if is_hk:
        bars_15m = None
        bars_60m = None
    else:
        # 15分钟 bars
        try:
            if is_futures:
                bars_15m = get_futures_minute(index_code, period="15")
            else:
                bars_15m = get_index_minute(index_code, scale=15)
        except Exception as e:
            print(f"    获取15分钟数据失败: {e}")
            bars_15m = None
        # 60分钟 bars
        try:
            if is_futures:
                bars_60m = get_futures_minute(index_code, period="60")
            else:
                bars_60m = get_index_minute(index_code, scale=60)
        except Exception as e:
            print(f"    获取60分钟数据失败: {e}")
            bars_60m = None

    cl_15m = run_chanlun_analysis(bars_15m) if bars_15m and len(bars_15m) >= 100 else {'signal': 'N/A', 'signal_text': 'N/A', 'signal_type': '', 'green_status': 'N/A', 'details': {}, 'direction': 0}
    cl_60m = run_chanlun_analysis(bars_60m) if bars_60m and len(bars_60m) >= 100 else {'signal': 'N/A', 'signal_text': 'N/A', 'signal_type': '', 'green_status': 'N/A', 'details': {}, 'direction': 0}
    cl_daily = run_chanlun_analysis(daily_bars) if daily_bars and len(daily_bars) >= 100 else {'signal': 'N/A', 'signal_text': 'N/A', 'signal_type': '', 'green_status': 'N/A', 'details': {}, 'direction': 0}
    cl_consensus, cl_score = aggregate_chanlun_signals(cl_15m, cl_60m, cl_daily)

    print(f"    XGBoost: signal={xgb_signal}, proba={xgb_proba}")
    print(f"    TCN: signal={tcn_signal}, proba={tcn_proba}")
    print(f"    LSTM+XGBoost: signal={lstm_xgb_signal}, proba={lstm_xgb_proba}")
    print(f"    TxDIF: signal={txdif_signal}, dif={txdif_dif}")
    print(f"    缠论: 15m={cl_15m['signal_text']}, 60m={cl_60m['signal_text']}, 日线={cl_daily['signal_text']}, 综合={cl_consensus}")

    return {
        'index_name': index_name,
        'index_code': index_code,
        'is_futures': is_futures,
        'is_hk': is_hk,
        'category': category,
        'latest_date': latest_date,
        'latest_close': latest_close,
        'xgb_signal': xgb_signal,
        'xgb_proba': xgb_proba,
        'tcn_signal': tcn_signal,
        'tcn_proba': tcn_proba,
        'tcn_signal_str': tcn_signal_str,
        'lstm_xgb_signal': lstm_xgb_signal,
        'lstm_xgb_proba': lstm_xgb_proba,
        'lstm_xgb_signal_str': lstm_xgb_signal_str,
        'lstm_raw_proba': lstm_raw_proba,
        'txdif_signal': txdif_signal,
        'txdif_signal_str': txdif_signal_str,
        'txdif_dif': txdif_dif,
        'chanlun_15m': cl_15m,
        'chanlun_60m': cl_60m,
        'chanlun_daily': cl_daily,
        'chanlun_consensus': cl_consensus,
        'chanlun_consensus_score': cl_score,
        'chanlun_green_daily': cl_daily.get('green_status', 'N/A'),
    }


# ============================================================
# 生成五算法日报
# ============================================================


def generate_report(results):
    """生成 Markdown 格式日报"""
    today = datetime.now()
    last_trading = get_last_trading_day(today)

    md = []
    md.append("此消息来自 coze.claude")
    md.append("")
    md.append("# 📊 涨跌预测日报（XGBoost + TCN + LSTM+XGBoost + TxDIF + 缠论 五算法）")
    md.append("")
    md.append(f"**📅 生成日期**: {today.strftime('%Y-%m-%d')} ({WEEKDAY_NAMES[today.weekday()]})")
    md.append(f"**📊 数据截止**: {last_trading.strftime('%Y-%m-%d')} ({WEEKDAY_NAMES[last_trading.weekday()]})")
    md.append(f"**🎯 预测目标**: {today.strftime('%Y-%m-%d')} ({WEEKDAY_NAMES[today.weekday()]}) 早盘涨跌")
    md.append("")

    # 分离股指、港股和期货
    index_results = [r for r in results if r and r.get('category') == 'index']
    hk_results = [r for r in results if r and r.get('category') == 'hk_index']
    futures_results = [r for r in results if r and r.get('category') == 'futures']

    # ---- 股指部分 ----
    md.append("## 📈 股指指数")
    md.append("")

    # 统计整体观点
    xgb_bullish = sum(1 for r in index_results if r and r['xgb_signal'] == 1)
    xgb_bearish = sum(1 for r in index_results if r and r['xgb_signal'] == 0)
    if xgb_bullish > xgb_bearish:
        xgb_view = "📈 偏多"
    elif xgb_bearish > xgb_bullish:
        xgb_view = "📉 偏空"
    else:
        xgb_view = "⚖️ 均衡"
    md.append(f"- **XGBoost**: {xgb_view} (多:{xgb_bullish} 空:{xgb_bearish})")

    tcn_bullish = sum(1 for r in index_results if r and r['tcn_signal'] == 1)
    tcn_bearish = sum(1 for r in index_results if r and r['tcn_signal'] == -1)
    tcn_neutral = sum(1 for r in index_results if r and r['tcn_signal'] == 0)
    if tcn_bullish > tcn_bearish and tcn_bullish > tcn_neutral:
        tcn_view = "📈 偏多"
    elif tcn_bearish > tcn_bullish and tcn_bearish > tcn_neutral:
        tcn_view = "📉 偏空"
    else:
        tcn_view = "⚖️ 均衡"
    md.append(f"- **TCN**: {tcn_view} (多:{tcn_bullish} 空:{tcn_bearish} 中:{tcn_neutral})")

    # LSTM+XGBoost 整体观点
    lstm_bullish = sum(1 for r in index_results if r and r.get('lstm_xgb_signal') == 1)
    lstm_bearish = sum(1 for r in index_results if r and r.get('lstm_xgb_signal') == -1)
    lstm_neutral = sum(1 for r in index_results if r and r.get('lstm_xgb_signal') == 0)
    if lstm_bullish > lstm_bearish and lstm_bullish > lstm_neutral:
        lstm_view = "📈 偏多"
    elif lstm_bearish > lstm_bullish and lstm_bearish > lstm_neutral:
        lstm_view = "📉 偏空"
    else:
        lstm_view = "⚖️ 均衡"
    md.append(f"- **LSTM+XGBoost**: {lstm_view} (多:{lstm_bullish} 空:{lstm_bearish} 中:{lstm_neutral})")

    txdif_bullish = sum(1 for r in index_results if r and r['txdif_signal'] == 1)
    txdif_bearish = sum(1 for r in index_results if r and r['txdif_signal'] == -1)
    if txdif_bullish > txdif_bearish:
        txdif_view = "📈 偏多"
    elif txdif_bearish > txdif_bullish:
        txdif_view = "📉 偏空"
    else:
        txdif_view = "⚖️ 均衡"
    md.append(f"- **TxDIF**: {txdif_view} (多:{txdif_bullish} 空:{txdif_bearish})")

    # 缠论多级别综合
    cl_bullish_idx = sum(1 for r in index_results if r and r.get('chanlun_consensus_score', 0) >= 2)
    cl_bearish_idx = sum(1 for r in index_results if r and r.get('chanlun_consensus_score', 0) <= -2)
    cl_neutral_idx = len(index_results) - cl_bullish_idx - cl_bearish_idx
    if cl_bullish_idx > cl_bearish_idx:
        cl_view = "📈 偏多"
    elif cl_bearish_idx > cl_bullish_idx:
        cl_view = "📉 偏空"
    else:
        cl_view = "⚖️ 均衡"
    md.append(f"- **缠论(多级别)**: {cl_view} (共振多:{cl_bullish_idx} 共振空:{cl_bearish_idx} 其他:{cl_neutral_idx})")
    md.append("")

    # 各指数详情
    for r in index_results:
        if r is None:
            continue

        signals = []
        if r['xgb_signal'] is not None:
            signals.append(1 if r['xgb_signal'] == 1 else -1)
        if r['tcn_signal'] is not None:
            signals.append(r['tcn_signal'])
        if r.get('lstm_xgb_signal') is not None:
            signals.append(r['lstm_xgb_signal'])
        if r['txdif_signal'] is not None:
            signals.append(r['txdif_signal'])
        cl_score = r.get('chanlun_consensus_score', 0)
        if cl_score != 0:
            signals.append(1 if cl_score >= 2 else (-1 if cl_score <= -2 else (1 if cl_score > 0 else -1)))

        agree_label = ""
        if len(signals) >= 3 and len(set(signals)) == 1:
            if len(signals) == 5:
                agree_label = " ⭐⭐⭐⭐⭐ 五策略一致！"
            elif len(signals) == 4:
                agree_label = " ⭐⭐⭐⭐ 四策略一致！"
            else:
                agree_label = " ⭐⭐⭐ 三策略一致！"

        md.append(f"### ▶ {r['index_name']} ({r['index_code']}){agree_label}")
        md.append(f"**收盘价**: {r['latest_close']:.2f}")
        md.append("")

        if r['xgb_signal'] is not None:
            xgb_str = "🟢 看多" if r['xgb_signal'] == 1 else "🔴 看空"
            md.append(f"- **XGBoost**: {xgb_str} (涨概率: {r['xgb_proba']:.1%})")
        else:
            md.append(f"- **XGBoost**: N/A")

        if r['tcn_signal'] is not None:
            md.append(f"- **TCN**: {r['tcn_signal_str']} (概率: {r['tcn_proba']:.3f})")
        else:
            md.append(f"- **TCN**: N/A")

        if r.get('lstm_xgb_signal') is not None:
            lstm_str = "🟢 看多" if r['lstm_xgb_signal'] == 1 else ("🔴 看空" if r['lstm_xgb_signal'] == -1 else "⚪ 中性")
            lstm_proba_str = f"{r['lstm_xgb_proba']:.1%}" if r['lstm_xgb_proba'] is not None else "N/A"
            md.append(f"- **LSTM+XGBoost**: {lstm_str} (涨概率: {lstm_proba_str})")
        else:
            md.append(f"- **LSTM+XGBoost**: N/A")

        if r['txdif_signal'] is not None:
            md.append(f"- **TxDIF**: {r['txdif_signal_str']} (DIF: {r['txdif_dif']:.4f})")
        else:
            md.append(f"- **TxDIF**: N/A")

        # 缠论多级别
        cl_15m = r.get('chanlun_15m', {})
        cl_60m = r.get('chanlun_60m', {})
        cl_daily = r.get('chanlun_daily', {})
        cl_15m_text = cl_15m.get('signal_text', 'N/A') if cl_15m else 'N/A'
        cl_60m_text = cl_60m.get('signal_text', 'N/A') if cl_60m else 'N/A'
        cl_daily_text = cl_daily.get('signal_text', 'N/A') if cl_daily else 'N/A'
        cl_consensus = r.get('chanlun_consensus', 'N/A')
        cl_green = r.get('chanlun_green_daily', 'N/A')
        md.append(f"- **缠论**: 15m={cl_15m_text} | 60m={cl_60m_text} | 日线={cl_daily_text} | 综合={cl_consensus} | 绿级={cl_green}")

        md.append("")

    # ---- 港股指数部分 ----
    if hk_results:
        md.append("## 🇭🇰 港股指数")
        md.append("")

        # 统计整体观点
        xgb_bullish_hk = sum(1 for r in hk_results if r and r['xgb_signal'] == 1)
        xgb_bearish_hk = sum(1 for r in hk_results if r and r['xgb_signal'] == 0)
        if xgb_bullish_hk > xgb_bearish_hk:
            xgb_view_hk = "📈 偏多"
        elif xgb_bearish_hk > xgb_bullish_hk:
            xgb_view_hk = "📉 偏空"
        else:
            xgb_view_hk = "⚖️ 均衡"
        md.append(f"- **XGBoost**: {xgb_view_hk} (多:{xgb_bullish_hk} 空:{xgb_bearish_hk})")

        tcn_bullish_hk = sum(1 for r in hk_results if r and r['tcn_signal'] == 1)
        tcn_bearish_hk = sum(1 for r in hk_results if r and r['tcn_signal'] == -1)
        tcn_neutral_hk = sum(1 for r in hk_results if r and r['tcn_signal'] == 0)
        if tcn_bullish_hk > tcn_bearish_hk and tcn_bullish_hk > tcn_neutral_hk:
            tcn_view_hk = "📈 偏多"
        elif tcn_bearish_hk > tcn_bullish_hk and tcn_bearish_hk > tcn_neutral_hk:
            tcn_view_hk = "📉 偏空"
        else:
            tcn_view_hk = "⚖️ 均衡"
        md.append(f"- **TCN**: {tcn_view_hk} (多:{tcn_bullish_hk} 空:{tcn_bearish_hk} 中:{tcn_neutral_hk})")

        lstm_bullish_hk = sum(1 for r in hk_results if r and r.get('lstm_xgb_signal') == 1)
        lstm_bearish_hk = sum(1 for r in hk_results if r and r.get('lstm_xgb_signal') == -1)
        lstm_neutral_hk = sum(1 for r in hk_results if r and r.get('lstm_xgb_signal') == 0)
        if lstm_bullish_hk > lstm_bearish_hk and lstm_bullish_hk > lstm_neutral_hk:
            lstm_view_hk = "📈 偏多"
        elif lstm_bearish_hk > lstm_bullish_hk and lstm_bearish_hk > lstm_neutral_hk:
            lstm_view_hk = "📉 偏空"
        else:
            lstm_view_hk = "⚖️ 均衡"
        md.append(f"- **LSTM+XGBoost**: {lstm_view_hk} (多:{lstm_bullish_hk} 空:{lstm_bearish_hk} 中:{lstm_neutral_hk})")

        txdif_bullish_hk = sum(1 for r in hk_results if r and r['txdif_signal'] == 1)
        txdif_bearish_hk = sum(1 for r in hk_results if r and r['txdif_signal'] == -1)
        if txdif_bullish_hk > txdif_bearish_hk:
            txdif_view_hk = "📈 偏多"
        elif txdif_bearish_hk > txdif_bullish_hk:
            txdif_view_hk = "📉 偏空"
        else:
            txdif_view_hk = "⚖️ 均衡"
        md.append(f"- **TxDIF**: {txdif_view_hk} (多:{txdif_bullish_hk} 空:{txdif_bearish_hk})")

        # 缠论（仅日线）
        cl_bullish_hk = sum(1 for r in hk_results if r and r.get('chanlun_daily', {}).get('direction', 0) == 1)
        cl_bearish_hk = sum(1 for r in hk_results if r and r.get('chanlun_daily', {}).get('direction', 0) == -1)
        cl_neutral_hk = len(hk_results) - cl_bullish_hk - cl_bearish_hk
        if cl_bullish_hk > cl_bearish_hk:
            cl_view_hk = "📈 偏多"
        elif cl_bearish_hk > cl_bullish_hk:
            cl_view_hk = "📉 偏空"
        else:
            cl_view_hk = "⚖️ 均衡"
        md.append(f"- **缠论(日线)**: {cl_view_hk} (多:{cl_bullish_hk} 空:{cl_bearish_hk} 中:{cl_neutral_hk})")
        md.append("")

        # 各港股指数详情
        for r in hk_results:
            if r is None:
                continue

            signals = []
            if r['xgb_signal'] is not None:
                signals.append(1 if r['xgb_signal'] == 1 else -1)
            if r['tcn_signal'] is not None:
                signals.append(r['tcn_signal'])
            if r.get('lstm_xgb_signal') is not None:
                signals.append(r['lstm_xgb_signal'])
            if r['txdif_signal'] is not None:
                signals.append(r['txdif_signal'])
            cl_daily_dir = r.get('chanlun_daily', {}).get('direction', 0)
            if cl_daily_dir != 0:
                signals.append(cl_daily_dir)

            agree_label = ""
            if len(signals) >= 3 and len(set(signals)) == 1:
                if len(signals) == 5:
                    agree_label = " ⭐⭐⭐⭐⭐ 五策略一致！"
                elif len(signals) == 4:
                    agree_label = " ⭐⭐⭐⭐ 四策略一致！"
                else:
                    agree_label = " ⭐⭐⭐ 三策略一致！"

            md.append(f"### ▶ {r['index_name']} ({r['index_code']}){agree_label}")
            md.append(f"**收盘价**: {r['latest_close']:.2f}")
            md.append("")

            if r['xgb_signal'] is not None:
                xgb_str = "🟢 看多" if r['xgb_signal'] == 1 else "🔴 看空"
                md.append(f"- **XGBoost**: {xgb_str} (涨概率: {r['xgb_proba']:.1%})")
            else:
                md.append(f"- **XGBoost**: N/A")

            if r['tcn_signal'] is not None:
                md.append(f"- **TCN**: {r['tcn_signal_str']} (概率: {r['tcn_proba']:.3f})")
            else:
                md.append(f"- **TCN**: N/A")

            if r.get('lstm_xgb_signal') is not None:
                lstm_str = "🟢 看多" if r['lstm_xgb_signal'] == 1 else ("🔴 看空" if r['lstm_xgb_signal'] == -1 else "⚪ 中性")
                lstm_proba_str = f"{r['lstm_xgb_proba']:.1%}" if r['lstm_xgb_proba'] is not None else "N/A"
                md.append(f"- **LSTM+XGBoost**: {lstm_str} (涨概率: {lstm_proba_str})")
            else:
                md.append(f"- **LSTM+XGBoost**: N/A")

            if r['txdif_signal'] is not None:
                md.append(f"- **TxDIF**: {r['txdif_signal_str']} (DIF: {r['txdif_dif']:.4f})")
            else:
                md.append(f"- **TxDIF**: N/A")

            # 缠论（仅日线）
            cl_daily = r.get('chanlun_daily', {})
            cl_daily_text = cl_daily.get('signal_text', 'N/A') if cl_daily else 'N/A'
            cl_green = r.get('chanlun_green_daily', 'N/A')
            md.append(f"- **缠论**: 日线={cl_daily_text} | 绿级={cl_green} (无分钟线数据)")

            md.append("")

    # ---- 国债期货部分 ----
    if futures_results:
        md.append("## 📊 国债期货")
        md.append("")

        for label in ["XGBoost", "TCN", "LSTM+XGBoost", "TxDIF"]:
            bullish = sum(1 for r in futures_results if r
                          and ((label == 'XGBoost' and r['xgb_signal'] == 1) or
                               (label == 'TCN' and r['tcn_signal'] == 1) or
                               (label == 'LSTM+XGBoost' and r.get('lstm_xgb_signal') == 1) or
                               (label == 'TxDIF' and r['txdif_signal'] == 1)))
            bearish = sum(1 for r in futures_results if r
                          and ((label == 'XGBoost' and r['xgb_signal'] == 0) or
                               (label == 'TCN' and r['tcn_signal'] == -1) or
                               (label == 'LSTM+XGBoost' and r.get('lstm_xgb_signal') == -1) or
                               (label == 'TxDIF' and r['txdif_signal'] == -1)))
            if label == 'XGBoost':
                view = "📈 偏多" if bullish > bearish else ("📉 偏空" if bearish > bullish else "⚖️ 均衡")
            elif label == 'TCN':
                neutral = sum(1 for r in futures_results if r and r['tcn_signal'] == 0)
                view = "📈 偏多" if bullish > bearish and bullish > neutral else ("📉 偏空" if bearish > bullish and bearish > neutral else "⚖️ 均衡")
            elif label == 'LSTM+XGBoost':
                neutral_f = sum(1 for r in futures_results if r and r.get('lstm_xgb_signal') == 0)
                view = "📈 偏多" if bullish > bearish and bullish > neutral_f else ("📉 偏空" if bearish > bullish and bearish > neutral_f else "⚖️ 均衡")
            else:
                view = "📈 偏多" if bullish > bearish else ("📉 偏空" if bearish > bullish else "⚖️ 均衡")
            md.append(f"- **{label}**: {view} (多:{bullish} 空:{bearish})")

        # 缠论多级别综合
        cl_bullish_fut = sum(1 for r in futures_results if r and r.get('chanlun_consensus_score', 0) >= 2)
        cl_bearish_fut = sum(1 for r in futures_results if r and r.get('chanlun_consensus_score', 0) <= -2)
        cl_neutral_fut = len(futures_results) - cl_bullish_fut - cl_bearish_fut
        cl_fut_view = "📈 偏多" if cl_bullish_fut > cl_bearish_fut else ("📉 偏空" if cl_bearish_fut > cl_bullish_fut else "⚖️ 均衡")
        md.append(f"- **缠论(多级别)**: {cl_fut_view} (共振多:{cl_bullish_fut} 共振空:{cl_bearish_fut} 其他:{cl_neutral_fut})")

        md.append("")

        for r in futures_results:
            if r is None:
                continue

            signals = []
            if r['xgb_signal'] is not None:
                signals.append(1 if r['xgb_signal'] == 1 else -1)
            if r['tcn_signal'] is not None:
                signals.append(r['tcn_signal'])
            if r.get('lstm_xgb_signal') is not None:
                signals.append(r['lstm_xgb_signal'])
            if r['txdif_signal'] is not None:
                signals.append(r['txdif_signal'])
            cl_score_f = r.get('chanlun_consensus_score', 0)
            if cl_score_f != 0:
                signals.append(1 if cl_score_f >= 2 else (-1 if cl_score_f <= -2 else (1 if cl_score_f > 0 else -1)))

            agree_label = ""
            if len(signals) >= 3 and len(set(signals)) == 1:
                if len(signals) == 5:
                    agree_label = " ⭐⭐⭐⭐⭐ 五策略一致！"
                elif len(signals) == 4:
                    agree_label = " ⭐⭐⭐⭐ 四策略一致！"
                else:
                    agree_label = " ⭐⭐⭐ 三策略一致！"

            md.append(f"### ▶ {r['index_name']} ({r['index_code']}){agree_label}")
            md.append(f"**收盘价**: {r['latest_close']:.3f}")
            md.append("")

            if r['xgb_signal'] is not None:
                xgb_str = "🟢 看多" if r['xgb_signal'] == 1 else "🔴 看空"
                md.append(f"- **XGBoost**: {xgb_str} (涨概率: {r['xgb_proba']:.1%})")
            else:
                md.append(f"- **XGBoost**: N/A")

            if r['tcn_signal'] is not None:
                md.append(f"- **TCN**: {r['tcn_signal_str']} (概率: {r['tcn_proba']:.3f})")
            else:
                md.append(f"- **TCN**: N/A")

            if r.get('lstm_xgb_signal') is not None:
                lstm_str_f = "🟢 看多" if r['lstm_xgb_signal'] == 1 else ("🔴 看空" if r['lstm_xgb_signal'] == -1 else "⚪ 中性")
                lstm_proba_f = f"{r['lstm_xgb_proba']:.1%}" if r['lstm_xgb_proba'] is not None else "N/A"
                md.append(f"- **LSTM+XGBoost**: {lstm_str_f} (涨概率: {lstm_proba_f})")
            else:
                md.append(f"- **LSTM+XGBoost**: N/A")

            if r['txdif_signal'] is not None:
                md.append(f"- **TxDIF**: {r['txdif_signal_str']} (DIF: {r['txdif_dif']:.4f})")
            else:
                md.append(f"- **TxDIF**: N/A")

            # 缠论多级别
            cl_15m_f = r.get('chanlun_15m', {})
            cl_60m_f = r.get('chanlun_60m', {})
            cl_daily_f = r.get('chanlun_daily', {})
            cl_15m_ft = cl_15m_f.get('signal_text', 'N/A') if cl_15m_f else 'N/A'
            cl_60m_ft = cl_60m_f.get('signal_text', 'N/A') if cl_60m_f else 'N/A'
            cl_daily_ft = cl_daily_f.get('signal_text', 'N/A') if cl_daily_f else 'N/A'
            cl_consensus_f = r.get('chanlun_consensus', 'N/A')
            cl_green_f = r.get('chanlun_green_daily', 'N/A')
            md.append(f"- **缠论**: 15m={cl_15m_ft} | 60m={cl_60m_ft} | 日线={cl_daily_ft} | 综合={cl_consensus_f} | 绿级={cl_green_f}")

            md.append("")

    # ---- 五策略汇总表 ----
    md.append("## 📋 五策略汇总")
    md.append("")
    md.append("| 品种 | XGBoost | TCN | LSTM+XGB | TxDIF | 缠论 | 共识 |")
    md.append("|------|---------|-----|----------|-------|------|------|")

    for r in results:
        if r is None:
            continue
        name = r['index_name']

        if r['xgb_signal'] is not None:
            xgb_s = "🟢多" if r['xgb_signal'] == 1 else "🔴空"
        else:
            xgb_s = "N/A"

        if r['tcn_signal'] is not None:
            if r['tcn_signal'] == 1:
                tcn_s = "🟢多"
            elif r['tcn_signal'] == -1:
                tcn_s = "🔴空"
            else:
                tcn_s = "⚪中"
        else:
            tcn_s = "N/A"

        if r.get('lstm_xgb_signal') is not None:
            if r['lstm_xgb_signal'] == 1:
                lstm_s = "🟢多"
            elif r['lstm_xgb_signal'] == -1:
                lstm_s = "🔴空"
            else:
                lstm_s = "⚪中"
        else:
            lstm_s = "N/A"

        if r['txdif_signal'] is not None:
            if r['txdif_signal'] == 1:
                txdif_s = "🟢多"
            elif r['txdif_signal'] == -1:
                txdif_s = "🔴空"
            else:
                txdif_s = "⚪中"
        else:
            txdif_s = "N/A"

        # 缠论列
        cl_consensus_r = r.get('chanlun_consensus', 'N/A')
        cl_score_r = r.get('chanlun_consensus_score', 0)
        if cl_consensus_r == 'N/A':
            cl_s = "N/A"
        elif cl_score_r >= 2:
            cl_s = "🟢共振多"
        elif cl_score_r <= -2:
            cl_s = "🔴共振空"
        elif cl_score_r == 1:
            cl_s = "🟢偏多"
        elif cl_score_r == -1:
            cl_s = "🔴偏空"
        else:
            cl_s = "⚪分歧"

        num_signals = []
        if r['xgb_signal'] is not None:
            num_signals.append(1 if r['xgb_signal'] == 1 else -1)
        if r['tcn_signal'] is not None:
            num_signals.append(r['tcn_signal'])
        if r.get('lstm_xgb_signal') is not None:
            num_signals.append(r['lstm_xgb_signal'])
        if r['txdif_signal'] is not None:
            num_signals.append(r['txdif_signal'])
        if cl_score_r != 0:
            num_signals.append(1 if cl_score_r >= 2 else (-1 if cl_score_r <= -2 else (1 if cl_score_r > 0 else -1)))

        if len(num_signals) == 5 and len(set(num_signals)) == 1:
            consensus = "⭐⭐⭐⭐⭐多" if num_signals[0] == 1 else "⭐⭐⭐⭐⭐空"
        elif len(num_signals) == 4 and len(set(num_signals)) == 1:
            consensus = "⭐⭐⭐⭐多" if num_signals[0] == 1 else "⭐⭐⭐⭐空"
        elif len(num_signals) == 3 and len(set(num_signals)) == 1:
            consensus = "⭐⭐⭐多" if num_signals[0] == 1 else "⭐⭐⭐空"
        elif len(num_signals) >= 2:
            bull_count = sum(1 for s in num_signals if s == 1)
            bear_count = sum(1 for s in num_signals if s == -1)
            if bull_count > bear_count:
                consensus = f"偏多({bull_count}/{len(num_signals)})"
            elif bear_count > bull_count:
                consensus = f"偏空({bear_count}/{len(num_signals)})"
            else:
                consensus = "分歧"
        else:
            consensus = "N/A"

        md.append(f"| {name} | {xgb_s} | {tcn_s} | {lstm_s} | {txdif_s} | {cl_s} | {consensus} |")

    md.append("")
    md.append("---")
    md.append("")
    md.append("**算法说明**:")
    md.append("- **XGBoost**: XGBClassifier (n_estimators=100, max_depth=5), 40日窗口 OHLCV 统计特征, 200日滚动训练, 涨概率>0.5→看多")
    md.append("- **TCN**: 4层残差块 (dilation=1,2,4,8, channels=32), 23维综合指标, Adam 30 epochs, proba≥0.55→做多, ≤0.45→做空")
    md.append("- **LSTM+XGBoost**: 2层LSTM(hidden=32)学习时序特征→32维隐状态嵌入+LSTM预测+23维特征=56维→XGBoost元学习器修正残差, proba≥0.55→看多, ≤0.45→看空")
    md.append("- **TxDIF**: 基于 DIF=EMA12-EMA26 方向, DIF上升→看多, DIF下降→看空")
    md.append("- **缠论(多级别)**: 15分钟+1小时+日线三级别独立分析，基于MACD(10,20,5)+均线(20/40/80)的缠论信号(买入/卖出/观望)，多级别共振汇总（港股指数仅日线级别，无分钟线数据）")
    md.append("- ⭐⭐⭐⭐⭐ = 五策略一致，⭐⭐⭐⭐ = 四策略一致，⭐⭐⭐ = 三策略一致，信号较强")

    return "\n".join(md)


def send_to_feishu(report_content):
    """通过飞书 Webhook 发送报告（富文本卡片格式）"""
    global FEISHU_WEBHOOK_URL
    env_webhook = os.environ.get('FEISHU_WEBHOOK_URL', '')
    if env_webhook:
        FEISHU_WEBHOOK_URL = env_webhook
    if not FEISHU_WEBHOOK_URL:
        print("未配置飞书 Webhook，跳过发送")
        return False

    # 提取标题（第一行 # 开头的内容）
    title = "涨跌预测日报"
    lines = report_content.strip().split('\n')
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('# '):
            title = stripped[2:].strip()
            break

    # 使用 interactive 卡片格式（支持 Markdown 渲染）
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title
                },
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": report_content
                }
            ]
        }
    }
    try:
        response = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get('code') == 0 or result.get('StatusCode') == 0:
                print("✓ 报告已发送到飞书（富文本卡片）")
                return True
            else:
                print(f"✗ 飞书发送失败: {result}")
                return False
        else:
            print(f"✗ 飞书请求失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ 飞书发送异常: {e}")
        return False


def main():
    print("=" * 60)
    print("涨跌预测系统 - 日报生成 (XGBoost + TCN + LSTM+XGBoost + TxDIF + 缠论 + 流动性)")
    print("=" * 60)

    results = []

    # 获取流动性数据（Shibor / Hibor）
    print("\n--- 获取流动性数据 ---")
    shibor_df = get_shibor_data()
    if shibor_df is not None:
        print(f"  Shibor 数据: {len(shibor_df)} 条")
    else:
        print("  Shibor 数据获取失败，将不使用流动性特征")
    hibor_df = get_hibor_data()
    if hibor_df is not None:
        print(f"  Hibor 数据: {len(hibor_df)} 条")
    else:
        print("  Hibor 数据获取失败，港股将不使用流动性特征")

    # 股指指数
    print("\n--- 股指指数 ---")
    for index_name, index_code in INDEX_MAP.items():
        result = run_single_symbol(index_name, index_code, category='index', shibor_df=shibor_df, hibor_df=hibor_df)
        results.append(result)

    # 国债期货
    print("\n--- 国债期货 ---")
    for futures_name, futures_code in FUTURES_MAP.items():
        result = run_single_symbol(futures_name, futures_code, category='futures', shibor_df=shibor_df, hibor_df=hibor_df)
        results.append(result)

    # 港股指数
    print("\n--- 港股指数 ---")
    for hk_name, hk_symbol in HK_INDEX_MAP.items():
        result = run_single_symbol(hk_name, hk_symbol, category='hk_index', shibor_df=shibor_df, hibor_df=hibor_df)
        results.append(result)

    report = generate_report(results)
    print("\n" + report)

    # 五策略结论落盘：任务侧判断 + 四档标签，供 task_tags_daily 转录进库
    # 判断口径只在 strategies/five_strategy_tags.py 一处，看板/通知都只读标签
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import five_strategy_tags as _fst
        payload = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'data_date': str(results[0]['latest_date'])[:10] if results and results[0] else '',
            'source': 'strategies/daily_report.py',
            'items': _fst.build_items(results),
        }
        _fst.write_json(payload)
        print("\n[five_strategy_tags] 已落盘 %d 个品种" % len(payload['items']))
    except Exception as _e:  # noqa: BLE001
        print("\n[five_strategy_tags] 落盘失败: %s: %s" % (type(_e).__name__, _e))

    send_to_feishu(report)
    return report


if __name__ == '__main__':
    main()
