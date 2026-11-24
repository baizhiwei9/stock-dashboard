# -*- coding: utf-8 -*-
"""v5 版式核查：用真实 gridspec 位置 + PIL 逐条测量文字宽度，判断是否溢出面板/图片。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import ImageFont

NOTO = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/.fonts/NotoSansSC-Regular.ttf"
FP = ImageFont.truetype(NOTO, 40)

DPI = 100.0
FIG_W, FIG_H = 18.9, 6.0 * 5 + 2.3
NW, NH = int(FIG_W * DPI), int(FIG_H * DPI)
print(f"figure px = {NW} x {NH}")

fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=100)
gs = fig.add_gridspec(5 + 1, 2, left=0.052, right=0.986, top=0.945, bottom=0.012,
                      hspace=0.44, wspace=0.15, height_ratios=[1.0] * 5 + [0.30])
pos = []
for i in range(10):
    ax = fig.add_subplot(gs[i // 2, i % 2])
    pos.append(ax.get_position())
axl = fig.add_subplot(gs[5, :])
lp = axl.get_position()
pw = (pos[0].x1 - pos[0].x0) * NW
ph = (pos[0].y1 - pos[0].y0) * NH
print(f"panel box px = {pw:.0f} x {ph:.0f}   legend box px = {(lp.x1-lp.x0)*NW:.0f} x {(lp.y1-lp.y0)*NH:.0f}")


def wid(txt, pt):
    px = pt * DPI / 72.0
    return FP.getlength(txt) * px / 40.0


PASS = True
TITLES = [
    "[A]【行业】银行   三买 B3 @10-23   均线√",
    "[A]【行业】路桥   二买 B2 @10-23   均线√",
    "[C]【概念】电机   待买点/待突破   均线√",
    "[C]【主题】机器人与智能制造   待买点/待突破   均线×",
    "[C]【概念】文化传媒概念   待买点/待突破   均线×",
]
for t in TITLES:
    w = wid(t, 20.5)
    ok = w < pw * 0.99
    PASS &= ok
    print(f"  title {w:6.0f}px / {pw:.0f}px  {'OK' if ok else 'OVERFLOW'}   {t}")

TOP = "名次#127 · 右侧已确认"
trunc = 0
for lvl in ["行业", "主题", "概念"]:
    for nm in ["机器人与智能制造", "文化传媒概念", "AI软件与应用", "虚拟数字人", "特斯拉概念"]:
        t = f"名次#127 · 右侧已确认"
        b = f"[{ 'C' }]【{lvl}】{nm}   待买点/待突破   均线×"
        if wid(b, 20.5) > pw:
            trunc += 1
INFO = ("最新 1306.2 (-1.30%) · MA10/20 -5.47% · 连涨0\n"
        "距MA40 -13.46% · 距中枢上沿 +2.33%\n"
        "锚定中枢 ZD=1161.9 ZG=1276.5 | 跨度216行(超宽) | 上偏35%/下偏0% · 中枢存疑(越界端点)")
for ln in INFO.split("\n"):
    w = wid(ln, 11.2)
    ok = w < pw * 0.99
    PASS &= ok
    print(f"  info  {w:6.0f}px / {pw:.0f}px  {'OK' if ok else 'OVERFLOW'}   {ln}")

LEG1 = "▲一买 B1(底背驰)   ▲二买 B2(回调不破前低)   ▲三买 B3(向上离开中枢后回抽不回中枢)   ▼一卖 S1 / 二卖 S2 / 三卖 S3   ★=本次所依据买点"
LEG2 = ("蓝折线=缠论笔    橙框=缠论中枢[ZD,ZG]    紫框+紫虚线=三买/三卖所依据的「锚定中枢」(ZG 处画水平线，可验「回抽不回中枢」)"
        "    均线：蓝=MA10 紫=MA20 黑粗虚线=MA40    红涨绿跌")
for ln in (LEG1, LEG2):
    w = wid(ln, 13.0)
    ok = w < (lp.x1 - lp.x0) * NW * 0.90
    PASS &= ok
    print(f"  legend {w:6.0f}px / {(lp.x1-lp.x0)*NW*0.90:.0f}px  {'OK' if ok else 'OVERFLOW'}")

SUP = ("因子合成 × 缠论买卖点 × 均线过滤 · C 档：点名关注 · 待买点/待突破（按因子名次 11-20）"
       "    |   数据截至 2026-10-29 收盘")
w = wid(SUP, 19.0)
ok = w < NW * 0.98
PASS &= ok
print(f"  suptitle {w:6.0f}px / {NW*0.98:.0f}px  {'OK' if ok else 'OVERFLOW'}")
print("VERDICT:", "PASS" if PASS else "FAIL")