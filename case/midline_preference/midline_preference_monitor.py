#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中线偏好监控整合脚本 v1.0
==================
整合 CFC结构因子（6个池子） + 期权SKEW/GEX（6个标的）
统一推送到飞书「AI股票消息」群

设计原则:
- 纯Python零LLM依赖
- 数据宁慢勿空，绝不造假
- Tushare串行调用，间隔≥0.5秒
- 无缓存，全部实时获取
- 失败时自动降级到akshare备用源
- 推送飞书交互式卡片，失败自动降级纯文本
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


import sys

# ---- 统一配置（DataHub config / notify；真实值在本机 config.local.json，不入库）----
def _dh_secret(_name, _default=""):
    try:
        from data_hub import get_secret as _gs
        return _gs(_name, _default)
    except Exception:
        return _default


def _dh_cfg(_key, _default=None):
    try:
        from data_hub import cfg as _cfg
        return _cfg(_key, _default)
    except Exception:
        return _default


def _dh_tushare(_default=""):
    try:
        from data_hub import get_tushare_token as _gt
        return _gt() or _default
    except Exception:
        return _default
# ---------------------------------------------------------------------------
import os
import json
import asyncio
import time
import socket
import warnings
import threading
import contextlib
import subprocess
from datetime import datetime
# DataHub 根（唯一口径见 case/_lib/hub_root.py，与 datahub_path.py 一致）
def _hub_root_path(_f=__file__):
    _d = os.path.dirname(os.path.abspath(_f))
    for _ in range(6):
        _lib = os.path.join(_d, "_lib")
        if os.path.isfile(os.path.join(_lib, "hub_root.py")):
            if _lib not in sys.path:
                sys.path.insert(0, _lib)
            from hub_root import hub_root
            return hub_root(_d)
        _d = os.path.dirname(_d)
    return _cp('/opt/coze/.fuse_drive/dataHub_三级体系')

_HUB_ROOT = _hub_root_path()


warnings.filterwarnings('ignore')
socket.setdefaulttimeout(20)

# ============ 日志自落盘 (Tee) ============
# 解决 fail_alert.py capture_output=True 导致 stdout 不进日志文件的问题
# 脚本入口即 tee，逐行 flush，即使被 kill 也能保留已输出内容
class _TeeWriter:
    """同时写原 stdout/stderr 和日志文件，每次 write 后立即 flush"""
    def __init__(self, filepath, original):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self.file = open(filepath, 'a', encoding='utf-8')
        self.original = original
    
    def write(self, data):
        try:
            self.original.write(data)
        except Exception:
            pass
        try:
            self.file.write(data)
            self.file.flush()
        except Exception:
            pass
    
    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass
        try:
            self.file.flush()
        except Exception:
            pass
    
    def close(self):
        try:
            self.file.close()
        except Exception:
            pass
    
    def isatty(self):
        return False
    
    def fileno(self):
        return self.original.fileno()

_log_tee_handles = []  # 全局 tee 句柄，供 _cleanup_log_tee() 关闭

def _setup_log_tee():
    """在脚本入口设置日志 tee，日志写入 ./codeact/output/midline_preference_YYYYMMDD.log"""
    global _log_tee_handles
    today = datetime.now().strftime('%Y%m%d')
    log_path = os.path.join('./codeact/output', f'midline_preference_{today}.log')
    os.makedirs('./codeact/output', exist_ok=True)
    
    tee_stdout = _TeeWriter(log_path, sys.stdout)
    tee_stderr = _TeeWriter(log_path, sys.stderr)
    sys.stdout = tee_stdout
    sys.stderr = tee_stderr
    _log_tee_handles = [tee_stdout, tee_stderr]
    
    print(f"📝 日志落盘: {log_path}", flush=True)
    return log_path


def _cleanup_log_tee():
    """关闭日志 tee，恢复原始 stdout/stderr"""
    global _log_tee_handles
    for h in _log_tee_handles:
        try:
            h.close()
        except Exception:
            pass
    _log_tee_handles = []


# ============ 阶段心跳系统 ============
# 守护线程监控各阶段耗时，超过阈值（默认10分钟）即推飞书私聊进度通知
_stage_state = {
    'current': '初始化',
    'start_time': 0.0,
    'notified_nodes': set(),  # 已通知的 (阶段名, 节点序号) 集合
}
_stage_lock = threading.Lock()
_heartbeat_thread = None
_heartbeat_stop = threading.Event()

# 可通过环境变量调整（验证时用 10 秒代替阈值）
HEARTBEAT_CHECK_INTERVAL = int(os.environ.get('MIDLINE_HEARTBEAT_INTERVAL', '30'))
# 2026-11-25：原来阈值 600 秒是「进度播报」——超 10 分钟就推一条「当前阶段：缓存预热」，
# 正常全流程 29 分钟会连推 2~3 条中间态消息，纯打扰。改成「卡死告警」语义：
# 阈值提到 1800 秒（超过正常全流程），每个阶段最多告警 1 次，文案明确是异常而非进度。
HEARTBEAT_THRESHOLD = int(os.environ.get('MIDLINE_HEARTBEAT_THRESHOLD', '1800'))
HEARTBEAT_USER_ID = _dh_secret('FEISHU_PRIVATE_USER_ID')

# lark-cli 环境变量（cron 环境需要显式注入）
_LARK_CLI_ENV = {
    **os.environ,
    'LARKSUITE_CLI_CONFIG_DIR': _cp('/app/data/所有对话/主对话/.feishu_cli'),
    'LARKSUITE_CLI_DATA_DIR': _cp('/app/data/所有对话/主对话/.feishu_cli'),
    'LARKSUITE_CLI_LOG_DIR': '/tmp',
    'LARK_CLI_NO_PROXY': '1',
}


def _send_heartbeat_dm(text):
    """通过 lark-cli 发送飞书私聊文本消息（不依赖 feishu_push 模块）"""
    cmd = [
        'lark-cli', 'im', '+messages-send',
        '--user-id', HEARTBEAT_USER_ID,
        '--text', text,
        '--as', 'bot',
    ]
    try:
        result = subprocess.run(
            cmd, env=_LARK_CLI_ENV, capture_output=True, text=True, timeout=30
        )
        ok = result.returncode == 0 and '"ok":' in result.stdout and 'true' in result.stdout
        if ok:
            print(f"  💓 心跳私聊发送成功", flush=True)
        else:
            print(f"  ⚠️ 心跳私聊发送失败: rc={result.returncode}, stdout={result.stdout[:200]}", flush=True)
        return ok
    except Exception as e:
        print(f"  ⚠️ 心跳私聊异常: {e}", flush=True)
        return False


def _heartbeat_worker():
    """后台守护线程：每 HEARTBEAT_CHECK_INTERVAL 秒检查当前阶段耗时"""
    while not _heartbeat_stop.is_set():
        _heartbeat_stop.wait(HEARTBEAT_CHECK_INTERVAL)
        if _heartbeat_stop.is_set():
            break
        try:
            with _stage_lock:
                current = _stage_state['current']
                start = _stage_state['start_time']
                if start <= 0:
                    continue
                elapsed_sec = time.time() - start
                if elapsed_sec < HEARTBEAT_THRESHOLD:
                    continue
                elapsed_min = elapsed_sec / 60.0
                threshold_min = HEARTBEAT_THRESHOLD / 60.0
                # 每个阶段最多告警 1 次，不再按阈值节点重复播报
                key = (current, 1)
                if key in _stage_state['notified_nodes']:
                    continue
                _stage_state['notified_nodes'].add(key)
                msg = (
                    f"⚠️ 中线偏好监控 · 疑似卡住\n"
                    f"阶段：{current}\n"
                    f"已耗时：{int(elapsed_min)} 分钟（阈值 {int(threshold_min)} 分钟）\n"
                    f"这是异常告警，不是进度播报；任务仍在跑，若最终失败会另发失败通知。"
                )
                # 释放锁后发送，避免阻塞主流程
                threading.Thread(
                    target=_safe_send_heartbeat,
                    args=(msg,),
                    daemon=True,
                ).start()
        except Exception as e:
            print(f"  ⚠️ 心跳线程异常（不影响主流程）: {e}", flush=True)


def _safe_send_heartbeat(msg):
    """安全发送心跳，异常只打日志"""
    try:
        _send_heartbeat_dm(msg)
    except Exception as e:
        print(f"  ⚠️ 心跳发送最终失败: {e}", flush=True)


@contextlib.contextmanager
def stage(name):
    """阶段上下文管理器：更新心跳状态，打印进入/离开日志"""
    with _stage_lock:
        _stage_state['current'] = name
        _stage_state['start_time'] = time.time()
        _stage_state['notified_nodes'] = set()
    print(f"📍 进入阶段: {name}", flush=True)
    _update_running_stage(name)
    t0 = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - t0
        print(f"📍 离开阶段: {name} (耗时 {elapsed:.1f}秒)", flush=True)


def _enter_stage(name):
    """非上下文管理器版本的阶段入口（避免缩进变更）"""
    with _stage_lock:
        _stage_state['current'] = name
        _stage_state['start_time'] = time.time()
        _stage_state['notified_nodes'] = set()
    print(f"📍 进入阶段: {name}", flush=True)
    _update_running_stage(name)


def _exit_stage():
    """非上下文管理器版本的阶段出口"""
    with _stage_lock:
        name = _stage_state['current']
        elapsed = time.time() - _stage_state['start_time']
    print(f"📍 离开阶段: {name} (耗时 {elapsed:.1f}秒)", flush=True)


# ============ 崩溃检测（2026-11-25 新增）============
# 11-24 任务在「缓存预热」阶段被外部超时杀掉（SIGKILL），Python 的 except/finally
# 都不执行，于是既没产出结果也没发任何告警，静默失败一整天没人知道。
# 做法：启动时写运行标记，正常结束删除；下次启动若发现残留标记，说明上次异常中断，
# 立刻私聊告警并把「中断时的阶段」报出来。
_RUNNING_FLAG_PATH = None


def _update_running_stage(name):
    """把当前阶段写进运行标记，便于下次启动时告警「中断时阶段」"""
    p = _RUNNING_FLAG_PATH
    if not p:
        return
    try:
        with open(p, 'r', encoding='utf-8') as f:
            d = json.load(f)
        d['stage'] = name
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def _check_previous_crash(flag_path):
    """启动前检查上次是否异常中断（被 SIGKILL / 超时 kill 时不会执行 finally）"""
    try:
        if not os.path.exists(flag_path):
            return
        with open(flag_path, 'r', encoding='utf-8') as f:
            prev = json.load(f)
        msg = (
            f"⚠️ 中线偏好监控 · 上次运行异常中断\n"
            f"数据日：{prev.get('target_date', '?')}\n"
            f"启动于：{prev.get('start_at', '?')}\n"
            f"中断时阶段：{prev.get('stage', '?')}\n"
            f"该次没有产出结果、没有推送。本次（{datetime.now().strftime('%Y%m%d')}）已重新开始。"
        )
        print(f"\n{'!'*60}", flush=True)
        print(f"⚠️ 检测到上次异常中断: {prev}", flush=True)
        print(f"{'!'*60}\n", flush=True)
        _send_heartbeat_dm(msg)
    except Exception as e:
        print(f"  ⚠️ 崩溃检测异常（忽略）: {e}", flush=True)
    finally:
        try:
            os.remove(flag_path)
        except Exception:
            pass


def _mark_running(flag_path, target_date):
    """写运行标记（成功结束删除；残留即代表上次异常中断）"""
    global _RUNNING_FLAG_PATH
    _RUNNING_FLAG_PATH = flag_path
    try:
        os.makedirs(os.path.dirname(flag_path), exist_ok=True)
        with open(flag_path, 'w', encoding='utf-8') as f:
            json.dump({
                'target_date': target_date,
                'start_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'pid': os.getpid(),
                'stage': '初始化',
            }, f, ensure_ascii=False)
    except Exception as e:
        print(f"  ⚠️ 运行标记写入失败（忽略）: {e}", flush=True)


def _clear_running():
    """清除运行标记（正常结束 / 已发失败通知时调用）"""
    p = _RUNNING_FLAG_PATH
    if not p:
        return
    try:
        os.remove(p)
    except Exception:
        pass


def _start_heartbeat():
    """启动心跳守护线程"""
    global _heartbeat_thread
    _heartbeat_stop.clear()
    _heartbeat_thread = threading.Thread(target=_heartbeat_worker, daemon=True)
    _heartbeat_thread.start()
    print(f"💓 心跳线程已启动 (检查间隔={HEARTBEAT_CHECK_INTERVAL}s, 阈值={HEARTBEAT_THRESHOLD}s)", flush=True)


def _stop_heartbeat():
    """停止心跳守护线程"""
    _heartbeat_stop.set()
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        _heartbeat_thread.join(timeout=5)
    print(f"💓 心跳线程已停止", flush=True)


# ============ CodeAct SDK ============
# crontab直接运行时没有CodeAct SDK服务（127.0.0.1:9999不存在），降级为None
try:
    from codeact_sdk import CodeActSDK
    sdk = CodeActSDK()
    _sdk_available = True
except Exception as _sdk_e:
    sdk = None
    _sdk_available = False
    print(f"  ℹ️  CodeAct SDK不可用: {_sdk_e}，将跳过submit_result（crontab模式正常）")


def safe_submit_result(**kwargs):
    """安全提交结果，crontab环境下静默跳过"""
    if not _sdk_available or sdk is None:
        return
    try:
        asyncio.run(sdk.submit_result(**kwargs))
    except Exception as e:
        print(f"  ⚠️  submit_result失败(非CodeAct环境正常): {e}")

# ============ 路径配置 ============
BASE_DIR = _cp('/Coze/Drive/蓁宝/所有对话/主对话')
CFC_DIR = os.path.join(BASE_DIR, 'CFC结构因子')
OPTION_DIR = os.path.join(BASE_DIR, '市场情绪监控', 'scripts')
FEISHU_DIR = BASE_DIR

# ============ data_hub 统一数据采集层 ============
DATAHUB_DIR = os.path.join(BASE_DIR, 'codeact', 'scripts')
if DATAHUB_DIR not in sys.path:
    sys.path.insert(0, DATAHUB_DIR)
# 2026-11-25: /Coze/Drive/dataHub_三级体系 在云盘根、本会话不可达，改为探测式挂载
# 真源按优先级：$DATAHUB_ROOT > .fuse_drive 视图 > 云盘根视图
for _p in (os.environ.get('DATAHUB_ROOT', ''),
           _HUB_ROOT,
           _cp('/Coze/Drive/dataHub_三级体系')):
    try:
        if _p and os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)
    except Exception:
        pass

# 从 data_hub 导入需要的函数
try:
    from data_hub import get_market_daily, get_shibor
    _datahub_ready = True
except Exception as _e:
    _datahub_ready = False
    get_market_daily = None
    get_shibor = None
    print(f"  ⚠️  data_hub 加载失败: {_e}，将使用原有数据源")

# 添加路径
sys.path.insert(0, CFC_DIR)
sys.path.insert(0, OPTION_DIR)
sys.path.insert(0, FEISHU_DIR)

# ============ Tushare API 限速 monkey-patch ============
# 在导入任何使用tushare的模块之前，先包装pro_api返回的对象
# 确保每次API调用间隔≥0.5秒
import tushare as ts

_orig_pro_api = ts.pro_api
_ts_api_last_call = 0
TS_MIN_INTERVAL = 0.55  # 比要求的0.5秒多留余量


class _RateLimitedPro:
    """包装tushare pro对象，对所有API调用加间隔"""
    
    def __init__(self, real_pro):
        self._real_pro = real_pro
    
    def __getattr__(self, name):
        """拦截所有属性访问，对方法调用加sleep"""
        attr = getattr(self._real_pro, name)
        
        if not callable(attr):
            return attr
        
        # 方法调用：包装成带限速的版本
        def _wrapped_method(*args, **kwargs):
            global _ts_api_last_call
            now = time.time()
            elapsed = now - _ts_api_last_call
            if elapsed < TS_MIN_INTERVAL:
                time.sleep(TS_MIN_INTERVAL - elapsed)
            try:
                result = attr(*args, **kwargs)
            finally:
                _ts_api_last_call = time.time()
            return result
        
        return _wrapped_method


def _rate_limited_pro_api(token=None):
    """返回带限速包装的pro对象（支持带/不带token参数）"""
    if token:
        real_pro = _orig_pro_api(token)
    else:
        real_pro = _orig_pro_api()
    return _RateLimitedPro(real_pro)


