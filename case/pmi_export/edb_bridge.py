# -*- coding: utf-8 -*-
"""DataHub EDB 桥接层：报告端唯一的宏观数据入口

原则：本工程不再直连统计局 / 东方财富 / akshare，所有宏观数据一律从
data_hub 的 EDB（SQLite）读取；取数、解析、落库、来源元信息全部由 EDB 负责。

仓库定位：默认用 workspace 下的 DataHub 本地副本，可用环境变量 EDB_REPO 覆盖。
读取方式：直接读 edb_points / edb_doc 两张表（EDB 的存储契约），不重复实现抓取。
"""
# ── 去云盘化：/Coze/Drive、/opt/coze/.fuse_drive、/app/data 三个旧根可重定向 ──
# 换机器时设 CASE_DRIVE_ROOT / CASE_FUSE_ROOT / CASE_APP_DATA，未设则原样返回。
def _cloud_translate(_f=__file__):
    import os as _o
    import sys as _s
    _d = _o.path.dirname(_o.path.abspath(_f))
    for _ in range(6):
        _c = _o.path.join(_d, "_lib")
        if _o.path.isfile(_o.path.join(_c, "cloud_paths.py")):
            if _c not in _s.path:
                _s.path.insert(0, _c)
            from cloud_paths import translate as _t
            return _t
        _d = _o.path.dirname(_d)
    return lambda p: p


_cp = _cloud_translate()
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(HERE)
# 老默认值指向会话级临时目录（ses_xxx/_dh），换机/换会话就失效，不能当唯一入口。
_LEGACY_REPO = os.path.join(WORKSPACE, "ses_gffe5e3bd864efffe9ENnDdpdPZ1V6E", "_dh")


def _find_edb_repo() -> str:
    """探测 DataHub 仓库：EDB_REPO 环境变量 → 常见挂载路径 → workspace 下候选。"""
    def ok(p: str) -> bool:
        return bool(p) and os.path.isdir(os.path.join(p, "data_hub"))

    env = (os.environ.get("EDB_REPO") or "").strip()
    if ok(env):
        return env

    # 从本文件向上逐级找 datahub_path.py（build.py / scheduler.py 用的同一口径），
    # 找到就以它为准；找不到再退回「同级 checkout」候选。刻意不按目录存在性
    # 在任意上层目录里猜 —— 机器上可能残留同名旧副本，猜错会静默连错库。
    cands = [env, (os.environ.get("DATA_HUB_PATH") or os.environ.get("DATAHUB_DIR")
                   or os.environ.get("EDB_REPO") or "")]
    _cur = HERE
    for _ in range(6):
        if os.path.isfile(os.path.join(_cur, "datahub_path.py")):
            cands.append(os.path.join(_cur, "datahub-multi-tier"))
            cands.append(os.path.join(os.path.dirname(_cur), "datahub-multi-tier"))
            break
        _nxt = os.path.dirname(_cur)
        if _nxt == _cur:
            break
        _cur = _nxt
    cands += [
        os.path.join(os.path.dirname(WORKSPACE), "datahub-multi-tier"),
        os.path.join(WORKSPACE, "datahub-multi-tier"),
        os.path.join(WORKSPACE, "dataHub_三级体系"),
        _LEGACY_REPO,
        _cp("/opt/coze/.fuse_drive/dataHub_三级体系"),
    ]
    try:
        for name in sorted(os.listdir(WORKSPACE)):
            cands.append(os.path.join(WORKSPACE, name, "_dh"))
    except OSError:
        pass
    for p in cands:
        if ok(p):
            return p
    return env or _LEGACY_REPO


EDB_REPO = _find_edb_repo()

_READY = False


def _ensure():
    """把 DataHub 仓库加入 sys.path 并校验 EDB 可用。"""
    global _READY
    if _READY:
        return
    if not os.path.isdir(os.path.join(EDB_REPO, "data_hub")):
        raise RuntimeError(f"EDB 仓库不存在：{EDB_REPO}（可用 EDB_REPO 指定）")
    if EDB_REPO not in sys.path:
        sys.path.insert(0, EDB_REPO)
    for p in (os.path.join(WORKSPACE, "py_deps"),):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    from data_hub.edb import store  # noqa: F401
    _READY = True


def store_mod():
    _ensure()
    from data_hub.edb import store
    return store


def api_mod():
    _ensure()
    from data_hub.edb import api
    return api


def update(codes=None, force=False):
    """调用 EDB 统一刷新入口；失败不抛出（由各序列自身状态标记），返回汇总 dict。

    默认不联网：EDB 的刷新由 run_monthly 的独立刷新步骤统一触发（EDB_UPDATE=1），
    避免取数脚本各自重复抓取拖慢整条链路。
    """
    if os.environ.get("EDB_UPDATE") != "1":
        return {"refreshed": [], "failed": {}, "skipped": "EDB_UPDATE!=1（读库模式）"}
    try:
        return api_mod().refresh(codes, force=force)
    except Exception as e:  # 网络/依赖异常不应中断报告生成
        return {"error": str(e)[:200], "refreshed": [], "failed": {}, "skipped": []}


def series(code):
    """读单条序列，返回 pandas.Series(index='YYYY-MM-DD', value)，按日期升序。"""
    st = store_mod()
    with st.connect() as conn:
        rows = conn.execute(
            "SELECT date, value FROM edb_points WHERE series_id=? ORDER BY date", (code,)).fetchall()
    if not rows:
        return pd.Series(dtype="float64")
    return pd.Series({r[0]: float(r[1]) for r in rows}, dtype="float64")


def latest(code):
    """返回 (date, value)；无数据返回 (None, None)。"""
    s = series(code)
    if s.empty:
        return None, None
    return s.index[-1], float(s.iloc[-1])


