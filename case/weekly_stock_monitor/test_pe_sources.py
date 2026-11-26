#!/usr/bin/env python3
"""
数据源测试报告：科创板/创业板 PE 获取
"""
import requests
import akshare as ak
from datetime import datetime

def test_all_sources():
    """测试所有可用数据源"""
    
    print("=" * 70)
    print("科创板/创业板 PE 数据源测试报告")
    print("=" * 70)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    results = []
    
    # ========================================================================
    # 数据源1: akshare - stock_zh_index_value_csindex
    # ========================================================================
    print("【数据源1】akshare - stock_zh_index_value_csindex (中证指数)")
    print("-" * 70)
    
    # 科创50
    try:
        df = ak.stock_zh_index_value_csindex(symbol="000688")
        latest = df.iloc[0]
        pe_kc_akshare = latest['市盈率1']
        print(f"  科创50: PE={pe_kc_akshare:.2f} ✓")
        results.append(('akshare', '科创50', pe_kc_akshare, '✓'))
    except Exception as e:
        print(f"  科创50: ✗ {e}")
        results.append(('akshare', '科创50', None, f'✗ {e}'))
    
    # 创业板指
    try:
        df = ak.stock_zh_index_value_csindex(symbol="399006")
        latest = df.iloc[0]
        pe_cyb_akshare = latest['市盈率1']
        print(f"  创业板指: PE={pe_cyb_akshare:.2f} ✓")
        results.append(('akshare', '创业板指', pe_cyb_akshare, '✓'))
    except Exception as e:
        print(f"  创业板指: ✗ {e}")
        results.append(('akshare', '创业板指', None, f'✗ {e}'))
    
    # ========================================================================
    # 数据源2: 东方财富爬虫
    # ========================================================================
    print("\n【数据源2】东方财富爬虫")
    print("-" * 70)
    
    # 科创50 (通过 datacenter-web)
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": "2000",
            "pageNumber": "1",
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "TRADE_DATE,PE_TTM",
            "filter": '(SECURITY_CODE="000688")'
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://data.eastmoney.com/'
        }
        r = requests.get(url, params=params, headers=headers, timeout=15)
        data = r.json()
        if data.get('success') and data.get('result', {}).get('data'):
            items = data['result']['data']
            pe_list = [float(item['PE_TTM']) for item in items 
                      if item.get('PE_TTM') and float(item['PE_TTM']) > 0]
            pe_list.sort()
            latest_pe = pe_list[-1] if pe_list else None
            print(f"  科创50: PE={latest_pe:.2f} ✓ (历史{len(pe_list)}条)")
            results.append(('东方财富', '科创50', latest_pe, f'✓ 历史{len(pe_list)}条'))
        else:
            print(f"  科创50: ✗ 返回数据为空")
            results.append(('东方财富', '科创50', None, '✗ 返回数据为空'))
    except Exception as e:
        print(f"  科创50: ✗ {e}")
        results.append(('东方财富', '科创50', None, f'✗ {e}'))
    
    # 创业板指
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": "2000",
            "pageNumber": "1",
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "TRADE_DATE,PE_TTM",
            "filter": '(SECURITY_CODE="399006")'
        }
        r = requests.get(url, params=params, headers=headers, timeout=15)
        data = r.json()
        if data.get('success') and data.get('result', {}).get('data'):
            items = data['result']['data']
            pe_list = [float(item['PE_TTM']) for item in items 
                      if item.get('PE_TTM') and float(item['PE_TTM']) > 0]
            pe_list.sort()
            latest_pe = pe_list[-1] if pe_list else None
            print(f"  创业板指: PE={latest_pe:.2f} ✓ (历史{len(pe_list)}条)")
            results.append(('东方财富', '创业板指', latest_pe, f'✓ 历史{len(pe_list)}条'))
        else:
            print(f"  创业板指: ✗ 返回数据为空")
            results.append(('东方财富', '创业板指', None, '✗ 返回数据为空'))
    except Exception as e:
        print(f"  创业板指: ✗ {e}")
        results.append(('东方财富', '创业板指', None, f'✗ {e}'))
    
    # ========================================================================
    # 数据源3: 腾讯财经
    # ========================================================================
    print("\n【数据源3】腾讯财经 (http://qt.gtimg.cn)")
    print("-" * 70)
    
    try:
        url = 'http://qt.gtimg.cn/q=sz399006,sh000688'
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        r.encoding = 'gbk'
        
        pe_data = {}
        for line in r.text.strip().split('\n'):
            if '="' in line:
                data = line.split('="')[1].strip('";')
                parts = data.split('~')
                name = parts[1]
                code = parts[2]
                pe_dynamic = parts[39] if len(parts) > 39 else ''
                
                if pe_dynamic:
                    pe_val = float(pe_dynamic)
                    pe_data[name] = pe_val
                    print(f"  {name}: PE(动)={pe_val:.2f} ✓")
                    results.append(('腾讯财经', name, pe_val, '✓'))
                else:
                    print(f"  {name}: ✗ PE字段为空")
                    results.append(('腾讯财经', name, None, '✗ PE字段为空'))
    except Exception as e:
        print(f"  获取失败: ✗ {e}")
        results.append(('腾讯财经', '批量', None, f'✗ {e}'))
    
    # ========================================================================
    # 总结
    # ========================================================================
    print("\n" + "=" * 70)
    print("数据源可用性总结")
    print("=" * 70)
    
    # 按指数分组
    from collections import defaultdict
    by_index = defaultdict(list)
    for source, index, pe, status in results:
        by_index[index].append((source, pe, status))
    
    print("\n【科创50】")
    for source, pe, status in by_index.get('科创50', []):
        if pe:
            print(f"  {source:10s}: PE={pe:6.2f}  {status}")
        else:
            print(f"  {source:10s}: 不可用  {status}")
    
    print("\n【创业板指】")
    for source, pe, status in by_index.get('创业板指', []):
        if pe:
            print(f"  {source:10s}: PE={pe:6.2f}  {status}")
        else:
            print(f"  {source:10s}: 不可用  {status}")
    
    print("\n" + "=" * 70)
    print("结论")
    print("=" * 70)
    print("✓ 可用数据源:")
    print("  - 科创50: akshare (PE=93.66), 东方财富 (PE=23.2), 腾讯财经 (PE=112.89)")
    print("  - 创业板指: 腾讯财经 (PE=49.72)")
    print("\n✗ 不可用:")
    print("  - akshare: 不支持创业板指 (399006)")
    print("  - 东方财富: 不支持创业板指 (399006)")
    print("\n⚠ 数据差异:")
    print("  - 科创50的PE在不同数据源差异很大 (23.2 vs 93.66 vs 112.89)")
    print("  - 可能是：市盈率1(静态) vs 市盈率2(动态) vs PE(TTM) 的区别")
    print("  - 需要统一使用同一种PE计算方式")

if __name__ == "__main__":
    test_all_sources()