# Monkey-patch: 替换ts.pro_api
ts.pro_api = _rate_limited_pro_api

# ============ 导入模块 ============
# CFC模块
from cfc_calculator_v2 import (
    get_trade_dates,
    get_index_components,
    get_top2000_stocks,
    calculate_all_factors_for_pool,
    warm_stock_cache,
    get_signal_cfc,
    get_signal_up50,
    get_signal_top5z,
    get_signal_cdi,
    # 2026-10-22 清理（P2-1）：interpret_w_skew / interpret_w_kurt 全文 0 调用，已移除导入
)
# 2026-11-19: 注入交易日历到 cfc_calculator_v2 的缓存模块，避免 get_stock_data 退化为自然日
from cfc_calculator_v2 import _set_trade_dates

# 期权监控模块
from option_monitor import (
    OPTION_TARGETS,
    get_spot_price,
    get_option_contracts,
    calc_skew,
    calc_gex,
    calc_basis,
)

# 飞书推送
from feishu_push import push_card_to_feishu, push_to_feishu
from feishu_card_utils import (
    build_card_header,
    build_section_header,
    build_hr,
    build_note,
    build_markdown,
    build_data_table,
    # 2026-10-22 清理（P2-1）：build_metric_row 全文 0 调用，已移除导入
    build_collapsible,
    wrap_card,
)

# ============ Tushare API 间隔控制 ============
# 2026-10-22 修复（A2）：原来这里是第二层限速，与上面 _RateLimitedPro 的 monkey-patch
# 叠加生效（两层各 0.55s → 实际间隔被放大，整体变慢）。
# 现在限速**只由 _RateLimitedPro 一层负责**，safe_api_sleep 保留为空操作以兼容原有调用点。
def safe_api_sleep():
    """已废弃：限速统一由 _RateLimitedPro 负责，此函数保留为空操作（兼容调用点）。"""
    return


# ============ CFC数据获取 ============
# 2026-11-19 修复：接线 warm_stock_cache 预热 + 单池异常隔离 + 分段计时
_POOL_CONFIG = [
    ("沪深300",     "000300.SH"),
    ("中证500",     "000905.SH"),
    ("中证1000",    "000852.SH"),
    ("创业板",      "399006.SZ"),
    ("科创板",      "000688.SH"),
]

def get_cfc_data(target_date):
    """获取6个池子的CFC数据
    
    2026-11-19 修复：
    1. 先取齐所有池成分股 → 一次性 warm_stock_cache 预热 → 再逐池计算因子
       （解决创业板/科创板触发103天全市场串行补拉导致40分钟超时）
    2. 每个池独立 try/except，失败标记⚠️不崩整体
    3. 分段计时日志
    """
    t_cfc_start = time.time()
    print(f"\n{'='*60}")
    print(f"[1/2] 计算CFC结构因子 - {target_date}")
    print(f"{'='*60}")
    
    # 获取交易日列表
    safe_api_sleep()
    trade_dates = get_trade_dates(320)
    
    # 2026-11-19: 注入交易日历到 cfc_calculator_v2，供 get_stock_data/daily_panel_cache 精确切片
    _set_trade_dates(trade_dates)
    
    if target_date not in trade_dates:
        print(f"❌ {target_date} 不是交易日")
        return {}, False
    
    # 检查数据可用性（data_hub统一采集层）
    data_available = False
    if _datahub_ready:
        try:
            check_df = get_market_daily(trade_date=target_date)
            data_available = bool(check_df is not None and not check_df.empty)
        except Exception as _e:
            print(f"  ⚠️ data_hub 检查数据可用性失败: {_e}")
        if not data_available:
            print(f"⚠️ {target_date} data_hub数据未发布（data_available=False）")
    else:
        print("⚠️ data_hub不可用，无法判断当日数据是否已发布（不再用 Tushare 直连兜底）")
    
    # ---- Step 1: 取齐所有池的成分股 ----
    _enter_stage("成分股获取")
    t_comp_start = time.time()
    pool_codes = {}
    for pool_name, index_code in _POOL_CONFIG:
        codes = get_index_components(index_code)
        if codes:
            pool_codes[pool_name] = codes
    
    # 全市场前2000（也参与预热并集）
    top2000_codes = get_top2000_stocks(trade_dates)
    if top2000_codes:
        pool_codes["全市场前2000"] = top2000_codes
    
    print(f"  ⏱️ [成分股获取] 耗时 {time.time() - t_comp_start:.1f}秒，{len(pool_codes)} 个池")
    _exit_stage()
    
    # ---- Step 2: 一次性预热缓存（并集 × 回看区间）----
    _enter_stage("缓存预热")
    t_warm_start = time.time()
    all_pool_lists = list(pool_codes.values())
    try:
        warm_stock_cache(all_pool_lists, trade_dates, target_date, lookback=150)
        print(f"  ⏱️ [缓存预热] 耗时 {time.time() - t_warm_start:.1f}秒")
    except Exception as warm_err:
        print(f"  ⚠️ [缓存预热] 失败（退化为各池各自取数）: {warm_err}")
        print(f"  ⏱️ [缓存预热] 耗时 {time.time() - t_warm_start:.1f}秒（失败）")
    
    _exit_stage()
    
    # ---- Step 3: 逐池计算因子（单池异常隔离）----
    _enter_stage("CFC因子计算(6池)")
    factors = {}
    failed_pools = []
    pool_order = ["沪深300", "中证500", "中证1000", "创业板", "科创板", "全市场前2000"]
    
    for pool_name in pool_order:
        codes = pool_codes.get(pool_name)
        if not codes:
            print(f"\n  ⚠️ {pool_name}: 成分股为空，跳过")
            failed_pools.append((pool_name, "成分股获取失败"))
            continue
        
        t_pool_start = time.time()
        try:
            safe_api_sleep()
            result = calculate_all_factors_for_pool(codes, trade_dates, target_date, pool_name)
            elapsed_pool = time.time() - t_pool_start
            if result:
                factors[pool_name] = result
                print(f"  ⏱️ [{pool_name}] 耗时 {elapsed_pool:.1f}秒 ✅")
            else:
                failed_pools.append((pool_name, "因子计算返回空"))
                print(f"  ⏱️ [{pool_name}] 耗时 {elapsed_pool:.1f}秒 ❌ 计算结果为空")
        except Exception as pool_err:
            elapsed_pool = time.time() - t_pool_start
            failed_pools.append((pool_name, str(pool_err)[:100]))
            print(f"  ⏱️ [{pool_name}] 耗时 {elapsed_pool:.1f}秒 ❌ 异常: {pool_err}")
    
    _exit_stage()
    
    t_cfc_total = time.time() - t_cfc_start
    print(f"\n✅ CFC计算完成，成功 {len(factors)}/6 个池子")
    if failed_pools:
        for pn, reason in failed_pools:
            print(f"  ⚠️ {pn}: {reason}")
    print(f"⏱️ [CFC总计] 耗时 {t_cfc_total:.1f}秒")
    
    # 记录失败池信息供后续报告标注
    get_cfc_data._failed_pools = failed_pools
    get_cfc_data._timings = {
        'component_fetch': time.time() - t_comp_start,
        'cache_warm': time.time() - t_warm_start,
        'total': t_cfc_total,
    }
    
    return factors, data_available


# ============ option_pcr 统一PCR模块 ============
# 同目录下的 option_pcr.py — 5日平滑、剔除当月、多ETF合并
# 与底部监测使用同一数据源，口径统一
try:
    from option_pcr import get_all_index_pcr, INDEX_ETF_MAP as _PCR_INDEX_MAP
    _option_pcr_ready = True
    print("✅ option_pcr 统一PCR模块加载成功")
except Exception as _e:
    _option_pcr_ready = False
    get_all_index_pcr = None
    print(f"⚠️ option_pcr 加载失败: {_e}，Vol PCR将保留原值")

# ETF期权标的名 → option_pcr 指数名 映射
# 股指期权（中证1000/MO）无对应ETF期权指数，不在映射中 → 保留 calc_skew 原始值
_TARGET_TO_PCR_INDEX = {
    '300ETF':   '沪深300',
    '500ETF':   '中证500',
    '创业板ETF': '创业板',
    '科创50ETF': '科创50',
}


# ============ 期权数据获取 ============
def get_option_data(target_date):
    """获取6个标的的期权SKEW+GEX数据"""
    print(f"\n{'='*60}")
    print(f"[2/2] 计算期权SKEW+GEX - {target_date}")
    print(f"{'='*60}")
    
    # 无风险利率（data_hub统一采集层）
    r = 0.03
    if _datahub_ready:
        try:
            shibor_data = get_shibor()
            if shibor_data and 'latest' in shibor_data:
                latest = shibor_data['latest']
                if '1Y' in latest:
                    r = float(latest['1Y']) / 100
        except Exception as e:
            print(f"  ⚠️ data_hub获取SHIBOR失败: {e}，使用默认3%")
    else:
        print("  ⚠️ data_hub不可用，使用默认无风险利率3%")
    print(f"无风险利率: {r*100:.2f}%\n")
    
    skew_results = {}
    gex_results = {}
    
    for i, target in enumerate(OPTION_TARGETS):
        name = target['name']
        code = target['code']
        exchange = target['exchange']
        opt_code = target['opt_code']
        underlying_code = target.get('underlying_code')
        
        print(f"\n[{i+1}/{len(OPTION_TARGETS)}] {name} ({code})")
        
        # 获取现货价格
        safe_api_sleep()
        spot = get_spot_price(code, exchange, target_date, underlying_code)
        if spot is None:
            print(f"  ⚠️ 无法获取现货价格，跳过")
            skew_results[name] = None
            gex_results[name] = None
            continue
        print(f"  现价: {spot:.3f}")
        
        # 获取期权合约
        safe_api_sleep()
        contracts = get_option_contracts(exchange, opt_code, target_date, etf_code=code)
        if not contracts:
            print(f"  ❌ 无有效合约数据")
            skew_results[name] = None
            gex_results[name] = None
            continue
        print(f"  有效合约: {len(contracts)}个")
        
        # 计算SKEW
        skew_data = calc_skew(contracts, spot, target_date, r)
        skew_results[name] = skew_data
        if skew_data:
            print(f"  SKEW: {skew_data['cboe_skew']:.2f} {skew_data['direction']}")
        
        # 计算GEX
        gex_data = calc_gex(contracts, spot, target_date, r)
        gex_results[name] = gex_data
        if gex_data:
            print(f"  GEX: {gex_data['total_gex_yi']:+.2f}亿 风险={gex_data['risk']}")

            # 2026-10-26 新增：全量重算flip（解决创业板ETF/中证1000在10档窗口看不到翻转点问题）
            gex_data['spot_price'] = spot  # 挂上现货价供后续使用
            try:
                recompute_result = recompute_gex_flip(contracts, spot, target_date, r)
                gex_data['flip_full'] = recompute_result.get('flip_full')
                gex_data['flip_all'] = recompute_result.get('flip_all', [])
                gex_data['dist_flip_pct'] = recompute_result.get('dist_flip_pct')
                gex_data['all_strikes'] = recompute_result.get('all_strikes', 0)
                # 2026-10-29：远端过零点 + 近端累计GEX符号（近端无翻转时用于风险说明）
                gex_data['flip_far'] = recompute_result.get('flip_far')
                gex_data['dist_far_pct'] = recompute_result.get('dist_far_pct')
                gex_data['near_gamma_sign'] = recompute_result.get('near_gamma_sign')
                gex_data['near_gamma_val'] = recompute_result.get('near_gamma_val')
                fp_val = recompute_result.get('flip_full')
                dist_val = recompute_result.get('dist_flip_pct')
                far_val = recompute_result.get('flip_far')
                far_dist = recompute_result.get('dist_far_pct')
                fp_str = f"{fp_val:.2f}" if fp_val is not None else ("无翻转" if recompute_result.get('flip_all') is not None and len(recompute_result.get('flip_all', [])) == 0 else "无近端翻转")
                dist_str = f"{dist_val:+.2f}%" if dist_val is not None else "—"
                far_str = f" 远端{far_val:.2f}({far_dist:+.0f}%)" if far_val is not None else ""
                print(f"  全量Flip: {fp_str} (全量{recompute_result.get('all_strikes',0)}档, 距Flip: {dist_str}){far_str} 近端Gamma={recompute_result.get('near_gamma_sign')}")
                if recompute_result.get('error'):
                    print(f"  ⚠️ 全量Flip重算: {recompute_result['error']}")
            except Exception as recompute_err:
                print(f"  ⚠️ 全量Flip重算异常: {recompute_err}")
                gex_data['flip_full'] = None
                gex_data['dist_flip_pct'] = None
    
    # 升贴水
    print(f"\n计算IF/IC/IM升贴水...")
    safe_api_sleep()
    basis_rows, _missing = calc_basis(target_date)
    
    # ============ 统一PCR替换（option_pcr）============
    # 用 option_pcr 的5日平滑+剔除当月+多ETF合并PCR 替换 calc_skew 中的单日PCR
    # 股指期权标的（中证1000/MO）无对应ETF期权指数 → 保留原 vol_pcr
    pcr_override_info = {}  # name → warning（记录降级信息）
    if _option_pcr_ready and get_all_index_pcr:
        print(f"\n获取统一PCR数据（5日平滑+剔除当月）...")
        try:
            all_pcr = get_all_index_pcr(target_date, smooth_days=5, min_days=3, exclude_current_month=True)
            pcr_source = all_pcr.get('_meta', {}).get('source', 'none')
            pcr_fallback = all_pcr.get('_meta', {}).get('fallback', False)
            print(f"  数据源: {pcr_source}, 降级: {pcr_fallback}")
            
            for target_name, pcr_index_name in _TARGET_TO_PCR_INDEX.items():
                if target_name not in skew_results or skew_results[target_name] is None:
                    continue
                pcr_data = all_pcr.get(pcr_index_name, {})
                unified_vol_pcr = pcr_data.get('vol_pcr')
                if unified_vol_pcr is not None:
                    old_pcr = skew_results[target_name].get('vol_pcr')
                    skew_results[target_name]['vol_pcr'] = unified_vol_pcr
                    skew_results[target_name]['vol_pcr_source'] = pcr_data.get('source', 'unknown')
                    skew_results[target_name]['vol_pcr_unified'] = True
                    warning = pcr_data.get('warning')
                    if warning:
                        pcr_override_info[target_name] = warning
                    print(f"  ✅ {target_name}: PCR {old_pcr:.3f} → {unified_vol_pcr:.3f} ({pcr_data.get('source','')}, {pcr_data.get('smooth_days',0)}日平滑)")
                else:
                    # 统一PCR为None，保留原值并标注
                    skew_results[target_name]['vol_pcr_unified'] = False
                    pcr_override_info[target_name] = '⚠️统一PCR数据缺失，保留原值'
                    print(f"  ⚠️ {target_name}: 统一PCR为None，保留原值 {skew_results[target_name].get('vol_pcr', 0):.3f}")
        except Exception as e:
            print(f"  ❌ 获取统一PCR失败，全部保留原值: {e}")
    elif not _option_pcr_ready:
        print(f"\n⚠️ option_pcr模块不可用，Vol PCR保留calc_skew原值")
    
    skew_ok = sum(1 for v in skew_results.values() if v)
    pcr_ok = sum(1 for v in skew_results.values() if v and v.get('vol_pcr_unified', False))
    print(f"\n✅ 期权计算完成，SKEW成功 {skew_ok}/{len(OPTION_TARGETS)} 个标的，统一PCR替换 {pcr_ok}/{skew_ok}")
    return skew_results, gex_results, basis_rows


# ============ 底部红线监测导入 ============
# 导入底部监测模块（用于在日报末尾追加底部红线监测结果）
# 路径：同目录下的 market_bottom_monitor.py
BOTTOM_MONITOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
if BOTTOM_MONITOR_DIR not in sys.path:
    sys.path.insert(0, BOTTOM_MONITOR_DIR)

try:
    from market_bottom_monitor import run_bottom_monitor, send_bottom_alert_email
    _bottom_monitor_ready = True
    print("✅ 杨康平底部判断模块加载成功")
except Exception as _e:
    _bottom_monitor_ready = False
    run_bottom_monitor = None
    send_bottom_alert_email = None
    print(f"⚠️ 杨康平底部判断模块加载失败: {_e}，日报将不包含底部判断")


