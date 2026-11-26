#!/usr/bin/env python3
"""
科创板/创业板 顶底机会监控
数据源优先级: akshare(中证指数) -> 腾讯财经(实时) -> tushare(token)
历史PE数据本地持久化到 JSON 文件，每周日跑可逐步积累
"""

import os
import sys
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

# ================================================================
# 配置
# ================================================================
EMAIL_CONFIG = {
    'smtp_server': 'smtp.163.com',
    'smtp_port': 465,
    'sender': 'songshang951@163.com',
    'password': 'TBmy7DAsj9aNJdxb',
    'receiver': '410119229@qq.com'
}

# 回测结论的阈值（PE用动态PE口径统一）
THRESHOLDS = {
    '科创50':   {'bottom_pe': 30, 'top_peg': 3.5, 'growth_cap': 50},
    '创业板指': {'bottom_pe': 28, 'top_peg': 3.0, 'growth_cap': 50},
}

# 指数代码映射
INDEX_CODES = {
    '科创50':   {'csindex': '000688', 'tencent': 'sh000688', 'tushare': '000688.SH'},
    '创业板指': {'csindex': None,    'tencent': 'sz399006', 'tushare': '399006.SZ'},
}

# 盈利增速经验值（数据源无法稳定提供时备用）
DEFAULT_GROWTH = {'科创50': 25.0, '创业板指': 20.0}

# 历史数据本地存储
HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pe_history')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}


# ================================================================
# 历史数据持久化（本地JSON，每周日跑逐步积累）
# ================================================================
def load_history(index_name):
    """加载历史PE数据"""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f'{index_name}.json')
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except:
            pass
    return []


def save_history(index_name, history):
    """保存历史PE数据"""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f'{index_name}.json')
    # 保留最近5年
    history = history[-1300:]
    with open(path, 'w') as f:
        json.dump(history, f, ensure_ascii=False)


def append_history(index_name, pe_value, date_str):
    """追加一条历史数据"""
    history = load_history(index_name)
    # 去重（同一天不重复添加）
    if history and history[-1].get('date') == date_str:
        history[-1]['pe'] = pe_value  # 更新当天值
    else:
        history.append({'date': date_str, 'pe': pe_value})
    save_history(index_name, history)
    return history


# ================================================================
# 数据源1: akshare (stock_zh_index_value_csindex 中证指数)
# ================================================================
def fetch_akshare(index_name):
    """
    akshare 中证指数估值
    返回: 市盈率1(静态PE) 和 市盈率2(动态PE)
    注意: 仅支持科创50(000688)，不支持创业板指(399006)
    """
    try:
        import akshare as ak
        code = INDEX_CODES[index_name]['csindex']
        if not code:
            print(f"  [akshare] {index_name} 不支持")
            return None

        print(f"  [akshare] 获取{index_name}({code})...")
        df = ak.stock_zh_index_value_csindex(symbol=code)

        if df is None or len(df) == 0:
            return None

        latest = df.iloc[0]
        pe_static = float(latest['市盈率1'])  # 静态PE
        pe_dynamic = float(latest['市盈率2'])  # 动态PE

        # 历史数据
        pe_history = []
        for _, row in df.iterrows():
            try:
                pe_history.append({
                    'date': str(row['日期'])[:10],
                    'pe': float(row['市盈率2'])  # 统一用动态PE
                })
            except:
                pass

        result = {
            'pe_ttm': pe_dynamic,  # 使用动态PE作为主指标
            'pe_static': pe_static,
            'date': str(latest['日期'])[:10],
            'pe_history': [p['pe'] for p in pe_history],
            'earnings_yoy': None,
            'source': 'akshare_csindex'
        }
        print(f"  [akshare] ✓ PE动态={pe_dynamic:.2f}, PE静态={pe_static:.2f}, 历史{len(pe_history)}条")
        return result

    except ImportError:
        print(f"  [akshare] 未安装")
        return None
    except Exception as e:
        print(f"  [akshare] 失败: {e}")
        return None


