# -*- coding: utf-8 -*-
"""中国房价行情（禧泰数据 creprice.cn）：京沪**挂牌均价**与**挂牌数量**。

为什么要单独写这一层
--------------------
`data_hub.house` 里只有网签（套数/面积，没有金额 → 推不出成交单价）与 70 城房价指数。
用户口径（2026-11-25）：没有成交价就用**挂牌价**替代，图也用挂牌价。所以这里补两条：

- **挂牌均价**（元/㎡）：桌面城市页 `/city/{code}.html` 给月度挂牌价 + 环比；
- **挂牌数量**（套）：桌面页没有，**移动端** `m.creprice.cn/city/{code}.html` 才有
  （实测 2026-10：北京 5,869 套、上海 6,514 套），另带平均总价、售租比。

反爬：不带会话直接连打会返回「访问过于频繁，请输入验证码」。实测先 GET 首页暖会话、
每请求间隔 ≥2.5 秒、带 Referer 就正常（2026-11-25 京沪 13 城全通）。
"""
from __future__ import annotations

import re
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
             "Mobile/15E148 Safari/604.1")
HOME = "https://www.creprice.cn/"
CITY_URL = "https://www.creprice.cn/city/{code}.html"
MOBILE_CITY_URL = "https://m.creprice.cn/city/{code}.html"

#: 站点城市代码 = 拼音首字母（京沪之外的城市可按需补）
CITY_CODES = {"北京": "bj", "上海": "sh", "深圳": "sz", "成都": "cd", "广州": "gz",
              "杭州": "hz", "南京": "nj", "武汉": "wh", "苏州": "su", "青岛": "qd"}
DEFAULT_CITIES = ("北京", "上海")

_RE_MONTH = re.compile(r"(\d{4})年(\d{1,2})月挂牌")
_RE_M_PRICE = re.compile(r"([\d,]{4,9})\s*元/㎡")
_RE_M_MOM = re.compile(r"环比：\s*[▼▲↑↓＋+\-]?\s*([\d.]+)\s*%")
_RE_M_LIST = re.compile(r"挂牌数量\s*([\d,]{1,9})\s*套")
_RE_M_TOTAL = re.compile(r"平均总价\s*([\d.]+)\s*万元")
_RE_M_RATIO = re.compile(r"售租比\s*([\d.]+)")


def _flat(html):
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[ \t\u3000\xa0\r\n]+", " ", t)


def parse_mobile(html, city=None):
    """移动端页 → ``{month, price_yuan, mom_pct, listings, avg_total_price_wan, sale_rent_ratio}``。"""
    if "验证码" in html[:4000] and "元/㎡" not in html:
        return {"ok": False, "city": city, "reason": "触发验证码页"}
    t = _flat(html)
    m, p = _RE_MONTH.search(t), _RE_M_PRICE.search(t)
    if not (m and p):
        return {"ok": False, "city": city, "reason": "未解析到月度挂牌价"}
    mom, lst = _RE_M_MOM.search(t), _RE_M_LIST.search(t)
    tot, ratio = _RE_M_TOTAL.search(t), _RE_M_RATIO.search(t)
    return {"ok": True, "city": city,
            "month": "%s-%02d" % (m.group(1), int(m.group(2))),
            "price_yuan": int(p.group(1).replace(",", "")),
            "mom_pct": round(float(mom.group(1)), 2) if mom else None,
            "listings": int(lst.group(1).replace(",", "")) if lst else None,
            "avg_total_price_wan": float(tot.group(1)) if tot else None,
            "sale_rent_ratio": float(ratio.group(1)) if ratio else None,
            "source": "中国房价行情 移动端城市页"}


def _sessions(timeout=25):
    desk = requests.Session()
    desk.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        desk.get(HOME, timeout=timeout)          # 暖会话：拿 cityredata / city cookie
    except Exception:
        pass
    mob = requests.Session()
    mob.headers.update({"User-Agent": MOBILE_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return desk, mob


def fetch(cities=None, delay=2.5, retries=1, timeout=25):
    """逐城抓挂牌均价与挂牌数量。被拦就退一倍节奏重试一次，仍拦则记该城失败。"""
    cities = list(cities or DEFAULT_CITIES)
    out = {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cities": {}, "errors": {}}
    desk, mob = _sessions(timeout=timeout)
    for city in cities:
        code = CITY_CODES.get(city)
        if not code:
            out["errors"][city] = "未登记城市代码"
            continue
        rec, err = None, None
        for attempt in range(retries + 1):
            try:
                r = mob.get(MOBILE_CITY_URL.format(code=code), timeout=timeout)
                r.raise_for_status()
                rec = parse_mobile(r.content.decode("utf-8", "ignore"), city=city)
            except Exception as exc:
                rec, err = None, "%s: %s" % (type(exc).__name__, exc)
            if rec and rec.get("ok"):
                break
            err = (rec or {}).get("reason") or err
            if attempt < retries:
                time.sleep(delay * 2)
                desk, mob = _sessions(timeout=timeout)
        if not rec or not rec.get("ok"):
            # 移动端挂了就退桌面页（有价格、没有挂牌量）
            try:
                r = desk.get(CITY_URL.format(code=code), timeout=timeout,
                             headers={"Referer": HOME})
                t = _flat(r.content.decode("utf-8", "ignore"))
                m, p = _RE_MONTH.search(t), re.search(r"([\d.]+)\s*万元/㎡", t)
                if m and p:
                    rec = {"ok": True, "city": city,
                           "month": "%s-%02d" % (m.group(1), int(m.group(2))),
                           "price_yuan": int(round(float(p.group(1)) * 10000)),
                           "mom_pct": None, "listings": None,
                           "source": "中国房价行情 桌面城市页（无挂牌量）"}
            except Exception as exc:
                err = "%s: %s" % (type(exc).__name__, exc)
        time.sleep(delay)
        if not rec or not rec.get("ok"):
            out["errors"][city] = err or "未知错误"
            continue
        out["cities"][city] = rec
    out["ok"] = bool(out["cities"])
    return out