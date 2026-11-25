#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加息/降息路径判断模块（从统一数据层读取，不做联网）
============================================================
数据来源：treasury_data_output.json（由 treasury_data_fetch.py 统一抓取 rates / fomc）

方法（用户算法，时间加权修正版）：
  Y2 ≈ 未来 24 个月平均联邦基金利率的预期（时间加权均值，非点预测）

取数（统一数据层 rates 板块，原始源为 FRED + Treasury.gov 备用）：
  R  = 联邦基金目标区间中值 = (DFEDTARU + DFEDTARL) / 2
  Y2 = 两年期美债收益率 DGS2（最新可得收盘）

计算：
  方向   ：Y2 < R -> 降息预期；Y2 > R -> 加息预期；Y2 ≈ R -> 无方向
  总空间 ：|R - Y2|
  核心方程：|R - Y2| = Σ Δi × (24 - ti) / 24
           Δi=第 i 次变动幅度(25/50bp)，ti=生效于第几个月，权重=(24-ti)/24
  反解   ：默认每季度一次节奏（t=0,3,6,...），逐次累加 25bp 直到累计≈总空间；
          对比"首步 50bp + 后续 25bp"方案的残差，判断首步幅度
  简单除法（对照，禁止单独用于结论）：总空间 / 25

定性档位（用户实例反推，加/降息对称）：
  差 ≈ 46bp  -> 预期降温（不到 2 次）
  差 ≈ 60bp  -> 下次会议行动"大概率"
  差 ≈ 83bp  -> 首动 50bp"大概率场景"
  差更大     -> 多轮常规节奏路径

触发变量：非农、CPI、鲍威尔讲话（风险平衡措辞）
纪律：
  - 读数是"大概率场景"不是精确值
  - 落地后 Y2 不动 = 已 price in = 符合预期，不构成新信号
  - 非农会被修来修去，勿迷信单点

