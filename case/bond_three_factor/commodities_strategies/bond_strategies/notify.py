"""利率三因子周报推送适配层：飞书交互式卡片 + 条件邮件告警。

安全约定：**仓库内不保存任何 webhook / app secret / 邮件凭据**。
- 飞书统一路由：优先 ``BOND_FEISHU_ROUTER_DIR``，回退 ``<workspace>/feishu_router``；
- 邮件告警：优先 ``BOND_EMAIL_PUSH_DIR``，回退工作区内含 ``email_push.py`` 的既有目录；
- 两者都不可用时只打印提示，不阻断周报落盘与退出码。

推送规则与旧版 ``weekly_slope_report.py --push`` 一致：
Slope 机会触发（``slope_signal`` 非空）或任一期限预测出现买入/卖出时推送飞书；
邮件仅在 Slope 机会触发时发送，无机会一律静默。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from typing import Dict, List

from ..config import ROOT
from .dataset import FOCUS

WS = ROOT.parent.parent
SUBJECT = "利率三因子周度预测和 Slope交易"
TERMS = list(FOCUS)


# ---------------------------------------------------------------- 外部私有组件定位
def _candidate_dirs(env_key: str, marker: str) -> List[str]:
    out: List[str] = []
    env = os.environ.get(env_key)
    if env:
        out.append(env)
    for p in sorted(WS.glob(f"*/{marker}")):
        out.append(str(p.parent))
    return out


def _load_module(directory: str, filename: str, alias: str):
    """按文件路径加载私有组件，避免同名模块被 sys.modules 缓存串用。"""
    path = os.path.join(directory, filename)
    if not os.path.isfile(path):
        return None
    if directory not in sys.path:
        sys.path.insert(0, directory)
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as err:  # noqa: BLE001
        sys.modules.pop(alias, None)
        print(f"[push] 加载 {filename} 失败({directory}): {err}", flush=True)
        return None
    return mod


def feishu_router():
    for d in _candidate_dirs("BOND_FEISHU_ROUTER_DIR", "feishu_router.py"):
        mod = _load_module(d, "feishu_router.py", "_bond_feishu_router")
        if mod is not None and hasattr(mod, "push_card"):
            return mod
    return None


def email_push():
    for d in _candidate_dirs("BOND_EMAIL_PUSH_DIR", "email_push.py"):
        mod = _load_module(d, "email_push.py", "_bond_email_push")
        if mod is not None and hasattr(mod, "send_alert_email"):
            return mod
    return None


# ---------------------------------------------------------------- 卡片构建
def _text(content: str, tag: str = "lark_md") -> Dict:
    return {"tag": "div", "text": {"tag": tag, "content": content}}


def _divider() -> Dict:
    return {"tag": "hr"}


def _note(content: str) -> Dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def _header(title: str, template: str = "blue") -> Dict:
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


def build_card(result: Dict) -> Dict:
    """按 slope_scan_state 结构生成交互式卡片：结论置顶 + 分节。"""
    date_str = result.get("date", "")
    next_week = result.get("next_week", "")
    sig = result.get("slope_signal")
    actives = result.get("pred_actions", {})
    cur = result.get("yields_cur", {})
    nxt = result.get("yields_next", {})
    hit = result.get("hit", {})

    elements = [_text(f"**报告来自于 DoMate**  |  {SUBJECT}  |  **{date_str}**"), _divider()]

    if sig:
        label = "做陡（押曲线陡峭化）" if sig == "steep" else "做平（押曲线平坦化）"
        lots = "TS×3 多 + T×1 空（做陡）" if sig == "steep" else "TS×3 空 + T×1 多（做平）"
        elements.append(_text(
            f"**⭐⭐⭐ 交易机会触发：{label} ⭐⭐⭐**\n"
            f"{result.get('slope_note', '')}\n"
            f"推荐组合：**{lots}**\n"
            f"⚠️ 实盘需日频连续2日确认、T+1 开盘执行；风控 s7/h20/tp2=0.70。"
        ))
    else:
        lines = ["**Slope：无交易机会（当前观望）**\n", "三因子方向：\n"]
        any_act = False
        for t in TERMS:
            a = actives.get(t, "观望")
            if t in cur and t in nxt:
                lines.append(f"· {t}：{cur[t]:.4f}% → {nxt[t]:.4f}%（{(nxt[t] - cur[t]) * 100:+.1f}bp）→ **{a}**")
            else:
                lines.append(f"· {t}：{a}")
            any_act = any_act or a != "观望"
        if not any_act:
            lines.append("· 全部期限均观望")
        elements.append(_text("\n".join(lines)))
    if result.get("near"):
        elements.append(_text(f"· 接近机会区：{result['near']}"))
    elements.append(_divider())

    seg = [f"**一、三因子周度预测与建议**（数据截止 {date_str}，目标周 {next_week}）\n"]
    for t in TERMS:
        a = actives.get(t, "观望")
        if t in cur and t in nxt:
            seg.append(f"· {t}：{cur[t]:.4f}% → {nxt[t]:.4f}%（{(nxt[t] - cur[t]) * 100:+.1f}bp）→ **{a}**，命中{hit.get(t, '-')}")
        else:
            seg.append(f"· {t}：{a}")
    elements.append(_text("\n".join(seg)))
    elements.append(_divider())

    dr = result.get("slope_DR007_DIR", 0)
    seg = [
        "**二、Slope（10Y-2Y）判定明细**\n",
        f"· SPREAD210（10Y-2Y 利差）：**{result.get('slope_SPREAD210', 0):.2f} bp**；250日滚动分位 **{result.get('slope_SPREAD_PCT', 0):.3f}**（<0.08 找做陡、>0.92 找做平）",
        f"· DR007 方向（MA5-MA20）：**{'下行(宽松)' if dr < 0 else '上行(收紧)' if dr > 0 else '走平'}**（{dr:.0f}）",
        f"· 残差波动分位 RESID_PCT：**{result.get('slope_RESID_PCT', 0):.3f}**（需 <0.80 过滤高波动）",
        f"· Slope 机会判定：**{'做陡' if sig == 'steep' else '做平' if sig == 'flat' else '无 Slope 机会'}**",
    ]
    elements.append(_text("\n".join(seg)))
    elements.append(_divider())
    elements.append(_note(
        "数据：DataHub（中债官方曲线 + EDB 资金面/宏观，含汇率与原油经 EDB），模拟口径不含票息与成本；"
        "Slope 周度扫描仅识别机会区间，实盘叠加日频确认与 T+1 开盘执行；仅供研究参考，不构成投资建议。"
    ))

    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"{SUBJECT} · {date_str}", "red" if sig else "blue"),
        "elements": elements,
    }


# ---------------------------------------------------------------- 推送入口
def push_feishu(result: Dict) -> bool:
    router = feishu_router()
    if router is None:
        print("[push] 未找到 feishu_router（可用 BOND_FEISHU_ROUTER_DIR 指定），跳过飞书推送", flush=True)
        return False
    res = router.push_card(build_card(result), tag="利率三因子周报")
    ok = router.all_ok(res)
    if not ok:
        print(f"[push] 飞书推送未全部成功: {res}", flush=True)
    return bool(ok)


def send_email(md_path: str, result: Dict) -> str:
    if not result.get("slope_signal"):
        return "无 Slope 机会，跳过邮件（静默）"
    mod = email_push()
    if mod is None:
        return "未找到 email_push（可用 BOND_EMAIL_PUSH_DIR 指定），跳过邮件"
    try:
        ok, msg = mod.send_alert_email(md_path, result)
        return f"{'成功' if ok else '失败'}: {msg}"
    except Exception as err:  # noqa: BLE001
        return f"邮件调用异常: {err}"


def push_all(md_path: str, result: Dict) -> Dict[str, str]:
    """按旧版规则推送：飞书条件推送 + Slope 机会时邮件告警。"""
    out = {"feishu": "未触发（无机会且全观望）", "email": "未触发"}
    if not result.get("should_push"):
        return out
    out["feishu"] = "已推送" if push_feishu(result) else "未推送"
    out["email"] = send_email(md_path, result)
    return out


def push_card_json(result: Dict) -> str:
    """离线预览卡片 JSON（--push-dry 用，不发送）。"""
    return json.dumps(build_card(result), ensure_ascii=False, indent=2)