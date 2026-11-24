#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多方法行业轮动机会风险日报 —— 每日执行入口（供每晚 19:00 定时任务调用）

流程（任一步失败 → 飞书推送失败备注 → exit 1）：
  1) 数据增量：申万 L1/L3 日线（代理通道）从本地最后日期续拉至今日
  2) 由 sw_l1 重建 datahub/indices 一级 json（开源轮动同源数据）
  3) TDA 结构风险 compute（n-days 720）→ tda_run/results.json
  4) 开源时序轮动（date0=上期交易日, date1=最新交易日）→ 行业轮动数据明细_{date}.csv
  5) STMOM 21日动量增量 → daily_momentum_out/signal_{date}.json
  6) 概念刷新：同花顺概念（存储芯片/CPO/光纤概念/白酒等）+ 自建 黄酒/光模块 → concepts/concepts.json
  7) 三方法加权汇总（每方法机会/风险权重=1）→ report_run/l1_combined.csv 等
  8) 生成桑基图 standalone HTML + dataURL → 云端浏览器截图 PNG
  9) 飞书推送：上传图 → 图卡（消息头「主题|日期|来自 DoMate」）→ 文字摘要卡
失败备注：任何异常统一推送飞书失败卡片，标题「行业轮动日报 执行失败 | 日期 | 来自 DoMate」。

用法：
  python run_daily_sector_rotation.py [--date YYYYMMDD] [--no-send] [--no-browser]
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
TDA_DIR = os.path.join(BASE, "tda_run")
REPORT = os.path.join(BASE, "report_run")
STOM_SES = os.path.join(BASE, "ses_gffe5f330c6707ffeqvFIsPha0gz5nv")
PLAYWRIGHT = os.path.join(BASE, "skills", "official", "dumate-browser-use", "scripts", "playwright-cli")
PYLIBS = os.path.join(BASE, "pylibs")
if PYLIBS not in sys.path:
    sys.path.insert(0, PYLIBS)
sys.path.insert(0, os.path.join(BASE, "ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG", "pylibs"))
# 直接读取用户权限目录 py_deps 下的 akshare/tushare
_pd = os.path.join(BASE, "ses_gffe5ea8aaca93ffeHyamlhKpZfCuW2", "py_deps")
if _pd not in sys.path and os.path.isdir(_pd):
    sys.path.insert(0, _pd)

import requests  # noqa: E402
import pandas as pd  # noqa: E402

# ---- DataHub 统一取数（股票模块，三级体系）----------------------------------
# 申万行业日线与概念成分个股日线改由 data_hub 提供，本文件不再自建 tushare 会话直连。
HUB_ROOT = os.path.join(BASE, "projects", "datahub-three-tier")
HUB_DEPS = os.path.join(BASE, "py_deps")
for _hp in (HUB_DEPS, HUB_ROOT):
    if _hp not in sys.path:
        sys.path.insert(0, _hp)

from ts_config import TS_TOKEN, TS_HTTP_URL  # noqa: E402

# 宽基指数判断模块（report_run 同目录：五指数涨跌/风险机会/轮动）
sys.path.insert(0, REPORT)
from broad_index_judge import run_broad_index_judge  # noqa: E402

# ---- 开源轮动 SW 一级映射（industry_rotation.py 内嵌表）----
SW = {
    "801010": "农林牧渔", "801030": "基础化工", "801040": "钢铁", "801050": "有色金属",
    "801080": "电子", "801110": "家用电器", "801120": "食品饮料", "801130": "纺织服饰",
    "801140": "轻工制造", "801150": "医药生物", "801160": "公用事业", "801170": "交通运输",
    "801180": "房地产", "801200": "商贸零售", "801210": "社会服务", "801710": "建筑材料",
    "801720": "建筑装饰", "801730": "电力设备", "801740": "国防军工", "801750": "计算机",
    "801760": "传媒", "801770": "通信", "801780": "银行", "801790": "非银金融",
    "801880": "汽车", "801890": "机械设备", "801950": "煤炭", "801960": "石油石化",
    "801970": "环保", "801980": "美容护理",
}

FAILED_REASON = [None]  # 记录失败原因，供飞书备注


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------- 飞书推送（成功卡 + 失败备注） ----------------

def _feishu_config():
    cfg_dir = os.path.join(BASE, "ses_gffe5febb5eaeeffe0mTIpBeu5w0Nq0")
    sys.path.insert(0, cfg_dir)
    import feishu_config as fc
    return fc.APP_ID, fc.APP_SECRET, fc.WEBHOOK_URL


def _tenant_token(app_id, app_secret):
    r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                      json={"app_id": app_id, "app_secret": app_secret}, timeout=30)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"飞书token失败: {d.get('msg')}")
    return d["tenant_access_token"]


def _upload_image(token, img_path):
    with open(img_path, "rb") as f:
        r = requests.post("https://open.feishu.cn/open-apis/im/v1/images",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"image": (os.path.basename(img_path), f, "image/png")},
                          data={"image_type": "message"}, timeout=60)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"图片上传失败: {d.get('msg')}")
    return d["data"]["image_key"]


def _send_webhook(webhook, card):
    r = requests.post(webhook, json=card, timeout=30)
    d = r.json()
    if d.get("code") != 0 and d.get("StatusCode") != 0:
        raise RuntimeError(f"推送失败: {d.get('msg') or d.get('StatusMessage')}")
    return True


