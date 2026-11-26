# -*- coding: utf-8 -*-
"""每日宽基指数技术分析多空平日报 — 主入口
流程: 拉数据 -> 6方法信号 -> 汇总 -> 出图 -> 文字 -> （可选）飞书投递
用法:
  python run_daily.py            # 仅生成图与文字
  python run_daily.py --send     # 生成并推送飞书（先图后文）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_fetch  # noqa: E402
from config import ETF_POOL, OUT_DIR  # noqa: E402
from consensus import consensus  # noqa: E402
from chart import draw_summary_chart  # noqa: E402
from report import build_text, build_one_liner  # noqa: E402

# 方法注册：单标的信号函数统一 (df) -> (view, detail)
METHODS = {}


def _register(mod_name):
    import importlib
    try:
        m = importlib.import_module(f"signals.{mod_name}")
        METHODS[m.METHOD_NAME] = m.signal
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [!] 加载 signals.{mod_name} 失败: {e}")
        return False


_ok = [
    _register("dxke"),
    _register("chan"),
    _register("madc"),
    _register("ac1vmd"),
    _register("dwt"),
    _register("smc"),
]
if _ok.count(True) < 4:
    print("警告：技术方法加载不全，仅:", list(METHODS.keys()))


def compute_all(data):
    """计算每只宽基的各方法观点，返回 (views, consensus_row, hist, smc_detail)"""
    all_methods = list(METHODS.keys()) + ["STMOM张量"]
    views = {mname: {} for mname in all_methods}
    hist = {}
    smc_detail = {}
    for e in ETF_POOL:
        code = e["code"]
        df = data[code]
        hist[code] = df
        for mname, fn in METHODS.items():
            try:
                v, _detail = fn(df)
                views[mname][code] = v
                if mname == "SMC日周线":
                    smc_detail[code] = _detail
            except Exception as exc:  # noqa: BLE001
                print(f"  [!] {mname}@{code} 失败: {type(exc).__name__} {exc}")
                views[mname][code] = "N/A"

    etf_codes = [e["code"] for e in ETF_POOL]

    # STMOM 张量为截面模型，需全池面板推理
    STMOM_NAME = "STMOM张量"
    try:
        from signals import stmom
        stom_codes = [c for c in stmom.STOM_POOL if c in data]
        if len(stom_codes) >= 10:
            p = data_fetch.aligned_panel(data, stom_codes)
            vmap = stmom.views_for_pool(p, etf_codes)
            for code, (v, _d) in vmap.items():
                views[STMOM_NAME][code] = v
        else:
            print(f"  [!] STMOM 面板缺标的: {set(stmom.STOM_POOL) - set(data)}")
            for e in ETF_POOL:
                views[STMOM_NAME][e["code"]] = "N/A"
    except Exception as exc:  # noqa: BLE001
        print(f"  [!] STMOM 失败: {type(exc).__name__} {exc}")
        for e in ETF_POOL:
            views[STMOM_NAME][e["code"]] = "N/A"

    # 21日横截面动量已按要求移除（2026-09用户确认）

    consensus_row = {}
    for e in ETF_POOL:
        per_view = {m: views[m].get(e["code"], "N/A") for m in all_methods}
        consensus_row[e["code"]] = consensus(per_view)
    return views, consensus_row, hist, smc_detail


def last_asof(data):
    dates = [df["date"].iloc[-1] for df in data.values() if len(df)]
    return max(dates).strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="推送飞书")
    ap.add_argument("--mail", action="store_true", help="发送邮件")
    ap.add_argument("--no-cache", action="store_true", help="强制重新拉取数据")
    args = ap.parse_args()

    print("拉取行情数据 ...")
    data = data_fetch.load_all(target="both", use_cache=not args.no_cache)
    if not data:
        print("数据为空，退出"); sys.exit(1)
    print(f"标的数: {len(data)}")

    print("计算技术信号 ...")
    views, consensus_row, hist, smc_detail = compute_all(data)
    asof = last_asof(data)

    # 近5日/当日涨跌（参考行情）
    ret5, ret1 = {}, {}
    for e in ETF_POOL:
        df = hist[e["code"]]
        c = df["close"]
        ret5[e["code"]] = float(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 6 else None
        ret1[e["code"]] = float(c.iloc[-1] / c.iloc[-2] - 1) if len(c) > 2 else None

    print("绘制汇总图 ...")
    all_methods = list(METHODS.keys()) + ["STMOM张量"]
    img_path = draw_summary_chart(
        all_methods, views, consensus_row, asof,
        ret5_row=ret5, out_name="tech_consensus_summary.png")
    print(f"图: {img_path}")

    text = build_text(views, consensus_row, asof, ret5_row=ret5, ret1_row=ret1,
                      smc_detail=smc_detail)
    one = build_one_liner(consensus_row, asof)
    print("=" * 60)
    print(one)
    print(text)

    # 落盘文字
    with open(os.path.join(OUT_DIR, "text_report.md"), "w") as f:
        f.write(text)

    if args.send:
        print("推送飞书 ...")
        import feishu_push
        header = f"宽基指数技术分析多空平日报 · {asof}"
        feishu_push.send_report(img_path, header, text)
        print("飞书推送完成。")

    if args.mail:
        print("发送邮件 ...")
        import mail_push
        brief = one.split("：", 1)[1] if "：" in one else one
        subject = f"宽基多空平日报 {asof}：{brief}"
        ok = mail_push.send_mail(subject, text, img_path=img_path, inline_img=True)
        print("邮件发送", "成功" if ok else "失败")
        if not ok:
            sys.exit(2)

    return 0


if __name__ == "__main__":
    sys.exit(main())