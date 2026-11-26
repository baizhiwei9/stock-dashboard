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
    all_methods = list(METHODS.keys())
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

    # 21日横截面动量已按要求移除（2026-09用户确认）
    # STMOM 张量已按要求移除（2026-10 用户确认）

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
    ap.add_argument("--alert-mail", action="store_true", help="发送多空转换提醒邮件（触发时）")
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

    # 多空转换提醒：单只基金内部逐方法对比昨日观点，翻转方法数 >3 即触发（跨基金不累加）
    import convert_alert  # noqa: E402
    _mnames = list(METHODS.keys())
    cur_views = {e["code"]: {m: views[m].get(e["code"], "N/A") for m in _mnames}
                 for e in ETF_POOL}
    fund_flips, convert_alert_text = convert_alert.check_and_build(cur_views, asof, ETF_POOL)
    _hit = [f for f in fund_flips if f["count"] > convert_alert.THRESHOLD]
    if fund_flips:
        _fdesc = "、".join("%s%d个方法翻转" % (f["name"], f["count"]) for f in fund_flips)
        print("单基金方法级转向：%s" % _fdesc)
    else:
        print("单基金方法级转向：无")
    if _hit:
        print("达到提醒阈值（单只 >%d）：%s" % (
            convert_alert.THRESHOLD, "、".join(f["name"] for f in _hit)))

    # 近5日/当日涨跌（参考行情）
    ret5, ret1 = {}, {}
    for e in ETF_POOL:
        df = hist[e["code"]]
        c = df["close"]
        ret5[e["code"]] = float(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 6 else None
        ret1[e["code"]] = float(c.iloc[-1] / c.iloc[-2] - 1) if len(c) > 2 else None

    print("绘制汇总图 ...")
    all_methods = list(METHODS.keys())
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
        # 检测到风险：6方法汇总建议卖出的宽基指数置顶 + 标题标记
        _risk_etfs = [e for e in ETF_POOL if consensus_row[e["code"]][0] == "卖"]
        _opp_etfs = [e for e in ETF_POOL if consensus_row[e["code"]][0] == "买"]
        has_risk = len(_risk_etfs) > 0
        has_opp = len(_opp_etfs) > 0
        header = f"宽基指数技术分析多空平日报 · {asof}"
        if has_risk:
            header += " | 注意风险"
        if has_opp:
            header += " | 注意机会"
        header += " | 来自 DoMate"
        if has_risk:
            _risk_names = "、".join(e["name"] for e in _risk_etfs)
            text = f"**⚠️ 检测到风险：{_risk_names}（6方法汇总建议卖出）**\n\n" + text
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

    # 多空转换提醒邮件：生产运行（--send）或显式 --alert-mail 时，达到阈值才发送
    if (args.send or args.alert_mail) and convert_alert_text:
        print("发送多空转换提醒邮件 ...")
        import mail_push
        ok = mail_push.send_mail(
            "⚠️ 宽基多空转换提醒 %s" % asof,
            convert_alert_text, img_path=img_path, inline_img=True)
        print("多空转换提醒邮件", "成功" if ok else "失败")

    return 0


if __name__ == "__main__":
    sys.exit(main())