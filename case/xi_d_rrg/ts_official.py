# -*- coding: utf-8 -*-
"""官方直连 Tushare (token 由用户提供, 2000积分) —— 仅用于本任务(成长价值轮动+RRG)的抓取脚本
不要改其他任务共用的 ts_config.py(代理15k积分, 供结构因子/TDA-SPD等任务使用)。

实测结论(2026-08-23):
  - 直连 https://api.tushare.pro 有效, 2000分可调 moneyflow/margin/income/daily_basic/adj_factor
  - 稳定档位: 0.6s间隔 ≈ 94次/分钟; 0.45s以下触发"IP数量超限(10个)"(沙箱NAT出口漂移所致)
  - 若接口偶发失败走退避重试即可, 不要调高频率
"""
import socket, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pylib'))
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(25)
import warnings; warnings.filterwarnings('ignore')
import tushare as ts

OFFICIAL_TOKEN = 'a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265'


def init_tushare():
    pro = ts.pro_api(OFFICIAL_TOKEN)
    # 关键: 保持 tushare 1.4.29 默认端点 http://api.waditu.com/dataapi
    # (旧客户端按 {url}/{api_name} 拼接; 官方新版 https://api.tushare.pro 用 body 传 api_name,
    #  协议不同, 指过去会静默返回空表, 2026-08-23 实测踩坑)
    return pro