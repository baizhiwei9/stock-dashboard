# -*- coding: utf-8 -*-
"""交易机会邮件通知。

设计原则：
- 主通道：钉钉企业邮箱（dws CLI，用户已授权）；回退：SMTP。
- 默认 dry-run，不连接 SMTP、不调用 dws；只有显式 --send 才发送。
- 收件人、发件人、SMTP 账号全部从环境变量读取，不把密码写进仓库。
- 只发送 LONG_SPREAD / SHORT_SPREAD 这类可执行机会；NO_SIGNAL、HOLD、WATCH_CONFLICT 不发。
- 没有机会时不发空邮件。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import smtplib
import ssl
import subprocess
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd
import yaml

from .config import ROOT, load_settings as load_app_settings
from .storage.sqlite_store import SQLiteStore

CONFIG = ROOT / "config" / "notifications.yaml"
DEFAULT_ACTIONS = ("LONG_SPREAD", "SHORT_SPREAD")


def _load_local_env() -> None:
    """读取仓库根 .env（已被 gitignore）作为环境变量，不覆盖已有值。

    与 structural-factor 的部署方式一致：本地 .env 放 EMAIL_FROM/EMAIL_TO，
    SMTP 授权码仍只由调用环境注入，不写入仓库。
    """
    path = ROOT / ".env"
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and not os.environ.get(key):
            os.environ[key] = value


@dataclass
class EmailSettings:
    enabled: bool = True
    dry_run: bool = True
    prefer_dws: bool = True
    subject_prefix: str = "期货策略交易机会"
    actions: List[str] = field(default_factory=lambda: list(DEFAULT_ACTIONS))
    dws_cli_env: str = "DWS_CLI"
    dws_from_env: str = "EMAIL_FROM"
    dws_to_env: str = "EMAIL_TO"
    host_env: str = "SMTP_HOST"
    port_env: str = "SMTP_PORT"
    user_env: str = "SMTP_USER"
    password_env: str = "SMTP_PASSWORD"
    from_env: str = "SMTP_FROM"
    to_env: str = "SMTP_TO"
    use_tls: bool = True
    use_ssl: bool = False


@dataclass
class Opportunity:
    spread_id: str
    name: str
    asof_date: str
    action: str
    direction: str
    pct: float
    z: float
    adf_p: float
    hurst: float
    reasons: List[str]

    @property
    def side(self) -> str:
        if self.action == "LONG_SPREAD" or self.direction == "LONG_SPREAD":
            return "做多价差"
        if self.action == "SHORT_SPREAD" or self.direction == "SHORT_SPREAD":
            return "做空价差"
        return self.action


def load_settings(path: Path | None = None) -> EmailSettings:
    p = Path(path) if path else CONFIG
    if not p.exists():
        return EmailSettings()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    e = raw.get("email", {}) or {}
    smtp = e.get("smtp", {}) or {}
    dws = e.get("dws", {}) or {}
    return EmailSettings(
        enabled=bool(e.get("enabled", True)),
        dry_run=bool(e.get("dry_run", True)),
        prefer_dws=bool(e.get("prefer_dws", True)),
        subject_prefix=str(e.get("subject_prefix", "期货策略交易机会")),
        actions=[str(x) for x in e.get("actions", DEFAULT_ACTIONS)],
        dws_cli_env=str(dws.get("cli_env", "DWS_CLI")),
        dws_from_env=str(dws.get("from_env", "EMAIL_FROM")),
        dws_to_env=str(dws.get("to_env", "EMAIL_TO")),
        host_env=str(smtp.get("host_env", "SMTP_HOST")),
        port_env=str(smtp.get("port_env", "SMTP_PORT")),
        user_env=str(smtp.get("user_env", "SMTP_USER")),
        password_env=str(smtp.get("password_env", "SMTP_PASSWORD")),
        from_env=str(smtp.get("from_env", "SMTP_FROM")),
        to_env=str(smtp.get("to_env", "SMTP_TO")),
        use_tls=bool(smtp.get("use_tls", True)),
        use_ssl=bool(smtp.get("use_ssl", False)),
    )


def _f(v: Any) -> float:
    try:
        x = float(v)
        return x if pd.notna(x) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _reasons(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.strip():
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
        return [v]
    return []


def collect_opportunities(store: SQLiteStore, asof: Optional[str] = None,
                          actions: Optional[Sequence[str]] = None) -> List[Opportunity]:
    df = store.latest_signals(asof=asof)
    if df is None or df.empty:
        return []
    wanted = set(actions or DEFAULT_ACTIONS)
    df = df[df["action"].isin(wanted)].copy()
    out: List[Opportunity] = []
    for _, r in df.iterrows():
        out.append(Opportunity(
            spread_id=str(r.get("spread_id", "")),
            name=str(r.get("name", r.get("spread_id", ""))),
            asof_date=str(r.get("asof_date", "")),
            action=str(r.get("action", "")),
            direction=str(r.get("direction", "")),
            pct=_f(r.get("pct")),
            z=_f(r.get("z")),
            adf_p=_f(r.get("adf_p")),
            hurst=_f(r.get("hurst")),
            reasons=_reasons(r.get("reasons_json")),
        ))
    return out


def _fmt_pct(v: float) -> str:
    return "—" if pd.isna(v) else f"{v:.1%}"


def _fmt_num(v: float, d: int = 2) -> str:
    return "—" if pd.isna(v) else f"{v:,.{d}f}"


def build_message(opps: Sequence[Opportunity], settings: EmailSettings) -> tuple[str, str, str]:
    if not opps:
        raise ValueError("没有交易机会，不生成邮件")
    dates = sorted({o.asof_date for o in opps if o.asof_date})
    subject = f"{settings.subject_prefix}｜{len(opps)} 条｜{dates[-1] if dates else '最新'}"
    lines = [
        f"{len(opps)} 条交易机会（{dates[-1] if dates else '最新'}）",
        "",
    ]
    rows = []
    for o in opps:
        reason = "；".join(o.reasons) if o.reasons else "—"
        lines += [
            f"- {o.name}（{o.spread_id}） {o.side}",
            f"  分位 {_fmt_pct(o.pct)}｜Z {_fmt_num(o.z)}｜ADF p {_fmt_num(o.adf_p, 4)}｜Hurst {_fmt_num(o.hurst)}",
            f"  原因：{reason}",
        ]
        rows.append(
            "<tr>"
            f"<td>{html.escape(o.name)}<br><small>{html.escape(o.spread_id)}</small></td>"
            f"<td>{html.escape(o.side)}</td>"
            f"<td>{_fmt_pct(o.pct)}</td><td>{_fmt_num(o.z)}</td>"
            f"<td>{_fmt_num(o.adf_p, 4)}</td><td>{_fmt_num(o.hurst)}</td>"
            f"<td>{html.escape(reason)}</td>"
            "</tr>"
        )
    body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="font:14px/1.7 -apple-system,BlinkMacSystemFont,'Noto Sans CJK SC','Microsoft YaHei',sans-serif;color:#1f2933">
<h2 style="margin:0 0 6px">期货策略交易机会</h2>
<p style="color:#64748b;margin:0 0 14px">共 {len(opps)} 条；日期 {html.escape(dates[-1] if dates else '最新')}。以下为价差分位回归的可执行信号，已通过 ADF/Hurst 双验证。</p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f1f5f9">
<th style="border-bottom:1px solid #e2e8f0;padding:7px;text-align:left">价差</th>
<th style="border-bottom:1px solid #e2e8f0;padding:7px;text-align:left">方向</th>
<th style="border-bottom:1px solid #e2e8f0;padding:7px">分位</th>
<th style="border-bottom:1px solid #e2e8f0;padding:7px">Z</th>
<th style="border-bottom:1px solid #e2e8f0;padding:7px">ADF p</th>
<th style="border-bottom:1px solid #e2e8f0;padding:7px">Hurst</th>
<th style="border-bottom:1px solid #e2e8f0;padding:7px;text-align:left">原因</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
<p style="color:#64748b;font-size:12px;margin-top:16px">仅研究信号，不构成交易建议。发送时间 {pd.Timestamp.now():%Y-%m-%d %H:%M}。</p>
</body></html>"""
    return subject, "\n".join(lines), body


