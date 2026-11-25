# -*- coding: utf-8 -*-
"""京沪房产成交取数 —— TaskManager 的日更 Task

跑的是 `data_hub.realestate.fetch_all()`：北京抓住建委官方页，上海 `git pull`
开源归档 `ahbencat/shanghai_fangdi_data` 后解析 Markdown。两地都不需要浏览器。

产物
----
- ``out/fetch.json``   本次抓取的完整结果（含每个城市 added/updated/total）
- ``out/summary.md``   京沪周度/月度人读摘要，可直接给日报或看板取用

退出码
------
0 两地都成功；2 有一边成功（部分可用）；1 全失败。
框架按非零判失败，这里区分 1/2 是为了让「上海通了、北京挂了」不至于整任务报红。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

try:
    from data_hub import realestate
except ImportError as exc:  # 部署机没装 datahub 时给明确提示，而不是抛一坨栈
    print("[FATAL] 无法 import data_hub.realestate：%s" % exc, file=sys.stderr)
    print("        需先安装 datahub-multi-tier（pip install -e . 或设 PYTHONPATH）",
          file=sys.stderr)
    raise SystemExit(1)

CITY_CN = {"BJ": "北京（存量房/二手）", "SH": "上海（二手房）", "SH_NEW": "上海（一手房）"}


def _fmt(v, unit=""):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return "%s%s" % (format(round(v, 2), ","), unit)
    return "%s%s" % (format(v, ","), unit)


def build_summary(res):
    """把库里的周度/月度拼成人读摘要。数据不足时明说不足，不四舍五入成整数糊过去。"""
    lines = ["# 京沪房产成交取数 %s" % (res.get("fetched_at") or "").split(" ")[0], ""]

    for city in ("BJ", "SH", "SH_NEW"):
        rows = realestate.daily(city)
        lines.append("## %s" % CITY_CN[city])
        if not rows:
            lines.append("- 库内无数据")
            lines.append("")
            continue
        lines.append("- 库内 %d 天，%s ~ %s" % (len(rows), rows[0]["date"], rows[-1]["date"]))
        for w in realestate.weekly(city, weeks=2):
            lines.append("- 周 %s：%s 套 / %s%s（%d 天%s）"
                         % (w["label"], _fmt(w["deals"]), _fmt(w["area"], " ㎡"), "",
                            w["days"], "，未满周" if w["partial"] else ""))
        for m in realestate.monthly(city, months=2):
            lines.append("- 月 %s：%s 套 / %s（%d 天%s）"
                         % (m["month"], _fmt(m["deals"]), _fmt(m["area"], " ㎡"),
                            m["days"], "，未满月" if m["partial"] else ""))
        if city == "BJ" and len(rows) < 7:
            lines.append("- 注意：北京官方只给最新一日 + 当月累计，**没有历史日序列**，"
                         "周度要等每日跑满 7 天才成立；当前周度数字不可用。")
        lines.append("")

    month = (res.get("cities") or {}).get("BJ_month") or {}
    if month:
        lines.append("## 北京官方月度（存量房，住建委月统计）")
        for ym, v in sorted(month.items()):
            lines.append("- %s：住宅 %s 套 / %s ㎡；全口径 %s 套 / %s ㎡"
                         % (ym, _fmt(v.get("res")), _fmt(v.get("res_area")),
                            _fmt(v.get("total")), _fmt(v.get("total_area"))))
        lines.append("")

    lines.append("---")
    lines.append("口径：北京「住宅」不含商业办公，上海「二手房」含商业与车位，两地不可直接横比。")
    lines.append("来源：北京 = 北京市住建委·房地产数据主题服务；上海 = ahbencat/shanghai_fangdi_data 归档。")
    return "\n".join(lines)


def main():
    res = realestate.fetch_all()
    os.makedirs(OUT, exist_ok=True)

    with open(os.path.join(OUT, "fetch.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)

    summary = build_summary(res)
    with open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8") as f:
        f.write(summary + "\n")

    print(json.dumps(res, ensure_ascii=False, default=str))
    print()
    print(summary)

    ok = [k for k, v in (res.get("cities") or {}).items()
          if isinstance(v, dict) and v.get("ok")
          and k not in ("SH_source", "BJ_source", "BJ_month")]
    return 0 if len(ok) >= 2 else (2 if ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())