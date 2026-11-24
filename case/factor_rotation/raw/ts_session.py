# -*- coding: utf-8 -*-
"""TuShare 会话钉住取数器
沙箱 NAT 出口按 TCP 连接分配 IP，pro_api 每次请求新建连接会换 IP 并触发
「ip超限，请不到在多个ip同时使用」。这里用 requests.Session 复用 TCP 连接
钉住单一出口 IP，并按 0.7s 间隔限速 + 失败退避重试，稳定通过代理通道取数。
"""
import sys, time, json
import requests
import pandas as pd

sys.path.insert(0, "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG/pylibs")
import ts_config  # noqa: E402

HTTP_URL = ts_config.TS_HTTP_URL
TOKEN = ts_config.TS_TOKEN
_S = requests.Session()
_S.headers.update({"Content-Type": "application/json"})
_LAST = [0.0]
MIN_GAP = 1.5


def api(api_name, retry=6, backoff=5.0, **params):
    """调用 tushare 接口，返回 DataFrame；失败按退避重试。"""
    for k in range(retry):
        gap = MIN_GAP - (time.time() - _LAST[0])
        if gap > 0:
            time.sleep(gap)
        _LAST[0] = time.time()
        try:
            r = _S.post(HTTP_URL, data=json.dumps({
                "api_name": api_name, "token": TOKEN, "params": params, "fields": ""}),
                timeout=40)
            j = r.json()
        except Exception as e:
            print(f"    [{api_name}] 网络异常 {type(e).__name__}: {str(e)[:80]}", flush=True)
            time.sleep(backoff * (k + 1))
            continue
        if j.get("code") == 0:
            d = j.get("data") or {}
            df = pd.DataFrame(d.get("items", []), columns=d.get("fields", []))
            return df
        msg = str(j.get("msg", ""))
        print(f"    [{api_name}] 第{k+1}次失败: {msg[:90]}", flush=True)
        if "超限" in msg or "限" in msg or "频率" in msg:
            time.sleep(backoff * (k + 2))
        else:
            time.sleep(backoff)
    raise RuntimeError(f"{api_name} 重试 {retry} 次仍失败: {msg}")


if __name__ == "__main__":
    for nm, kw in [("ths_hot", {"trade_date": "20261027"}),
                   ("ths_daily", {"ts_code": "886069.TI", "start_date": "20250101", "end_date": "20261027"}),
                   ("ths_member", {"ts_code": "885467.TI"}),
                   ("limit_list_d", {"trade_date": "20261027"})]:
        try:
            df = api(nm, **kw)
            print(f"[OK] {nm}: shape={df.shape} cols={list(df.columns)[:8]}")
            print(df.head(2).to_string()[:400])
        except Exception as e:
            print(f"[FAIL] {nm}: {e}")
        print("-" * 60)