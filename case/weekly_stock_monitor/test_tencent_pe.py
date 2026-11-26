#!/usr/bin/env python3
"""
测试腾讯财经 API 获取指数 PE 数据
"""
import requests
import json

def get_index_pe_tencent(index_code):
    """
    腾讯财经获取指数 PE
    
    index_code: 'sz399006' (创业板指) 或 'sh000688' (科创50)
    """
    url = f'http://qt.gtimg.cn/q={index_code}'
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.encoding = 'gbk'
        text = r.text
        
        # 解析格式: v_sz399006="51~创业板指~399006~2866.76~..."
        # 字段顺序（从1开始）：
        # 1:市场 2:名称 3:代码 4:最新价 5:昨收 6:今开 7:成交量
        # 8:外盘 9:内盘 ...
        # 39:市盈率(动) 40:市盈率(TTM) 41:市净率
        
        if '="' in text:
            data = text.split('="')[1].strip('";')
            parts = data.split('~')
            
            result = {
                'name': parts[1] if len(parts) > 1 else '',
                'code': parts[2] if len(parts) > 2 else '',
                'price': parts[3] if len(parts) > 3 else '',
                'pe_dynamic': parts[39] if len(parts) > 39 else '',  # 市盈率(动)
                'pe_ttm': parts[40] if len(parts) > 40 else '',  # 市盈率(TTM)
                'pb': parts[41] if len(parts) > 41 else '',  # 市净率
            }
            
            return result
    except Exception as e:
        print(f'腾讯财经获取失败: {e}')
    
    return None

# 测试
if __name__ == '__main__':
    # 创业板指
    print('=== 创业板指 ===')
    data = get_index_pe_tencent('sz399006')
    if data:
        print(f"名称: {data['name']}")
        print(f"代码: {data['code']}")
        print(f"最新价: {data['price']}")
        print(f"市盈率(动): {data['pe_dynamic']}")
        print(f"市盈率(TTM): {data['pe_ttm']}")
        print(f"市净率: {data['pb']}")
    
    # 科创50
    print('\n=== 科创50 ===')
    data = get_index_pe_tencent('sh000688')
    if data:
        print(f"名称: {data['name']}")
        print(f"代码: {data['code']}")
        print(f"最新价: {data['price']}")
        print(f"市盈率(动): {data['pe_dynamic']}")
        print(f"市盈率(TTM): {data['pe_ttm']}")
        print(f"市净率: {data['pb']}")