# ================================================================
# 数据源2: 腾讯财经爬虫
# ================================================================
def fetch_tencent(index_name):
    """
    腾讯财经实时行情
    字段39: PE(动态)
    注意: 只有实时数据，历史需本地积累
    """
    try:
        code = INDEX_CODES[index_name]['tencent']
        print(f"  [腾讯财经] 获取{index_name}({code})...")

        url = f'http://qt.gtimg.cn/q={code}'
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.encoding = 'gbk'

        if '="' not in r.text:
            print(f"  [腾讯财经] 返回格式异常")
            return None

        data = r.text.split('="')[1].strip('";')
        parts = data.split('~')

        if len(parts) < 40 or not parts[39]:
            print(f"  [腾讯财经] PE字段为空")
            return None

        pe_dynamic = float(parts[39])
        today = datetime.now().strftime('%Y-%m-%d')

        # 追加到本地历史
        history = append_history(index_name, pe_dynamic, today)
        pe_history_list = [p['pe'] for p in history]

        result = {
            'pe_ttm': pe_dynamic,
            'date': today,
            'pe_history': pe_history_list,
            'earnings_yoy': None,
            'source': 'tencent'
        }
        print(f"  [腾讯财经] ✓ PE动态={pe_dynamic:.2f}, 本地历史{len(pe_history_list)}条")
        return result

    except Exception as e:
        print(f"  [腾讯财经] 失败: {e}")
        return None


# ================================================================
# 数据源3: tushare（需要token）
# ================================================================
def fetch_tushare(index_name):
    """tushare获取指数估值（需要设置TUSHARE_TOKEN环境变量）"""
    try:
        import tushare as ts
        token = os.environ.get('TUSHARE_TOKEN', '')
        if not token:
            print(f"  [tushare] 未设置TUSHARE_TOKEN环境变量，跳过")
            return None

        ts_code = INDEX_CODES[index_name]['tushare']
        print(f"  [tushare] 获取{index_name}({ts_code})...")

        pro = ts.pro_api(token)
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=1500)).strftime('%Y%m%d')

        df = pro.index_dailybasic(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields='trade_date,pe_ttm'
        )

        if df is None or len(df) == 0:
            return None

        df = df.sort_values('trade_date')
        latest = df.iloc[-1]
        pe_list = df[df['pe_ttm'] > 0]['pe_ttm'].tolist()

        result = {
            'pe_ttm': float(latest['pe_ttm']),
            'date': str(latest['trade_date'])[:10],
            'pe_history': pe_list,
            'earnings_yoy': None,
            'source': 'tushare'
        }
        print(f"  [tushare] ✓ PE={result['pe_ttm']:.2f}, 历史{len(pe_list)}条")
        return result

    except ImportError:
        print(f"  [tushare] 未安装")
        return None
    except Exception as e:
        print(f"  [tushare] 失败: {e}")
        return None


# ================================================================
# 统一获取（三层fallback）
# ================================================================
def fetch_index_data(index_name):
    """三层数据源: akshare -> 腾讯财经 -> tushare"""
    for fetcher in [fetch_akshare, fetch_tencent, fetch_tushare]:
        data = fetcher(index_name)
        if data:
            if not data.get('earnings_yoy'):
                data['earnings_yoy'] = DEFAULT_GROWTH.get(index_name, 20.0)
            # 合并本地历史数据（腾讯财经的数据已有，akshare/tushare的追加到本地）
            if data.get('source') != 'tencent' and data.get('pe_ttm'):
                append_history(index_name, data['pe_ttm'], data.get('date', datetime.now().strftime('%Y-%m-%d')))
            return data
    return None


