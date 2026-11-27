#!/usr/bin/env python3
"""
国债期货四因子预测框架 - 完全自动化脚本（零token）
所有数据通过 akshare / 网页爬取自动采集
2026-06-16 重写8个数据采集函数，聚焦pbc.gov.cn + CFETS + akshare
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
import json
import os
import re
import argparse
from datetime import datetime, timedelta
from urllib.parse import urljoin

# 飞书推送工具路径
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 主对话根目录
sys.path.insert(0, ROOT_DIR)

# Python 3.13 compatibility
if 'pkgutil.ImpImporter' not in sys.modules:
    sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})()

import akshare as ak
import pandas as pd
import numpy as np

# ============ 共享LLM模块 ============
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from llm_utils import llm_analyze
    LLM_AVAILABLE = True
except Exception as e:
    print(f"⚠️ LLM模块加载失败: {e}")
    LLM_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    try:
        from coze_workload_identity import requests
    except ImportError:
        import requests
    HAS_WEB_SCRAPER = True
except ImportError:
    HAS_WEB_SCRAPER = False

# ============================================================
# 工具函数
# ============================================================

def safe_fetch(func, *args, desc="data", timeout=30, **kwargs):
    """安全调用数据接口，带超时"""
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(func, *args, **kwargs)
            result = future.result(timeout=timeout)
        if result is None or (hasattr(result, 'empty') and result.empty):
            return None
        return result
    except concurrent.futures.TimeoutError:
        print(f"  ⏰ {desc} 获取超时({timeout}s)，跳过")
        return None
    except Exception as e:
        return None


def fetch_pbc_page(url, timeout=15):
    """获取央行官网页面，强制GBK解码"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Connection': 'keep-alive',
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding or 'gbk'
            return resp.text
    except Exception as e:
        pass
    return None


# ============================================================
# CFETS 数据采集（中国货币网 chinamoney.com.cn）
# ============================================================

_cfets_cache = None

def fetch_cfets_homepage():
    """从CFETS中国货币网首页抓取关键利率数据（DR007/IRS/Shibor等）
    注意：CFETS首页是JS动态加载，直接爬取无法获取利率数据
    此函数作为占位符，实际利率数据从akshare Shibor获取
    """
    global _cfets_cache
    if _cfets_cache is not None:
        return _cfets_cache

    print("\n📊 [CFETS] 尝试抓取中国货币网首页...")
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        resp = requests.get("https://www.chinamoney.com.cn/chinese/", headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"  ⚠ CFETS首页返回 {resp.status_code}")
            _cfets_cache = {}
            return _cfets_cache

        html = resp.text
        try:
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text()
        except:
            text = html

        data = {}

        # 尝试提取数据（CFETS首页是JS动态加载，通常提取不到）
        dr007_match = re.search(r'DR007\s*[#]?\s*([\d.]+)', text)
        if dr007_match:
            data['dr007'] = float(dr007_match.group(1))

        irs_match = re.search(r'FR007\s*1Y\s*[#]?\s*([\d.]+)', text)
        if irs_match:
            data['irs_fr007_1y'] = float(irs_match.group(1))

        if data:
            print(f"  ✓ CFETS首页提取到 {len(data)} 项数据")
        else:
            print(f"  ⚠ CFETS首页为JS动态加载，无法提取利率数据")

        _cfets_cache = data
        return data

    except Exception as e:
        print(f"  ⚠ CFETS首页抓取失败: {e}")
        _cfets_cache = {}
        return {}


# ============================================================
# 数据采集 - 政策信号
# ============================================================

def fetch_omo_net_injection():
    """获取央行OMO净投放（从央行官网爬取）
    策略：抓最近7天公告，计算操作量之和作为近似净投放
    """
    print("\n📊 [政策信号] OMO净投放...")

    pbc_omo_url = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"

    text = fetch_pbc_page(pbc_omo_url)
    if not text:
        print("  ⚠ 无法获取PBC OMO页面")
        return None

    try:
        soup = BeautifulSoup(text, 'html.parser')
    except:
        return None

    links = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text_content = a.get_text().strip()
        if ('公开市场业务交易公告' in text_content or '逆回购' in text_content) and href:
            full_url = urljoin(pbc_omo_url, href)
            date_match = re.search(r'(\d{8})', href)
            date_str = date_match.group(1) if date_match else ''
            links.append({'url': full_url, 'date': date_str, 'title': text_content})

    if not links:
        print("  ⚠ 未找到OMO公告链接")
        return None

    links.sort(key=lambda x: x['date'], reverse=True)

    total_op = 0
    processed = 0
    last_date = ""

    for link in links[:7]:
        detail_text = fetch_pbc_page(link['url'])
        if not detail_text:
            continue

        try:
            detail_soup = BeautifulSoup(detail_text, 'html.parser')
            pure_text = detail_soup.get_text()
        except:
            pure_text = detail_text

        date_m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', pure_text)
        if date_m:
            d = f"{date_m.group(1)}-{date_m.group(2).zfill(2)}-{date_m.group(3).zfill(2)}"
            last_date = d

        op_matches = re.findall(r'开展.*?(\d+)亿元.*?逆回购', pure_text)
        if op_matches:
            for m in op_matches:
                try:
                    amount = int(m)
                    if amount > 0:
                        total_op += amount
                        processed += 1
                except:
                    pass

    if processed > 0 and total_op > 0:
        result = total_op if processed == 1 else round(total_op / processed, 0)
        print(f"  ✓ OMO净投放(近似): {int(result)}亿 (基于{processed}条公告, 最近日期:{last_date})")
        return int(result)
    else:
        print("  ⚠ 未解析到逆回购操作量")
        return None


