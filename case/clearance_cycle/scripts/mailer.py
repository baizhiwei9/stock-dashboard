#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""邮件出口：优先走 dashboard 的 data_hub.notify（邮件通道），失败落盘草稿。

设计原则：
  · 不写死任何收件人 / 凭据，全部走 notify_init + data_hub.notify 的应用绑定；
  · 发不出去不许让任务失败（dispatch 阶段 optional），但必须留下可读草稿，
    并在 out/mail_log.json 记一条，避免「以为发了其实没发」。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")
LIB = os.path.abspath(os.path.join(ROOT, "..", "_lib"))


def _ensure_lib():
    if os.path.isdir(LIB) and LIB not in sys.path:
        sys.path.insert(0, LIB)


def _log(rec):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "mail_log.json")
    try:
        with open(p, encoding="utf-8") as f:
            hist = json.load(f)
        if not isinstance(hist, list):
            hist = []
    except (OSError, ValueError):
        hist = []
    hist.append(rec)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(hist[-50:], f, ensure_ascii=False, indent=2)
        f.write("\n")


def _draft(subject, body, tag=""):
    os.makedirs(OUT, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    p = os.path.join(OUT, f"mail_draft-{tag or 'mail'}-{stamp}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"# {subject}\n\n- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n\n{body}\n")
    return p


def send_mail(subject: str, body: str, tag: str = "", dry: bool = False) -> dict:
    """发一封邮件。返回 {ok, via, detail, draft}。永不抛异常。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec = {"ts": ts, "subject": subject, "ok": False, "via": "", "detail": "", "draft": ""}

    if dry:
        rec.update(ok=False, via="dry-run", detail="dry-run 未真发")
        rec["draft"] = _draft(subject, body, tag)
        _log(rec)
        print(f"[mail][dry] {subject}\n{body[:400]}")
        return rec

    err = ""
    try:
        _ensure_lib()
        import notify_init  # noqa: PLC0415

        notify_init.init()
        from data_hub import notify  # noqa: PLC0415

        # 优先显式指定邮件通道；不同版本签名不同，逐档降级而不是猜
        for kwargs in ({"channel": "email"}, {"channels": ["email"]}, {}):
            try:
                r = notify.send(subject, body, **kwargs)
            except TypeError:
                continue
            rec.update(ok=True, via="data_hub.notify" + (f" {kwargs}" if kwargs else " 默认通道"),
                       detail=str(r)[:180])
            _log(rec)
            print(f"[mail] 已发：{subject}（{rec['via']}）")
            return rec
        err = "notify.send 的所有签名都不可用"
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    rec.update(via="draft-only", detail=err[:200])
    rec["draft"] = _draft(subject, body, tag)
    _log(rec)
    print(f"[mail][WARN] 邮件未发出（{err[:120]}），已落草稿 {os.path.basename(rec['draft'])}")
    return rec


if __name__ == "__main__":
    args = sys.argv[1:]
    subj = args[0] if args else "出清周期监控·测试邮件"
    body = args[1] if len(args) > 1 else "这是一封测试邮件。"
    print(json.dumps(send_mail(subj, body, tag="test", dry="--dry-run" in args), ensure_ascii=False, indent=2))
