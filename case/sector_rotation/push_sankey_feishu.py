# -*- coding: utf-8 -*-
"""行业轮动机会风险报告 - 商机流转图飞书推送（带消息头，署名置标题末尾）
复用既有飞书推送凭据与工具：momentum-risk-control/feishu_config.py + tech_consensus_daily/feishu_push.py
流程：上传桑基图 PNG -> 图片卡片（header 带消息头）-> 文字摘要卡片
"""
import sys
import os

BASE = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
IMG = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f1798deecffeV40w0HvYAOHLpR/sankey_full_20260928.png"

sys.path.insert(0, os.path.join(BASE, "ses_gffe5f28eb4508ffeXvGpuDQNknTjQn", "tech_consensus_daily"))
import feishu_push as fp

HEADER = "行业轮动机会风险报告 | 2026-09-28 | 来自 DoMate"

TEXT = """**行业轮动机会风险报告 · 商机流转图**（数据截至 2026-09-28 收盘）

**一、核心结论（三方法等权加权：TDA / 开源轮动 / STMOM21日动量，每方法机会权重1、风险权重1）**
- 🟢 **机会**：银行(3)、医药生物(3)、房地产(2)、传媒(2)；煤炭、石油石化、公用事业、交通运输、农林牧渔、纺织服饰、食品饮料等(各1)
- 🔴 **风险**：电力设备、有色金属、电子、通信、非银金融居前，建筑材料、机械设备、汽车、基础化工、环保等跟跌
- 📊 **横截面动量 Top5**：银行、房地产、煤炭、医药生物、石油石化
- 📉 **横截面动量 Bottom5**：基础化工、非银金融、机械设备、有色金属、电力设备

**二、桑基图读法（左→右）**
- 左列=流出端（综合风险≥1 的申万一级，宽口径，节点带动量排名#），右列=流入端（机会**下钻**：申万三级细分 + 细概念），流宽=机会强度
- 下钻细分示例：银行→其他银行/股份制银行/城商行；医药→原料药/其他生物制品/互联网药店；传媒→院线/影视动漫制作
- 细概念：存储芯片、共封装光学(CPO)、白酒概念、人工智能、机器人概念、人形机器人

**三、口径与缺口**
- 黄酒无独立概念指数（归入「其他酒类」）；光模块无独立指数，以 CPO 代理；16个停更申万三级行业（稀土/锂等）已剔除
- 数据：TuShare 申万2021 + 同花顺概念指数；全部结论为概率化研判，仅供参考，不构成投资建议"""


def main():
    app_id, app_secret, webhook = fp._config()
    tk = fp._tenant_token(app_id, app_secret)
    print("[1/3] 获取 token OK")
    key = fp.upload_image(tk, IMG)
    print("[2/3] 上传桑基图 OK, image_key:", key[:24] + "...")
    fp.push_image_card(webhook, tk, key, HEADER)
    print("[3/3] 图片卡片已发送")
    fp.push_text_card(webhook, tk, HEADER, TEXT)
    print("文字摘要卡片已发送")
    return True


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)