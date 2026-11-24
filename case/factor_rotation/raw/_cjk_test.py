# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
FP = FontProperties(fname="/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf")
FPB = FontProperties(fname="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
fig, ax = plt.subplots(figsize=(6,3), dpi=110)
ax.plot([0,1,2],[1,2,3])
ax.set_title("测试：行业·银行 三买 B3 @10-23", fontproperties=FP, fontsize=12)
ax.set_xlabel("交易日（日线）", fontproperties=FP, fontsize=9)
ax.text(0.5, 1.6, "中文标注 中枢 ZG=1234.5 一买/三买 ok", fontproperties=FP, fontsize=10)
ax.text(0.5, 1.2, "MixedEN123 pattern", fontproperties=FPB, fontsize=9)
fig.tight_layout(); fig.savefig("raw/_cjk_test.png")
print("saved")