def latest_n(code, n=2):
    """返回最近 n 期 [(date, value)]，按日期降序。"""
    s = series(code)
    if s.empty:
        return []
    out = [(d, float(v)) for d, v in s.items()]
    return out[::-1][:n]


def month_series(code, limit=36):
    """返回近 limit 期的 [(月标签 'YYYY-MM', value)]，按时间降序。"""
    s = series(code)
    if s.empty:
        return []
    out = {d[:7]: float(v) for d, v in s.items()}
    return sorted(out.items(), reverse=True)[:limit]


def doc(doc_id):
    """读发布稿元信息 {title,url,extra}。"""
    try:
        return store_mod().load_doc(doc_id)
    except Exception:
        return {}


def month_stats(code):
    """日频序列 -> 上月月均/去年同期月均/同比、本月至今均/同比。"""
    import datetime as dt
    s = series(code)
    if s.empty:
        return {}
    d = pd.to_datetime(s.index)
    g = s.groupby(d.strftime("%Y-%m")).mean()
    today = dt.date.today()
    cur_m = today.strftime("%Y-%m")
    prev_m = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")

    def _yoy(m):
        y = str(int(m[:4]) - 1) + m[4:]
        if m in g.index and y in g.index and g[y]:
            return round((g[m] / g[y] - 1) * 100, 1)
        return None

    out = {"month": prev_m,
           "avg": round(float(g[prev_m]), 2) if prev_m in g.index else None,
           "base_avg": round(float(g.get(str(int(prev_m[:4]) - 1) + prev_m[4:])), 2)
           if (str(int(prev_m[:4]) - 1) + prev_m[4:]) in g.index else None,
           "yoy": _yoy(prev_m),
           "recent": {k: round(float(v), 2) for k, v in g.tail(6).items()}}
    if cur_m in g.index:
        out["cur_month"] = cur_m
        out["cur_avg"] = round(float(g[cur_m]), 2)
        out["cur_yoy"] = _yoy(cur_m)
    return out


def monthly_mom(code):
    """月频序列 -> (最新月均值环比%, 上月值, 上上月值)。"""
    ms = month_series(code, limit=3)
    if len(ms) < 2:
        return None, None, None
    cur, prev = ms[0][1], ms[1][1]
    mom = round((cur / prev - 1) * 100, 2) if prev else None
    return mom, cur, prev


# 报告链路需要的全部序列（用于统一刷新；顺序无关）
REPORT_CODES = [
    "CN.PPI.YOY", "CN.PPI.MOM", "CN.PPI.CUM", "CN.PPIRM.YOY", "CN.PPIRM.MOM",
    "CN.PPI.PROD.YOY", "CN.PPI.PROD.MOM", "CN.PPI.LIVE.YOY", "CN.PPI.LIVE.MOM",
    "CN.PPI.MINING.YOY", "CN.PPI.MINING.MOM", "CN.PPI.RAW.YOY", "CN.PPI.RAW.MOM",
    "CN.PPI.PROC.YOY", "CN.PPI.PROC.MOM", "CN.PPI.FOOD.YOY", "CN.PPI.CLOTH.YOY",
    "CN.PPI.DAILY.YOY", "CN.PPI.DURABLE.YOY",
    "CN.PPIRM.FUEL.YOY", "CN.PPIRM.FERROUS.YOY", "CN.PPIRM.NONFERROUS.YOY", "CN.PPIRM.CHEM.YOY",
    "CN.PPI.IND.COAL.YOY", "CN.PPI.IND.OILGAS.YOY", "CN.PPI.IND.FUELPROC.YOY",
    "CN.PPI.IND.FERROUS.YOY", "CN.PPI.IND.NONFERROUS.YOY", "CN.PPI.IND.FERROUS.MINING.YOY",
    "CN.PPI.IND.CHEM.YOY", "CN.PPI.IND.NONMETAL.YOY",
    "CN.PPI.PULL.PROD.YOY", "CN.PPI.PULL.LIVE.YOY", "CN.PPI.PREV.MOM", "CN.PPI.PREV.YOY",
    "CN.PPI.UPSIDE9.PTS", "CN.PPI.DOWNSIDE5.PTS", "CN.PPI.MOM.UP6.PTS",
    "CN.PMI.MFG", "CN.PMI.PROD", "CN.PMI.NEW.ORDER", "CN.PMI.NEW.EXPORT",
    "CN.PMI.INPUT.PRICE", "CN.PMI.OUTPUT.PRICE", "CN.PMI.SVC.NEW.ORDER", "CN.PMI.CONS.NEW.ORDER",
    "CN.PROFIT.CUM.YOY", "CN.PROFIT.MONTH.YOY", "CN.PROFIT.REVENUE.AMT",
    "CN.PROFIT.REVENUE.YOY", "CN.PROFIT.AMT",
    "CN.PROFIT.IND.计算机、通信和其他电子设备制造业", "CN.PROFIT.IND.有色金属冶炼和压延加工业",
    "CN.PROFIT.IND.煤炭开采和洗选业", "CN.PROFIT.IND.汽车制造业", "CN.PROFIT.IND.黑色金属冶炼和压延加工业",
    "CN.EXP.YOY", "CN.EXP.CUM.YOY", "CN.IMP.YOY", "CN.IMP.CUM.YOY", "CN.EXP.AMT",
    "CN.M1.YOY", "CN.M2.YOY",
    "GLOBAL.BRENT", "GLOBAL.LME.CU", "GLOBAL.LME.AL", "CN.REBAR.SPOT", "CN.REBAR.FUT",
    "CN.COMMODITY.CCPI",
]