def push_header(title):
    return {"msg_type": "interactive", "card": {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text", "content": title}},
        "elements": [],
    }}


def push_image_card(webhook, token, image_key, title, note=""):
    card = push_header(title)
    card["card"]["elements"].append(
        {"tag": "img", "img_key": image_key,
         "alt": {"tag": "plain_text", "content": "行业流动桑基图"}})
    if note:
        card["card"]["elements"].append(
            {"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
    return _send_webhook(webhook, card)


def push_text_card(webhook, token, title, markdown, note=""):
    card = push_header(title)
    card["card"]["elements"].append(
        {"tag": "div", "text": {"tag": "lark_md", "content": markdown}})
    if note:
        card["card"]["elements"].append(
            {"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
    return _send_webhook(webhook, card)


def push_failure(webhook, date_str, stage, reason):
    """失败备注：标题带「执行失败」，正文给阶段与原因。"""
    card = push_header(f"行业轮动日报 执行失败 | {date_str} | 来自 DoMate")
    card["card"]["header"]["template"] = "red"
    card["card"]["elements"].append({
        "tag": "div",
        "text": {"tag": "lark_md",
                 "content": f"**任务失败**\n- 阶段: {stage}\n- 原因: {reason}\n\n"
                            f"请检查数据源/网络后重跑：\n"
                            f"`python report_run/run_daily_sector_rotation.py`"}})
    try:
        _send_webhook(webhook, card)
    except Exception as e:  # noqa: BLE001
        log(f"[warn] 失败备注推送也失败: {e}")


# ---------------- 1. 申万数据增量（代理通道） ----------------

import concurrent.futures as _cf
from datetime import datetime, timedelta

_TS_SESSION = requests.Session()          # 复用连接，钉住出口 IP（降握手与限频风险）
_TS_SEM = threading.Semaphore(4)          # 限制代理通道最大并发 4，防限频同时显著提速
CONCEPT_DEGRADED = []                     # 本次取数不完整（被跳过/降级）的自建概念名


def _call(api_name, params, retry=1):
    """通过代理通道调用 tushare 接口；信号量限并发，复用持久会话。"""
    with _TS_SEM:
        last = None
        for i in range(retry + 1):
            try:
                r = _TS_SESSION.post(TS_HTTP_URL, json={"api_name": api_name,
                                                        "token": TS_TOKEN,
                                                        "params": params}, timeout=30)
                j = r.json()
                if j.get("code") == 0:
                    return j["data"]["fields"], j["data"]["items"]
                last = f"code={j.get('code')} msg={j.get('msg')}"
            except Exception as e:  # noqa: BLE001
                last = repr(e)[:120]
            # 无论异常还是 code!=0（如代理「ip超限」），重试前都退避，降低瞬时限频命中率
            if i < retry:
                time.sleep(2 + i * 2)
        raise RuntimeError(f"{api_name} {params} failed: {last}")


def _df_to_fields_items(df):
    """DataFrame -> (fields, items)，兼容本文件既有的 (fields, items) 消费方式。"""
    fields = [str(c) for c in df.columns]
    items = [tuple(row) for row in df.itertuples(index=False, name=None)]
    return fields, items


def _fetch_daily_backoff(ts_code, start, end, attempts=12):
    """单代码个股日线：DataHub 股票模块 get_stock_daily，失败按退避重试。

    成功返回 (fields, items)；全部失败返回 None，交由调用方降级处理，不中断全链路。
    """
    last = "未知"
    for i in range(attempts):
        try:
            from data_hub.collectors.stocks import get_stock_daily
            df = get_stock_daily(ts_code, start_date=start, end_date=end)
            if df is not None and len(df):
                df = df.copy()
                if 'trade_date' not in df.columns and 'date' in df.columns:
                    df['trade_date'] = df['date']
                return _df_to_fields_items(df)
            last = "返回空数据"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:140]
        time.sleep(min(1.5 + i * 0.6, 7.0))
    log(f"[warn] daily {ts_code} 取数 {attempts} 次仍失败: {last}")
    return None


def trade_date_items(date_str):
    """按交易日一次取回申万全行业行（DataHub 股票模块 sw_daily 单日口径）。"""
    try:
        from data_hub.collectors.stocks.sw_index_daily import get_sw_daily_by_date
        df = get_sw_daily_by_date(date_str)
    except Exception as e:  # noqa: BLE001
        log(f"[warn] sw_daily {date_str} 取数异常: {str(e)[:100]}")
        return [], []
    if df is None or len(df) == 0:
        return [], []
    return _df_to_fields_items(df)


def sw_daily_by_date(start, end, max_days=40):
    """批量按交易日拉取 (start, end] 区间全部 L1/L2/L3 行。
    返回 {trade_date: (fields, items)}，探测窗口默认 40 个自然日。
    """
    out = {}
    d = datetime.strptime(start, "%Y%m%d") + timedelta(days=1)
    endd = datetime.strptime(end, "%Y%m%d")
    n = 0
    while d <= endd and n < max_days:
        ds = d.strftime("%Y%m%d")
        f, it = trade_date_items(ds)
        if it:
            out[ds] = (f, it)
        n += 1
        d += timedelta(days=1)
    return out


def _fetch_daily_retry(ts_code, start, end, retries=1):
    """单代码增量拉取，宽容模式：失败返回 None 而不抛出（容错）。"""
    try:
        return ts_code, fetch_sw_daily(ts_code, start, end)
    except Exception:
        for _ in range(retries):
            try:
                return ts_code, fetch_sw_daily(ts_code, start, end)
            except Exception:
                time.sleep(1.0)
        return ts_code, None


def fetch_sw_daily(ts_code, start, end):
    """DataHub 股票模块申万行业日线：返回 {YYYYMMDD: close} 升序 dict。"""
    from data_hub.collectors.stocks import fetch_sw_index_daily
    df = fetch_sw_index_daily(ts_code, start_date=start, end_date=end)
    out = {}
    if df is None or len(df) == 0:
        return out
    for _, row in df.sort_values('trade_date').iterrows():
        out[str(row['trade_date'])] = float(row['close'])
    return out


def incr_update_sw_l3(start, end):
    """按 tda_run/sw_l3_daily.csv 现有 orig_code 列表增量续拉（按 trade_date 批量，秒级）。"""
    l3 = pd.read_csv(os.path.join(TDA_DIR, "sw_l3_daily.csv"), parse_dates=["date"])
    last_day = l3["date"].max().strftime("%Y%m%d")
    if last_day >= end:
        log(f"L3 数据已是最新 ({last_day})，跳过增量")
        return last_day
    # 元数据映射：orig_code -> (name, code, level) 保持不变
    meta = {}
    for _, r in l3.iterrows():
        if r["orig_code"] not in meta:
            meta[r["orig_code"]] = (r["name"], r["code"], r["level"])
    rows_by_day = sw_daily_by_date(last_day, end)
    if not rows_by_day:
        log(f"L3 增量: {last_day}~{end} 无新交易日数据")
        return last_day
    add = []
    for day, (f, it) in sorted(rows_by_day.items()):
        cols = {k: i for i, k in enumerate(f)}
        for row in it:
            ts = row[cols["ts_code"]]
            if ts not in meta:
                continue
            name, code, level = meta[ts]
            add.append({"date": day, "close": float(row[cols["close"]]), "name": name,
                        "code": code, "level": level, "orig_code": ts})
    full = l3
    if add:
        ndf = pd.DataFrame(add, columns=l3.columns)
        ndf["date"] = pd.to_datetime(ndf["date"])
        full = pd.concat([l3, ndf], ignore_index=True).drop_duplicates(["date", "orig_code"], keep="last")
        full.to_csv(os.path.join(TDA_DIR, "sw_l3_daily.csv"), index=False)
        log(f"L3 增量: +{len(add)} 行（{len(rows_by_day)} 个交易日）-> {full['date'].max().date()}")
    return str(full["date"].max().strftime("%Y%m%d"))


def incr_update_sw_l1(end):
    l1 = pd.read_csv(os.path.join(TDA_DIR, "sw_l1_daily.csv"), parse_dates=["date"])
    last_day = l1["date"].max().strftime("%Y%m%d")
    if last_day >= end:
        log(f"L1 数据已是最新 ({last_day})，跳过增量")
        return last_day
    name_by_code = dict(zip(l1["code"], l1["name"]))
    rows_by_day = sw_daily_by_date(last_day, end)
    add = []
    for day, (f, it) in sorted(rows_by_day.items()):
        cols = {k: i for i, k in enumerate(f)}
        for row in it:
            ts = row[cols["ts_code"]]
            if ts not in name_by_code:
                continue
            add.append({"date": day, "close": float(row[cols["close"]]),
                        "name": name_by_code[ts], "code": ts})
    if add:
        ndf = pd.DataFrame(add, columns=l1.columns)
        ndf["date"] = pd.to_datetime(ndf["date"])
        full = pd.concat([l1, ndf], ignore_index=True).drop_duplicates(["date", "code"], keep="last")
        full.to_csv(os.path.join(TDA_DIR, "sw_l1_daily.csv"), index=False)
        log(f"L1 增量: +{len(add)} 行 -> {full['date'].max().date()}")
        return str(full["date"].max().strftime("%Y%m%d"))
    log(f"L1 无新增行（末日 {last_day}）")
    return last_day


def rebuild_datahub_indices():
    """从 sw_l1_daily.csv 重建 datahub/indices/801xxx.json（开源轮动同源）。"""
    l1 = pd.read_csv(os.path.join(TDA_DIR, "sw_l1_daily.csv"), parse_dates=["date"])
    dh_dir = os.path.join(BASE, "datahub", "indices")
    os.makedirs(dh_dir, exist_ok=True)
    n = 0
    for code6, name in SW.items():
        sub = l1[l1["name"] == name].sort_values("date")
        if not len(sub):
            continue
        rows = [{"trade_date": r["date"].strftime("%Y%m%d"), "close": round(float(r["close"]), 4)}
                for _, r in sub.iterrows()]
        with open(os.path.join(dh_dir, f"{code6}.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        n += 1
    log(f"重建 datahub/indices: {n}/30 个一级行业 json（末行一致性已校验）")
    return n


# ---------------- 2. 三方法执行 ----------------

def run_tda():
    script = os.path.join(BASE, "skills", "user", "tda行业轮动", "scripts", "compute.py")
    r = subprocess.run([sys.executable, script, "--data-dir", TDA_DIR, "--n-days", "720"],
                       cwd=TDA_DIR, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"TDA compute 失败: {r.stderr[-500:]}")
    res = json.load(open(os.path.join(TDA_DIR, "results.json")))
    n_opp = sum(1 for x in res["industries"] if x["label"] in ("机会", "机会观察"))
    n_risk = sum(1 for x in res["industries"] if x["label"] in ("风险", "风险观察"))
    log(f"TDA: 机会/观察 {n_opp} | 风险/观察 {n_risk}")
    return res


def run_open_rotation(date0, date1, out_dir):
    script = os.path.join(BASE, "skills", "user", "open-source-industry-rotation",
                          "scripts", "industry_rotation.py")
    idx_dir = os.path.join(BASE, "datahub", "indices")
    r = subprocess.run([sys.executable, script, "--indices-dir", idx_dir,
                        "--date0", date0, "--date1", date1, "--out-dir", out_dir],
                       capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"开源轮动失败: {r.stderr[-500:]}")
    csv = os.path.join(out_dir, f"行业轮动数据明细_{date1.replace('-', '')}.csv")
    if not os.path.exists(csv):
        raise RuntimeError(f"开源轮动输出缺失: {csv}")
    log(f"开源轮动: {os.path.basename(csv)}")
    return csv


def run_stom():
    script = os.path.join(STOM_SES, "daily_momentum_signal.py")
    r = subprocess.run([sys.executable, script], cwd=STOM_SES,
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"STMOM 失败: {r.stderr[-500:]}")
    outs = sorted([f for f in os.listdir(os.path.join(STOM_SES, "daily_momentum_out"))
                   if f.startswith("signal_")])
    signal = os.path.join(STOM_SES, "daily_momentum_out", outs[-1])
    log(f"STMOM: {os.path.basename(signal)}")
    return signal


# ---------------- 3. 概念刷新 ----------------

def refresh_concepts(end):
    """同花顺概念 + 自建黄酒/光模块，写 concepts/concepts.json。"""
    os.makedirs(os.path.join(REPORT, "concepts"), exist_ok=True)
    import akshare as ak
    ths = ["存储芯片", "共封装光学(CPO)", "白酒概念", "光刻机", "机器人概念",
           "人形机器人", "算力租赁", "创新药", "光伏概念", "锂电池概念", "人工智能",
           "光纤概念"]
    out = {}
    for c in ths:
        try:
            df = ak.stock_board_concept_index_ths(symbol=c, start_date="20240101", end_date=end)
            out[c] = [{"trade_date": str(r["日期"]).replace("-", ""), "close": float(r["收盘价"])}
                      for _, r in df.iterrows()]
            log(f"  概念 {c}: {len(out[c])} 行")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"概念 {c} 拉取失败: {str(e)[:120]}")
        time.sleep(1.2)
    # 自建概念：黄酒 / 光模块（复用 fetch_custom_concepts 的构建函数）
    custom = build_custom_concepts(end)
    out.update(custom)
    with open(os.path.join(REPORT, "concepts", "concepts.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"概念库刷新完成: {len(out)} 个（含自建 黄酒/光模块）")
    return out


def build_custom_concepts(end):
    CUSTOM = {
        "黄酒": ["600059.SH", "601579.SH", "600616.SH"],
        "光模块": ["300308.SZ", "300502.SZ", "300394.SZ",
                  "002281.SZ", "300570.SZ", "603083.SH"],
    }
    out = {}
    degraded = []
    for name, codes in CUSTOM.items():
        series = {}
        miss = []
        for c in codes:
            got = _fetch_daily_backoff(c, "20250101", end)
            if got is None:
                miss.append(c)
                continue
            f, items = got
            di = f.index("trade_date")
            ci = f.index("close")
            for it in items or []:
                series.setdefault(str(it[di]), []).append(float(it[ci]))
        dates = sorted(series)
        prev = None
        idx = 1000.0
        rows = []
        for d in dates:
            vals = [v for v in series[d] if v and v > 0]
            if not vals:
                continue
            cur = sum(vals) / len(vals)
            if prev is not None and prev > 0 and cur > 0:
                idx *= cur / prev
                rows.append({"trade_date": d, "close": round(idx, 4)})
            prev = cur
        if not rows:
            # 成分全部取不到：跳过该概念（绝不产出空概念），并在日志与推送中外示
            log(f"[warn] 自建概念「{name}」本次无任何可取行情，已跳过（成分 {codes}）")
            degraded.append(name)
            continue
        if miss:
            log(f"[warn] 自建概念「{name}」成分 {miss} 取数失败，按可用成分等权构建")
            degraded.append(name)
        out[name] = rows
    CONCEPT_DEGRADED[:] = sorted(set(degraded))
    return out


# ---------------- 4. 加权汇总（三方法等权） ----------------

def combine_all(open_csv, stom_signal, asof_date):
    """复制 combine.py 逻辑但参数化，输出 l1_combined.csv / l3_drilldown.csv / combined.json。"""
    import numpy as np
    l1_meta = json.load(open(os.path.join(TDA_DIR, "L1_meta.json")))
    l3_meta = json.load(open(os.path.join(TDA_DIR, "L3_meta.json")))
    l2_meta = json.load(open(os.path.join(TDA_DIR, "L2_meta.json")))
    l1_by_code = {m["industry_code"]: m["industry_name"] for m in l1_meta}
    l3_index2l1 = {}
    for m in l3_meta:
        head = m["industry_code"][:2] + "0000"
        l3_index2l1[m["index_code"]] = l1_by_code.get(head, "未知")
    l2_index2l1 = {}
    for m in l2_meta:
        head = m["industry_code"][:2] + "0000"
        l2_index2l1[m["index_code"]] = l1_by_code.get(head, "未知")

    def tda_code_to_l1(code, level):
        if level == "L3":
            return l3_index2l1.get(code, "未知")
        if level == "L2":
            return l2_index2l1.get(code, "未知")
        if level == "L1":
            for m in l1_meta:
                if m["index_code"] == code:
                    return m["industry_name"]
        return "未知"

    tda = json.load(open(os.path.join(TDA_DIR, "results.json")))
    tda_l1_opp, tda_l1_risk = {}, {}
    tda_l3_opp_list, tda_l3_risk_list = [], []
    for ind in tda["industries"]:
        l1 = tda_code_to_l1(ind.get("code", ""), ind.get("level", ""))
        if l1 == "未知":
            continue
        lbl, nm, lv = ind.get("label", ""), ind.get("name", ""), ind.get("level", "")
        if lbl in ("机会", "机会观察"):
            tda_l1_opp[l1] = tda_l1_opp.get(l1, 0) + 1
            tda_l3_opp_list.append({"name": nm, "l1": l1, "label": lbl, "level": lv})
        elif lbl in ("风险", "风险观察"):
            tda_l1_risk[l1] = tda_l1_risk.get(l1, 0) + 1
            tda_l3_risk_list.append({"name": nm, "l1": l1, "label": lbl, "level": lv})

    open_rows = pd.read_csv(open_csv)
    open_opp = set(open_rows.loc[open_rows["标记"].str.contains("★", na=False), "行业"])
    open_risk = set(open_rows.loc[open_rows["标记"].str.contains("🔴", na=False), "行业"])

    ETF2L1 = {
        "银行ETF华宝": "银行", "银行ETF汇添富": "银行", "房地产ETF南方": "房地产",
        "军工ETF国泰": "国防军工", "医药ETF易方达": "医药生物", "食品饮料ETF华夏": "食品饮料",
        "消费ETF汇添富": "食品饮料", "生物医药ETF汇添富": "医药生物", "生物医药ETF国泰": "医药生物",
        "传媒ETF广发": "传媒", "芯片ETF国泰": "电子", "芯片ETF广发": "电子",
        "芯片ETF华夏": "电子", "通信ETF华夏": "通信", "光伏ETF华泰柏瑞": "电力设备",
        "环保ETF广发": "环保", "新能源ETF南方": "电力设备", "新能源车ETF博时": "汽车",
        "新能源车ETF华夏": "汽车", "有色金属ETF南方": "有色金属",
    }
    st = json.load(open(stom_signal))
    stom_opp, stom_risk = {}, {}
    for x in st["B_top10"]:
        l1 = ETF2L1.get(x["name"])
        if l1:
            stom_opp[l1] = stom_opp.get(l1, 0) + 1
    for x in st["B_bottom10"]:
        l1 = ETF2L1.get(x["name"])
        if l1:
            stom_risk[l1] = stom_risk.get(l1, 0) + 1

    l1 = pd.read_csv(os.path.join(TDA_DIR, "sw_l1_daily.csv"), parse_dates=["date"])
    l1w = l1.pivot_table(index="date", columns="name", values="close").sort_index()
    last = l1w.index[-1]
    ret = l1w.pct_change()
    mom20 = l1w / l1w.shift(20) - 1
    mom60 = l1w / l1w.shift(60) - 1
    strength = ret.clip(lower=0).abs().rolling(14).sum() / ret.abs().rolling(14).sum()
    dnow = l1w.loc[last]
    m20, m60, st_now = mom20.loc[last], mom60.loc[last], strength.loc[last]

    def zscore_s(s):
        return (s - s.mean()) / s.std()

    score = (zscore_s(m20) + zscore_s(m60) + zscore_s(st_now)) / 3
    mom_rank = score.rank(ascending=False, method="min").astype(int)
    mom_order = score.sort_values(ascending=False)

    all_l1 = set(open_opp) | set(open_risk) | set(tda_l1_opp) | set(tda_l1_risk) \
        | set(stom_opp) | set(stom_risk)
    rows = []
    for l1n in sorted(all_l1, key=lambda x: -mom_rank.get(x, 99)):
        opp = (1 if l1n in open_opp else 0) + (1 if l1n in tda_l1_opp else 0) \
            + (1 if l1n in stom_opp else 0)
        risk = (1 if l1n in open_risk else 0) + (1 if l1n in tda_l1_risk else 0) \
            + (1 if l1n in stom_risk else 0)
        det = []
        for src, s_, w_ in (("开源", l1n in open_opp, l1n in open_risk),
                            ("TDA", l1n in tda_l1_opp, l1n in tda_l1_risk),
                            ("STMOM", l1n in stom_opp, l1n in stom_risk)):
            if s_ and w_:
                det.append(f"{src}:对冲")
            elif s_:
                det.append(f"{src}:机会")
            elif w_:
                det.append(f"{src}:风险")
            else:
                det.append(f"{src}:-")
        rows.append({"一级行业": l1n, "机会分": opp, "风险分": risk, "净分": opp - risk,
                     "横截面动量排名": int(mom_rank.get(l1n, 99)),
                     "20日动量%": round(float(m20.get(l1n, np.nan)) * 100, 2),
                     "60日动量%": round(float(m60.get(l1n, np.nan)) * 100, 2),
                     "方法明细": " / ".join(det)})
    comb = pd.DataFrame(rows).sort_values(["净分", "机会分", "横截面动量排名"],
                                          ascending=[False, False, True])
    comb.to_csv(os.path.join(REPORT, "l1_combined.csv"), index=False, encoding="utf-8-sig")

    drill = pd.DataFrame(tda_l3_opp_list + tda_l3_risk_list)
    if len(drill):
        drill["动量排名"] = drill["l1"].map(mom_rank)
        drill = drill.sort_values(["动量排名", "label"])
        drill.to_csv(os.path.join(REPORT, "l3_drilldown.csv"), index=False, encoding="utf-8-sig")

    json.dump({"asof": str(last.date()), "l1_rows": comb.to_dict("records"),
               "l3_drill": drill.to_dict("records") if len(drill) else [],
               "tda_level": tda.get("tda_latest", {}),
               "tda_contrib": tda.get("contrib", [])},
              open(os.path.join(REPORT, "combined.json"), "w"), ensure_ascii=False, default=str)
    log(f"加权汇总 OK: 机会行业 {int((comb['机会分']>=1).sum())} | 风险行业 {int((comb['风险分']>=1).sum())}")
    return comb


# ---------------- 5. 桑基图 HTML + dataURL + 截图 ----------------

def gen_sankey_standalone(combined_json_path):
    comb = pd.read_csv(os.path.join(REPORT, "l1_combined.csv"))
    drill = pd.read_csv(os.path.join(REPORT, "l3_drilldown.csv"))
    concepts = json.load(open(os.path.join(REPORT, "concepts", "concepts.json")))

    concept_rows = []
    for k, v in concepts.items():
        s = pd.Series({r["trade_date"]: r["close"] for r in v}).sort_index()
        if len(s) < 61:
            continue
        concept_rows.append((k, (s.iloc[-1] / s.iloc[-21] - 1) * 100, (s.iloc[-1] / s.iloc[-61] - 1) * 100))
    concept_df = pd.DataFrame(concept_rows, columns=["概念", "m20", "m60"]).sort_values("m20", ascending=False)

    risk_l1 = comb[comb["风险分"] >= 1].sort_values(["风险分", "机会分"], ascending=[False, True]).head(12)
    inflow = []
    opp_l1 = comb[comb["机会分"] >= 1].sort_values(["机会分", "横截面动量排名"], ascending=[False, True])
    drill_ok = drill[drill["label"].isin(["机会", "机会观察"])]
    seen = {}
    for _, r in opp_l1.iterrows():
        l1n = r["一级行业"]
        sub = drill_ok[drill_ok["l1"] == l1n].sort_values("动量排名").head(3)
        for _, s in sub.iterrows():
            if (l1n, s["name"]) in seen:
                continue
            seen[(l1n, s["name"])] = 1
            inflow.append({"name": f"{l1n}·{s['name']}", "weight": float(r["机会分"])})
    concept_keep = concept_df[concept_df["m20"] > concept_df["m20"].median()].head(6)
    fixed_extra = {"存储芯片", "共封装光学(CPO)", "白酒概念", "黄酒", "光模块", "光纤概念"}
    avail = set(concept_df["概念"])
    miss = fixed_extra - avail
    if miss:  # 概念库缺该名（如自建指数本次无有效行情）时安全跳过，不再越界
        log(f"[warn] 下钻固定概念不在概念库，已跳过: {sorted(miss)}")
    extra = {c for c in fixed_extra if c in avail}
    for _, c in concept_keep.iterrows():
        extra.add(c["概念"])
    for cn in sorted(extra, key=lambda x: -float(concept_df.loc[concept_df["概念"] == x, "m20"].iloc[0])):
        row = concept_df[concept_df["概念"] == cn].iloc[0]
        inflow.append({"name": f"概念·{cn}", "weight": 1.0, "m20": row["m20"]})
    seen2, uniq = set(), []
    for it in inflow:
        if it["name"] in seen2:
            continue
        seen2.add(it["name"])
        uniq.append(it)
    inflow = uniq

    nodes = []
    for _, r in risk_l1.iterrows():
        nodes.append({"name": f"{r['一级行业']}（流出·动量#{int(r['横截面动量排名'])}）",
                      "itemStyle": {"color": "#e06c6c"}})
    for it in inflow:
        nodes.append({"name": it["name"], "itemStyle": {"color": "#5fa8d3"}})
    links = []
    for _, r in risk_l1.iterrows():
        src = f"{r['一级行业']}（流出·动量#{int(r['横截面动量排名'])}）"
        for it in inflow:
            links.append({"source": src, "target": it["name"],
                          "value": round(float(max(it.get("weight", 1.0), 0.5)), 2)})

    SANK = json.dumps({"nodes": nodes, "links": links}, ensure_ascii=False)
    tpl = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script></head>
<body style="margin:0;background:#fff"><div id="sankey" style="width:1500px;height:860px"></div>
<script>
const SANK = __SANKJSON__;
const chart = echarts.init(document.getElementById("sankey"));
chart.setOption({
  tooltip: {trigger: "item", triggerOn: "mousemove", formatter: function(p){
    if (p.dataType === "edge") return "<b>" + p.data.source + " → " + p.data.target + "</b><br/>流强度: " + p.data.value;
    return "<b>" + p.data.name + "</b>";
  }},
  series: [{type: "sankey", layoutIterations: 64, nodeWidth: 18, nodeGap: 14,
    data: SANK.nodes,
    links: SANK.links.map(function(l){ return Object.assign({}, l, {lineStyle: {color: "gradient", opacity: 0.45, curveness: 0.5}}); }),
    label: {fontSize: 13, color: "#303133"},
    emphasis: {focus: "adjacency"}}]
});
</script></body></html>"""
    html = tpl.replace("__SANKJSON__", SANK)
    with open(os.path.join(REPORT, "sankey_standalone.html"), "w", encoding="utf-8") as f:
        f.write(html)
    import base64
    b = base64.b64encode(html.encode("utf-8")).decode()
    with open(os.path.join(REPORT, "sankey_dataurl.txt"), "w") as f:
        f.write("data:text/html;base64," + b)
    log(f"桑基图 HTML/dataURL 生成: nodes={len(nodes)} links={len(links)}")
    return os.path.join(REPORT, "sankey_dataurl.txt")


def browser_screenshot(sankey_png, retry=1):
    """云端浏览器打开 dataURL 截取 #sankey 元素。返回 PNG 路径。"""
    dataurl = open(os.path.join(REPORT, "sankey_dataurl.txt"), encoding="utf-8").read()
    last = None
    for i in range(retry + 1):
        try:
            subprocess.run(["bash", PLAYWRIGHT, "attach"], capture_output=True, text=True, timeout=60)
            subprocess.run(["bash", PLAYWRIGHT, "goto", dataurl],
                           capture_output=True, text=True, timeout=90)
            time.sleep(3)
            subprocess.run(["bash", PLAYWRIGHT, "screenshot", "#sankey", "--filename", sankey_png],
                           capture_output=True, text=True, timeout=90)
            if os.path.exists(sankey_png) and os.path.getsize(sankey_png) > 20_000:
                log(f"桑基图截图 OK: {sankey_png} ({os.path.getsize(sankey_png)//1024}KB)")
                return sankey_png
            last = f"截图文件异常: {sankey_png} 大小={os.path.getsize(sankey_png) if os.path.exists(sankey_png) else '不存在'}"
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:150]
        time.sleep(4)
    raise RuntimeError(f"浏览器截图失败: {last}")


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="截止交易日 YYYYMMDD（默认今天）")
    ap.add_argument("--no-send", action="store_true", help="不推送飞书")
    ap.add_argument("--no-browser", action="store_true", help="跳过浏览器截图")
    args = ap.parse_args()

    app_id = app_secret = webhook = None
    if not args.no_send:
        app_id, app_secret, webhook = _feishu_config()

    end = args.date or time.strftime("%Y%m%d")
    date_disp = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    header = f"行业轮动机会风险日报 | {date_disp} | 来自 DoMate"

    try:
        # 1) 数据增量
        log(f"== 1/9 申万数据增量（至 {end}）==")
        incr_update_sw_l3("20210101", end)
        act_last = incr_update_sw_l1(end)
        if end > act_last:
            log(f"注意: 数据末行为 {act_last}（可能非交易日或未收盘），按实际末行截面计算")
        # 从 sw_l1 取实际最新交易日
        l1 = pd.read_csv(os.path.join(TDA_DIR, "sw_l1_daily.csv"), parse_dates=["date"])
        asof = l1["date"].max().strftime("%Y%m%d")
        date_disp = f"{asof[:4]}-{asof[4:6]}-{asof[6:]}"
        # 上一交易日
        prev = l1["date"].max() - pd.Timedelta(days=14)
        d0 = l1.loc[l1["date"] < l1["date"].max(), "date"].max().strftime("%Y-%m-%d")
        d1 = l1["date"].max().strftime("%Y-%m-%d")
        log(f"  实际截面: {d1}（上期 {d0}）")

        # 2) 重建 datahub/indices（开源轮动同源）
        log("== 2/9 datahub 一级指数重建 ==")
        rebuild_datahub_indices()

        # 3) TDA
        log("== 3/9 TDA 结构风险 ==")
        run_tda()

        # 4) 开源轮动
        log("== 4/9 开源时序轮动 ==")
        open_csv = run_open_rotation(d0, d1, REPORT)

        # 5) STMOM
        log("== 5/9 STMOM 21日动量 ==")
        stom_signal = run_stom()

        # 6) 概念刷新
        log("== 6/9 概念刷新（同花顺+自建黄酒/光模块+光纤）==")
        refresh_concepts(end if end >= date_disp.replace("-", "") else date_disp.replace("-", ""))

        # 6b) 宽基指数判断（五宽基涨跌/风险机会/轮动）
        log("== 6.5/9 宽基指数判断（沪深300/中证500/中证1000/创业板指/科创50）==")
        bj = run_broad_index_judge(asof)

        # 7) 加权汇总
        log("== 7/9 三方法加权汇总 ==")
        combine_all(open_csv, stom_signal, date_disp)

        # 8) 桑基图 + 截图
        log("== 8/9 桑基图生成 ==")
        gen_sankey_standalone(os.path.join(REPORT, "combined.json"))
        png_path = os.path.join(REPORT, f"sankey_flow_{asof}.png")
        if not args.no_browser:
            browser_screenshot(png_path)
        else:
            log("跳过浏览器截图（--no-browser）")

        if args.no_send:
            log("== 9/9 跳过飞书推送（--no-send）==")
            log("[DONE] 全链路完成（未推送）")
            return 0

        # 9) 飞书推送
        log("== 9/9 飞书推送 ==")
        tk = _tenant_token(app_id, app_secret)
        comb = pd.read_csv(os.path.join(REPORT, "l1_combined.csv"))
        opp_top = "、".join(comb.sort_values("净分", ascending=False).head(5)["一级行业"])
        _risky = comb[comb["风险分"] >= 1] if "风险分" in comb.columns else comb.iloc[0:0]
        has_risk = len(_risky) > 0
        has_opp = bool((comb["机会分"] >= 1).any()) if "机会分" in comb.columns else False
        # 标题标记：风险→注意风险，机会→注意机会（图卡、文字卡共用）
        _mark = ("注意风险 | " if has_risk else "") + ("注意机会 | " if has_opp else "")
        if _mark:
            header = f"行业轮动机会风险日报 | {date_disp} | {_mark}来自 DoMate"
        if not args.no_browser:
            key = _upload_image(tk, png_path)
            push_image_card(webhook, tk, key, header,
                            note=f"商机流转桑基图 | 流出=风险一级宽口径带动量# | 流入=机会下钻三级+概念")
        risk_top = "、".join(_risky.sort_values(["风险分", "净分"],
                           ascending=[False, True]).head(5)["一级行业"])
        mom_top = "、".join(comb.sort_values("横截面动量排名").head(5)["一级行业"])
        mom_bot = "、".join(comb.sort_values("横截面动量排名", ascending=False).head(5)["一级行业"])
        # 标题标记已在上方按 注意风险/注意机会 统一生成（图卡、文字卡共用）
        _risk_head = f"**⚠️ 检测到风险：{risk_top}**\n\n" if has_risk else ""
        # 宽基指数判断段落（算法同源：20/60动量+趋势强度轮动；方向=5信号投票；风险机会=方向+波动+RSI）
        bj_lines = []
        if bj:
            bj_labels = "、".join(f"{x['code']}({x['direction']}·{x['label']})" for x in bj["indices"])
            bj_lines.append(f"**宽基指数判断**（算法：涨跌=动量+均线5信号投票；风险/机会=方向+波动分位+RSI修正）")
            for x in bj["indices"]:
                bj_lines.append(
                    f"- {x['code']}：方向 **{x['direction']}**({x['conf']}) | {x['label']} | "
                    f"20日 {x['m20']}% | 60日 {x['m60']}% | RSI {x['rsi']} | 强度#{x.get('rank', '-')}")
            bj_lines.append(f"- 指数轮动：{bj['rotation']['desc']}")
        bj_block = "\n".join(bj_lines)
        text = (_risk_head +
                f"**三方法等权汇总**（TDA结构风险 / 开源时序轮动 / STMOM 21日动量，每方法机会、风险权重均=1）\n"
                f"- 🟢 机会Top：{opp_top}\n- 🔴 风险Top：{risk_top}\n"
                f"- 📈 动量Top5：{mom_top}\n- 📉 动量Bottom5：{mom_bot}\n\n"
                f"{bj_block}\n\n"
                f"**桑基图读法**：左=流出端（风险一级宽口径，节点动量#），右=流入端（机会下钻至申万三级+细概念："
                f"存储芯片、光纤、光模块、黄酒、CPO、白酒概念等）\n\n"
                f"**口径**：数据至 {date_disp} 收盘；黄酒/光模块为自建等权概念指数；"
                f"存储芯片/光纤/CPO 为同花顺概念。结论仅供参考，不构成投资建议。")
        if CONCEPT_DEGRADED:
            text += (f"\n\n**数据提示**：以下自建概念本次取数不完整，已按可用成分构建或跳过："
                     f"{'、'.join(CONCEPT_DEGRADED)}")
# [baseline-stats]
        try:
            import sys as _bsys
            if '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared' not in _bsys.path:
                _bsys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared')
            from portfolio_stats import stats_block as _sb
            _bst = _sb('sector_rotation')
            if _bst:
                text += "\n\n" + _bst
        except Exception:
            pass

        push_text_card(webhook, tk, header, text)
        log("[DONE] 飞书推送完成（图卡+文字卡）")
        return 0

    except Exception as e:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        log(f"!! 任务失败: {e}")
        log(tb[-1200:])
        if webhook:
            push_failure(webhook, date_disp, "run_daily_sector_rotation", f"{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())