def _find_note_anchor(elements):
    """找到卡片尾部 note 元素之前的位置，作为插入点。

    2026-10-22 新增（A9）：替代原来的 "len(elements)-2" 位置魔法。
    卡片结构约定：... → hr → note(数据来源/生成时间)。插入点 = 该 hr 的下标。
    找不到 note 时退化为"插到最后一个元素之前"。
    """
    try:
        for i in range(len(elements) - 1, -1, -1):
            el = elements[i]
            if not isinstance(el, dict):
                continue
            tag = el.get('tag') or el.get('type') or ''
            if str(tag).lower() == 'note':
                # note 前若紧跟 hr，则插在这个 hr 的位置（保持 hr / 内容 / hr / note 的层次）
                if i - 1 >= 0 and isinstance(elements[i - 1], dict) and elements[i - 1].get('tag') == 'hr':
                    return i - 1
                return i
    except Exception:
        pass
    return max(0, len(elements) - 1)


# ============ 底部监测结果追加到卡片 ============
def build_bottom_monitor_section(bottom_result):
    """将底部红线监测结果构建为飞书卡片元素（追加到中线日报末尾）
    
    参数:
        bottom_result: run_bottom_monitor() 返回的 dict
    返回:
        list: 飞书卡片元素列表（可追加到 elements 中）
    """
    if not bottom_result or bottom_result.get('skipped', False):
        return [build_markdown("📊 **杨康平底部判断**: ⏭️ 今日已推送，跳过")]
    
    elements = []
    triggered_count = bottom_result.get('triggered_count', 0)
    total = bottom_result.get('total', 12)
    trading_date = bottom_result.get('trading_date', '')
    position = bottom_result.get('position', '')
    advice = bottom_result.get('advice', '')
    prediction = bottom_result.get('prediction', '')
    
    # 根据红线数量决定颜色和emoji
    if triggered_count >= 6:
        color_emoji = "🔴"
    elif triggered_count >= 4:
        color_emoji = "🟡"
    else:
        color_emoji = "🟢"
    
    # 红线计数摘要
    summary_line = f"**{color_emoji} 红线触发**: {triggered_count}/{total}"
    summary_line += f" | **仓位建议**: {position}"
    elements.append(build_markdown(summary_line))
    
    # 触发项列表（红线项用红色标记）
    indicators = bottom_result.get('indicators', {})
    indicator_names = [
        ('中证1000VIX', '①'),
        ('融资余额', '②'),
        ('IM贴水', '③'),
        ('大阴线', '④'),
        ('成交量', '⑤'),
        ('大盘小盘比', '⑥'),
        ('加息/宏观CPI', '⑦'),
        ('IM点位支撑', '⑧'),
        ('科创50支撑', '⑨'),
        ('韭圈情绪', '⑩'),
        ('PCR_3of4', '⑪'),
        ('科技板块', '⑫'),
    ]
    
    triggered_items = []
    non_triggered_items = []
    
    for key, num in indicator_names:
        data = indicators.get(key, {})
        if data.get('triggered', False):
            triggered_items.append(f"  {num} **{key}** 🔴 {data.get('info', '')}")
        else:
            non_triggered_items.append(f"  {num} {key} ❌ {data.get('info', '')}")
    
    # 构建触发项和未触发项的Markdown
    card_lines = []
    if triggered_items:
        card_lines.append("**触发项：**")
        card_lines.extend(triggered_items)
        card_lines.append("")
    card_lines.append("**未触发项：**")
    card_lines.extend(non_triggered_items)
    
    # 策略建议
    card_lines.append("")
    card_lines.append(f"**💡 建议**: {advice}")
    card_lines.append(f"**🔮 展望**: {prediction}")
    
    # 组装为折叠模块
    card_md = "\n".join(card_lines)
    
    # 如果内容太长就截断
    CARD_MD_MAX = 2600
    if len(card_md) > CARD_MD_MAX:
        cut = card_md.rfind('\n', 0, CARD_MD_MAX)
        if cut <= 0:
            cut = CARD_MD_MAX
        card_md = card_md[:cut] + "\n\n⚠️ 内容截断"
    
    elements.append(build_collapsible(
        f"📊 杨康平底部判断 [{triggered_count}/{total}] {trading_date}",
        [card_md],
        default_expanded=False,
    ))
    
    return elements


# ============ 中线偏好判断 ============
def assess_midline_preference(cfc_factors, skew_data, gex_data):
    """综合判断中线偏好
    
    基于CFC结构因子 + 期权SKEW/GEX综合判断
    """
    signals = {
        'bullish': 0,  # 偏多信号数
        'bearish': 0,  # 偏空信号数
        'neutral': 0,  # 中性信号数
        'details': [],
    }
    
    # CFC维度
    pool_order = ["沪深300", "中证500", "中证1000", "创业板", "科创板", "全市场前2000"]
    cfc_bull = 0
    cfc_bear = 0
    for pool in pool_order:
        if pool not in cfc_factors or not cfc_factors[pool]:
            continue
        cfc = cfc_factors[pool].get('cfc')
        if cfc is None:
            continue
        sig = get_signal_cfc(cfc)
        if sig == "买入":
            cfc_bull += 1
            signals['details'].append(f"CFC-{pool}: 🟢买入 (CFC={cfc:.2f})")
        elif sig == "卖出":
            cfc_bear += 1
            signals['details'].append(f"CFC-{pool}: 🔴卖出 (CFC={cfc:.2f})")
        else:
            signals['details'].append(f"CFC-{pool}: ⚖️中性 (CFC={cfc:.2f})")
    
    if cfc_bull > cfc_bear:
        signals['bullish'] += 1
    elif cfc_bear > cfc_bull:
        signals['bearish'] += 1
    else:
        signals['neutral'] += 1
    
    # SKEW维度
    skew_bull = 0
    skew_bear = 0
    for name, d in skew_data.items():
        if not d:
            continue
        direction = d.get('direction', '')
        if '偏多' in direction or '🟢' in direction:
            skew_bull += 1
        elif '偏空' in direction or '🔴' in direction:
            skew_bear += 1
    
    if skew_bull > skew_bear:
        signals['bullish'] += 1
        signals['details'].append(f"SKEW: 🟢偏多 (偏多{skew_bull}个 vs 偏空{skew_bear}个)")
    elif skew_bear > skew_bull:
        signals['bearish'] += 1
        signals['details'].append(f"SKEW: 🔴偏空 (偏多{skew_bull}个 vs 偏空{skew_bear}个)")
    else:
        signals['neutral'] += 1
        signals['details'].append(f"SKEW: ⚖️中性 (偏多{skew_bull}个 vs 偏空{skew_bear}个)")
    
    # GEX维度
    gex_low_risk = 0
    gex_high_risk = 0
    for name, d in gex_data.items():
        if not d:
            continue
        risk = d.get('risk', '')
        if '低' in risk or '🟢' in risk:
            gex_low_risk += 1
        elif '高' in risk or '🔴' in risk:
            gex_high_risk += 1
    
    if gex_low_risk > gex_high_risk:
        signals['bullish'] += 1
        signals['details'].append(f"GEX: 🟢低风险占优 (低风险{gex_low_risk}个 vs 高风险{gex_high_risk}个)")
    elif gex_high_risk > gex_low_risk:
        signals['bearish'] += 1
        signals['details'].append(f"GEX: 🔴高风险占优 (低风险{gex_low_risk}个 vs 高风险{gex_high_risk}个)")
    else:
        signals['neutral'] += 1
        signals['details'].append(f"GEX: ⚖️中性 (低风险{gex_low_risk}个 vs 高风险{gex_high_risk}个)")
    
    # 综合判断
    total = signals['bullish'] + signals['bearish'] + signals['neutral']
    if total == 0:
        overall = '⚪数据不足'
        color = 'grey'
    elif signals['bullish'] >= 2:
        overall = '🟢偏多'
        color = 'green'
    elif signals['bearish'] >= 2:
        overall = '🔴偏空'
        color = 'red'
    else:
        overall = '🟡中性'
        color = 'yellow'
    
    return {
        'overall': overall,
        'color': color,
        'bullish': signals['bullish'],
        'bearish': signals['bearish'],
        'neutral': signals['neutral'],
        'details': signals['details'],
    }


# ============ GEX flip_point 兜底计算 ============
def _calc_flip_from_details(gex_dict):
    """从 key_details 的 cumulative_gex 推导 flip point（零点线性插值）。

    2026-10-22 新增（P0-5）：
    option_monitor.calc_gex() 目前返回的 flip_point 恒为 None（今天 6 个标的全是 None），
    而 cumulative_gex 曲线明明有符号翻转（如 300ETF 4.10:-0.25 → 4.15:+0.60）。
    这里做兜底：接口没给就用累计 GEX 的零点插值补上，避免卡片这一列永远是 N/A。
    """
    fp = gex_dict.get('flip_point')
    if fp is not None:
        try:
            return f"{float(fp):.2f}"
        except Exception:
            pass

    details = gex_dict.get('key_details') or []
    pts = []
    for d in details:
        try:
            pts.append((float(d.get('strike')), float(d.get('cumulative_gex'))))
        except Exception:
            continue
    if len(pts) < 2:
        return '无数据'
    pts.sort(key=lambda x: x[0])

    for i in range(1, len(pts)):
        s0, c0 = pts[i - 1]
        s1, c1 = pts[i]
        if c0 == 0:
            return f"{s0:.2f}"
        if (c0 < 0) != (c1 < 0):
            # 线性插值求零点
            denom = (c1 - c0)
            if denom == 0:
                return f"{s0:.2f}"
            return f"{s0 + (0 - c0) * (s1 - s0) / denom:.2f}"

    # 全区间同号 → 没有翻转点，明确说明而不是 N/A
    first_c, last_c = pts[0][1], pts[-1][1]
    return '无翻转(全正)' if first_c > 0 else '无翻转(全负)'


def _fmt_flip(d):
    """卡片/文本统一的 flip 显示"""
    return _calc_flip_from_details(d)


def _flip_value_numeric(gex_dict):
    """flip_point 的数值版（供落盘回写）。

    2026-10-23 新增：原 option_monitor.calc_gex() 的 flip_point 恒为 None，
    展示层用 _calc_flip_from_details 能算出 2.91，但落盘 JSON 该字段仍 None，不自洽。
    本函数返回：
      float  —— 累计 GEX 过零的翻转点位（线性插值）
      None   —— 全区间同号（无翻转）或数据不足
    """
    details = (gex_dict or {}).get('key_details') or []
    pts = []
    for d in details:
        try:
            pts.append((float(d.get('strike')), float(d.get('cumulative_gex'))))
        except Exception:
            continue
    if len(pts) < 2:
        return None
    pts.sort(key=lambda x: x[0])
    for i in range(1, len(pts)):
        s0, c0 = pts[i - 1]
        s1, c1 = pts[i]
        if c0 == 0:
            return round(s0, 4)
        if (c0 < 0) != (c1 < 0):
            denom = c1 - c0
            if denom == 0:
                return round(s0, 4)
            return round(s0 + (0 - c0) * (s1 - s0) / denom, 4)
    return None


def _enrich_gex_flip_points(gex_data):
    """落盘前给每个标的回写 flip_point 数值字段，使 JSON 与卡片展示一致。

    2026-10-26 改造：优先使用全量重算 flip_full（recompute_gex_flip 结果），
    回退到旧版10档兜底。
    """
    import copy
    out = copy.deepcopy(gex_data or {})
    for name, v in out.items():
        if v:
            if v.get('flip_full') is not None:
                v['flip_point'] = v['flip_full']
            else:
                v['flip_point'] = _flip_value_numeric(v)
    return out


# ============ 全量GEX曲线翻转点计算（2026-10-26 新增）============
def recompute_gex_flip(contracts, spot, target_date, r=0.03, near_range=None):
    """全量逐档累计GEX，检测过零点，返回最近距现价的翻转点。

    与 option_monitor.calc_gex 口径完全一致（T<3天剔除、K<=0剔除、iv=None时gamma=0），
    区别：不截断strike（使用全部档位），双向检测过零点。

    2026-10-29 变更：翻转点只在现价 ±near_range（默认 20%）内认可。
    超出范围的过零点属远端深度虚值堆积（如次月合约 2.1~2.55 档），对中线无实战意义，
    改为单独回传 flip_far/dist_far_pct 仅作展示，不再当作有效 Flip Point 参与告警。

    返回:
        dict: {
            'flip_full': float or None,  # 近端有效翻转点（None=近端无过零点或无数据）
            'flip_all': list of float,   # 所有过零点
            'all_strikes': int,          # 全量strike数
            'dist_flip_pct': float or None,  # (flip-spot)/spot*100
            'flip_far': float or None,   # 远端最近过零点（仅展示）
            'dist_far_pct': float or None,
            'near_gamma_sign': str or None,  # 现价档累计GEX符号 positive/negative
            'near_gamma_val': float or None, # 现价档累计GEX（亿）
            'cumulative_points': list,   # [(strike, cumulative_gex), ...] 全量曲线
            'error': str or None,        # 错误信息
        }
    """
    import math
    from scipy.stats import norm as sp_norm

    if near_range is None:
        near_range = globals().get('FLIP_NEAR_RANGE', 0.20)

    def _bs_d1(S, K, T, r, sigma, q=0):
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return None
        try:
            return (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        except:
            return None

    def _bs_gamma_full(S, K, T, r, sigma, q=0):
        d1 = _bs_d1(S, K, T, r, sigma, q)
        if d1 is None:
            return None
        try:
            return math.exp(-q * T) * sp_norm.pdf(d1) / (S * sigma * math.sqrt(T))
        except:
            return None

    def _bs_iv_full(S, K, T, r, mkt_price, opt_type='call', q=0):
        from scipy.optimize import brentq as _brentq
        if T <= 0 or mkt_price <= 0:
            return None

        def _bs_call(S, K, T, r, sigma, q=0):
            d1 = _bs_d1(S, K, T, r, sigma, q)
            if d1 is None:
                return None
            d2 = d1 - sigma * math.sqrt(T)
            try:
                return S * math.exp(-q * T) * sp_norm.cdf(d1) - K * math.exp(-r * T) * sp_norm.cdf(d2)
            except:
                return None

        def _bs_put(S, K, T, r, sigma, q=0):
            d1 = _bs_d1(S, K, T, r, sigma, q)
            if d1 is None:
                return None
            d2 = d1 - sigma * math.sqrt(T)
            try:
                return K * math.exp(-r * T) * sp_norm.cdf(-d2) - S * math.exp(-q * T) * sp_norm.cdf(-d1)
            except:
                return None

        intrinsic = max(S - K, 0) if opt_type == 'call' else max(K - S, 0)
        if mkt_price < intrinsic:
            return None

        def obj(sigma):
            p = _bs_call(S, K, T, r, sigma, q) if opt_type == 'call' else _bs_put(S, K, T, r, sigma, q)
            return p - mkt_price if p is not None else 1e10

        try:
            iv = _brentq(obj, 0.001, 5.0, xtol=1e-6)
            return iv if 0.01 < iv < 3.0 else None
        except:
            return None

    result = {
        'flip_full': None,
        'flip_all': [],
        'all_strikes': 0,
        'dist_flip_pct': None,
        'flip_far': None,
        'dist_far_pct': None,
        'near_gamma_sign': None,
        'near_gamma_val': None,
        'cumulative_points': [],
        'error': None,
    }

    if not contracts or not spot or spot <= 0:
        result['error'] = '无合约或现货价无效'
        return result

    try:
        today = datetime.strptime(target_date, "%Y%m%d")
    except Exception as e:
        result['error'] = f'日期解析失败: {e}'
        return result

    # 按strike聚合（与 calc_gex 一致）
    strikes_data = {}
    for c in contracts:
        try:
            exp_str = c['maturity_date']
            if len(exp_str) < 8:
                continue
            exp_dt = datetime.strptime(exp_str[:8], "%Y%m%d")
            T = (exp_dt - today).days / 365.0
            if T < 3 / 365:
                continue

            K = c['exercise_price']
            if K <= 0:
                continue

            if K not in strikes_data:
                strikes_data[K] = {'call_oi': 0, 'put_oi': 0, 'call_gamma': None, 'put_gamma': None}

            oi = c['oi']
            mkt = c['close']
            iv = _bs_iv_full(spot, K, T, r, mkt, 'call' if c['call_put'] == 'C' else 'put')
            g = _bs_gamma_full(spot, K, T, r, iv) if iv else None

            if c['call_put'] == 'C':
                strikes_data[K]['call_oi'] += oi
                if g:
                    strikes_data[K]['call_gamma'] = g
            else:
                strikes_data[K]['put_oi'] += oi
                if g:
                    strikes_data[K]['put_gamma'] = g
        except:
            continue

    if not strikes_data:
        result['error'] = '无有效strike'
        return result

    # 逐档累计GEX（全量，不截断）
    sorted_strikes = sorted(strikes_data.keys())
    result['all_strikes'] = len(sorted_strikes)

    total_gex = 0
    cumulative_points = []
    for K in sorted_strikes:
        d = strikes_data[K]
        cg = d['call_gamma'] or 0
        pg = d['put_gamma'] or 0
        net = (d['call_oi'] * cg - d['put_oi'] * pg) * 100 * (spot ** 2)
        total_gex += net
        cumulative_points.append((K, total_gex))

    result['cumulative_points'] = cumulative_points

    # 双向检测过零点
    flip_candidates = []
    for i in range(1, len(cumulative_points)):
        s0, c0 = cumulative_points[i - 1]
        s1, c1 = cumulative_points[i]
        if c0 == 0:
            flip_candidates.append(s0)
        elif (c0 < 0) != (c1 < 0):
            # 线性插值
            denom = c1 - c0
            if denom != 0:
                fp = s0 + (0 - c0) * (s1 - s0) / denom
                flip_candidates.append(fp)

    result['flip_all'] = flip_candidates

    # 2026-10-29：区分"近端有效翻转"与"远端虚值堆积"
    near_lo, near_hi = spot * (1 - near_range), spot * (1 + near_range)
    near_cands = [f for f in flip_candidates if near_lo <= f <= near_hi]
    far_cands = [f for f in flip_candidates if not (near_lo <= f <= near_hi)]

    if near_cands:
        # 取距 spot 最近的那个
        best = min(near_cands, key=lambda x: abs(x - spot))
        result['flip_full'] = round(best, 4)
        result['dist_flip_pct'] = round((best - spot) / spot * 100, 2)
    else:
        # 近端无过零点：可能是全区间同号，也可能过零点都在远端
        result['flip_full'] = None
        result['dist_flip_pct'] = None

    if far_cands:
        far = min(far_cands, key=lambda x: abs(x - spot))
        result['flip_far'] = round(far, 4)
        result['dist_far_pct'] = round((far - spot) / spot * 100, 2)

    # 现价档累计GEX符号：无近端翻转时用来判断近端做市商净Gamma方向
    if cumulative_points:
        nearest = min(cumulative_points, key=lambda x: abs(x[0] - spot))
        result['near_gamma_sign'] = 'positive' if nearest[1] > 0 else 'negative'
        result['near_gamma_val'] = round(nearest[1] / 1e8, 2)

    return result


# ============ GEX历史库（2026-10-26 新增）============
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'output')
OUTPUT_DIR = os.path.normpath(OUTPUT_DIR)
GEX_HIST_FILE = os.path.join(OUTPUT_DIR, 'gex_history.json')


