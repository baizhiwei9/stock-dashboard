# -*- coding: utf-8 -*-
"""周频跟踪入口：《库存周期 × 上下游利润摆动》周报（每周一 09:00）。

为什么是"周频"，能跟到什么程度
------------------------------
PMI（月末）、PPI（次月 9 号）、规上工企利润（27 号）都是月频，周里没有新数据。
所以周频真正能跟踪的只有两件事：

1. **代理动量**：期货主力价格（煤焦钢、有色、化工、建材）、交易所库存、LME、CCPI ——
   看上游成本与库存摆动方向，判断"上下游价差在走阔还是收窄"的边际；
2. **月度落地校准**：PMI/PPI/利润一发布，立刻重算四象限、三个宽度与利润份额，
   周与周之间的实质变化只发生在这些发布日。

周中读数只作方向预判，正式结论以月度工企效益数据为准（报告页脚已注明）。

步骤：compute_edb（EDB 取数 + 算指标）→ build_report（出 HTML，无日期戳）
→ 推飞书文本摘要。

用法
----
  python3 weekly_track.py              # 全流程：取数 → 出报 → 推飞书
  python3 weekly_track.py --no-feishu  # 只取数出报，不推送（供流水 fetch 阶段）
  python3 weekly_track.py --push-only  # 只推送（读现有 data/key_numbers.json，供 dispatch）
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(BASE, "..", "_lib"))
for _p in (BASE, LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "out")
KEYS = os.path.join(DATA, "key_numbers.json")
REPORT = os.path.join(OUT, "库存周期与利润转移周报.html")
PNG = os.path.join(OUT, "库存周期与利润转移图表.png")


def _pct(v, d=1, sign=False):
    if v is None:
        return "—"
    try:
        return ("{:+." + str(d) + "f}") if sign else ("{:." + str(d) + "f}")
    except Exception:  # noqa: BLE001
        return str(v)


def build_text(k: dict) -> str:
    """把关键数字拼成飞书文本摘要（只放会影响判断的项，不堆表格）。"""
    g = k.get
    def f(key, d=1, suf="", sign=False):
        v = g(key)
        if v is None:
            return "—"
        try:
            return (("{:+." if sign else "{:.") + str(d) + "f}").format(v) + suf
        except (TypeError, ValueError):
            return str(v)

    lines = [
        "【库存周期 × 上下游摆动 周报】%s" % g("last", "—"),
        "",
        "库存：%s，自 %s 连续 %s 个月" % (g("quad", "—"), g("quad_since", "—"), f("quad_months", 0)),
        "　名义 %s%% / 实际 %s%%（实际=名义剔除 PPI）｜库销比 %s｜周转 %s 天｜回收期 %s 天"
        % (f("inv_yoy"), f("real_inv"), f("ratio", 2), f("inv_days"), f("ar_days")),
        "",
        "宽度（ppi−cpi %spp / 采掘−加工 %spp / 出厂−购进 %spp）"
        % (f("w_ppi_cpi", 1, sign=True), f("w_mp", 1, sign=True), f("w_out_in", 2, sign=True)),
        "利润份额：上游 %s%%｜中游 %s%%｜下游 %s%%"
        % (f("share_up"), f("share_mid"), f("share_down")),
        "PMI：制造业 %s｜原料库存 %s｜产成品库存 %s"
        % (f("pmi"), f("pmi_raw"), f("pmi_fin")),
        "",
        "报告：%s" % os.path.relpath(REPORT, BASE),
        "（周中读数只作方向预判，正式结论以月度工企效益数据为准）",
        "",
        "署名：DuMate 库存周期周频跟踪",
    ]
    return "\n".join(x for x in lines if x != "　")


def _feishu_creds():
    """返回 (webhook, app_id, app_secret)。_lib 的 load_credentials 返回 (dict, 来源)。"""
    try:
        from feishu_creds import load_credentials
    except Exception as e:  # noqa: BLE001
        print("[飞书] 凭据模块不可用:", str(e)[:120])
        return "", "", ""
    c = load_credentials()
    if isinstance(c, (tuple, list)):
        c = c[0] or {}
    c = c or {}
    return (c.get("webhook_url") or c.get("feishu.webhook") or "",
            c.get("app_id") or "", c.get("app_secret") or "")


def _upload_image(png: str, app_id: str, app_secret: str) -> str:
    """把 PNG 换成飞书 image_key；拿不到返回空串（不抛错，退回纯文本）。"""
    import requests
    try:
        tok = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret}, timeout=20).json()
        token = (tok.get("tenant_access_token") or "")
        if not token:
            print("[飞书] 取 token 失败:", str(tok)[:160])
            return ""
        up = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": "Bearer " + token},
            files={"image": (os.path.basename(png), open(png, "rb"), "image/png")},
            data={"image_type": "message"}, timeout=40).json()
        key = (up.get("data") or {}).get("image_key") or ""
        print("[飞书] image_key:", key or str(up)[:160])
        return key
    except Exception as e:  # noqa: BLE001
        print("[飞书] 上传图片异常:", str(e)[:160])
        return ""


def push_feishu(text: str, png: str = "") -> bool:
    """推一条：有 app 凭据就「图 + 文本」，没有就纯文本（并提示图已生成在 out/）。

    飞书 image 消息必须用 image_key，而 image_key 只能靠 app_id/app_secret 上传；
    只配了 webhook 的机器人发不了图，所以这里降级而不是假装成功。
    """
    try:
        import requests
    except Exception as e:  # noqa: BLE001
        print("[飞书] requests 缺失，跳过推送:", e)
        return False
    hook, app_id, app_secret = _feishu_creds()
    if not hook:
        print("[飞书] 未配置 webhook，跳过推送")
        return False

    ok = True
    if png and os.path.exists(png) and app_id and app_secret:
        key = _upload_image(png, app_id, app_secret)
        if key:
            r = requests.post(hook, json={"msg_type": "image", "content": {"image_key": key}}, timeout=30).json()
            print("[飞书] 图片消息:", str(r)[:160])
            ok = ok and (r.get("StatusCode") == 0 or r.get("code") == 0)
        else:
            ok = False
    elif png and os.path.exists(png):
        print("[飞书] 缺 app_id/app_secret，发不了图（image_key 需应用身份上传），退回纯文本")

    if not (png and os.path.exists(png) and app_id and app_secret and ok):
        text = text + "\n\n（配图已生成：%s；飞书带图需配置 feishu.app_id / app_secret）" % os.path.relpath(png, BASE) if png else text
    r = requests.post(hook, json={"msg_type": "text", "content": {"text": text}}, timeout=30).json()
    good = r.get("StatusCode") == 0 or r.get("code") == 0
    print("[飞书] 文本消息:", "成功" if good else "失败", str(r)[:200])
    return good


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-feishu", action="store_true", help="只取数出报，不推送")
    ap.add_argument("--push-only", action="store_true", help="只推送现有 key_numbers.json")
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    if not args.push_only:
        import compute_edb  # 顶层执行：EDB 取数 + 算指标 + 落 data/*.csv、key_numbers.json
        import build_report  # 顶层执行：读 data/*.csv 出 out/库存周期与利润转移周报.html
        print("[出报]", REPORT, os.path.exists(REPORT))
        try:
            import render_weekly_chart
            render_weekly_chart.main()
        except Exception as e:  # noqa: BLE001
            print("[出图] 失败（不阻断推送）:", str(e)[:200])

    k = json.load(open(KEYS, encoding="utf-8")) if os.path.exists(KEYS) else {}
    if k:
        # 供框架 emit 抽指标：写一份带 as_of 的副本到 out/（extract 不拿运行日兜底）
        try:
            asof = str(k.get("last") or "")
            if len(asof) == 7:
                asof += "-01"
            json.dump({**k, "as_of": asof},
                      open(os.path.join(OUT, "key_numbers.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1, default=str)
        except Exception as e:  # noqa: BLE001
            print("[提示] out/key_numbers.json 写入失败:", str(e)[:120])
    if not k:
        print("[提示] 没有 key_numbers.json，跳过推送内容生成")
        return
    text = build_text(k)
    print("---- 摘要 ----")
    print(text)
    if args.no_feishu:
        print("[飞书] --no-feishu，跳过推送")
    else:
        push_feishu(text, PNG)
    print("[完成] macro_weekly")


if __name__ == "__main__":
    main()