输出：rate_path_output.json
"""
import json
import sys
from datetime import date


def weighted_solve(total_bp, step=25.0, first_step=None, stride=3):
    """按每 stride 个月一次、每次 step bp 累加时间加权贡献，直到达到 total_bp。

    返回 (次数, 残差bp, 累计贡献bp, [各次生效月]).
    若 first_step 指定，则第一次用该幅度。
    """
    acc, n, residual, months_used = 0.0, 0, None, []
    t = 0
    while acc < total_bp:
        s = first_step if (n == 0 and first_step is not None) else step
        w = (24 - t) / 24.0
        if w <= 0:
            break  # 推到远端无权重，数学无解
        acc += s * w
        months_used.append(t)
        n += 1
        t += stride
    residual = acc - total_bp
    return n, residual, acc, months_used


def _month_hint(iso_date):
    """把 'YYYY-MM-DD' 转成 'YYYY年M月X' 的大约表述（初/中/末旬）。"""
    y, m, d = iso_date.split("-")
    y, m, d = int(y), int(m), int(d)
    if d <= 10:
        seg = "初"
    elif d <= 20:
        seg = "中旬"
    else:
        seg = "末"
    return "%d年%d月%s" % (y, m, seg)


def build_schedule(run_date, fomc_dates, total_bp, n_weighted, n25, n50, first_step, direction):
    """把加权反解出的次数映射到 FOMC 日历未来会议，返回带时间点的路径序列。

    口径：
      - 主场面数 = 胜出方案的次数 n_weighted；若胜出方案是首步 50bp，则首场 50bp。
      - 对照给出 n25（全 25bp）与 n50（首步 50bp）两种场数。
      - 时间点取 FOMC 日历上 run_date 之后的最近 N 场会议（约 6-7 周一档）；
        不足 N 场时按 6-7 周节奏外推兜底。
    """
    future = [d for d in fomc_dates if d > run_date]
    # 首场是否 50bp：胜出方案文本含"首步 50bp"
    first50 = "首步 50bp" in first_step and "均可" not in first_step

    def _extend(futs, need):
        """不足时按 6-7 周节奏外推（跨年继续）。"""
        out = list(futs)
        if not out:
            out = [run_date]  # 兜底起点
        import datetime
        cur = datetime.date.fromisoformat(out[-1])
        while len(out) < need:
            cur = cur + datetime.timedelta(days=49)  # 7 周
            # 对齐到周三附近会议（FOMC 决议日多为周二/周三）：取整数周即可
            out.append(cur.isoformat())
        return out

    def _steps_for(count, first_bp50):
        ps = []
        remaining = count
        for i in range(count):
            step = 50.0 if (i == 0 and first_bp50) else 25.0
            ps.append(step)
        return ps

    n_main = n_weighted if n_weighted > 0 else max(n25, 1)
    main_steps = _steps_for(n_main, first50)
    main_dates = _extend(future, n_main)

    items = []
    cum = 0.0
    for d, s in zip(main_dates, main_steps):
        cum += s
        items.append({
            "meeting": d,
            "month_hint": _month_hint(d),
            "step_bp": int(s),
            "cumulative_bp": int(cum),
        })

    total_simple = int(sum(main_steps))
    if first50:
        mode = "首步 50bp + 其余 25bp（共 %d 场）" % n_main
    else:
        mode = "每场 25bp（共 %d 场）" % n_main

    compare = {
        "n25": n25,
        "n50": n50 if n50 > 0 else None,
        "simple_divide": round(total_bp / 25.0, 2),
        "note": "n25=全25bp场数；n50=首步50bp+后续25bp场数；simple_divide=总空间/25 对照（仅参考）",
    }

    human = None
    if items:
        first_h = items[0]["month_hint"]
        last_h = items[-1]["month_hint"]
        human = ("%s路径：约 %d 场%s、合计约 %dbp（时间加权现值 %.1fbp)；"
                 "时点：%s → %s，%s") % (
            direction, n_main, "（首场50bp）" if first50 else "",
            total_simple, total_bp, first_h, last_h,
            "约6-7周一档、共%d场" % len(items) if len(items) > 1 else "单场")

    return {
        "direction": direction,
        "mode": mode,
        "n_meetings": n_main,
        "future_start": items[0]["meeting"] if items else None,
        "future_end": items[-1]["meeting"] if items else None,
        "items": items,
        "total_simple_bp": total_simple,
        "compare": compare,
        "human": human,
    }


def analyze(data):
    """data 来自统一数据层 treasury_data_output.json，只做逻辑判断，不再联网。"""
    r = data["rates"]
    r_date, r_up, r_lo, r_src = r["r_date"], r["r_up"], r["r_lo"], r["r_source"]
    y2_date, y2, y2_src = r["y2_date"], r["y2"], r["y2_source"]
    r_mid = r["r_mid"]

    gap_bp = round(abs(r_mid - y2) * 100.0, 2)
    direction = "降息" if y2 < r_mid else ("加息" if y2 > r_mid else "无方向性")
    total_bp = gap_bp

    # 简单除法（对照口径，仅参考）
    simple_n = round(total_bp / 25.0, 2)

    # 时间加权反解：常规 25bp 节奏
    months, months50 = [], []
    if total_bp > 0:
        n25, res25, acc25, months = weighted_solve(total_bp, step=25.0)
        # 首步 50bp 方案
        n50, res50, acc50, months50 = weighted_solve(total_bp, step=25.0, first_step=50.0)
        # 步长判定：残差更小方案胜出；若两方案残差接近(差<1.5bp)判"无法区分"
        if abs(res25 - res50) < 1.5:
            first_step = "常规 25bp 与首步 50bp 均可（残差接近，无法从价格区分）"
            n_weighted, residual = n25, res25
        elif res25 <= res50:
            first_step = "常规 25bp 节奏（残差 %.1fbp < 50bp 方案 %.1fbp）" % (res25, res50)
            n_weighted, residual = n25, res25
        else:
            first_step = "首步 50bp 更贴（残差 %.1fbp < 25bp 方案 %.1fbp）" % (res50, res25)
            n_weighted, residual = n50, res50
    else:
        n25 = n50 = n_weighted = 0
        residual = 0.0
        first_step = "—"

    # 定性档位（按方向对称）
    if total_bp == 0:
        band = "无方向性预期"
    elif total_bp < 50:
        band = "预期降温（不到 2 次，%s 路径弱化）" % direction
    elif total_bp < 70:
        band = "下次会议%s\"大概率\"（60bp 档位附近）" % direction
    elif total_bp < 95:
        band = "首动 50bp%s\"大概率场景\"（83bp 档位附近）" % direction
    else:
        band = "多轮常规节奏路径（%s方向已深度定价，超过 4 次 25bp 当量）" % direction

    # 路径位置：最近一次变动的生效月
    if months:
        nearest = months[0]
        position = "近端（首次变动权重高，%s 集中在未来 %d 个月）" % (
            direction, months[-1] + 3) if len(months) > 1 else "近端（仅一次）"
        if months[-1] > 18:
            position = "远端（需推到 t=%d 才凑满，远端贡献趋零，路径置信度低）" % months[-1]
    else:
        position = "—"

    # 触发事件（最近一次已知 FOMC 决策，作为读数上下文）
    trigger = ("%s FOMC 加息 25bp（%s 方向读数基于此后利率水平）"
               % (r_date, "加息" if direction == "加息" else "降息"))

    # 置信度
    if total_bp > 0 and residual <= 3.0 and n_weighted <= 8:
        conf = "高（残差 %.1fbp，节奏拟合好）" % residual
    elif total_bp > 0 and residual <= 8.0:
        conf = "中（残差 %.1fbp）" % residual
    else:
        conf = "低（残差 %.1fbp 或需远端推演）" % residual

    # FOMC 日历时间点序列：加息/降息对称（方向由读数决定，两方向同一套场次映射）
    schedule = None
    if total_bp > 0:
        schedule = build_schedule(
            data["run_date"], data["fomc"]["meeting_dates"], total_bp,
            n_weighted, n25, n50, first_step, direction,
        )

    return {
        "run_date": data["run_date"],
        "data_date": {"r": r_date, "y2": y2_date},
        "r_source": r_src,
        "y2_source": y2_src,
        "r_up": r_up, "r_lo": r_lo, "r_mid": r_mid, "y2": y2,
        "direction": direction, "total_bp": total_bp,
        "simple_n": simple_n,
        "weighted_n": n_weighted, "weighted_residual_bp": round(residual, 2),
        "n25": n25, "n50": n50,
        "first_step": first_step,
        "band": band,
        "position": position,
        "trigger": trigger,
        "confidence": conf,
        "schedule": schedule,
        "discipline": [
            "读数是'大概率场景'不是精确值（实例：读数 50bp 实际落地 25bp）",
            "落地后 Y2 不动 = 已 price in = 符合预期，不构成新信号",
            "非农会被'修来修去'，勿迷信单点数据",
        ],
    }


def render(d):
    lines = []
    lines.append("=" * 60)
    lines.append("美债监控 - 加息/降息路径   运行日: %s" % d["run_date"])
    lines.append("R(目标区间中值) = %s%% (上界 %s / 下界 %s, 数据日 %s, %s)" % (
        d["r_mid"], d["r_up"], d["r_lo"], d["data_date"]["r"], d["r_source"]))
    lines.append("Y2(两年美债)    = %s%% (数据日 %s, %s)" % (
        d["y2"], d["data_date"]["y2"], d.get("y2_source", "FRED DGS2")))
    lines.append("-" * 60)
    lines.append("方向: %s | 总空间: %.1fbp" % (d["direction"], d["total_bp"]))
    lines.append("简单除法(对照): %.2f 次 | 加权反解: %d 次 (残差 %.2fbp)"
                 % (d["simple_n"], d["weighted_n"], d["weighted_residual_bp"]))
    lines.append("步长判定: %s" % d["first_step"])
    lines.append("定性档位: %s" % d["band"])
    lines.append("路径位置: %s" % d["position"])
    lines.append("触发事件: %s" % d["trigger"])
    lines.append("置信度: %s" % d["confidence"])
    if d.get("schedule"):
        sc = d["schedule"]
        lines.append("-" * 60)
        lines.append("未来路径时间表（FOMC 日历映射,%s）:" % sc["mode"])
        for it in sc["items"]:
            lines.append("  %s (%s): %s %+dbp, 累计至 %s%%" % (
                it["month_hint"], it["meeting"], d["direction"], it["step_bp"],
                round((d["r_mid"] * 100 + it["cumulative_bp"] * (1 if d["direction"] == "加息" else -1)) / 100, 3)))
        lines.append("  合计(简单加总) %d bp | 时间加权现值 %.1f bp" % (
            sc["total_simple_bp"], d["total_bp"]))
        if sc["human"]:
            lines.append("  一句话: %s" % sc["human"])
    lines.append("纪律:")
    for s in d["discipline"]:
        lines.append("  - %s" % s)
    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    out_json = None
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        out_json = sys.argv[2] if len(sys.argv) > 2 else "rate_path_output.json"

    with open("treasury_data_output.json", encoding="utf-8") as fp:
        data = json.load(fp)
    d = analyze(data)
    print(render(d))
    if out_json:
        with open(out_json, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=2)
        print("\nJSON 已落盘: %s" % out_json)


if __name__ == "__main__":
    main()