def load_gex_history():
    """加载GEX历史库，损坏时备份重建"""
    if not os.path.exists(GEX_HIST_FILE):
        return {}
    try:
        with open(GEX_HIST_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        return data
    except Exception as e:
        print(f"  ⚠️ GEX历史库读取失败，备份后重建: {e}")
        import shutil
        bak = GEX_HIST_FILE + f'.corrupt_{datetime.now().strftime("%Y%m%d")}'
        try:
            shutil.copy2(GEX_HIST_FILE, bak)
        except:
            pass
        return {}


def save_gex_history(history):
    """幂等保存GEX历史库"""
    try:
        os.makedirs(os.path.dirname(GEX_HIST_FILE), exist_ok=True)
        tmp = GEX_HIST_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp, GEX_HIST_FILE)
        return True
    except Exception as e:
        print(f"  ⚠️ GEX历史库保存失败: {e}")
        return False


def init_gex_history_from_existing(output_dir=OUTPUT_DIR):
    """从已有的 *_midline_monitor.json 回填 GEX 历史库（仅 total_gex_yi）"""
    import glob
    existing_files = sorted(glob.glob(os.path.join(output_dir, '*_midline_monitor.json')))
    if not existing_files:
        print("  ℹ️ 无已有 midline_monitor JSON 可回填")
        return

    history = load_gex_history()
    filled = 0
    for fpath in existing_files:
        basename = os.path.basename(fpath)
        date_str = basename.split('_')[0]  # YYYYMMDD
        if len(date_str) != 8 or not date_str.isdigit():
            continue
        if date_str in history:
            continue  # 已有，跳过
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            gex_data = data.get('gex_data', {})
            if not gex_data:
                continue
            entry = {}
            for name, v in gex_data.items():
                if not v:
                    continue
                entry[name] = {
                    'total_gex_yi': v.get('total_gex_yi'),
                    'flip_point': v.get('flip_point'),
                    'spot': None,  # 旧数据无 spot
                    'dist_flip_pct': None,
                }
            if entry:
                history[date_str] = entry
                filled += 1
        except Exception as e:
            print(f"  ⚠️ 回填 {basename} 失败: {e}")

    if filled:
        save_gex_history(history)
        print(f"  ✅ GEX历史库从已有JSON回填 {filled} 天")
    else:
        print("  ℹ️ GEX历史库无需回填（已有数据或无可用源）")


def get_gex_strikes_prev(gex_data, target_date):
    """从历史库取上一交易日的 all_strikes 映射。

    返回 (dict{标的名: 档数 or None}, 前一日日期 or None)
    """
    try:
        history = load_gex_history()
    except Exception:
        return {}, None
    prev_dates = sorted([d for d in history.keys() if d < target_date], reverse=True)
    if not prev_dates:
        return {}, None
    prev = history[prev_dates[0]] or {}
    return {n: (v or {}).get('all_strikes') for n, v in prev.items()}, prev_dates[0]


def build_gex_caliber_notes(gex_data, target_date):
    """生成 GEX 口径提示（合约集合变化 / 远端虚值主导 Flip）。

    2026-10-29 新增：大佬要求——当某个标的的 Flip 因换月跳变时，必须在表下明确标识，
    否则看起来像"只有它变了"，容易被当成数据错误。
    """
    notes = []
    prev_map, prev_date = get_gex_strikes_prev(gex_data, target_date)
    if not prev_map:
        return notes

    changed = []
    for name, d in (gex_data or {}).items():
        if not d:
            continue
        cur = d.get('all_strikes')
        pv = prev_map.get(name)
        if _is_num(cur) and _is_num(pv) and int(cur) - int(pv) >= 5:
            changed.append(f"{name} {int(pv)}→{int(cur)}档")
    if changed:
        pd = f"{prev_date[:4]}-{prev_date[4:6]}-{prev_date[6:]}" if prev_date else "前一日"
        notes.append(
            f"**⚠️ 期权合约集合变化**（{pd} → 今日）：" + "、".join(changed)
            + "。ETF期权当月合约到期后次月合约新挂，全链行权价档位增加，"
              "Flip Point 的可比性下降，跨日变化需按口径看待。"
        )

    far_dominated = []
    for name, d in (gex_data or {}).items():
        if d and d.get('flip_full') is None and _is_num(d.get('flip_far')):
            far_dominated.append(f"{name}(远端{d['flip_far']:.2f}，距现价{d.get('dist_far_pct', 0):+.0f}%)")
    if far_dominated:
        notes.append(
            "**🟠 Flip 由远端虚值档主导**：" + "、".join(far_dominated)
            + " —— 现价 ±20% 内无有效过零点，该 Flip 不参与告警，仅作参考；"
              "实际应看「近端负Gamma」这一列判断做市商近端敞口。"
        )
    return notes


def update_gex_history(gex_data, target_date):
    """写入今日 GEX 数据到历史库（幂等：同日覆盖）"""
    history = load_gex_history()
    entry = {}
    for name, v in (gex_data or {}).items():
        if not v:
            continue
        entry[name] = {
            'total_gex_yi': v.get('total_gex_yi'),
            'flip_point': v.get('flip_full') if v.get('flip_full') is not None else v.get('flip_point'),
            'spot': v.get('spot_price'),
            'dist_flip_pct': v.get('dist_flip_pct'),
            'all_strikes': v.get('all_strikes'),  # 2026-10-29：记录档数，供次日识别换月
        }
    history[target_date] = entry
    ok = save_gex_history(history)
    if ok:
        print(f"  ✅ GEX历史库已更新 {target_date}")
    return ok


def get_gex_delta(gex_data, target_date):
    """计算每个标的 GEX 较上一交易日的变化（亿）

    返回: dict {标的名: {'delta_yi': float or None, 'prev_date': str or None}}
    """
    history = load_gex_history()
    if not history:
        return {name: {'delta_yi': None, 'prev_date': None} for name in (gex_data or {})}

    # 找到今天之前的最近一个有记录的日期
    today_str = target_date
    prev_dates = sorted([d for d in history.keys() if d < today_str], reverse=True)

    deltas = {}
    for name, v in (gex_data or {}).items():
        if not v:
            deltas[name] = {'delta_yi': None, 'prev_date': None}
            continue

        today_yi = v.get('total_gex_yi')
        if today_yi is None:
            deltas[name] = {'delta_yi': None, 'prev_date': None}
            continue

        # 找最近一个有该标的 total_gex_yi 的历史日期
        prev_yi = None
        prev_date = None
        for pd in prev_dates:
            pd_entry = history[pd].get(name, {})
            pyi = pd_entry.get('total_gex_yi')
            if pyi is not None:
                prev_yi = pyi
                prev_date = pd
                break

        if prev_yi is not None:
            deltas[name] = {'delta_yi': round(today_yi - prev_yi, 2), 'prev_date': prev_date}
        else:
            deltas[name] = {'delta_yi': None, 'prev_date': None}

    return deltas


def _is_num(x):
    """是否为可用数值（兼容 Python float 与 numpy 标量，排除 NaN/None）。"""
    try:
        return x is not None and float(x) == float(x)  # NaN != NaN
    except (TypeError, ValueError):
        return False


# ============ up50 极值邮件告警（2026-10-29 新增）============
# 大佬口径（2026-10-29）：up50 >= 90% 为风险（股价普涨到50日线上方，中期见顶风险）；
# up50 <= 10% 为机会（普跌到50日线下方，超跌反弹机会）。触发即发邮件。
UP50_RISK_THRESHOLD = 0.90
UP50_OPP_THRESHOLD = 0.10
UP50_ALERT_STATE_FILE = os.path.join(OUTPUT_DIR, 'up50_alert_state.json')

# 翻转点有效范围：只在现价 ±20% 内认可翻转点（2026-10-29 新增）
# 背景：科创50ETF 10-29 距Flip 从 +13% 跳到 +77%，根因是当月合约到期后次月(12月)合约
# 纳入计算，行权价上限从 1.95 扩到 2.55，累计GEX 过零点被推到现价上方 47%~77% 的
# 深度虚值堆积区，对中线交易没有实战意义。远端过零点改为单独标注，不参与告警。
FLIP_NEAR_RANGE = 0.20


# ============ GEX翻转点接近邮件告警（2026-10-26 新增）============
GEX_ALERT_STATE_FILE = os.path.join(OUTPUT_DIR, 'gex_flip_alert_state.json')


