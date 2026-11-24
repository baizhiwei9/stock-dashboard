# -*- coding: utf-8 -*-
"""Session 复用直连 Tushare 快速客户端
====================================
解决沙箱出口 IP 漂移问题:
  - 沙箱 NAT 按"TCP 连接"分配出口 IP: 同一连接上所有请求固定同一出口;
    每次新建连接 -> 新出口 IP —> 官方看到同一 token 从多个 IP 访问, 触发
    "IP 数量超限(最大10个)"。
  - tushare 官方库 client.py 每次调用都是 requests.post(...) 新建连接, 故限频
    只能压到 ~10/min。
  - 本模块用 requests.Session 复用一个 keep-alive 连接, 出口 IP 固定,
    实测 0.6s 间隔 ≈ 95/min 全成功; 0.8s ≈ 73/min; 1.2s ≈ 50/min。

用法(与官方库一致):
    from ts_fast import init_tushare
    pro = init_tushare()
    df = pro.moneyflow(ts_code='000001.SZ', start_date='20250101', end_date='20250110')
    df = pro.daily_basic(trade_date='20250110', fields='ts_code,close,circ_mv,trade_date')

权限: OFFICIAL_TOKEN(2000分) 已验证可调 moneyflow / margin_detail / income /
      daily_basic / index_classify / index_member / index_daily / adj_factor /
      trade_cal / daily 等本任务所需全部接口。
"""
import socket, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pylib'))
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **kw: [r for r in _orig(*a, **kw) if r[0] == socket.AF_INET]
socket.setdefaulttimeout(25)
import warnings; warnings.filterwarnings('ignore')
import time
import requests
import pandas as pd

OFFICIAL_TOKEN = 'a7c1ab22e3162aac86bf77323755b2d99e9cadfad4ae631a50574265'
# 实测: http://api.waditu.com + body 传 api_name 协议可行且复用连接出口固定
API_URL = 'http://api.waditu.com'


class FastPro:
    """Session 复用的 tushare 客户端, 方法与官方 pro_api 一致"""

    def __init__(self, token=OFFICIAL_TOKEN, url=API_URL, sleep=0.6):
        self._token = token
        self._url = url
        self._sleep = sleep
        self._session = requests.Session()  # 关键: 复用连接 -> 固定出口 IP
        self._last = 0.0

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)

        def _call(**params):
            # 节流: 保持最小间隔, 防止瞬时突发
            gap = time.time() - self._last
            if gap < self._sleep:
                time.sleep(self._sleep - gap)
            payload = {'api_name': name, 'token': self._token,
                       'params': params, 'fields': ''}
            resp = self._session.post(self._url, json=payload, timeout=25)
            j = resp.json()
            self._last = time.time()
            if j.get('code') != 0:
                raise Exception(str(j.get('msg'))[:200])
            data = j.get('data') or []
            if isinstance(data, dict):  # 新协议: {fields:[...], items:[[...]]}
                fields, items = data.get('fields') or [], data.get('items') or []
                return pd.DataFrame(items, columns=fields)
            return pd.DataFrame(data)

        return _call

    def reconnect(self):
        """丢弃旧连接重开(出口 IP 会变), 一般无需调用"""
        self._session.close()
        self._session = requests.Session()


def init_tushare():
    return FastPro()


if __name__ == '__main__':
    # 自测: 0.6s 间隔连打 30 次 + 核心接口权限
    pro = init_tushare()
    ok = 0
    for i in range(30):
        df = pro.trade_cal(exchange='SSE', start_date='20260101', end_date='20260201')
        ok += 1
        time.sleep(0.6)
    print(f'self-test 0.6s x30: ok={ok}')
    for api, kw in [('moneyflow', dict(ts_code='000001.SZ', start_date='20250101', end_date='20250110')),
                    ('margin_detail', dict(ts_code='000001.SZ', start_date='20250101', end_date='20250110')),
                    ('income', dict(ts_code='000001.SZ', start_date='20181230', end_date='20250110',
                                    fields='ts_code,ann_date,f_ann_date,end_date,report_type,n_income,total_revenue')),
                    ('daily_basic', dict(ts_code='000001.SZ', start_date='20250101', end_date='20250110'))]:
        df = getattr(pro, api)(**kw)
        print(f'  {api}: rows={len(df)}')