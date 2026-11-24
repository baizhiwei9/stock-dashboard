# -*- coding: utf-8 -*-
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
NOTO = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"
FP = FontProperties(fname=NOTO)
fig, ax = plt.subplots(figsize=(6,3), dpi=110)
ax.plot([0,1,2],[1,2,3])
ax.set_title("测试A：行业·银行 三买 B3 @10-23 中枢 ZG=1234.5", fontproperties=FP, fontsize=11)
ax.set_xlabel("交易日（日线）", fontproperties=FP, fontsize=9)
fig.tight_layout(); fig.savefig("raw/_cjk_noto.png"); print("saved noto")

from PIL import Image, ImageDraw, ImageFont
W,H=700,180
im=Image.new("RGB",(W,H),"white"); d=ImageDraw.Draw(im)
f1=ImageFont.truetype("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",22,layout_engine=ImageFont.Layout.BASIC)
f2=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",22,layout_engine=ImageFont.Layout.BASIC)
d.text((10,10),"测试B 中文：行业·银行 三买 B3 @10-23",font=f1,fill="black")
d.text((10,50),"MixedEN 123 pattern ZG=1234.5",font=f2,fill="black")
d.text((10,90),"▲一买 ▼一卖 中枢 [1234,1567]",font=f1,fill="black")
im.save("raw/_cjk_pil.png"); print("saved pil")
