#!/usr/bin/env python3
import sys, pkgutil
sys.modules['pkgutil.ImpImporter'] = type('ImpImporter', (), {'find_module': lambda *a: None})()

import requests, json, time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime

def make_sess():
    s = requests.Session()
    r = Retry(total=5, backoff_factor=2, status_forcelist=[500,502,503,504], allowed_methods=["GET"])
    a = HTTPAdapter(max_retries=r, pool_connections=5, pool_maxsize=5)
    s.mount("https://", a)
    return s

HD = {"User-Agent":"Mozilla/5.0","Referer":"https://quote.eastmoney.com/"}

print(f"=== 板块筛选v3 {datetime.now().strftime('%H:%M:%S')} ===")

# Step1: 板块列表
print("Step1: 获取板块列表...")
all_blocks = []
for btype, label in [(2,"概念"),(3,"行业")]:
    s = make_sess()
    for pg in range(1,8):
        p = {"pn":pg,"pz":100,"po":1,"np":1,"ut":"bd1d9ddb04089700cf9c27f6f7426281","fltt":2,"invt":2,"fid":"f3","fs":f"m:90+t:{btype}","fields":"f12,f14,f2,f3,f62","_":int(time.time()*1000)}
        try:
            r = s.get("https://push2.eastmoney.com/api/qt/clist/get",params=p,headers=HD,timeout=20)
            d = r.json()
            if d.get("data") and d["data"].get("diff"):
                items = d["data"]["diff"]
                for it in items:
                    all_blocks.append({"code":it["f12"],"name":it["f14"],"type":label,"pct":it.get("f3",0)})
                if len(items) < 100: break
            else: break
        except:
            time.sleep(5); s.close(); s = make_sess()
        time.sleep(1)
    s.close()
    cnt = len([b for b in all_blocks if b["type"]==label])
    print(f"  {label}: {cnt}个")
print(f"  总计: {len(all_blocks)}个")

# Step2: 预筛
cands = [b for b in all_blocks if b["pct"] > -3]
print(f"\nStep2: 预筛(涨幅>-3%): {len(cands)}个")

# Step3: 分析
print(f"\nStep3: 逐板块分析...")
passed = []
special = []
fail = 0
sess = make_sess()
rc = 0

for i, bl in enumerate(cands):
    if i % 50 == 0:
        print(f"  [{i}/{len(cands)}] 通过:{len(passed)} 特殊:{len(special)} 跳过:{fail}")
    secid = f"90.{bl['code']}"
    
    # K线
    kl = None
    for att in range(3):
        try:
            p = {"secid":secid,"ut":"fa5fd1943c7b386f172d6893dbfba10b","fields1":"f1,f2,f3,f4,f5,f6","fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61","klt":101,"fqt":1,"end":"20500101","lmt":120,"_":int(time.time()*1000)}
            r = sess.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",params=p,headers=HD,timeout=20)
            if r.status_code==200 and len(r.text)>100:
                d = r.json()
                if d.get("data") and d["data"].get("klines"): kl = d["data"]["klines"]
            break
        except:
            time.sleep(3+att*3); sess.close(); sess = make_sess()
    rc += 1; time.sleep(1.5)
    
    if not kl or len(kl) < 85: fail += 1; continue
    
    closes = [float(k.split(",")[2]) for k in kl]
    ma40t=sum(closes[-40:])/40; ma40y=sum(closes[-41:-1])/40
    ma80t=sum(closes[-80:])/80; ma80y=sum(closes[-81:-1])/80
    cl = closes[-1]
    
    if cl<=ma40t or ma40t<=ma40y or ma80t<=ma80y: fail+=1; continue
    if len(closes)<6: fail+=1; continue
    g5=(cl-closes[-6])/closes[-6]*100
    if g5<=2: fail+=1; continue
    
    # 资金流
    fl = None
    for att in range(3):
        try:
            p = {"secid":secid,"fields1":"f1,f2,f3,f7,f8,f9,f10,f11,f12,f13","fields2":"f51,f52,f53,f54,f55","klt":101,"lmt":5,"ut":"b2884a393a59ad64002282a27eecbf13","_":int(time.time()*1000)}
            r = sess.get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",params=p,headers=HD,timeout=20)
            if r.status_code==200 and len(r.text)>50:
                d = r.json()
                if d.get("data") and d["data"].get("klines"): fl = d["data"]["klines"]
            break
        except:
            time.sleep(3+att*3); sess.close(); sess = make_sess()
    rc += 1; time.sleep(1.5)
    
    if not fl or len(fl)<3: fail+=1; continue
    
    m3=m5=t5=0
    for idx,f in enumerate(fl):
        pts=f.split(","); mn=float(pts[1]); tn=mn+float(pts[2])+float(pts[3])+float(pts[4])
        m5+=mn; t5+=tn
        if idx>=len(fl)-3: m3+=mn
    
    rec={"code":bl["code"],"name":bl["name"],"type":bl["type"],"close":cl,"ma40":ma40t,"ma80":ma80t,
         "ma40_up":ma40t>ma40y,"ma80_up":ma80t>ma80y,"above_ma40":cl>ma40t,"m3":m3,"m5":m5,"t5":t5,"g5":g5}
    
    if m3>0: passed.append(rec)
    elif m5>0 and t5<0: special.append(rec)
    
    if rc%200==0:
        print(f"  >> 暂停10s (已{rc}次请求)..."); time.sleep(10); sess.close(); sess=make_sess()

