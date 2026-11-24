"""周报渲染：三因子预测 + Slope 判定（机会触发时标题星标放大）。"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .dataset import FOCUS
from .slope import LOT, label


def gen(date_str: str, latest: pd.Series, pred: pd.Series, next_week: str, hit: dict,
        fvals, slope_row: pd.Series, signal, note: str, near: str = "") -> str:
    lines = []
    if signal:
        lines.append(f"# ⭐⭐⭐ 利率三因子周度预测和 Slope交易：{label(signal)}机会触发 ⭐⭐⭐")
    else:
        lines.append("# 利率三因子周度预测和 Slope交易")
    lines += [
        "",
        f"- 生成时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 数据截止：**{date_str}**（DataHub bond 子包的中债官方国债曲线；资金面 DR007 取 DataHub EDB `LIQ.CN.DR007`，FDR007 代理口径、与 DR007 方向一致）",
        f"- 预测目标周：**{next_week}**",
        "",
        "## 一、三因子周度预测与模拟交易建议",
        "",
        "| 期限 | 当前收益率 | 下周预测 | 预测变化 | 交易建议 | 操作释义 | 历史方向命中 |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in FOCUS:
        cur, nxt = float(latest[t]), float(pred[t])
        d = (nxt - cur) * 100
        act, tail = ("买入", "做多/加仓") if d <= -2 else (("卖出", "做空/减仓") if d >= 2 else ("观望", "持仓不动"))
        lines.append(f"| {t} | {cur:.4f}% | {nxt:.4f}% | {d:+.1f}bp | {act} | {tail} | {hit.get(t, '-')} |")
    lines += [
        "",
        "交易规则：预测变化 ≤ -2.0bp（收益率下行，债券上涨）→ 买入；≥ +2.0bp → 卖出；|变化| < 2.0bp → 观望。"
        "历史方向命中率来自 2025-01~2026-10 walk-forward XGB 回测。",
        "",
        "| 因子 | 最新值 | 下周预测 |",
        "|---|---|---|",
    ]
    for k, name in enumerate(["PC1（利率水平）", "PC2（斜率/期限利差）", "PC3（曲率）"]):
        lines.append(f"| {name} | {fvals[0][k]:.6f} | {fvals[1][k]:.6f} |")
    lines += [
        "",
        "## 二、Slope（10Y-2Y 期限利差）交易机会",
        "",
        f"- 最新交易日：{date_str}",
        f"- SPREAD210（10Y-2Y 利差）：**{slope_row['SPREAD210']:.2f} bp**；250 日滚动分位 "
        f"**{slope_row['SPREAD_PCT']:.3f}**（<0.08 找做陡、>0.92 找做平）",
        f"- DR007 方向（MA5-MA20）：**{'下行(宽松)' if slope_row['DR007_DIR'] < 0 else ('上行(收紧)' if slope_row['DR007_DIR'] > 0 else '走平')}**"
        f"（{slope_row['DR007_DIR']:.0f}）",
        f"- 残差波动分位 RESID_PCT：**{slope_row['RESID_PCT']:.3f}**（需 <0.80 过滤高波动）",
    ]
    if signal:
        lines += ["", f"### ⭐⭐⭐ 交易机会：**{label(signal)}** ⭐⭐⭐", "", f"**{note}**", "",
                  f"**推荐组合：{LOT.get(signal, '')}**", "",
                  "> ⚠️ 实盘需日频 confirm=2 连续 2 日确认、T+1 开盘成交；风控参考 "
                  "s7(利差反向 7bp 止损)/h20(20 日超时平仓)/tp2=0.70(深度回归 70% 止盈)。", ""]
    else:
        lines += ["- 机会判定：**无 Slope 机会**", "", note,
                  "- 当前不满足 Slope 机会触发条件；仅当分位进入极值区（<0.08 或 >0.92）且资金面方向过滤通过、"
                  "残差未失控时，才进入下一周期待名单并在触发时推送。", ""]
    if near:
        lines += [f"- 接近机会区提示：{near}", ""]
    lines += [
        "## 三、数据口径",
        "",
        "- 取数：全部经 DataHub（`data_hub`，债券走 bond 子包，宏观与流动性走 EDB，业务侧只认 series_id）",
        "- 国债收益率：中债官方 `czbQueryYz` 全期限曲线（bond.treasury_curve），序列 `RATE.CN.1Y/2Y/5Y/7Y/10Y/30Y`，日频→周频（周五收盘）",
        "- 资金利率：`LIQ.CN.DR007`（FDR007 存款类机构回购定盘代理，与 CFETS DR007 常态偏离 5-10bp、方向一致）",
        "- Slope 指标：`RATE.SPREAD.CN.10Y2Y`=(10Y-2Y)*100 bp，250 交易日滚动分位；残差=日变化滚动 PCA(60日) 前三个期限残差标准差，250 日分位",
        "- 机会判定沿曲线交易回测推荐方案 X_tp20.70_h20：阈值分位 0.08/0.92、DR007 方向过滤、残差 0.80 过滤",
        "",
        "## 四、风险提示",
        "",
        "- 本报告为模拟口径：不含票息、交易成本与滑点，久期为近似值；仅供研究参考，不构成投资建议",
        "- Slope 周度扫描仅识别机会区间，实盘应叠加日频确认（连续 2 日保持条件）与 T+1 开盘执行，避免信号翻转",
        "- 回测样本 2025-01~2026-10（94 周）偏短，高命中率需警惕过拟合与区间依赖",
        "",
    ]
    return "\n".join(lines) + "\n"