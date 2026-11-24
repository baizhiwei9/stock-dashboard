# -*- coding: utf-8 -*-
import sys, numpy as np, pandas as pd
BASE="/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5ed3f1d6b1ffeo164XKTVu7vvVA"
sys.path.insert(0, f"{BASE}/raw")
from ts_session import api
ti=api("ths_index")
names=["银行","航空","半导体","石油开采","电信运营","矿物制品","玻璃","铁路","路桥","煤炭开采","元器件","种植业","装修装饰","染料涂料","电机","国产航母","培育钻石"]
for nm in names:
    s=ti[ti.name==nm]
    print(nm, s[["ts_code","name","type","count"]].to_dict("records")[:3])