sess.close()
passed.sort(key=lambda x:x["m3"],reverse=True)
special.sort(key=lambda x:x["m5"],reverse=True)

def fv(v):
    if abs(v)>=1e8: return f"{v/1e8:.2f}亿"
    elif abs(v)>=1e4: return f"{v/1e4:.0f}万"
    return f"{v:.0f}"

today=datetime.now().strftime("%Y-%m-%d")
md=f"""# A股板块综合筛选v3（概念+行业）
**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | **数据源**: 东方财富
**范围**: 概念{len([b for b in all_blocks if b['type']=='概念'])}个 + 行业{len([b for b in all_blocks if b['type']=='行业'])}个

## 筛选条件
1. 收盘>40MA  2. 40MA向上  3. 80MA向上  4. 近3日主力净流入>0  5. 近5日涨幅>2%

---

## 一、通过全部条件的板块（按近3日主力净流入降序）

"""
if passed[:30]:
    md+="| # | 板块 | 代码 | 类型 | 收盘 | 40MA | 80MA | 3日主力净流入 | 5日涨幅 |\n"
    md+="|:---:|:---|:---:|:---:|---:|---:|---:|---:|---:|\n"
    for i,b in enumerate(passed[:30],1):
        md+=f"| {i} | **{b['name']}** | {b['code']} | {b['type']} | {b['close']:.2f} | {b['ma40']:.2f} | {b['ma80']:.2f} | {fv(b['m3'])} | {b['g5']:+.2f}% |\n"
else:
    md+="暂无\n"

md+=f"\n---\n\n## 二、✖ 未通过但主力资金净流入>0（散户卖主力买）\n\n"
if special:
    md+="| # | 板块 | 代码 | 类型 | 收盘 | 40MA↑ | 80MA↑ | 5日主力 | 5日总净流入 | 5日涨幅 |\n"
    md+="|:---:|:---|:---:|:---:|---:|:---:|:---:|---:|---:|---:|\n"
    for i,b in enumerate(special,1):
        md+=f"| ✖{i} | {b['name']} | {b['code']} | {b['type']} | {b['close']:.2f} | {'✅' if b['ma40_up'] else '❌'} | {'✅' if b['ma80_up'] else '❌'} | {fv(b['m5'])} | {fv(b['t5'])} | {b['g5']:+.2f}% |\n"
else:
    md+="暂无\n"

md+=f"\n---\n分析{len(cands)}个, 通过{len(passed)}个, ✖标注{len(special)}个, 跳过{fail}个\n"

out=f"/app/data/所有对话/主对话/板块筛选v3_{today}.md"
with open(out,"w",encoding="utf-8") as f: f.write(md)

print(f"\n=== 完成 ===")
print(f"通过:{len(passed)} ✖:{len(special)} 跳过:{fail}")
print(f"\n--- 通过 ---")
for i,b in enumerate(passed[:30],1):
    print(f"  {i}. {b['name']}({b['type']}) 3日:{fv(b['m3'])} 5日涨:{b['g5']:+.2f}%")
print(f"\n--- ✖特殊标注 ---")
for i,b in enumerate(special,1):
    print(f"  ✖{i}. {b['name']}({b['type']}) 主力:{fv(b['m5'])} 总:{fv(b['t5'])}")
print(f"\n文件: {out}")