# ================================================================
# 信号判断
# ================================================================
def check_signal(index_name, data):
    """检查是否触发顶底信号，返回 (bottom_msg, top_msg)"""
    pe = data['pe_ttm']
    growth = data.get('earnings_yoy', DEFAULT_GROWTH.get(index_name, 20.0))
    pe_history = data.get('pe_history', [])
    config = THRESHOLDS[index_name]

    bottom_msg = None
    top_msg = None

    # 底部: PE < 阈值（PE分位数法）
    if pe < config['bottom_pe']:
        if pe_history:
            percentile = sum(1 for p in pe_history if p < pe) / len(pe_history)
            bottom_msg = f"PE={pe:.1f}倍 (阈值{config['bottom_pe']}倍, 历史分位{percentile:.0%}, 样本{len(pe_history)}条)"
        else:
            bottom_msg = f"PE={pe:.1f}倍 (阈值{config['bottom_pe']}倍)"

    # 顶部: 改良PEG > 阈值
    if growth and growth > 0:
        capped_growth = min(growth, config['growth_cap'])
        peg = pe / capped_growth
        if peg > config['top_peg']:
            top_msg = f"改良PEG={peg:.2f} (PE={pe:.1f}, 增速={capped_growth:.1f}%, 阈值{config['top_peg']})"

    return bottom_msg, top_msg


# ================================================================
# 邮件
# ================================================================
def send_alert(alerts):
    """发送邮件"""
    if not alerts:
        print("无信号，不发送邮件")
        return

    subject = f"⚡ 科创板/创业板 顶底机会提醒 ({datetime.now().strftime('%Y-%m-%d')})"
    body = "检测到以下信号：\n\n"
    body += "\n\n".join(alerts)
    body += "\n\n---\n"
    body += "信号规则（回测验证）：\n"
    body += "• 底部：PE分位数法（科创50<30倍 / 创业板<28倍）\n"
    body += "  → 回测: 科创底部召回率100%, 创业板50%\n"
    body += "• 顶部：改良PEG法（科创50>3.5 / 创业板>3.0）\n"
    body += "  → 回测: 创业板顶部召回率50%, 精确率100%\n"
    body += f"\n检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    body += "数据源: akshare(中证指数) -> 腾讯财经 -> tushare"

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG['sender']
        msg['To'] = EMAIL_CONFIG['receiver']
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        server = smtplib.SMTP_SSL(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        server.login(EMAIL_CONFIG['sender'], EMAIL_CONFIG['password'])
        server.send_message(msg)
        server.quit()
        print("✓ 邮件发送成功")

    except Exception as e:
        print(f"✗ 邮件发送失败: {e}")


# ================================================================
# 主函数
# ================================================================
def main():
    print(f"{'='*60}")
    print(f"科创板/创业板 顶底机会监控")
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"数据源: akshare(中证指数) -> 腾讯财经 -> tushare")
    print(f"{'='*60}")

    alerts = []

    for index_name in ['科创50', '创业板指']:
        print(f"\n[{index_name}]")
        data = fetch_index_data(index_name)

        if not data:
            print(f"  ✗ 三层数据源均失败，跳过")
            continue

        print(f"  当前PE(动态): {data['pe_ttm']:.1f}倍, 日期: {data.get('date', 'N/A')}")
        if data.get('pe_history'):
            print(f"  历史PE: {len(data['pe_history'])}条, 范围[{min(data['pe_history']):.1f}, {max(data['pe_history']):.1f}]")
        print(f"  盈利增速: {data.get('earnings_yoy', 'N/A')}%")
        print(f"  数据来源: {data.get('source', 'unknown')}")

        bottom_msg, top_msg = check_signal(index_name, data)

        if bottom_msg:
            alerts.append(f"🟢 【{index_name} 底部信号】{bottom_msg}\n建议：分批买入")
            print(f"  >>> 触发底部信号！{bottom_msg}")
        if top_msg:
            alerts.append(f"🔴 【{index_name} 顶部信号】{top_msg}\n建议：考虑减仓")
            print(f"  >>> 触发顶部信号！{top_msg}")
        if not bottom_msg and not top_msg:
            print(f"  → 中间地带，无信号")

    print(f"\n{'='*60}")
    if alerts:
        print(f"发现 {len(alerts)} 个信号，发送邮件...")
        send_alert(alerts)
    else:
        print("当前无顶底信号")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