def fetch_mlf_operation():
    """获取MLF操作（从央行官网MLF子页面）"""
    print("\n📊 [政策信号] MLF操作...")

    today = datetime.now()
    if not (10 <= today.day <= 28):
        print(f"  ℹ 今日({today.day}日)非MLF操作窗口")
        return None, None, None

    pbc_mlf_url = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125437/125446/125873/index.html"

    text = fetch_pbc_page(pbc_mlf_url)
    if not text:
        print("  ⚠ 无法获取PBC MLF页面")
        return None, None, None

    try:
        soup = BeautifulSoup(text, 'html.parser')
    except:
        return None, None, None

    links = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text_content = a.get_text().strip()
        if ('中期借贷便利' in text_content or 'MLF' in text_content) and href:
            full_url = urljoin(pbc_mlf_url, href)
            date_match = re.search(r'(\d{8})', href)
            date_str = date_match.group(1) if date_match else ''
            links.append({'url': full_url, 'date': date_str, 'title': text_content})

    if not links:
        print("  ℹ 未找到MLF公告")
        return None, None, None

    links.sort(key=lambda x: x['date'], reverse=True)

    for link in links[:3]:
        detail_text = fetch_pbc_page(link['url'])
        if not detail_text:
            continue

        try:
            soup_d = BeautifulSoup(detail_text, 'html.parser')
            pure_text = soup_d.get_text()
        except:
            pure_text = detail_text

        date_m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', pure_text)
        op_date = f"{date_m.group(1)}-{date_m.group(2).zfill(2)}-{date_m.group(3).zfill(2)}" if date_m else link['date']

        # 提取操作量
        op_matches = re.findall(r'开展.*?(\d+)亿元.*?MLF', pure_text)
        # 提取期限
        term_matches = re.findall(r'期限为(\d+)年期', pure_text)
        # 提取利率（多种正则）
        rate_matches = re.findall(r'利率为?([0-9.]+)%', pure_text)
        if not rate_matches:
            rate_matches = re.findall(r'利率\s*([0-9.]+)%', pure_text)
        if not rate_matches:
            rate_matches = re.findall(r'(\d+\.\d+)%', pure_text)

        if op_matches:
            操作量 = int(op_matches[0])
            期限 = int(term_matches[0]) if term_matches else None
            利率 = float(rate_matches[0]) if rate_matches else None

            print(f"  ✓ MLF操作: {操作量}亿, 期限:{期限}年, 利率:{利率}%, 日期:{op_date}")
            return 操作量, 利率, op_date

    print("  ℹ 本月无MLF操作记录")
    return None, None, None


def fetch_bond_buy_sell():
    """获取央行国债买卖操作（从央行官网国债买卖子页面）
    策略：优先取当月最新数据；非月末/月初窗口时，回退使用最近一次公布的数据（上月末）"""
    print("\n📊 [政策信号] 央行国债买卖...")

    pbc_bond_url = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/5442785/index.html"

    text = fetch_pbc_page(pbc_bond_url)
    if not text:
        print("  ⚠ 无法获取PBC国债买卖页面，尝试上月数据回退")
        return _bond_buy_sell_fallback()

    try:
        soup = BeautifulSoup(text, 'html.parser')
    except:
        return _bond_buy_sell_fallback()

    links = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text_content = a.get_text().strip()
        if ('国债买卖' in text_content or '国债购买' in text_content) and href:
            full_url = urljoin(pbc_bond_url, href)
            date_match = re.search(r'(\d{8})', href)
            date_str = date_match.group(1) if date_match else ''
            links.append({'url': full_url, 'date': date_str, 'title': text_content})

    if not links:
        print("  ℹ 未找到国债买卖公告，尝试上月数据回退")
        return _bond_buy_sell_fallback()

    links.sort(key=lambda x: x['date'], reverse=True)

    for link in links[:3]:
        detail_text = fetch_pbc_page(link['url'])
        if not detail_text:
            continue

        try:
            soup_d = BeautifulSoup(detail_text, 'html.parser')
            pure_text = soup_d.get_text()
        except:
            pure_text = detail_text

        if '未开展' in pure_text or '暂无' in pure_text:
            print(f"  ℹ {link['date']}: 未开展国债买卖操作")
            return 0

        buy_matches = re.findall(r'买入.*?(\d+)亿', pure_text)
        sell_matches = re.findall(r'卖出.*?(\d+)亿', pure_text)

        if buy_matches or sell_matches:
            total_buy = sum(int(m) for m in buy_matches)
            total_sell = sum(int(m) for m in sell_matches)
            net = total_buy - total_sell
            direction = "净买入" if net > 0 else "净卖出"
            date_desc = link.get('date', '')
            today = datetime.now()
            is_current_month = date_desc[:6] == today.strftime('%Y%m')
            fallback_tag = "" if is_current_month else f"(回退{date_desc[:4]}-{date_desc[4:6]})"
            print(f"  ✓ 国债买卖{fallback_tag}: {direction}{abs(net)}亿 (买入{total_buy}亿, 卖出{total_sell}亿)")
            return net

    print("  ℹ 未解析到国债买卖数据，尝试上月数据回退")
    return _bond_buy_sell_fallback()