def _split_addrs(raw: str) -> List[str]:
    return [x.strip() for x in (raw or "").replace(";", ",").split(",") if x.strip()]


def _resolve(settings: EmailSettings) -> Dict[str, Any]:
    to = _split_addrs(os.getenv(settings.to_env, ""))
    dws_to = _split_addrs(os.getenv(settings.dws_to_env, ""))
    default_dws = str(Path.home() / ".local" / "bin" / "dws")
    return {
        "host": os.getenv(settings.host_env, "").strip(),
        "port": int(os.getenv(settings.port_env, "587") or 587),
        "user": os.getenv(settings.user_env, "").strip(),
        "password": os.getenv(settings.password_env, ""),
        "from": os.getenv(settings.from_env, "").strip(),
        "to": to,
        "dws_cli": os.getenv(settings.dws_cli_env, "").strip() or default_dws,
        "dws_from": os.getenv(settings.dws_from_env, "").strip(),
        "dws_to": dws_to,
    }


def _dws_sender(cli: str) -> str:
    """从 dws 已授权邮箱里取第一个地址；只读操作，不打印地址。"""
    if not cli or not Path(cli).exists():
        return ""
    try:
        proc = subprocess.run([cli, "mail", "mailbox", "list", "--format", "json"],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return ""
        data = json.loads(proc.stdout)
        for item in data.get("emailAccounts", []) or []:
            email = str(item.get("email", "")).strip()
            if email:
                return email
    except Exception:
        pass
    return ""


def _send_via_dws(cfg: Dict[str, Any], subject: str, text: str) -> Dict[str, Any]:
    """钉钉企业邮箱主通道。返回 sent/reason/channel，不抛网络异常。"""
    if not cfg["dws_to"]:
        return {"sent": False, "reason": "no_dws_to", "channel": "dws"}
    cli = cfg["dws_cli"]
    if not Path(cli).exists():
        return {"sent": False, "reason": "dws_cli_missing", "channel": "dws"}
    sender = cfg["dws_from"] or _dws_sender(cli)
    if not sender:
        return {"sent": False, "reason": "no_dws_from", "channel": "dws"}
    cmd = [cli, "mail", "message", "send",
           "--from", sender, "--to", ",".join(cfg["dws_to"]),
           "--subject", subject, "--content", text, "--format", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except Exception as exc:
        return {"sent": False, "reason": f"dws_error:{type(exc).__name__}", "channel": "dws"}
    out = (proc.stdout or "").strip()
    ok = False
    try:
        payload = json.loads(out)
        ok = payload.get("success") is True or str(payload.get("success", "")).lower() == "true"
    except Exception:
        ok = proc.returncode == 0 and '"success"' in out and "true" in out.lower()
    if not ok:
        return {"sent": False, "reason": f"dws_failed:{out[:160] or proc.stderr[:160]}",
                "channel": "dws"}
    return {"sent": True, "channel": "dws", "to": cfg["dws_to"]}


def send_email(settings: EmailSettings, subject: str, text: str, html_body: str,
               dry_run: bool = True) -> Dict[str, Any]:
    if not settings.enabled:
        return {"sent": False, "reason": "disabled"}
    cfg = _resolve(settings)
    preview_to = cfg["to"] or cfg["dws_to"]
    if dry_run:
        return {"sent": False, "dry_run": True, "subject": subject,
                "to": preview_to, "text": text}

    dws_error = ""
    if settings.prefer_dws:
        dws = _send_via_dws(cfg, subject, text)
        if dws.get("sent"):
            return {"sent": True, "dry_run": False, "channel": "dws",
                    "subject": subject, "to": cfg["dws_to"]}
        dws_error = str(dws.get("reason", "dws_failed"))

    missing = [k for k in ("host", "from") if not cfg[k]] + ([] if cfg["to"] else ["to"])
    if missing:
        raise RuntimeError(
            f"邮件通道未配置（dws: {dws_error or '未尝试'}；SMTP 缺失: {', '.join(missing)}）。"
            f"检查 dws 的 {settings.dws_to_env}/{settings.dws_from_env}，"
            f"或 SMTP 的 {settings.host_env}/{settings.from_env}/{settings.to_env}"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    if settings.use_ssl:
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
    try:
        if settings.use_tls and not settings.use_ssl:
            server.starttls(context=ssl.create_default_context())
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return {"sent": True, "dry_run": False, "channel": "smtp",
            "subject": subject, "to": cfg["to"]}


def notify(dry_run: Optional[bool] = None, asof: Optional[str] = None,
           config_path: Path | None = None, store: Optional[SQLiteStore] = None,
           settings: Optional[EmailSettings] = None,
           actions: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    _load_local_env()
    st = settings or load_settings(config_path)
    run_dry = st.dry_run if dry_run is None else bool(dry_run)
    if store is None:
        app = load_app_settings()
        store = SQLiteStore(app.data.db_path)
    opps = collect_opportunities(store, asof=asof, actions=actions or st.actions)
    if not opps:
        return {"sent": False, "reason": "no_opportunities", "count": 0, "dry_run": run_dry}
    subject, text, html_body = build_message(opps, st)
    result = send_email(st, subject, text, html_body, dry_run=run_dry)
    result["count"] = len(opps)
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="交易机会邮件通知（默认 dry-run）")
    ap.add_argument("--send", action="store_true", help="真实发送；不加则只预览")
    ap.add_argument("--asof", help="指定信号日期 YYYY-MM-DD")
    ap.add_argument("--config", help="配置文件路径")
    args = ap.parse_args(argv)
    result = notify(dry_run=not args.send, asof=args.asof,
                    config_path=Path(args.config) if args.config else None)
    if result.get("reason") == "no_opportunities":
        print("没有 LONG_SPREAD / SHORT_SPREAD 交易机会，不发送邮件。")
        return 0
    if result.get("dry_run"):
        print(f"[dry-run] subject: {result.get('subject')}")
        print(f"[dry-run] to: {', '.join(result.get('to') or []) or '(未配置 EMAIL_TO/SMTP_TO)'}")
        print(result.get("text", ""))
        return 0
    if result.get("sent"):
        channel = result.get("channel", "smtp")
        print(f"已发送({channel}): {result.get('subject')} -> {', '.join(result.get('to') or [])}")
        return 0
    print(f"未发送: {result.get('reason')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())