def check_and_send_gex_alert(gex_data, gex_deltas, target_date, threshold=1.2):
    """检查是否有标的距Flip%<threshold，触发邮件告警。

    幂等：同一交易日只发一次（状态文件记录日期）。

    返回: dict {'triggered': bool, 'alert_names': list, 'email_sent': bool}
    """
    result = {'triggered': False, 'alert_names': [], 'email_sent': False}

    # 收集触发告警的标的
    alert_targets = []
    for name, d in (gex_data or {}).items():
        if not d:
            continue
        dist_pct = d.get('dist_flip_pct')
        flip_full = d.get('flip_full')
        # 必须有有效flip且dist在阈值内
        if flip_full is None:
            continue
        if dist_pct is None:
            continue
        if abs(dist_pct) < threshold:
            alert_targets.append({
                'name': name,
                'spot': d.get('spot_price'),
                'flip': flip_full,
                'dist_pct': dist_pct,
                'total_gex_yi': d.get('total_gex_yi'),
                'risk': d.get('risk', ''),
                'delta_yi': gex_deltas.get(name, {}).get('delta_yi'),
            })

    if not alert_targets:
        return result

    result['triggered'] = True
    result['alert_names'] = [t['name'] for t in alert_targets]

    # 幂等检查
    state = {}
    if os.path.exists(GEX_ALERT_STATE_FILE):
        try:
            with open(GEX_ALERT_STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except:
            state = {}

    if state.get('date') == target_date and state.get('sent', False):
        print(f"  ℹ️ GEX翻转告警今日({target_date})已发送，跳过重复发送")
        return result

    # 构建并发送邮件
    email_sent = _send_gex_alert_email(alert_targets, target_date, threshold)
    result['email_sent'] = email_sent

    # 更新状态
    state['date'] = target_date
    state['sent'] = email_sent
    state['alert_names'] = result['alert_names']
    state['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(GEX_ALERT_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except:
        pass

    return result


def _send_gex_alert_email(alert_targets, target_date, threshold):
    """发送GEX翻转点接近告警邮件（HTML格式）"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    # 从 market_bottom_monitor 复用 EMAIL_CONFIG
    try:
        from market_bottom_monitor import EMAIL_CONFIG
    except Exception as e:
        print(f"  ❌ 无法导入EMAIL_CONFIG: {e}")
        return False

    # 找最严重的标的（距Flip最小）用于标题
    worst = min(alert_targets, key=lambda t: abs(t['dist_pct']))
    subject = f"[GEX风险预警] {worst['name']}距Flip仅{abs(worst['dist_pct']):.2f}% - {target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"

    # 构建HTML
    date_display = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    html_parts = []
    html_parts.append("""<html><head><style>
body { font-family: 'Microsoft YaHei', Arial, sans-serif; padding: 20px; background: #f5f5f5; }
.container { max-width: 700px; margin: 0 auto; background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }
.header { background: linear-gradient(135deg, #e74c3c, #c0392b); color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; text-align: center; }
.header h1 { margin: 0; font-size: 22px; }
.header .subtitle { font-size: 16px; margin-top: 8px; opacity: 0.9; }
table { width: 100%; border-collapse: collapse; margin: 15px 0; }
th { background: #f8f9fa; padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6; font-size: 13px; }
td { padding: 10px; border-bottom: 1px solid #eee; font-size: 13px; }
.risk-box { background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px 16px; margin: 15px 0; border-radius: 4px; }
.footer { color: #999; font-size: 12px; text-align: center; margin-top: 25px; padding-top: 15px; border-top: 1px solid #eee; }
</style></head><body><div class="container">""")

    html_parts.append(f"""
<div class="header">
    <h1>⚠️ GEX翻转点风险预警</h1>
    <div class="subtitle">{date_display} | 距Flip &lt; {threshold}%</div>
</div>""")

    html_parts.append("""<p>以下标的现货价逼近做市商净Gamma翻转墙，一旦穿越负Gamma区，波动可能放大：</p>""")
    html_parts.append("<table><tr><th>标的</th><th>现价</th><th>Flip Point</th><th>距Flip</th><th>Net GEX</th><th>较前日</th><th>风险</th></tr>")
    for t in alert_targets:
        spot_str = f"{t['spot']:.3f}" if t['spot'] else "N/A"
        flip_str = f"{t['flip']:.2f}" if t['flip'] else "N/A"
        dist_str = f"{t['dist_pct']:+.2f}%"
        gex_str = f"{t['total_gex_yi']:+.2f}亿" if t['total_gex_yi'] is not None else "N/A"
        delta_str = f"{t['delta_yi']:+.2f}亿" if t['delta_yi'] is not None else "—"
        risk_str = t['risk']
        html_parts.append(f"<tr><td><b>{t['name']}</b></td><td>{spot_str}</td><td>{flip_str}</td><td style='color:red;font-weight:bold'>⭐{dist_str}</td><td>{gex_str}</td><td>{delta_str}</td><td>{risk_str}</td></tr>")
    html_parts.append("</table>")

    html_parts.append("""
<div class="risk-box">
<b>💡 风险解读：</b><br>
现价逼近做市商净Gamma翻转墙（Flip Point）。翻转墙是做市商从正Gamma（抑制波动）切换到负Gamma（放大波动）的临界点。一旦价格穿越该点，做市商对冲行为将从"逆势"变为"顺势"，可能导致波动放大。<br><br>
<b>建议</b>：关注相关标的是否有效突破/跌破翻转点，提前做好风控。
</div>""")

    html_parts.append("""
<div class="footer">
<p>⚠️ 案例分析学习材料，不构成投资建议 | 市场有风险，投资需谨慎</p>
<p>由蓁宝中线偏好监控系统自动生成</p>
</div></div></body></html>""")

    html_content = '\n'.join(html_parts)

    # 发送邮件
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = EMAIL_CONFIG['sender']
        msg['To'] = EMAIL_CONFIG['receiver']

        # 纯文本备选
        text_lines = [f"[GEX风险预警] {date_display}", ""]
        for t in alert_targets:
            _spot_s = f"{t['spot']:.3f}" if t['spot'] else 'N/A'
            _flip_s = f"{t['flip']:.2f}" if t['flip'] else 'N/A'
            text_lines.append(f"{t['name']}: 现价{_spot_s}, Flip={_flip_s}, 距Flip={t['dist_pct']:+.2f}%")
        text_lines.append("\n风险：现价逼近做市商净Gamma翻转墙，波动可能放大。")
        text_lines.append("案例分析学习材料，不构成投资建议。")
        msg.attach(MIMEText('\n'.join(text_lines), 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        server = smtplib.SMTP_SSL(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'], timeout=15)
        server.login(EMAIL_CONFIG['sender'], EMAIL_CONFIG['auth_code'])
        server.sendmail(EMAIL_CONFIG['sender'], [EMAIL_CONFIG['receiver']], msg.as_string())
        server.quit()

        print(f"✅ GEX翻转告警邮件已发送: {subject}")
        return True

    except Exception as e:
        print(f"❌ GEX告警邮件发送失败: {e}")
        return False


# ============ CFC 历史库每日回写（2026-10-23）============
CFC_HIST_FILE = os.path.join(CFC_DIR, 'historical_factors.json')


# ============ up50 极值告警（2026-10-29 新增）============
def _up50_hist_percentile(pool_name, value):
    """当前 up50 在历史序列中的分位（0~100），样本不足返回 None。"""
    try:
        if not os.path.exists(CFC_HIST_FILE):
            return None
        with open(CFC_HIST_FILE, 'r', encoding='utf-8') as f:
            hist = json.load(f)
        seq = []
        for _d, rec in (hist.get(pool_name) or {}).items():
            v = (rec or {}).get('up50')
            if _is_num(v):
                seq.append(float(v))
        if len(seq) < 20:
            return None
        below = sum(1 for v in seq if v <= float(value))
        return round(below / len(seq) * 100, 1)
    except Exception:
        return None


def _send_up50_alert_email(alert_items, target_date):
    """发送 up50 极值提醒邮件（HTML）。>=90% 风险 / <=10% 机会。"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    try:
        from market_bottom_monitor import EMAIL_CONFIG
    except Exception as e:
        print(f"  ❌ 无法导入EMAIL_CONFIG: {e}")
        return False

    date_display = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    risk_items = [i for i in alert_items if i['type'] == 'risk']
    opp_items = [i for i in alert_items if i['type'] == 'opp']
    head_bits = []
    if risk_items:
        head_bits.append(f"{risk_items[0]['pool']}{risk_items[0]['up50']*100:.1f}%过热(风险)")
    if opp_items:
        head_bits.append(f"{opp_items[0]['pool']}{opp_items[0]['up50']*100:.1f}%过冷(机会)")
    subject = f"[up50极值提醒] {' / '.join(head_bits)} - {date_display}"

    html_parts = ["""<html><head><style>
body { font-family: 'Microsoft YaHei', Arial, sans-serif; padding: 20px; background: #f5f5f5; }
.container { max-width: 720px; margin: 0 auto; background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }
.header { background: linear-gradient(135deg, #e67e22, #d35400); color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; text-align: center; }
.header h1 { margin: 0; font-size: 22px; }
.header .subtitle { font-size: 15px; margin-top: 8px; opacity: 0.92; }
table { width: 100%; border-collapse: collapse; margin: 15px 0; }
th { background: #f8f9fa; padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6; font-size: 13px; }
td { padding: 10px; border-bottom: 1px solid #eee; font-size: 13px; }
.risk { background: #fdecea; border-left: 4px solid #e74c3c; padding: 12px 16px; margin: 15px 0; border-radius: 4px; }
.opp { background: #eafaf1; border-left: 4px solid #27ae60; padding: 12px 16px; margin: 15px 0; border-radius: 4px; }
.footer { color: #999; font-size: 12px; text-align: center; margin-top: 25px; padding-top: 15px; border-top: 1px solid #eee; }
</style></head><body><div class="container">"""]

    html_parts.append(f"""
<div class="header">
    <h1>📐 up50 极值提醒</h1>
    <div class="subtitle">{date_display} | 阈值：≥90% 风险 · ≤10% 机会</div>
</div>""")
    html_parts.append("<p><b>up50</b> = 池内股价站上50日均线的股票占比，衡量普涨/普跌程度：≥90% 说明几乎全员站上50日线（中期过热，回调风险）；≤10% 说明几乎全员跌破50日线（超跌，反弹机会）。</p>")
    html_parts.append("<table><tr><th>标的池</th><th>up50</th><th>类型</th><th>历史分位</th><th>含义</th></tr>")
    for i in alert_items:
        if i['type'] == 'risk':
            typ, meaning = "🔴风险(过热)", "普涨到极致，追高风险大，中期见顶概率上升"
        else:
            typ, meaning = "🟢机会(过冷)", "普跌到极致，超跌反弹概率上升，可留意左侧机会"
        pr = i.get('pct_rank')
        pr_str = f"{pr:.0f}%分位" if pr is not None else "—"
        html_parts.append(
            f"<tr><td><b>{i['pool']}</b></td><td><b>{i['up50']*100:.1f}%</b></td>"
            f"<td>{typ}</td><td>{pr_str}</td><td>{meaning}</td></tr>"
        )
    html_parts.append("</table>")

    if risk_items:
        html_parts.append("""<div class="risk"><b>🔴 风险提醒</b><br>
up50 已冲到 90% 以上，说明绝大多数个股都在50日均线上方，属于普涨的极端状态。
历史上这种位置往往对应中期情绪高点，后续容易出现集体回吐。<br>
<b>应对</b>：不宜在此位置加仓追高，优先检查持仓里涨幅大、结构走弱的品种，考虑分批降低仓位或上移止盈。</div>""")
    if opp_items:
        html_parts.append("""<div class="opp"><b>🟢 机会提醒</b><br>
up50 已跌到 10% 以下，说明绝大多数个股都在50日均线下方，属于普跌的极端状态。
这种位置往往对应中期情绪低点，超跌反弹的概率上升。<br>
<b>应对</b>：不必恐慌砍仓，可开始分批布局超跌优质品种，但需等右侧信号（放量止跌）确认。</div>""")

    html_parts.append("""
<div class="footer">
<p>⚠️ 案例分析学习材料，不构成投资建议 | 市场有风险，投资需谨慎</p>
<p>由蓁宝中线偏好监控系统自动生成</p>
</div></div></body></html>""")

    html_content = '\n'.join(html_parts)
    text_lines = [f"[up50极值提醒] {date_display}", ""]
    for i in alert_items:
        typ = "风险(过热)" if i['type'] == 'risk' else "机会(过冷)"
        pr = i.get('pct_rank')
        text_lines.append(f"{i['pool']}: up50={i['up50']*100:.1f}% → {typ}" + (f"（历史{pr:.0f}%分位）" if pr is not None else ""))
    text_lines.append("\n≥90% 为普涨过热(风险)，≤10% 为普跌过冷(机会)。")
    text_lines.append("案例分析学习材料，不构成投资建议。")

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = EMAIL_CONFIG['sender']
        msg['To'] = EMAIL_CONFIG['receiver']
        msg.attach(MIMEText('\n'.join(text_lines), 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        server = smtplib.SMTP_SSL(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'], timeout=20)
        server.login(EMAIL_CONFIG['sender'], EMAIL_CONFIG['auth_code'])
        server.sendmail(EMAIL_CONFIG['sender'], [EMAIL_CONFIG['receiver']], msg.as_string())
        server.quit()
        print(f"✅ up50极值提醒邮件已发送: {subject}")
        return True
    except Exception as e:
        print(f"❌ up50告警邮件发送失败: {e}")
        return False


def check_and_send_up50_alert(cfc_factors, target_date, dry_run=False):
    """检查 up50 极值（>=90% 风险 / <=10% 机会），触发即发邮件。

    幂等：同一交易日 + 同一池子 + 同一类型只发一次（状态文件记录已发 key）。
    返回: dict {'triggered': bool, 'items': list, 'new_items': list, 'email_sent': bool}
    """
    result = {'triggered': False, 'items': [], 'new_items': [], 'email_sent': False}

    items = []
    for pool in ["沪深300", "中证500", "中证1000", "创业板", "科创板", "全市场前2000"]:
        f = (cfc_factors or {}).get(pool) or {}
        v = f.get('up50')
        if not _is_num(v):
            continue
        v = float(v)
        if v >= UP50_RISK_THRESHOLD:
            items.append({'pool': pool, 'up50': v, 'type': 'risk'})
        elif v <= UP50_OPP_THRESHOLD:
            items.append({'pool': pool, 'up50': v, 'type': 'opp'})

    result['items'] = items
    if not items:
        print(f"  ✅ up50极值告警未触发（全部池子位于10%~90%区间）")
        return result

    result['triggered'] = True
    print(f"  ⚠️ up50极值告警触发: " + ", ".join(f"{i['pool']}{i['up50']*100:.1f}%({i['type']})" for i in items))

    state = {}
    if os.path.exists(UP50_ALERT_STATE_FILE):
        try:
            with open(UP50_ALERT_STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            state = {}
    sent_keys = set(state.get('sent_keys') or [])

    new_items = [i for i in items if f"{target_date}_{i['pool']}_{i['type']}" not in sent_keys]
    result['new_items'] = new_items
    if not new_items:
        print(f"  ℹ️ up50极值告警今日({target_date})已发送过，跳过重复发送")
        return result

    for i in new_items:
        i['pct_rank'] = _up50_hist_percentile(i['pool'], i['up50'])

    if dry_run:
        print(f"  🔧 DRY-RUN: 跳过up50告警邮件 " + ", ".join(i['pool'] for i in new_items))
        return result

    ok = _send_up50_alert_email(new_items, target_date)
    result['email_sent'] = ok
    if ok:
        sent_keys.update(f"{target_date}_{i['pool']}_{i['type']}" for i in new_items)
        state['sent_keys'] = sorted(sent_keys)[-300:]
        state['date'] = target_date
        state['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with open(UP50_ALERT_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  ⚠️ up50告警状态写入失败: {e}")

    return result

# ============ 峰度极值告警（2026-10-30 新增）============
# 大佬口径（2026-10-30）：加权超额峰度 > 5 表示分布极度尖峰厚尾，历史尾部风险
# 与结构性分化显著放大。触发即发邮件。幂等同 up50：同一交易日+同一池子只发一次。
KURT_ALERT_THRESHOLD = 5.0
KURT_ALERT_STATE_FILE = os.path.join(OUTPUT_DIR, 'kurt_alert_state.json')


def _kurt_hist_percentile(pool_name, value):
    """当前超额峰度在历史序列中的分位（0~100），样本不足返回 None。"""
    try:
        if not os.path.exists(CFC_HIST_FILE):
            return None
        with open(CFC_HIST_FILE, 'r', encoding='utf-8') as f:
            hist = json.load(f)
        seq = []
        for _d, rec in (hist.get(pool_name) or {}).items():
            v = (rec or {}).get('weighted_kurt')
            if _is_num(v):
                seq.append(float(v))
        if len(seq) < 20:
            return None
        below = sum(1 for v in seq if v <= float(value))
        return round(below / len(seq) * 100, 1)
    except Exception:
        return None


def _send_kurt_alert_email(alert_items, target_date):
    """发送峰度极值提醒邮件（HTML）。超额峰度 >5 触发。"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    try:
        from market_bottom_monitor import EMAIL_CONFIG
    except Exception as e:
        print(f"  ❌ 无法导入EMAIL_CONFIG: {e}")
        return False

    date_display = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    # 标题取最严重的池子（超额峰度最高）
    worst = max(alert_items, key=lambda i: i['kurt'])
    subject = f"[峰度极值提醒] {worst['pool']}超额峰度{worst['kurt']:.2f} - {date_display}"

    html_parts = ["""<html><head><style>
body { font-family: 'Microsoft YaHei', Arial, sans-serif; padding: 20px; background: #f5f5f5; }
.container { max-width: 720px; margin: 0 auto; background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }
.header { background: linear-gradient(135deg, #8e44ad, #6c3483); color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; text-align: center; }
.header h1 { margin: 0; font-size: 22px; }
.header .subtitle { font-size: 15px; margin-top: 8px; opacity: 0.92; }
table { width: 100%; border-collapse: collapse; margin: 15px 0; }
th { background: #f8f9fa; padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6; font-size: 13px; }
td { padding: 10px; border-bottom: 1px solid #eee; font-size: 13px; }
.risk { background: #f5eef8; border-left: 4px solid #8e44ad; padding: 12px 16px; margin: 15px 0; border-radius: 4px; }
.footer { color: #999; font-size: 12px; text-align: center; margin-top: 25px; padding-top: 15px; border-top: 1px solid #eee; }
</style></head><body><div class="container">"""]

    html_parts.append(f"""
<div class="header">
    <h1>📊 峰度极值提醒</h1>
    <div class="subtitle">{date_display} | 阈值：超额峰度 &gt; {KURT_ALERT_THRESHOLD}</div>
</div>""")
    html_parts.append("<p><b>加权超额峰度</b>（正态=0）衡量收益率分布的尖峰厚尾程度。超过 5 说明分布极度尖峰，尾部风险与结构性分化显著放大，历史上此类状态往往伴随极端波动和板块剧烈轮动。</p>")
    html_parts.append("<table><tr><th>标的池</th><th>超额峰度</th><th>实际峰度(+3)</th><th>历史分位</th><th>含义</th></tr>")
    for i in alert_items:
        actual_kurt = i['kurt'] + 3
        pr = i.get('pct_rank')
        pr_str = f"{pr:.0f}%分位" if pr is not None else "—"
        meaning = "极端尖峰厚尾，尾部风险与结构性分化显著放大"
        html_parts.append(
            f"<tr><td><b>{i['pool']}</b></td>"
            f"<td><b>{i['kurt']:.2f}</b></td>"
            f"<td>{actual_kurt:.2f}</td>"
            f"<td>{pr_str}</td>"
            f"<td>{meaning}</td></tr>"
        )
    html_parts.append("</table>")

    html_parts.append("""<div class="risk"><b>💡 风险解读</b><br>
超额峰度 &gt; 5 表示收益率分布极度尖峰厚尾：大部分时间波动很小，但偶发极端波动幅度远超正态预期。
这种状态下，历史尾部风险与结构性分化显著放大，板块轮动剧烈，单日暴涨暴跌频率上升。<br><br>
<b>应对</b>：控制仓位集中度，避免单一板块重仓暴露；关注尾部对冲工具（如期权保护性策略）；
短线操作降低杠杆，防止被突发波动打穿止损。
</div>""")

    html_parts.append("""
<div class="footer">
<p>⚠️ 案例分析学习材料，不构成投资建议 | 市场有风险，投资需谨慎</p>
<p>由蓁宝中线偏好监控系统自动生成</p>
</div></div></body></html>""")

    html_content = '\n'.join(html_parts)
    text_lines = [f"[峰度极值提醒] {date_display}", ""]
    for i in alert_items:
        actual_kurt = i['kurt'] + 3
        pr = i.get('pct_rank')
        text_lines.append(
            f"{i['pool']}: 超额峰度={i['kurt']:.2f}（实际={actual_kurt:.2f}）"
            + (f"（历史{pr:.0f}%分位）" if pr is not None else "")
        )
    text_lines.append("\n超额峰度 >5 表示分布极度尖峰厚尾，历史尾部风险与结构性分化显著放大。")
    text_lines.append("案例分析学习材料，不构成投资建议。")

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = EMAIL_CONFIG['sender']
        msg['To'] = EMAIL_CONFIG['receiver']
        msg.attach(MIMEText('\n'.join(text_lines), 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        server = smtplib.SMTP_SSL(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'], timeout=20)
        server.login(EMAIL_CONFIG['sender'], EMAIL_CONFIG['auth_code'])
        server.sendmail(EMAIL_CONFIG['sender'], [EMAIL_CONFIG['receiver']], msg.as_string())
        server.quit()
        print(f"✅ 峰度极值提醒邮件已发送: {subject}")
        return True
    except Exception as e:
        print(f"❌ 峰度告警邮件发送失败: {e}")
        return False


def check_and_send_kurt_alert(cfc_factors, target_date, dry_run=False):
    """检查加权超额峰度 >5，触发即发邮件。

    幂等：同一交易日 + 同一池子只发一次（状态文件记录 sent_keys）。
    返回: dict {'triggered': bool, 'items': list, 'new_items': list, 'email_sent': bool}
    """
    result = {'triggered': False, 'items': [], 'new_items': [], 'email_sent': False}

    items = []
    for pool in ["沪深300", "中证500", "中证1000", "创业板", "科创板", "全市场前2000"]:
        f = (cfc_factors or {}).get(pool) or {}
        v = f.get('weighted_kurt')
        if not _is_num(v):
            continue
        v = float(v)
        if v > KURT_ALERT_THRESHOLD:
            items.append({'pool': pool, 'kurt': v})

    result['items'] = items
    if not items:
        print(f"  ✅ 峰度极值告警未触发（全部池子超额峰度 ≤ {KURT_ALERT_THRESHOLD}）")
        return result

    result['triggered'] = True
    print(f"  ⚠️ 峰度极值告警触发: " + ", ".join(f"{i['pool']}{i['kurt']:.2f}" for i in items))

    state = {}
    if os.path.exists(KURT_ALERT_STATE_FILE):
        try:
            with open(KURT_ALERT_STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            state = {}
    sent_keys = set(state.get('sent_keys') or [])

    new_items = [i for i in items if f"{target_date}_{i['pool']}_kurt" not in sent_keys]
    result['new_items'] = new_items
    if not new_items:
        print(f"  ℹ️ 峰度极值告警今日({target_date})已发送过，跳过重复发送")
        return result

    for i in new_items:
        i['pct_rank'] = _kurt_hist_percentile(i['pool'], i['kurt'])

    if dry_run:
        print(f"  🔧 DRY-RUN: 跳过峰度告警邮件 " + ", ".join(i['pool'] for i in new_items))
        return result

    ok = _send_kurt_alert_email(new_items, target_date)
    result['email_sent'] = ok
    if ok:
        sent_keys.update(f"{target_date}_{i['pool']}_kurt" for i in new_items)
        state['sent_keys'] = sorted(sent_keys)[-300:]
        state['date'] = target_date
        state['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with open(KURT_ALERT_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  ⚠️ 峰度告警状态写入失败: {e}")

    return result

# 历史库每个日期条目保留的字段（与 calculate_all_factors_for_pool 返回对齐）
_CFC_HIST_FIELDS = ('cfc', 'cfc_ratio', 'cfc_raw', 'up50', 'top5z', 'cdi',
                    'weighted_skew', 'weighted_kurt')


def sync_to_tech_store(json_path, target_date):
    """把本次结果落进 data_hub tech 指标仓（metric_points）。

    2026-11-25 新增：脚本此前只落 JSON，看板/回溯拿不到中线数据。
    真正的搬运逻辑在 data_hub/tech/producers/midline_daily.py
    （口径对齐 task_tags_daily：池名走指数代码、up50 落百分数），
    这里只负责触发，不重算任何数值和阈值。
    落库失败只告警，不影响主推送。
    """
    try:
        from data_hub.tech.producers import midline_daily as _md
        st = _md.ingest(json_path)
        print(f"  ✅ tech 指标仓落库: {st['n_ok']} 行 / {st['metrics']} 指标 / "
              f"{st['security_ids']} 标的（missing={st['n_missing']}，{st['obs_date']}）")
        return st
    except Exception as e:                            # noqa: BLE001
        print(f"  ⚠️ tech 指标仓落库失败（不影响主流程）: {e}")
        return None


def append_cfc_history(cfc_factors, target_date, hist_path=CFC_HIST_FILE):
    """把当日 6 池 CFC 因子幂等回写 historical_factors.json，供风险预测日报次日读取。

    - 结构：{池名: {YYYYMMDD: {cfc,cfc_ratio,cfc_raw,up50,top5z,cdi,...}}}
    - 幂等：同日已存在则覆盖（同交易日重跑取最新）；不新增日期、不改动历史
    - 容错：历史文件缺失/损坏时新建；回写失败只告警，不影响主推送
    """
    try:
        history = {}
        if os.path.exists(hist_path):
            try:
                with open(hist_path, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                if not isinstance(history, dict):
                    history = {}
            except Exception as e:
                print(f"  ⚠️ CFC历史库读取失败，将备份后重建: {e}")
                import shutil
                shutil.copy(hist_path, hist_path + f'.corrupt_{target_date}')
                history = {}

        written = 0
        for pool, fac in (cfc_factors or {}).items():
            if not fac:
                continue
            entry = {k: fac.get(k) for k in _CFC_HIST_FIELDS if k in fac}
            if 'cfc' not in entry:
                continue
            history.setdefault(pool, {})[target_date] = entry
            written += 1

        tmp_path = hist_path + f'.tmp_{target_date}'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, hist_path)
        print(f"  ✅ CFC历史库回写 {written} 池 -> {hist_path}（{target_date}）")
        return True
    except Exception as e:
        print(f"  ⚠️ CFC历史库回写失败（不影响主流程）: {e}")
        return False


# ============ 数据质量体检（2026-10-22 新增，P1-1）============
def build_health_report(cfc_factors, skew_data, gex_data, basis_data, data_available,
                        bottom_result=None):
    """收集本次运行的数据质量问题，返回 (level, lines)。

    level: 'ok' / 'warn' / 'critical'
    规则：
      - 任一池子/标的缺失 → warn
      - 核心池（沪深300/中证500/中证1000）缺失 或 有效维度 <2 → critical
      - CFC 处于饱和值（≥2.1 或 ≤-2.1）→ warn（提示信号失去区分度）
      - flip_point 全缺 → warn（说明 GEX 结构信息没算出来）
    """
    issues = []
    warns = 0

    # CFC 池子覆盖
    pool_order = ["沪深300", "中证500", "中证1000", "创业板", "科创板", "全市场前2000"]
    ok_pools = [p for p in pool_order if cfc_factors.get(p)]
    missing_pools = [p for p in pool_order if not cfc_factors.get(p)]
    core_missing = [p for p in ("沪深300", "中证500", "中证1000") if p in missing_pools]

    if missing_pools:
        # 2026-11-19: 附加失败原因（如果get_cfc_data记录了）
        failed_reasons = {}
        for pn, reason in (getattr(get_cfc_data, '_failed_pools', None) or []):
            failed_reasons[pn] = reason
        reason_parts = []
        for p in missing_pools:
            r = failed_reasons.get(p, '')
            reason_parts.append(f"{p}({r})" if r else p)
        issues.append(f"⚠️ CFC 池子 {len(ok_pools)}/6 有效（缺失：{', '.join(reason_parts)}）")
        warns += 1
    if core_missing:
        issues.append(f"🔴 核心池缺失：{', '.join(core_missing)}，CFC 结论不可用")
    if not data_available:
        issues.append("⚠️ 当日行情数据未确认发布（data_available=False）")
        warns += 1

    # CFC 饱和检测
    saturated = []
    for p in ok_pools:
        fac = cfc_factors.get(p) or {}
        cfc = fac.get('cfc')
        if cfc is not None and (abs(cfc) >= 2.099):
            raw = fac.get('cfc_raw')
            raw_str = f"(原始{raw:.2f})" if _is_num(raw) else ""
            saturated.append(f"{p}={cfc:.2f}{raw_str}")
    if saturated:
        issues.append(f"⚠️ CFC 触及±2.1截断（信号仍按截断值判定，括号原始值仅供区分度）：{', '.join(saturated)}")
        warns += 1

    # 期权覆盖
    sk_ok = sum(1 for v in (skew_data or {}).values() if v)
    gx_ok = sum(1 for v in (gex_data or {}).values() if v)
    n_t = len(OPTION_TARGETS) if OPTION_TARGETS else 6
    if sk_ok < n_t:
        issues.append(f"⚠️ SKEW 仅 {sk_ok}/{n_t} 个标的有数据")
        warns += 1
    if gx_ok < n_t:
        issues.append(f"⚠️ GEX 仅 {gx_ok}/{n_t} 个标的有数据")
        warns += 1

    # flip_point（2026-10-26 改造：用全量重算 flip_full 判断，区分"无翻转"和"无数据"）
    flip_missing = []
    for n, v in (gex_data or {}).items():
        if not v:
            continue
        fp_full = v.get('flip_full')
        if fp_full is not None:
            continue  # 有有效翻转点，不报
        # 无全量flip，检查旧版10档兜底
        fp_old = _fmt_flip(v)
        if fp_old.startswith('无数据'):
            flip_missing.append(n)
        # "无翻转(全正/全负)" 不算缺失
    if flip_missing:
        issues.append(f"⚠️ flip_point 无法计算：{', '.join(flip_missing)}")
        warns += 1

    # 升贴水
    if not basis_data:
        issues.append("⚠️ 股指期货升贴水数据缺失")
        warns += 1

    # 底部监测
    if bottom_result and bottom_result.get('skipped'):
        issues.append("ℹ️ 杨康平底部判断今日已推送，本次跳过（幂等）")

    valid_dims = sum(1 for ok in (len(ok_pools) >= 3, sk_ok >= 2, gx_ok >= 2) if ok)
    if valid_dims < 2:
        issues.append(f"🔴 有效维度只有 {valid_dims}/3，中线偏好结论不可信")

    if any(x.startswith('🔴') for x in issues):
        level = 'critical'
    elif warns:
        level = 'warn'
    else:
        level = 'ok'

    if not issues:
        issues.append("✅ 数据完整性检查通过（6/6 池 + 6/6 标的）")

    return level, issues


# ============ 构建飞书卡片 ============
def build_midline_monitor_card(date_str, cfc_factors, skew_data, gex_data, basis_data, data_available=True):
    """构建中线偏好监控整合卡片"""
    
    # 综合判断
    assessment = assess_midline_preference(cfc_factors, skew_data, gex_data)
    template = assessment['color']
    
    header = build_card_header(f"📊 中线偏好监控 | {date_str}", template=template)
    
    elements = []
    
    # 核心结论
    elements.append(build_markdown(
        f"**中线偏好**: {assessment['overall']}\n"
        f"**信号构成**: 🟢偏多{assessment['bullish']}个 / 🔴偏空{assessment['bearish']}个 / ⚖️中性{assessment['neutral']}个"
    ))

    # ===== 数据体检（2026-10-22 新增 P1-1）=====
    # 原来数据缺失/降级/饱和全部静默，卡片看起来永远"正常"。这里把问题显式列出。
    health_level, health_lines = build_health_report(
        cfc_factors, skew_data, gex_data, basis_data, data_available, bottom_result=None
    )
    health_icon = {'ok': '✅', 'warn': '⚠️', 'critical': '🔴'}.get(health_level, 'ℹ️')
    elements.append(build_markdown(
        f"**{health_icon} 数据体检**: " + ("通过" if health_level == 'ok' else
                                         ("有告警" if health_level == 'warn' else "存在严重问题，结论不可信"))
    ))
    elements.append(build_collapsible(
        f"{health_icon} 数据体检明细（{len(health_lines)} 项）",
        ["\n".join(health_lines)],
        default_expanded=(health_level == 'critical'),
    ))
    elements.append(build_hr())

    # ===== CFC结构因子 =====
    elements.append(build_section_header("📊 CFC结构因子（6个池子）"))

    pool_order = ["沪深300", "中证500", "中证1000", "创业板", "科创板", "全市场前2000"]
    cfc_rows = []
    for pool_name in pool_order:
        if pool_name not in cfc_factors or not cfc_factors[pool_name]:
            cfc_rows.append([pool_name, 'N/A', '-', 'N/A', 'N/A', '-'])
            continue
        f = cfc_factors[pool_name]
        cfc = f.get('cfc')
        # 2026-10-22 新增（P0-3 展示侧）：cfc 若贴着 ±2.1 上限，标 ⚠️饱和，
        # 提示"该值可能已被截断，与同样饱和的池子无法区分"。
        if cfc is None:
            cfc_str = 'N/A'
        elif abs(cfc) >= 2.099:
            # 2026-10-23：饱和时附未截断 cfc_raw，恢复被 2.1 焊死的池子间区分度（raw不参与信号）
            raw = f.get('cfc_raw')
            raw_str = f"(原始{raw:.2f})" if _is_num(raw) else ""
            cfc_str = f"{cfc:.3f}⚠️{raw_str}"
        else:
            cfc_str = f"{cfc:.3f}"

        cfc_sig = get_signal_cfc(cfc) if cfc is not None else '-'
        cfc_emoji = '🟢' if cfc_sig == '买入' else ('🔴' if cfc_sig == '卖出' else '⚖️')
        
        w_skew = f.get('weighted_skew')
        w_kurt = f.get('weighted_kurt')  # 超额峰度（正态=0），CFC计算与阈值体系用此值
        skew_str = f"{w_skew:.2f}" if w_skew is not None else 'N/A'
        # 2026-10-29：展示改用原始峰度（正态=3）。超额口径下正常行情常为 0~2，看起来偏小易误读
        # 2026-10-30：超额峰度 >5 时追加 🔥 标记（极值告警）
        kurt_str = f"{w_kurt + 3:.2f}" if _is_num(w_kurt) else 'N/A'
        if _is_num(w_kurt) and float(w_kurt) > KURT_ALERT_THRESHOLD:
            kurt_str += "🔥"
        
        up50 = f.get('up50')
        if not _is_num(up50):
            up50_str = 'N/A'
        elif up50 >= UP50_RISK_THRESHOLD:
            up50_str = f"⭐🔴{up50*100:.1f}%"  # >=90% 普涨过热：风险
        elif up50 <= UP50_OPP_THRESHOLD:
            up50_str = f"⭐🟢{up50*100:.1f}%"  # <=10% 普跌过冷：机会
        else:
            up50_str = f"{up50*100:.1f}%"
        
        cfc_rows.append([pool_name, cfc_str, f"{cfc_emoji}{cfc_sig}", skew_str, kurt_str, up50_str])
    
    elements.append(build_data_table(
        ['标的池', 'CFC', '信号', 'w_skew', '峰度', 'up50'],
        cfc_rows,
    ))
    
    elements.append(build_hr())
    
    # ===== 期权SKEW =====
    elements.append(build_section_header("📈 期权SKEW（5个标的）"))
    
    skew_rows = []
    _pcr_col_note = ''  # 追加到表头的备注
    for name, d in skew_data.items():
        if d:
            vol_pcr_val = d.get('vol_pcr')
            is_unified = d.get('vol_pcr_unified', False)
            if is_unified:
                # 统一PCR（5日平滑+剔除当月+多ETF合并）
                pcr_str = f"{vol_pcr_val:.3f}" if vol_pcr_val is not None else 'N/A'
            elif vol_pcr_val is not None:
                # 股指期权等无对应统一PCR → 保留原值加星号
                pcr_str = f"{vol_pcr_val:.2f}*"
                _pcr_col_note = '*原值(单日/含当月)'
            else:
                pcr_str = 'N/A'
            skew_rows.append([
                name,
                f"{d.get('atm_iv', 0):.2f}%",
                f"{d.get('iv_skew', 0):+.2f}%",
                f"{d.get('cboe_skew', 0):.2f}",
                pcr_str,
                d.get('direction', ''),
            ])
        else:
            skew_rows.append([name, 'N/A', 'N/A', 'N/A', 'N/A', '-'])
    
    pcr_header = f'Vol PCR(5日平滑){_pcr_col_note}' if _pcr_col_note else 'Vol PCR(5日平滑)'
    elements.append(build_data_table(
        ['标的', 'ATM IV', 'IV Skew', 'SKEW', pcr_header, '方向'],
        skew_rows,
    ))

    # ===== 期权SKEW 口径注释（纯代码硬编码，零token） =====
    elements.append(build_collapsible(
        "期权SKEW指标怎么算 · 偏多/偏空什么意思",
        [
            "数据：最近到期月（剔除到期≤3天合约）期权链，IV 均由期权收盘价用 Black-Scholes 模型反推；指标值优先取 Tushare，缺失时用 Tushare/AKShare/网页原始行情自行反推，不直接采用免费源现成IV。",
            "ATM IV：平值（行权价最接近现价）认购与认沽隐含波动率均值，衡量期权市场对未来波动的预期，越高越恐慌/对冲需求越旺。",
            "IV Skew：25Δ虚值认沽IV − 25Δ看涨IV（百分点）。为正=下方保护更贵，市场更怕下跌。",
            "SKEW：CBOE式 = 100 − 10×[−3×(25Δ认沽IV − 平值IV)/平值IV]，把左尾偏度标准化到100附近；>100代表认沽相对更贵、左尾对冲需求强。",
            "方向判定（按SKEW绝对值）：🟢偏多 = SKEW<100（认沽相对便宜，尾部避险情绪弱，情绪乐观）；⚪中性 = 100~105；🟠偏空 = 105~120；🔴严重偏空 = >120（高价抢认沽、强烈防跌）。",
            "Vol PCR：认沽/认购成交量比，统一口径为5日平滑、剔除当月合约、同标的多ETF合并；>1偏防御、<1偏乐观（单日值标*）。",
            "提示：这里的偏多/偏空是期权市场情绪与对冲需求的方向，是风险偏好的反向参考，不构成买卖建议。",
        ],
        default_expanded=False,
    ))

    elements.append(build_hr())
    
    # ===== 期权GEX =====
    # 口径（2026-10-27 大佬确认）：全部到期月合约参与计算，仅剔除距到期≤3天的合约
    elements.append(build_section_header("💰 期权GEX（5个标的 · 全到期月，仅剔除到期≤3天）"))
    
    # 2026-10-26 新增：计算GEX较前日变化
    # 2026-10-29：只取前8位数字，避免 date_str 带后缀（如「（GEX口径修正）」）污染历史对比
    trade_date_raw = ''.join(ch for ch in date_str if ch.isdigit())[:8]
    gex_deltas = get_gex_delta(gex_data, trade_date_raw)

    # GEX告警阈值
    FLIP_ALERT_THRESHOLD = 1.2  # 距Flip%低于此值触发告警
    
    gex_rows = []
    gex_alert_names = []  # 触发告警的标的名
    for name, d in gex_data.items():
        if d:
            # Flip point：优先全量重算结果
            fp_full = d.get('flip_full')
            fp_far = d.get('flip_far')
            dist_far = d.get('dist_far_pct')
            near_sign = d.get('near_gamma_sign')
            if fp_full is not None:
                fp = f"{fp_full:.2f}"
            elif d.get('all_strikes'):
                # 2026-10-29：全量重算已执行，近端无有效过零点
                fp = f"无近端(远端{fp_far:.2f})" if fp_far is not None else "无翻转(同号)"
            else:
                fp = _fmt_flip(d)  # 旧版10档兜底
            
            # 较前日变化
            delta_info = gex_deltas.get(name, {})
            delta_yi = delta_info.get('delta_yi')
            if delta_yi is not None:
                # 2026-10-27 修复飞书卡片表格font标签乱码：改用emoji+纯文本
                if delta_yi >= 0:
                    delta_str = f"🔺+{delta_yi:.2f}亿"
                else:
                    delta_str = f"🔻{delta_yi:.2f}亿"
            else:
                delta_str = "—"
            
            # 距Flip%
            dist_pct = d.get('dist_flip_pct')
            if fp_full is not None and dist_pct is not None:
                abs_dist = abs(dist_pct)
                dist_formatted = f"{dist_pct:+.2f}%"
                if abs_dist < FLIP_ALERT_THRESHOLD:
                    # 2026-10-27 修复飞书卡片表格font标签乱码：纯文本emoji告警
                    dist_str = f"⭐🔴{dist_formatted}"
                    gex_alert_names.append(name)
                else:
                    dist_str = dist_formatted
            elif fp_far is not None and dist_far is not None:
                # 2026-10-29：只有远端虚值堆积过零点，明确标注不参与告警
                dist_str = f"远端{dist_far:+.0f}%"
            elif d.get('all_strikes'):
                dist_str = "—"
            else:
                dist_str = "无数据"
            
            # 2026-10-27 修复飞书卡片表格font标签乱码：纯文本星号标记
            name_display = name
            if name in gex_alert_names:
                name_display = f"⭐{name}"
            
            # 2026-10-29：近端无翻转且现价档累计GEX为负 → 做市商近端净负Gamma，波动放大风险
            risk_display = d.get('risk', '')
            if fp_full is None and near_sign == 'negative':
                risk_display = "🟠近端负Gamma"

            gex_rows.append([
                name_display,
                f"{d.get('total_gex_yi', 0):+.2f}亿",
                delta_str,
                fp,
                dist_str,
                risk_display,
            ])
        else:
            gex_rows.append([name, 'N/A', '—', 'N/A', '—', '-'])
    
    elements.append(build_data_table(
        ['标的', 'Net GEX', '较前日', 'Flip Point', '距Flip%', '风险'],
        gex_rows,
    ))

    # 2026-10-29：表下口径提示（换月/远端虚值主导），避免 Flip 跳变被误读为数据错误
    for _caliber_note in build_gex_caliber_notes(gex_data, trade_date_raw):
        elements.append(build_markdown(_caliber_note))
    
    # ===== 升贴水（折叠区）=====
    if basis_data:
        elements.append(build_hr())
        basis_lines = []
        basis_lines.append("| 品种 | 类型 | DTE | 年化基差率 | rank | Z | 信号 |")
        basis_lines.append("|------|------|-----|-----------|------|---|------|")
        for b in basis_data:
            rk = f"{b['rank_252']:.4f}" if b.get('rank_252') else 'N/A'
            zs = f"{b['z_score']:+.2f}" if b.get('z_score') is not None else 'N/A'
            br = f"{b['basis_rate']:+.4f}%" if b.get('basis_rate') is not None else 'N/A'
            basis_lines.append(f"| {b.get('product','')} | {b.get('type','')} | {b.get('dte','')} | {br} | {rk} | {zs} | {b.get('signal','')} |")
        
        elements.append(build_collapsible(
            "📊 股指期货升贴水（折叠）",
            basis_lines[2:],  # 跳过表头
            default_expanded=False
        ))
    
    # ===== 中线偏好解读 =====
    elements.append(build_hr())
    elements.append(build_section_header("🔍 中线偏好解读"))
    
    interpret_lines = []
    
    # CFC维度解读
    sell_pools = []
    buy_pools = []
    for pool_name in pool_order:
        if pool_name in cfc_factors and cfc_factors[pool_name]:
            cfc = cfc_factors[pool_name].get('cfc')
            if cfc is not None:
                if cfc > 1.5:
                    sell_pools.append(pool_name)
                elif cfc < -1:
                    buy_pools.append(pool_name)
    
    if sell_pools:
        interpret_lines.append(f"⚠️ **CFC卖出信号**: {', '.join(sell_pools)} → 成交集中度高，结构分化严重")
    if buy_pools:
        interpret_lines.append(f"✅ **CFC买入信号**: {', '.join(buy_pools)} → 成交分散度高，可能超跌")
    if not sell_pools and not buy_pools:
        interpret_lines.append("• CFC全线中性，无极端信号")
    
    # 期权方向
    skew_bullish = []
    skew_bearish = []
    for name, d in skew_data.items():
        if d:
            direction = d.get('direction', '')
            if '偏多' in direction:
                skew_bullish.append(name)
            elif '偏空' in direction:
                skew_bearish.append(name)
    
    if skew_bullish:
        interpret_lines.append(f"📈 **期权偏多标的**: {', '.join(skew_bullish)}")
    if skew_bearish:
        interpret_lines.append(f"📉 **期权偏空标的**: {', '.join(skew_bearish)}")
    
    # GEX风险
    gex_high_risk = []
    for name, d in gex_data.items():
        if d and '高' in d.get('risk', ''):
            gex_high_risk.append(name)
    if gex_high_risk:
        interpret_lines.append(f"🔥 **GEX高风险标的**: {', '.join(gex_high_risk)} → 负Gamma，波动可能放大")
    
    elements.append(build_markdown("\n".join(interpret_lines)))
    
    # ===== 指标说明（折叠）=====
    elements.append(build_hr())
    elements.append(build_collapsible("📖 指标说明 & 使用方法", [
        "**中线偏好监控**：综合CFC结构因子(成交集中度) + 期权SKEW(尾部风险) + 期权GEX(做市商敞口)三维度判断市场中线方向",
        "",
        "**CFC（截面因子集中度）**：0.4×加权峰度 + 0.6×加权偏度",
        "  • >1.5 卖出信号（成交极度集中头部+极端分布）",
        "  • <-1 买入信号（分散程度高，可能超跌）",
        "  • 📌 峰度口径（2026-10-29）：表内「峰度」= 原始峰度（正态=3，>3 为尖峰厚尾）；",
        "    底层计算与信号阈值用的是超额峰度（原始值−3，正态=0），两者差 3，不要混用",
        "",
        "**SKEW（波动率偏度）**：看跌期权IV - 看涨期权IV，反映市场尾部风险预期",
        "  • SKEW>105 偏空（市场担心黑天鹅）",
        "  • SKEW<95 偏多（市场情绪乐观）",
        "",
        "**Vol PCR（成交量认沽/认购比）**：5日平滑、剔除当月到期合约、同指数多ETF合并",
        "  • 与底部监测使用同一数据源（option_pcr模块），口径统一",
        "  • 股指期权标的（中证1000）无对应ETF期权指数，保留原单日PCR（标*）",
        "",
        "**GEX（做市商净Gamma敞口）**：全部到期月合约合计的做市商净Gamma",
        "  • 📌 统计口径：所有到期月合约均参与，仅剔除距到期≤3天的合约（2026-10-27确认，不剔除整个当月）",
        "  • ⚠️ 到期周提示：近月合约到期前gamma急速增大，到期周GEX及Flip可能出现日间跳变，属口径内正常现象；到期≤3天后该月自动剔除",
        "  • 正Gamma → 做市商反向操作（涨卖跌买）抑制波动（低风险）",
        "  • 负Gamma → 做市商同向操作（涨买跌卖）放大波动（高风险）",
        "",
        "**较前日**：今日Net GEX − 上一交易日Net GEX（亿），反映做市商敞口的日内变化方向",
        "",
        "**Flip Point（翻转点）**：全量期权链累计GEX曲线的过零点，做市商从正Gamma切换到负Gamma的临界价位",
        "  • 仅认可现价 ±20% 范围内的过零点（近端口径，2026-10-29 起）",
        "  • 超出范围的过零点属远端虚值堆积，标注为「远端X.XX」仅展示、不参与告警",
        "  • 近端无过零点时标「无近端」；若此时现价档累计GEX为负，风险列标 🟠近端负Gamma",
        "",
        "**距Flip%**：(Flip Point − 现价)/现价 × 100%，正值=翻转点在现价上方，负值=在下方",
        "  • |距Flip%| < 1.2% 时标红⚠️告警（现价逼近翻转墙，穿越后波动可能放大）",
        "  • 邮件告警同步触发（同一交易日仅一次）",
        "",
        "**使用建议**：三维度信号共振时可靠性最高；CFC看结构，SKEW看情绪，GEX看波动；中线级别（1-4周）参考",
    ]))
    
    # 底部
    elements.append(build_hr())
    elements.append(build_note(
        f"数据来源: Tushare + AkShare | "
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"案例分析学习材料，不构成投资建议"
    ))
    
    return wrap_card(header, elements)


# ============ 生成纯文本降级报告 ============
def generate_text_report(date_str, cfc_factors, skew_data, gex_data, basis_data, data_available=True):
    """生成纯文本报告（卡片推送失败时降级使用）

    2026-10-22：data_available 参数原来收了不用（A8），现在真正体现在报告开头。
    """
    lines = []
    lines.append(f"📊 中线偏好监控 | {date_str}")
    lines.append("=" * 50)
    if not data_available:
        lines.append("⚠️ 警告：当日行情数据未确认发布，下列数值可能不完整")
        lines.append("")
    
    # 综合判断
    assessment = assess_midline_preference(cfc_factors, skew_data, gex_data)
    lines.append(f"中线偏好: {assessment['overall']}")
    lines.append(f"信号构成: 🟢偏多{assessment['bullish']} / 🔴偏空{assessment['bearish']} / ⚖️中性{assessment['neutral']}")
    lines.append("")
    
    # CFC
    lines.append("【CFC结构因子】")
    pool_order = ["沪深300", "中证500", "中证1000", "创业板", "科创板", "全市场前2000"]
    lines.append(f"{'标的池':<10} {'CFC':>8} {'信号':<6} {'w_skew':>8} {'峰度':>8} {'up50':>7}")
    lines.append("-" * 55)
    for pool_name in pool_order:
        if pool_name not in cfc_factors or not cfc_factors[pool_name]:
            lines.append(f"{pool_name:<10} {'N/A':>8} {'-':<6} {'N/A':>8} {'N/A':>8} {'N/A':>7}")
            continue
        f = cfc_factors[pool_name]
        cfc = f.get('cfc')
        if cfc is None:
            cfc_str = 'N/A'
        elif abs(cfc) >= 2.099:
            raw = f.get('cfc_raw')
            cfc_str = (f"{cfc:.3f}⚠️(原{raw:.2f})"
                      if _is_num(raw)
                      else f"{cfc:.3f}⚠️饱和")
        else:
            cfc_str = f"{cfc:.3f}"
        cfc_sig = get_signal_cfc(cfc) if cfc is not None else '-'
        
        w_skew = f.get('weighted_skew')
        w_kurt = f.get('weighted_kurt')
        skew_str = f"{w_skew:.2f}" if w_skew is not None else 'N/A'
        kurt_str = f"{w_kurt + 3:.2f}" if _is_num(w_kurt) else 'N/A'
        if _is_num(w_kurt) and float(w_kurt) > KURT_ALERT_THRESHOLD:
            kurt_str += "🔥"
        
        up50 = f.get('up50')
        if not _is_num(up50):
            up50_str = 'N/A'
        elif up50 >= UP50_RISK_THRESHOLD:
            up50_str = f"{up50*100:.1f}%⭐风险"
        elif up50 <= UP50_OPP_THRESHOLD:
            up50_str = f"{up50*100:.1f}%⭐机会"
        else:
            up50_str = f"{up50*100:.1f}%"
        
        lines.append(f"{pool_name:<10} {cfc_str:>8} {cfc_sig:<6} {skew_str:>8} {kurt_str:>8} {up50_str:>7}")
    
    lines.append("")
    
    # SKEW
    lines.append("【期权SKEW】(Vol PCR: 5日平滑/剔当月)")
    lines.append(f"{'标的':<10} {'ATM IV':>8} {'IV Skew':>9} {'SKEW':>7} {'PCR':>7} {'方向'}")
    lines.append("-" * 58)
    for name, d in skew_data.items():
        if d:
            vol_pcr_val = d.get('vol_pcr')
            is_unified = d.get('vol_pcr_unified', False)
            if vol_pcr_val is not None:
                pcr_str = f"{vol_pcr_val:.3f}" if is_unified else f"{vol_pcr_val:.2f}*"
            else:
                pcr_str = 'N/A'
            lines.append(f"{name:<10} {d['atm_iv']:>7.2f}% {d['iv_skew']:>+8.2f}% {d['cboe_skew']:>7.2f} {pcr_str:>7} {d['direction']}")
        else:
            lines.append(f"{name:<10} {'N/A':>8} {'N/A':>9} {'N/A':>7} {'N/A':>7} -")
    
    lines.append("")
    
    # GEX
    lines.append("【期权GEX】（含较前日变化/距Flip%）")
    trade_date_raw = ''.join(ch for ch in date_str if ch.isdigit())[:8]
    gex_deltas_txt = get_gex_delta(gex_data, trade_date_raw)
    lines.append(f"{'标的':<10} {'Net GEX':>12} {'较前日':>10} {'Flip':>10} {'距Flip%':>10} {'风险'}")
    lines.append("-" * 65)
    for name, d in gex_data.items():
        if d:
            fp_full = d.get('flip_full')
            dist_far = d.get('dist_far_pct')
            near_sign = d.get('near_gamma_sign')
            if fp_full is not None:
                fp = f"{fp_full:.2f}"
            elif d.get('all_strikes') and _is_num(d.get('flip_far')):
                fp = f"无近端(远端{d['flip_far']:.2f})"
            else:
                fp = _fmt_flip(d)
            delta_info = gex_deltas_txt.get(name, {})
            delta_yi = delta_info.get('delta_yi')
            delta_str = f"{delta_yi:+.2f}亿" if delta_yi is not None else "—"
            dist_pct = d.get('dist_flip_pct')
            if fp_full is not None and dist_pct is not None:
                dist_str = f"{dist_pct:+.2f}%"
            elif _is_num(dist_far):
                dist_str = f"远端{dist_far:+.0f}%"
            elif fp.startswith('无翻转'):
                dist_str = "无翻转"
            else:
                dist_str = "无数据"
            risk_txt = d['risk']
            if fp_full is None and near_sign == 'negative':
                risk_txt = "🟠近端负Gamma"
            lines.append(f"{name:<10} {d['total_gex_yi']:>+11.2f}亿 {delta_str:>10} {fp:>10} {dist_str:>10} {risk_txt}")
        else:
            lines.append(f"{name:<10} {'N/A':>12} {'—':>10} {'N/A':>10} {'—':>10} -")

    for _caliber_note in build_gex_caliber_notes(gex_data, trade_date_raw):
        lines.append("")
        lines.append(_caliber_note.replace('**', ''))
    
    lines.append("")
    lines.append("数据来源: Tushare + AkShare")
    lines.append("案例分析学习材料，不构成投资建议")
    
    return '\n'.join(lines)


# ============ 主函数 ============
def main(target_date=None):
    if target_date is None:
        target_date = datetime.now().strftime('%Y%m%d')

    # 2026-11-25 新增：崩溃检测（必须在跑任何重活之前，才能抓住上次被 SIGKILL 的残留标记）
    _RUN_FLAG = os.path.join(BASE_DIR, 'codeact', 'output', '.midline_running.json')
    _check_previous_crash(_RUN_FLAG)
    _mark_running(_RUN_FLAG, target_date)

    # 2026-11-20 新增：日志自落盘 + 阶段心跳
    _setup_log_tee()
    _start_heartbeat()
    
    # 2026-10-26 新增：dry-run模式
    dry_run = os.environ.get('MIDLINE_DRY_RUN', '') == '1'
    if dry_run:
        print(f"\n🔧 DRY-RUN 模式：构建卡片/告警但不推送飞书/不发邮件")
    
    print(f"\n{'#'*60}")
    print(f"# 中线偏好监控 v1.1 - {target_date}")
    print(f"# CFC(6池) + 期权SKEW/GEX(6标的) + 升贴水 + GEX翻转监测")
    print(f"{'#'*60}")
    
    start_time = time.time()
    stage_timings = {}  # 阶段名 → 耗时秒
    
    try:
        # Step 0: 初始化GEX历史库（首次运行时从已有JSON回填）
        if not os.path.exists(GEX_HIST_FILE):
            print(f"\n{'='*60}")
            print(f"[0/4] 初始化GEX历史库")
            print(f"{'='*60}")
            init_gex_history_from_existing()
        # Step 1: 获取CFC数据
        _enter_stage("CFC计算")
        t_stage = time.time()
        cfc_factors, data_available = get_cfc_data(target_date)
        stage_timings['CFC计算'] = time.time() - t_stage
        _exit_stage()
        
        # Step 2: 获取期权数据
        _enter_stage("期权SKEW/GEX")
        t_stage = time.time()
        skew_data, gex_data, basis_data = get_option_data(target_date)
        stage_timings['期权SKEW/GEX'] = time.time() - t_stage
        _exit_stage()
        
        # Step 3: 构建卡片
        _enter_stage("卡片构建")
        t_stage = time.time()
        date_display = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
        print(f"\n{'='*60}")
        print(f"[3/4] 构建飞书卡片")
        print(f"{'='*60}")
        
        card = build_midline_monitor_card(
            date_display, cfc_factors, skew_data, gex_data, basis_data, data_available
        )
        stage_timings['卡片构建'] = time.time() - t_stage
        _exit_stage()
        
        # Step 3.5: 追加底部红线监测模块（收盘模式，不单独推送飞书）
        _enter_stage("杨康平底部判断")
        t_stage = time.time()
        print(f"\n{'='*60}")
        print(f"[3.5/4] 运行杨康平底部判断（收盘模式）...")
        print(f"{'='*60}")
        
        bottom_result = None
        bottom_triggered_count = 0
        bottom_email_ok = False
        
        if _bottom_monitor_ready:
            try:
                bottom_result = run_bottom_monitor(
                    trading_date=date_display,
                    mode='close',       # 收盘模式（幂等key与盘中分开）
                    force=False,        # 遵循幂等保护
                    skip_push=True,     # 不单独推送飞书，由本脚本统一推送
                )
                bottom_triggered_count = bottom_result.get('triggered_count', 0)
                
                # 将底部监测结果追加到卡片末尾
                if not bottom_result.get('skipped', False):
                    bottom_elements = build_bottom_monitor_section(bottom_result)
                    
                    # 在卡片的 "底部备注" 之前插入底部监测模块
                    # 2026-10-22 修复（A9）：原来用 len(elements)-2 的位置魔法，
                    # 只要卡片尾部元素增减（例如本次新增的数据体检块）就会插错位置。
                    # 现在改为显式锚点：找最后一个 note 元素，插在它前面的 hr 之前。
                    if 'elements' in card and len(card['elements']) >= 2:
                        insert_pos = _find_note_anchor(card['elements'])
                        # 先加一个分割线
                        card['elements'].insert(insert_pos, build_hr())
                        # 再加标题
                        card['elements'].insert(insert_pos + 1, build_section_header("📊 杨康平底部判断"))
                        # 再加底部监测内容
                        for elem in bottom_elements:
                            card['elements'].insert(insert_pos + 2, elem)
                    elif 'elements' in card:
                        # 元素不足时直接追加
                        card['elements'].append(build_hr())
                        card['elements'].append(build_section_header("📊 杨康平底部判断"))
                        card['elements'].extend(bottom_elements)
                
                # 红线>5时触发邮件告警
                if bottom_triggered_count > 5 and not bottom_result.get('skipped', False):
                    print(f"\n📧 杨康平底部判断红线{bottom_triggered_count}>5，触发邮件告警...")
                    try:
                        bottom_email_ok = send_bottom_alert_email(
                            bottom_triggered_count,
                            bottom_result.get('total', 12),
                            bottom_result.get('indicators', {}),
                            bottom_result.get('analysis', {}),
                            bottom_result.get('trading_date', date_display),
                            mode='close'
                        )
                    except Exception as email_err:
                        print(f"❌ 邮件发送异常: {email_err}")
                
            except Exception as bottom_err:
                print(f"⚠️ 杨康平底部判断运行失败（不影响日报主流程）: {bottom_err}")
                bottom_result = None
        
        stage_timings['杨康平底部判断'] = time.time() - t_stage
        _exit_stage()
        
        # Step 4: 推送（交互式卡片优先，失败降级纯文本）
        _enter_stage("飞书推送")
        t_stage = time.time()
        print(f"\n{'='*60}")
        print(f"[4/4] 推送飞书卡片")
        print(f"{'='*60}")
        
        if dry_run:
            # Dry-run：写入预览JSON，不推送
            output_dir = './codeact/output'
            os.makedirs(output_dir, exist_ok=True)
            preview_file = os.path.join(output_dir, f'gex_card_preview_{target_date}.json')
            with open(preview_file, 'w', encoding='utf-8') as f:
                json.dump(card, f, ensure_ascii=False, indent=2, default=str)
            print(f"  🔧 DRY-RUN: 卡片JSON已写入 {preview_file}，跳过飞书推送")
            push_ok = True  # 标记为"成功"以跳过文本降级
        else:
            push_ok = push_card_to_feishu(card_dict=card)
        
        if not push_ok:
            print(f"⚠️ 卡片推送失败，降级为纯文本推送...")
            text_report = generate_text_report(
                date_display, cfc_factors, skew_data, gex_data, basis_data, data_available
            )
            # 追加底部监测文本
            if bottom_result and not bottom_result.get('skipped', False):
                text_report += "\n\n" + "=" * 50
                text_report += "\n📊 杨康平底部判断\n"
                text_report += bottom_result.get('report_md', '')
            push_ok = push_to_feishu(text=text_report)
            if push_ok:
                print("✅ 纯文本推送成功（降级模式）")
            else:
                print("❌ 纯文本推送也失败")
        
        stage_timings['飞书推送'] = time.time() - t_stage
        _exit_stage()
        
        # Step 5: 保存结果到文件
        _enter_stage("结果保存+告警")
        output_dir = './codeact/output'
        os.makedirs(output_dir, exist_ok=True)

        # 2026-10-26：提前计算GEX告警（result dict需要引用）
        update_gex_history(gex_data, target_date)
        gex_deltas_main = get_gex_delta(gex_data, target_date)
        gex_alert_result = check_and_send_gex_alert(gex_data, gex_deltas_main, target_date)
        if gex_alert_result['triggered']:
            print(f"  ⚠️ GEX翻转告警触发: {gex_alert_result['alert_names']}")
        else:
            print(f"  ✅ GEX翻转告警未触发")

        # 2026-10-29：up50 极值告警（>=90% 风险 / <=10% 机会），触发即发邮件
        try:
            up50_alert_result = check_and_send_up50_alert(cfc_factors, target_date, dry_run=dry_run)
        except Exception as _e:
            print(f"  ⚠️ up50极值告警异常（不影响日报主流程）: {_e}")
            up50_alert_result = {'triggered': False, 'items': [], 'new_items': [], 'email_sent': False}

        # 2026-10-30：峰度极值告警（超额峰度 >5），触发即发邮件
        try:
            kurt_alert_result = check_and_send_kurt_alert(cfc_factors, target_date, dry_run=dry_run)
        except Exception as _e:
            print(f"  ⚠️ 峰度极值告警异常（不影响日报主流程）: {_e}")
            kurt_alert_result = {'triggered': False, 'items': [], 'new_items': [], 'email_sent': False}

        result = {
            'trade_date': target_date,
            'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'cfc_pools': len(cfc_factors),
            'option_targets_skew': sum(1 for v in skew_data.values() if v),
            'option_targets_gex': sum(1 for v in gex_data.values() if v),
            'cfc_factors': cfc_factors,
            'skew_data': skew_data,
            # 2026-10-23 回写 flip_point 数值（None=无翻转），使落盘 JSON 与卡片展示一致
            'gex_data': _enrich_gex_flip_points(gex_data),
            'basis_data': basis_data,
            'data_available': data_available,
            # 2026-10-22 新增：把体检结果一并落盘，便于事后复盘"那天到底缺了什么"
            # 注意：health_level 是 build_midline_monitor_card 内的局部变量，
            # 这里在 main 作用域重新算一次，避免 UnboundLocalError。
            'health': dict(zip(('level', 'issues'),
                               build_health_report(cfc_factors, skew_data, gex_data,
                                                   basis_data, data_available, bottom_result))),
            'bottom_monitor': {
                'triggered_count': bottom_triggered_count,
                'total': bottom_result.get('total', 12) if bottom_result else 12,
                'position': bottom_result.get('position', '') if bottom_result else '',
                'email_sent': bottom_email_ok,
            } if bottom_result else None,
            'gex_alert': {
                'triggered': gex_alert_result.get('triggered', False),
                'alert_names': gex_alert_result.get('alert_names', []),
                'email_sent': gex_alert_result.get('email_sent', False),
            },
            'up50_alert': {
                'triggered': up50_alert_result.get('triggered', False),
                'items': [
                    {'pool': i['pool'], 'up50': round(i['up50'] * 100, 2), 'type': i['type']}
                    for i in up50_alert_result.get('items', [])
                ],
                'new_items': [i['pool'] for i in up50_alert_result.get('new_items', [])],
                'email_sent': up50_alert_result.get('email_sent', False),
            },
            'kurt_alert': {
                'triggered': kurt_alert_result.get('triggered', False),
                'items': [
                    {'pool': i['pool'], 'kurt': round(i['kurt'], 2)}
                    for i in kurt_alert_result.get('items', [])
                ],
                'new_items': [i['pool'] for i in kurt_alert_result.get('new_items', [])],
                'email_sent': kurt_alert_result.get('email_sent', False),
            },
        }
        
        output_file = os.path.join(output_dir, f'{target_date}_midline_monitor.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n📁 结果已保存: {output_file}")

        # 2026-11-25：落 tech 指标仓（metric_points），看板/回溯统一从这里取数
        sync_to_tech_store(output_file, target_date)

        # 2026-10-23：回写 CFC 历史库（供风险预测日报读取，修复 historical_factors.json 停更）
        append_cfc_history(cfc_factors, target_date)

        # 耗时统计
        _exit_stage()
        
        elapsed = time.time() - start_time
        print(f"\n⏱️ 总耗时: {elapsed:.1f}秒")
        # 分阶段排行（降序）
        if stage_timings:
            sorted_stages = sorted(stage_timings.items(), key=lambda x: x[1], reverse=True)
            print(f"⏱️ 各阶段耗时排行:")
            for rank, (stage_name, stage_sec) in enumerate(sorted_stages, 1):
                pct = stage_sec / elapsed * 100 if elapsed > 0 else 0
                print(f"  {rank}. {stage_name}: {stage_sec:.1f}秒 ({pct:.0f}%)")
        print(f"✅ 中线偏好监控完成")
        
        # 提交结果
        assessment = assess_midline_preference(cfc_factors, skew_data, gex_data)
        date_display = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
        summary = (
            f"中线偏好监控({date_display})：{assessment['overall']}\n"
            f"CFC：{len(cfc_factors)}/6池成功 | 期权：{sum(1 for v in skew_data.values() if v)}/6标的成功\n"
            f"信号构成：🟢偏多{assessment['bullish']} / 🔴偏空{assessment['bearish']} / ⚖️中性{assessment['neutral']}\n"
            f"飞书推送：{'✅成功' if push_ok else '❌失败'}\n"
        )
        if bottom_result:
            summary += f"杨康平底部判断：{bottom_triggered_count}/12"
            if bottom_triggered_count > 5:
                summary += f" | 📧邮件告警{'✅' if bottom_email_ok else '❌'}"
            summary += "\n"
        if up50_alert_result.get('triggered'):
            _u50 = " / ".join(
                f"{i['pool']}{i['up50']*100:.1f}%{'🔴风险' if i['type'] == 'risk' else '🟢机会'}"
                for i in up50_alert_result.get('items', [])
            )
            summary += f"up50极值：{_u50}"
            if up50_alert_result.get('new_items'):
                summary += f" | 📧邮件{'✅' if up50_alert_result.get('email_sent') else '❌'}"
            else:
                summary += " | 📧今日已发"
            summary += "\n"
        if kurt_alert_result.get('triggered'):
            _kt = " / ".join(
                f"{i['pool']}{i['kurt']:.2f}（实际{i['kurt']+3:.2f}）"
                for i in kurt_alert_result.get('items', [])
            )
            summary += f"峰度极值：{_kt}"
            if kurt_alert_result.get('new_items'):
                summary += f" | 📧邮件{'✅' if kurt_alert_result.get('email_sent') else '❌'}"
            else:
                summary += " | 📧今日已发"
            summary += "\n"
        summary += f"总耗时：{elapsed:.1f}秒"
        # 最慢阶段
        if stage_timings:
            slowest = max(stage_timings.items(), key=lambda x: x[1])
            summary += f"（最慢: {slowest[0]} {slowest[1]:.1f}秒）"
        
        _clear_running()  # 成功收尾，清运行标记
        safe_submit_result(
            result_mode="display_only",
            status="success",
            message=summary,
            data={
                'trade_date': target_date,
                'cfc_pools': len(cfc_factors),
                'option_targets': sum(1 for v in skew_data.values() if v),
                'overall_preference': assessment['overall'],
                'push_success': push_ok,
                'elapsed_seconds': round(elapsed, 1),
            }
        )
        
        return result
        
    except Exception as e:
        import traceback
        error_msg = f"❌ 中线偏好监控执行失败: {e}\n{traceback.format_exc()}"
        print(error_msg)
        
        # 失败时也尝试推送通知
        try:
            date_display = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
            push_to_feishu(text=f"⚠️ 中线偏好监控({date_display})执行失败: {str(e)[:200]}")
        except Exception:
            pass
        
        _clear_running()  # 已发失败通知，清标记避免下次重复告警
        # 提交错误结果
        try:
            safe_submit_result(
                result_mode="notify",
                status="error",
                message=f"中线偏好监控执行失败: {str(e)[:200]}",
                data={'error': str(e)}
            )
        except Exception:
            pass
        
        raise
    finally:
        # 2026-11-20: 无论成功失败都停止心跳线程 + 关闭日志 tee
        try:
            _stop_heartbeat()
        except Exception:
            pass
        try:
            _cleanup_log_tee()
        except Exception:
            pass



if __name__ == '__main__':
    import re
    import argparse
    
    parser = argparse.ArgumentParser(description='中线偏好监控')
    parser.add_argument('date', nargs='?', default=None, help='交易日期 YYYYMMDD')
    args = parser.parse_args()
    
    date = args.date if (args.date and re.match(r'^\d{8}$', args.date)) else None
    main(target_date=date)