def _bond_buy_sell_fallback():
    """国债买卖回退：从历史评分数据中取最近一次非空值"""
    output_dir = _cp("/Coze/Drive/蓁宝/所有对话/主对话/国债期货四因子")
    if not os.path.exists(output_dir):
        output_dir = _cp("/Coze/Drive/蓁宝/所有对话/主对话/国债期货四因子")
    if not os.path.exists(output_dir):
        print("  ⚠ 回退失败：输出目录不存在")
        return None

    try:
        import glob
        json_files = sorted(glob.glob(os.path.join(output_dir, "*_评分数据.json")), reverse=True)

        for jf in json_files[:8]:
            try:
                with open(jf, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                detail = data.get('policy_detail', '') or ''
                m = re.search(r'国债买卖=([-\d]+)亿', detail)
                if m:
                    val = int(m.group(1))
                    fname = os.path.basename(jf)
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', fname)
                    date_tag = date_match.group(1) if date_match else ''
                    print(f"  ✓ 国债买卖(回退{date_tag}): {val}亿（取自历史数据）")
                    return val
            except:
                continue

        print("  ℹ 历史数据中也未找到国债买卖值")
    except Exception as e:
        print(f"  ⚠ 回退异常: {e}")

    return None


def fetch_dr007():
    """获取DR007（优先CFETS，再Shibor 1W代理）"""
    print("\n📊 [资金信号] DR007...")

    # 优先从CFETS（占位，实际从Shibor获取）
    cfets = fetch_cfets_homepage()
    if cfets and 'dr007' in cfets and cfets['dr007']:
        print(f"  ✓ DR007(CFETS): {cfets['dr007']}%")
        return cfets['dr007'], datetime.now().strftime('%Y-%m-%d')

    # Fallback: 从Shibor 1W作为DR007代理
    try:
        df = safe_fetch(ak.macro_china_shibor_all, desc="Shibor")
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            date_str = str(latest.iloc[0])[:10]

            dr007_val = None
            for col in df.columns:
                if '1W' in str(col) or '1周' in str(col):
                    try:
                        dr007_val = float(latest[col])
                        break
                    except:
                        pass

            if dr007_val is None and '1W-定价' in df.columns:
                dr007_val = float(latest['1W-定价'])

            if dr007_val:
                print(f"  ✓ DR007(1W Shibor代理): {dr007_val}% ({date_str})")
                return dr007_val, date_str
    except Exception as e:
        print(f"  ⚠ Shibor fallback失败: {e}")

    print("  ⚠ DR007获取失败")
    return None, ""


def fetch_shibor_1m():
    """获取1个月Shibor"""
    print("\n📊 [资金信号] 1M Shibor...")

    try:
        df = safe_fetch(ak.macro_china_shibor_all, desc="Shibor")
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            date_str = str(latest.iloc[0])[:10]

            shibor_1m_val = None
            for col in df.columns:
                if '1M' in str(col) or '1个月' in str(col):
                    try:
                        shibor_1m_val = float(latest[col])
                        break
                    except:
                        pass

            if shibor_1m_val is None and '1M-定价' in df.columns:
                shibor_1m_val = float(latest['1M-定价'])

            if shibor_1m_val:
                print(f"  ✓ 1M Shibor: {shibor_1m_val}% ({date_str})")
                return shibor_1m_val, date_str
    except:
        pass

    return None, ""


def fetch_cd_rate_1y():
    """获取1年期AAA同业存单利率
    策略：Shibor 1Y + 约20bp利差（CD通常高于Shibor 1Y约20-30bp）
    """
    print("\n📊 [资金信号] 1Y AAA同业存单...")

    try:
        df = safe_fetch(ak.macro_china_shibor_all, desc="Shibor")
        if df is not None and not df.empty:
            latest = df.iloc[-1]

            shibor_1y_val = None
            for col in df.columns:
                if '1Y' in str(col) and '定价' in str(col):
                    try:
                        shibor_1y_val = float(latest[col])
                        break
                    except:
                        pass

            if shibor_1y_val is None and '1Y-定价' in df.columns:
                shibor_1y_val = float(latest['1Y-定价'])

            if shibor_1y_val:
                # CD 1Y通常高于Shibor 1Y约20-30bp，取25bp
                cd_rate = shibor_1y_val + 0.25
                print(f"  ✓ 1Y AAA CD (Shibor1Y {shibor_1y_val}% + 0.25%): {cd_rate:.3f}%")
                return cd_rate
    except:
        pass

    print("  ℹ CD利率需手动补充")
    return None


def fetch_repo_volume():
    """获取银行间质押式回购成交量（万亿元）
    策略1：Playwright 抓取CFETS每日速递质押式回购日报（JS动态渲染）
    策略2：东方财富GC001历史数据估算
    策略3：历史数据回退"""
    print("\n📊 [资金信号] 质押式回购成交量...")

    # ===== 策略1：Playwright 抓CFETS每日速递 =====
    try:
        from playwright.sync_api import sync_playwright

        url = "https://www.chinamoney.com.cn/chinese/mtdexdaily/?tab=2"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1600, "height": 2500})
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(5000)

            # JS提取表格数据
            js_code = """
() => {
    const tables = document.querySelectorAll('table');
    const result = [];
    tables.forEach((table, idx) => {
        const rows = table.querySelectorAll('tr');
        const tableData = [];
        rows.forEach(row => {
            const cells = row.querySelectorAll('th, td');
            const rowData = [];
            cells.forEach(cell => rowData.push(cell.innerText.trim()));
            if (rowData.length > 0) tableData.push(rowData);
        });
        if (tableData.length > 0) result.push(tableData);
    });
    return result;
}
"""
            tables = page.evaluate(js_code)
            browser.close()

        # 找含成交量列的表格，累加所有品种
        total_vol = 0
        for table in tables:
            if not table:
                continue
            header = [h.strip() for h in table[0]]
            if any('成交量' in h for h in header) and any('品种' in h for h in header):
                vol_idx = None
                for i, h in enumerate(header):
                    if '成交量' in h and '亿元' in h:
                        vol_idx = i
                        break
                if vol_idx is None:
                    continue
                for row in table[1:]:
                    if len(row) > vol_idx and row[0] != '合计':
                        try:
                            vol = float(row[vol_idx].replace(',', ''))
                            total_vol += vol
                        except (ValueError, IndexError):
                            pass

        if total_vol > 1000:
            vol_tw = round(total_vol / 10000, 2)
            print(f"  ✓ 回购成交量(CFETS日报): {vol_tw}万亿 ({total_vol:.0f}亿元, 全品种合计)")
            return vol_tw
        else:
            print(f"  ⚠ CFETS数据异常，total={total_vol}")

    except Exception as e:
        print(f"  ⚠ Playwright抓取CFETS失败: {e}")

    # ===== 策略2：东方财富 GC001 估算（降级）=====
    try:
        df = safe_fetch(ak.bond_buy_back_hist_em, symbol='GC001', desc="回购历史", timeout=30)
        if df is not None and not df.empty and len(df) >= 5:
            vol_col = None
            for col in df.columns:
                col_l = str(col).lower()
                if 'volume' in col_l or '成交' in str(col) or '量' in str(col):
                    vol_col = col
                    break
            if vol_col:
                recent = pd.to_numeric(df[vol_col], errors='coerce').dropna().tail(5)
                if len(recent) > 0:
                    avg_vol = recent.mean()
                    vol_tw = round(avg_vol / 10000, 2)
                    print(f"  ✓ 回购成交量(GC001估算{avg_vol:.0f}亿 ≈ {vol_tw}万亿)")
                    return vol_tw
    except Exception as e:
        print(f"  ⚠ 东方财富回购数据失败: {e}")

    # ===== 策略3：历史数据回退 =====
    try:
        output_dir = _cp("/Coze/Drive/蓁宝/所有对话/主对话/国债期货四因子")
        if not os.path.exists(output_dir):
            output_dir = _cp("/Coze/Drive/蓁宝/所有对话/主对话/国债期货四因子")
        import glob
        json_files = sorted(glob.glob(os.path.join(output_dir, "*_评分数据.json")), reverse=True)
        for jf in json_files[:8]:
            try:
                with open(jf, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                detail = data.get('fund_detail', '') or ''
                m = re.search(r'回购量=([\d.]+)万亿', detail)
                if m:
                    val = float(m.group(1))
                    fname = os.path.basename(jf)
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', fname)
                    date_tag = date_match.group(1) if date_match else ''
                    print(f"  ✓ 回购成交量(回退{date_tag}): {val}万亿（取自历史数据）")
                    return val
            except:
                continue
        print("  ℹ 历史数据中也未找到回购量值")
    except Exception as e:
        print(f"  ⚠ 历史回退失败: {e}")

    print("  ℹ 回购成交量需手动补充")
    return None


def fetch_tl_position_change():
    """获取TL期货持仓变化（多空力量对比）
    使用akshare get_cffex_rank_table获取中金所持仓排名
    """
    print("\n📊 [情绪信号] TL持仓变化...")

    # 尝试多个交易日（T, T-1, T-2, ...），因为持仓数据周末/非交易日不发布
    today = datetime.now()
    dates_to_try = [today.strftime('%Y%m%d')]
    for i in range(1, 5):
        dates_to_try.append((today - timedelta(days=i)).strftime('%Y%m%d'))

    rank_data = None
    used_date = None
    for date_str in dates_to_try:
        try:
            result = ak.get_cffex_rank_table(date=date_str, vars_list=['TL'])
            if result and isinstance(result, dict) and len(result) > 0:
                rank_data = result
                used_date = date_str
                print(f"  ℹ TL持仓数据取自 {used_date} ({date_str})")
                break
        except Exception:
            pass

    # 尝试获取TL持仓数据（遍历多个可能交易日，因为持仓数据周末不发布）
    rank_data = None
    used_date = None
    today_dt = datetime.now()
    for i in range(5):
        date_str = (today_dt - timedelta(days=i)).strftime('%Y%m%d')
        try:
            result = ak.get_cffex_rank_table(date=date_str, vars_list=['TL'])
            if result and isinstance(result, dict) and len(result) > 0:
                rank_data = result
                used_date = date_str
                break
        except Exception:
            pass

    if rank_data and len(rank_data) > 0:
        total_long_oi = 0
        total_short_oi = 0
        total_long_chg = 0
        total_short_chg = 0
        contracts_found = []

        for contract, df in rank_data.items():
            if df is not None and not df.empty:
                cols = df.columns.tolist()
                if 'long_open_interest' in cols:
                    total_long_oi += pd.to_numeric(df['long_open_interest'], errors='coerce').sum()
                if 'short_open_interest' in cols:
                    total_short_oi += pd.to_numeric(df['short_open_interest'], errors='coerce').sum()
                if 'long_open_interest_chg' in cols:
                    total_long_chg += pd.to_numeric(df['long_open_interest_chg'], errors='coerce').sum()
                if 'short_open_interest_chg' in cols:
                    total_short_chg += pd.to_numeric(df['short_open_interest_chg'], errors='coerce').sum()
                contracts_found.append(contract)

        if total_long_oi > 0 and total_short_oi > 0:
            net_pos = total_long_oi - total_short_oi
            net_chg = total_long_chg - total_short_chg
            total_oi = total_long_oi + total_short_oi
            net_ratio = net_pos / total_oi * 100

            base_signal = max(20, min(80, 50 + net_ratio * 0.8))
            if net_chg > 5000:
                base_signal = min(80, base_signal + 10)
                desc_extra = f"多头增仓{int(total_long_chg)}手"
            elif net_chg < -5000:
                base_signal = max(20, base_signal - 10)
                desc_extra = f"空头增仓{int(abs(total_short_chg))}手"
            else:
                desc_extra = "多空持仓稳定"

            signal = round(base_signal, 1)
            desc = f"净持仓{int(net_pos)}手(多{int(total_long_oi)}空{int(total_short_oi)}), {desc_extra}"
            print(f"  ✓ TL持仓(数据源:{used_date}): {desc}")
            print(f"    → 信号:{signal} (net_chg={int(net_chg)}手, 净多比={net_ratio:.1f}%)")
            return signal, desc
        else:
            print(f"  ⚠ TL持仓数据为空 (contracts: {contracts_found})")
    else:
        print(f"  ⚠ TL持仓排名无可用数据（尝试T至T-4日），使用成交量Fallback")

    # Fallback: 用成交量变化
    print("  ℹ 使用TL成交量变化Fallback...")
    try:
        today_str = datetime.now().strftime('%Y%m%d')
        df = safe_fetch(ak.futures_zh_hist_sina, symbol="TL0",
                        start_date=today_str, end_date=today_str, desc="TL期货")
        if df is not None and len(df) >= 2:
            vol_latest = float(df['成交量'].iloc[-1])
            vol_prev = float(df['成交量'].iloc[-2])
            vol_change_pct = ((vol_latest - vol_prev) / vol_prev * 100) if vol_prev > 0 else 0
            close_latest = float(df['收盘'].iloc[-1])
            close_prev = float(df['收盘'].iloc[-2])
            price_change = close_latest - close_prev

            if vol_change_pct > 5 and price_change > 0:
                signal, desc = 70, "增仓+上涨"
            elif vol_change_pct < -5 and price_change > 0:
                signal, desc = 50, "减仓+上涨"
            elif vol_change_pct > 5 and price_change < 0:
                signal, desc = 30, "增仓+下跌"
            elif vol_change_pct < -5 and price_change < 0:
                signal, desc = 25, "减仓+下跌"
            else:
                signal, desc = 50, "无明显变化"

            print(f"  ✓ TL成交量Fallback: {vol_change_pct:.2f}%, {desc}")
            return signal, desc
    except:
        pass

    print("  ⚠ TL持仓数据获取失败")
    return None, None


def fetch_bond_yield_spread():
    """获取10Y-30Y国债利差（从新浪国债期货获取国债现货收益率）"""
    print("\n📊 [情绪信号] 10Y-30Y利差...")

    # 从新浪获取国债现货数据
    try:
        df_10y = safe_fetch(ak.bond_gb_zh_sina, symbol='中国10年期国债', desc="10Y国债", timeout=15)
        df_30y = safe_fetch(ak.bond_gb_zh_sina, symbol='中国30年期国债', desc="30Y国债", timeout=15)

        if df_10y is not None and not df_10y.empty and df_30y is not None and not df_30y.empty:
            latest_10y = df_10y.iloc[-1]
            latest_30y = df_30y.iloc[-1]

            # 'close'列是收益率（%）
            yield_10y = float(latest_10y['close'])
            yield_30y = float(latest_30y['close'])

            if yield_10y > 0 and yield_30y > 0:
                spread_bp = round((yield_30y - yield_10y) * 100, 1)  # 转为bp
                date_10y = str(latest_10y['date'])
                date_30y = str(latest_30y['date'])

                print(f"  ✓ 10Y={yield_10y:.3f}%, 30Y={yield_30y:.3f}%, 利差={spread_bp}bp")
                print(f"    (10Y日期:{date_10y}, 30Y日期:{date_30y})")
                return spread_bp
    except Exception as e:
        print(f"  ⚠ 新浪国债数据失败: {e}")

    # Fallback: 使用akshare债券收益率
    try:
        today = datetime.now().strftime('%Y%m%d')
        df_yield = safe_fetch(ak.bond_china_yield, start_date=today,
                               end_date=today, desc="国债收益率", timeout=25)
        if df_yield is not None and not df_yield.empty:
            latest = df_yield.iloc[-1]
            for _, row in df_yield.iterrows():
                if '国债' in str(row.get('曲线名称', '')):
                    yield_10y = None
                    yield_30y = None
                    for col in df_yield.columns:
                        if '10' in str(col) and '年' in str(col):
                            try:
                                yield_10y = float(row[col])
                            except:
                                pass
                        if '30' in str(col) and '年' in str(col):
                            try:
                                yield_30y = float(row[col])
                            except:
                                pass
                    if yield_10y and yield_30y and yield_10y > 0:
                        spread_bp = round((yield_30y - yield_10y) * 100, 1)
                        print(f"  ✓ 10Y={yield_10y:.3f}%, 30Y={yield_30y:.3f}%, 利差={spread_bp}bp (akshare)")
                        return spread_bp
    except:
        pass

    print("  ⚠ 10Y-30Y利差获取失败")
    return None


def fetch_irs_rate():
    """获取IRS利率（FR007 1Y）
    策略：推导 + Shibor利差
    IRS FR007 1Y ≈ Shibor 1Y + 利差(约30-50bp)
    """
    print("\n📊 [情绪信号] IRS利率...")

    # 获取Shibor 1Y
    try:
        df = safe_fetch(ak.macro_china_shibor_all, desc="Shibor")
        if df is not None and not df.empty:
            latest = df.iloc[-1]

            shibor_1y_val = None
            for col in df.columns:
                if '1Y' in str(col) and '定价' in str(col):
                    try:
                        shibor_1y_val = float(latest[col])
                        break
                    except:
                        pass
            if shibor_1y_val is None and '1Y-定价' in df.columns:
                shibor_1y_val = float(latest['1Y-定价'])

            if shibor_1y_val:
                # IRS FR007 1Y通常高于Shibor 1Y约30-50bp，取40bp
                irs_val = shibor_1y_val + 0.40
                date_str = str(latest.get('日期', latest.iloc[0]))[:10]

                # 信号判断
                if irs_val < 1.35:
                    irs_signal = 70
                    desc = "IRS偏低(宽松信号)"
                elif irs_val < 1.45:
                    irs_signal = 60
                    desc = "IRS偏低"
                elif irs_val < 1.55:
                    irs_signal = 50
                    desc = "IRS中性"
                elif irs_val < 1.65:
                    irs_signal = 40
                    desc = "IRS偏高"
                else:
                    irs_signal = 30
                    desc = "IRS偏高(偏紧信号)"

                print(f"  ✓ IRS FR007 1Y (Shibor1Y {shibor_1y_val}% + 0.40%): {irs_val:.3f}%")
                print(f"  ✓ IRS信号: {irs_signal} → {desc}")
                return irs_signal, desc
    except Exception as e:
        print(f"  ⚠ IRS推导失败: {e}")

    print("  ℹ IRS数据需手动补充")
    return None, None


def fetch_vol_position_ratio():
    """获取成交量/持仓比"""
    print("\n📊 [情绪信号] 成交/持仓比...")
    print("  ℹ 需从中金所查询")
    return None


# ============================================================
# 数据采集 - 股债信号
# ============================================================

def fetch_stock_index_5d():
    """获取股指近5日涨跌幅"""
    print("\n📊 [股债信号] 股指5日涨跌幅...")

    results = {}
    indices = {
        '上证指数': 'sh000001',
        '创业板指': 'sz399006',
        '科创50': 'sh000688',
    }

    for name, code in indices.items():
        try:
            df = safe_fetch(ak.stock_zh_index_daily, symbol=code, desc=name)
            if df is not None and len(df) >= 5:
                close_col = 'close' if 'close' in df.columns else df.columns[-1]
                latest_5 = df[close_col].iloc[-5:]
                pct_change = round((latest_5.iloc[-1] / latest_5.iloc[0] - 1) * 100, 2)
                results[name] = pct_change
                print(f"  ✓ {name}: {pct_change}%")
        except:
            pass

    return results if results else None


def fetch_northbound_5d():
    """获取北向资金近5日净流入
    注意：2024年8月起北向资金停止实时公布，返回None
    """
    print("\n📊 [股债信号] 北向资金...")

    try:
        hgt = safe_fetch(ak.stock_hsgt_hist_em, symbol='沪股通', desc="沪股通", timeout=20)
        sgt = safe_fetch(ak.stock_hsgt_hist_em, symbol='深股通', desc="深股通", timeout=20)

        total_5d = 0
        count = 0

        for name, df in [('沪股通', hgt), ('深股通', sgt)]:
            if df is not None and not df.empty:
                net_col = None
                for col in df.columns:
                    if '净流入' in str(col) or 'Net' in str(col):
                        net_col = col
                        break

                if net_col:
                    last5 = pd.to_numeric(df[net_col], errors='coerce').iloc[-5:]
                    last5 = last5.dropna()
                    if len(last5) > 0:
                        net_sum = last5.sum()
                        if pd.notna(net_sum) and abs(net_sum) > 1:
                            total_5d += net_sum
                            count += 1
                            unit_label = "亿" if abs(net_sum) > 10000 else "万"
                            print(f"  ✓ {name}近5日净流入: {round(net_sum, 2)}{unit_label}")

        if count > 0:
            # 转为亿元
            total_5d_billion = total_5d / 1e4 if abs(total_5d) < 1e6 else total_5d
            print(f"  ✓ 北向近5日合计净流入: {round(total_5d_billion, 2)}亿")
            return round(total_5d_billion, 2)

    except Exception as e:
        print(f"  ⚠ akshare北向接口失败: {e}")

    print("  ℹ 北向资金已停止实时公布(2024年8月起)，标注为缺失")
    return None


# ============================================================
# 评分计算（完全自动化，无LLM）
# ============================================================

def calc_policy_score(omo_val=None, mlf_score=None, bond_buy_score=None):
    """政策信号自动评分"""

    scores = []
    details = []

    if omo_val is not None:
        if omo_val > 1000:
            s = 70
        elif omo_val < -1000:
            s = 30
        elif omo_val > 0:
            s = 50 + min(20, omo_val / 100)
        else:
            s = 50 + max(-20, omo_val / 100)
        s = round(s, 1)
        scores.append(s)
        details.append(f"OMO={omo_val}亿→{s}")
    else:
        details.append("OMO=缺失")

    if mlf_score is not None:
        scores.append(mlf_score)
        details.append(f"MLF→{mlf_score}")
    else:
        details.append("MLF=缺失")

    if bond_buy_score is not None:
        if bond_buy_score > 500:
            s = 70
        elif bond_buy_score > 200:
            s = 60
        elif bond_buy_score > 0:
            s = 50
        elif bond_buy_score < -100:
            s = 30
        else:
            s = 40
        scores.append(s)
        details.append(f"国债买卖={bond_buy_score}亿→{s}")
    else:
        details.append("国债买卖=缺失")

    if not scores:
        return 50, "数据缺失，使用中性50"

    avg_score = round(sum(scores) / len(scores), 1)
    weight = len(scores) / 3.0
    if weight < 1:
        avg_score = round(avg_score * (0.5 + 0.5 * weight), 1)

    return avg_score, "; ".join(details)


def calc_fund_score(dr007=None, shibor_1m=None, cd_1y=None, repo_vol=None):
    """资金信号自动评分"""

    scores = []
    details = []

    if dr007 is not None:
        if dr007 < 1.35:
            s = 70
        elif dr007 < 1.40:
            s = 60
        elif dr007 < 1.50:
            s = 50
        elif dr007 < 1.55:
            s = 40
        else:
            s = 30
        scores.append(s)
        details.append(f"DR007={dr007}%→{s}")
    else:
        details.append("DR007=缺失")

    if shibor_1m is not None:
        if shibor_1m < 1.40:
            s = 70
        elif shibor_1m < 1.50:
            s = 60
        elif shibor_1m < 1.60:
            s = 50
        else:
            s = 40
        scores.append(s)
        details.append(f"1M Shibor={shibor_1m}%→{s}")
    else:
        details.append("1M Shibor=缺失")

    if cd_1y is not None:
        if cd_1y < 1.60:
            s = 70
        elif cd_1y < 1.70:
            s = 60
        elif cd_1y < 1.80:
            s = 50
        else:
            s = 40
        scores.append(s)
        details.append(f"1Y CD={cd_1y:.3f}%→{s}")
    else:
        details.append("1Y CD=缺失")

    if repo_vol is not None:
        if repo_vol > 5:
            s = 70
        elif repo_vol > 3:
            s = 50
        else:
            s = 30
        scores.append(s)
        details.append(f"回购量={repo_vol}万亿→{s}")
    else:
        details.append("回购量=缺失")

    if not scores:
        return 50, "数据缺失，使用中性50"

    avg_score = round(sum(scores) / len(scores), 1)
    weight = len(scores) / 4.0
    if weight < 1:
        avg_score = round(avg_score * (0.5 + 0.5 * weight), 1)

    return avg_score, "; ".join(details)


def calc_sentiment_score(tl_pos_signal=None, bond_spread=None, irs_signal=None, vol_pos_ratio=None):
    """情绪信号自动评分"""

    scores = []
    details = []

    if tl_pos_signal is not None:
        scores.append(tl_pos_signal)
        details.append(f"TL持仓→{tl_pos_signal}")
    else:
        details.append("TL持仓=缺失")

    if bond_spread is not None:
        if bond_spread > 50:
            s = 70
        elif bond_spread > 30:
            s = 50
        else:
            s = 30
        scores.append(s)
        details.append(f"利差={bond_spread}bp→{s}")
    else:
        details.append("利差=缺失")

    if irs_signal is not None:
        scores.append(irs_signal)
        details.append(f"IRS→{irs_signal}")
    else:
        details.append("IRS=缺失")

    if vol_pos_ratio is not None:
        if vol_pos_ratio > 1.5:
            s = 40
        elif vol_pos_ratio > 0.5:
            s = 50
        else:
            s = 60
        scores.append(s)
        details.append(f"成交/持仓比={vol_pos_ratio}→{s}")
    else:
        details.append("成交/持仓比=缺失")

    if not scores:
        return 50, "数据缺失，使用中性50"

    avg_score = round(sum(scores) / len(scores), 1)
    weight = len(scores) / 4.0
    if weight < 1:
        avg_score = round(avg_score * (0.5 + 0.5 * weight), 1)

    return avg_score, "; ".join(details)


def calc_stock_bond_score(index_5d=None, northbound_5d=None):
    """股债信号自动评分"""

    scores = []
    details = []

    avg_5d = None
    if index_5d and isinstance(index_5d, dict):
        avg_5d = round(sum(index_5d.values()) / len(index_5d), 2)
    elif isinstance(index_5d, (int, float)):
        avg_5d = index_5d

    if avg_5d is not None:
        if avg_5d > 5:
            s = 20
        elif avg_5d > 2:
            s = 35
        elif avg_5d > -2:
            s = 50
        elif avg_5d > -5:
            s = 65
        else:
            s = 80
        scores.append(s)
        details.append(f"股指5日={avg_5d}%→{s}")
    else:
        details.append("股指=缺失")

    if northbound_5d is not None:
        if northbound_5d > 100:
            s = 20
        elif northbound_5d > -100:
            s = 50
        else:
            s = 80
        scores.append(s)
        details.append(f"北向5日={northbound_5d}亿→{s}")
    else:
        details.append("北向=缺失")

    if not scores:
        return 50, "数据缺失，使用中性50"

    avg_score = round(sum(scores) / len(scores), 1)
    weight = len(scores) / 2.0
    if weight < 1:
        avg_score = round(avg_score * (0.5 + 0.5 * weight), 1)

    return avg_score, "; ".join(details)


# ============================================================
# 方向判断
# ============================================================

def judge_direction(total_score):
    """根据总分判断方向"""
    if total_score > 60:
        return "看多", "多头仓位70-80%", "🟢"
    elif total_score > 50:
        return "震荡偏多", "多头仓位40-50%", "🟡"
    elif total_score > 40:
        return "震荡观望", "轻仓或空仓", "🟠"
    else:
        return "看空", "空仓或反手做空", "🔴"


# ============================================================
# 主流程
# ============================================================

def bond_llm_analysis(result: dict) -> str:
    """调用DeepSeek V4 Flash进行国债期货四因子综合研判"""
    if not LLM_AVAILABLE:
        return ""

    system_prompt = """你是国债期货分析师，基于四因子框架（政策30%/资金30%/情绪20%/股债20%）进行综合研判。

分析框架：
1. 政策信号（30%）：央行OMO操作方向、LPR变动、降准预期——政策宽松利多国债期货
2. 资金信号（30%）：Shibor/CD利率/资金面松紧——资金宽松利多国债期货
3. 情绪信号（20%）：TL持仓变化、技术指标——多头增仓+技术多头利多
4. 股债信号（20%）：股债跷跷板效应、风险偏好——股市走弱利多国债期货

输出要求：
- 总分解读：根据四因子总分给出方向判断（看多>0.3/看空<-0.3/震荡）
- 3-4段核心研判，每段聚焦一个因子的关键发现
- 给出操作建议：多/空/观望
- 数据不足的因子要明确指出"""

    llm_input = {
        "date": result.get("date"),
        "total_score": result.get("total_score"),
        "direction": result.get("direction"),
        "position": result.get("position"),
        "coverage": result.get("coverage_percent"),
        "missing": result.get("missing_items", []),
        "factors": {
            "policy": {
                "score": result.get("policy_score"),
                "detail": result.get("policy_detail", "")
            },
            "fund": {
                "score": result.get("fund_score"),
                "detail": result.get("fund_detail", "")
            },
            "sentiment": {
                "score": result.get("sentiment_score"),
                "detail": result.get("sentiment_detail", "")
            },
            "stock_bond": {
                "score": result.get("stock_bond_score"),
                "detail": result.get("stock_bond_detail", "")
            }
        }
    }

    return llm_analyze(
        system_prompt,
        json.dumps(llm_input, ensure_ascii=False, indent=2),
        max_tokens=1500
    ) or ""


def _run_analysis():
    """实际的分析逻辑（原main函数内容）"""

    print("=" * 60)
    print("国债期货四因子周报 - 完全自动化版")
    print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # ── 1. 数据采集 ──
    print("\n" + "=" * 40)
    print("第一步：数据采集")
    print("=" * 40)

    # 政策信号
    omo_val = fetch_omo_net_injection()
    mlf_op, mlf_rate, _ = fetch_mlf_operation()
    bond_buy_val = fetch_bond_buy_sell()

    if mlf_op is not None:
        if mlf_rate is not None:
            current_mlf = 2.5
            mlf_score = 80 if mlf_rate < current_mlf else 50
        else:
            # 有MLF操作但无利率，按操作规模评分（6000亿=大规模操作→50分）
            mlf_score = 50
    else:
        mlf_score = None

    # 资金信号
    dr007_val, dr007_date = fetch_dr007()
    shibor_1m_val, shibor_date = fetch_shibor_1m()
    cd_1y_val = fetch_cd_rate_1y()
    repo_vol_val = fetch_repo_volume()

    # 情绪信号
    tl_pos_signal, tl_desc = fetch_tl_position_change()
    bond_spread_val = fetch_bond_yield_spread()
    irs_signal, irs_desc = fetch_irs_rate()
    vol_pos_ratio = fetch_vol_position_ratio()

    # 股债信号
    stock_5d = fetch_stock_index_5d()
    northbound_val = fetch_northbound_5d()

    # ── 2. 评分计算 ──
    print("\n" + "=" * 40)
    print("第二步：评分计算")
    print("=" * 40)

    policy_score, policy_detail = calc_policy_score(omo_val, mlf_score, bond_buy_val)
    fund_score, fund_detail = calc_fund_score(dr007_val, shibor_1m_val, cd_1y_val, repo_vol_val)
    sentiment_score, sentiment_detail = calc_sentiment_score(tl_pos_signal, bond_spread_val, irs_signal, vol_pos_ratio)
    stock_bond_score, stock_bond_detail = calc_stock_bond_score(stock_5d, northbound_val)

    # ── 3. 总分计算 ──
    print("\n" + "=" * 40)
    print("第三步：总分计算")
    print("=" * 40)

    total = (
        policy_score * 0.30 +
        fund_score * 0.30 +
        sentiment_score * 0.20 +
        stock_bond_score * 0.20
    )
    total = round(total, 1)

    direction, position, emoji = judge_direction(total)

    # ── 4. 生成报告 ──
    report_date = datetime.now().strftime('%Y-%m-%d')

    # 统计数据覆盖
    total_items = 12
    fetched_items = 0
    if omo_val is not None: fetched_items += 1
    if mlf_score is not None: fetched_items += 1
    if bond_buy_val is not None: fetched_items += 1
    if dr007_val is not None: fetched_items += 1
    if shibor_1m_val is not None: fetched_items += 1
    if cd_1y_val is not None: fetched_items += 1
    if repo_vol_val is not None: fetched_items += 1
    if tl_pos_signal is not None: fetched_items += 1
    if bond_spread_val is not None: fetched_items += 1
    if irs_signal is not None: fetched_items += 1
    if stock_5d is not None: fetched_items += 1
    if northbound_val is not None: fetched_items += 1

    coverage = round(fetched_items / total_items * 100, 1)

    score_table = f"""## 国债期货四因子周报 | {report_date}

### 评分总表
| 因子 | 权重 | 评分 | 加权分 | 说明 |
|------|------|------|--------|------|
| 政策信号 | 30% | {policy_score} | {round(policy_score*0.3, 1)} | {policy_detail} |
| 资金信号 | 30% | {fund_score} | {round(fund_score*0.3, 1)} | {fund_detail} |
| 情绪信号 | 20% | {sentiment_score} | {round(sentiment_score*0.2, 1)} | {sentiment_detail} |
| 股债信号 | 20% | {stock_bond_score} | {round(stock_bond_score*0.2, 1)} | {stock_bond_detail} |
| **总分** | | **{total}** | | |

### 方向判断
{emoji} **{direction}** | 建议仓位：{position}

"""

    data_detail = "### 自动采集数据\n\n"
    if omo_val is not None:
        data_detail += f"- OMO净投放: {omo_val}亿\n"
    if mlf_op is not None:
        data_detail += f"- MLF操作: {mlf_op}亿\n"
    if bond_buy_val is not None:
        data_detail += f"- 国债买卖: {bond_buy_val}亿\n"
    if dr007_val is not None:
        data_detail += f"- DR007: {dr007_val}% ({dr007_date})\n"
    if shibor_1m_val is not None:
        data_detail += f"- 1M Shibor: {shibor_1m_val}% ({shibor_date})\n"
    if cd_1y_val is not None:
        data_detail += f"- 1Y AAA CD: {cd_1y_val:.3f}%\n"
    if repo_vol_val is not None:
        data_detail += f"- 回购成交量: {repo_vol_val}万亿\n"
    if tl_pos_signal is not None:
        data_detail += f"- TL持仓变化: {tl_desc}\n"
    if bond_spread_val is not None:
        data_detail += f"- 10Y-30Y利差: {bond_spread_val}bp\n"
    if irs_signal is not None:
        data_detail += f"- IRS信号: {irs_desc}\n"
    if stock_5d:
        for idx, pct in stock_5d.items():
            data_detail += f"- {idx} 5日涨跌: {pct}%\n"
    if northbound_val is not None:
        data_detail += f"- 北向资金近5日净流入: {northbound_val}亿\n"

    missing_items = []
    if omo_val is None: missing_items.append("OMO净投放")
    if mlf_score is None: missing_items.append("MLF操作")
    if bond_buy_val is None: missing_items.append("国债买卖")
    if cd_1y_val is None: missing_items.append("1Y AAA同业存单利率")
    if repo_vol_val is None: missing_items.append("回购成交量")
    if tl_pos_signal is None: missing_items.append("TL持仓变化")
    if irs_signal is None: missing_items.append("IRS利率")
    if northbound_val is None: missing_items.append("北向资金(已停止实时公布)")

    if missing_items:
        data_detail += f"\n### ⚠️ 缺失数据 ({len(missing_items)}项)\n\n"
        for item in missing_items:
            data_detail += f"- {item}\n"

    data_detail += f"\n📊 数据自动采集覆盖率: {coverage}% ({fetched_items}/{total_items}项)"

    full_report = score_table + data_detail

    print("\n" + "=" * 60)
    print(full_report)
    print("=" * 60)

    # 保存报告
    output_dir = _cp("/Coze/Drive/蓁宝/所有对话/主对话/国债期货四因子")
    os.makedirs(output_dir, exist_ok=True)

    output_file = f"{output_dir}/{report_date}_国债期货四因子周报.md"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(full_report)

    print(f"\n✅ 报告已保存: {output_file}")

    result = {
        "date": report_date,
        "policy_score": policy_score,
        "fund_score": fund_score,
        "sentiment_score": sentiment_score,
        "stock_bond_score": stock_bond_score,
        "policy_detail": policy_detail,
        "fund_detail": fund_detail,
        "sentiment_detail": sentiment_detail,
        "stock_bond_detail": stock_bond_detail,
        "total_score": total,
        "direction": direction,
        "position": position,
        "emoji": emoji,
        "coverage_percent": coverage,
        "fetched_items": fetched_items,
        "total_items": total_items,
        "missing_items": missing_items,
        "report_file": output_file,
        "is_fully_automated": len(missing_items) == 0
    }

    json_file = f"{output_dir}/{report_date}_评分数据.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"✅ 评分数据: {json_file}")

    # LLM研判分析
    if LLM_AVAILABLE:
        print("\n[LLM] DeepSeek V4 Flash 研判分析中...")
        llm_text = bond_llm_analysis(result)
        result["llm_analysis"] = llm_text
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        if llm_text:
            with open(output_file, 'a', encoding='utf-8') as f:
                f.write(f"\n\n---\n\n## 🔍 四因子综合研判（DeepSeek V4 Flash）\n\n{llm_text}\n")
        print(f"    研判完成 ({len(llm_text)} 字符)")

    # ── 5. 飞书卡片推送 ──
    if not no_push:
        print("\n" + "=" * 40)
        print("第五步：飞书卡片推送")
        print("=" * 40)
        _push_to_feishu(result, output_file)

    return result


def _push_to_feishu(result, md_file):
    """推送飞书卡片，失败则降级为纯文本推送"""
    try:
        from feishu_card_utils import build_bond_futures_card
        from feishu_push import push_card_to_feishu, push_to_feishu

        date_str = result.get('date', datetime.now().strftime('%Y-%m-%d'))

        # 构建卡片
        card = build_bond_futures_card(date_str, result)

        # 推送卡片
        print("  🚀 推送飞书交互式卡片...")
        success = push_card_to_feishu(card_dict=card)

        if success:
            print("  ✅ 飞书卡片推送成功")
            return True
        else:
            print("  ⚠️  卡片推送失败，降级为纯文本推送...")
            # 降级：推送markdown报告前半部分（避免过长）
            with open(md_file, 'r', encoding='utf-8') as f:
                md_content = f.read()
            # 取前3000字符
            text = md_content[:3000]
            if len(md_content) > 3000:
                text += "\n\n... (报告较长，完整内容见文件)"
            ok = push_to_feishu(text=text)
            if ok:
                print("  ✅ 降级纯文本推送成功")
            else:
                print("  ❌ 纯文本推送也失败了")
            return ok

    except Exception as e:
        print(f"  ❌ 推送异常: {e}")
        import traceback
        traceback.print_exc()
        # 再尝试降级纯文本
        try:
            from feishu_push import push_to_feishu
            with open(md_file, 'r', encoding='utf-8') as f:
                text = f.read()[:3000]
            push_to_feishu(text=text)
        except:
            pass
        return False


def main():
    """主流程（带参数解析）"""
    global no_push

    parser = argparse.ArgumentParser(description='国债期货四因子周报')
    parser.add_argument('--no-push', action='store_true',
                        help='只生成本地报告，不推送飞书')
    args = parser.parse_args()

    no_push = args.no_push

    return _run_analysis()


if __name__ == "__main__":
    main()
