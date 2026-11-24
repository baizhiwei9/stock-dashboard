#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OT Daily Report v2 - 统一OT多空+三方法桑基流转
================================================
文字报告只聚焦最优传输OT（多空观点+仓位建议）。
两张桑基图同时展示OT/SPD/Hodge三种算法的资金流转方向：
  绿色=OT W1 | 蓝色=SPD | 红色=Hodge

消息头: 报告标题末尾带「来自 DoMate」
"""

import sys, os
sys.path.insert(0, '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f86bdd5bfffeXHo4SFOaOa2sWG/pylibs')

import numpy as np, pandas as pd, requests, json, warnings, time
warnings.filterwarnings('ignore')
from PIL import Image, ImageDraw, ImageFont
from numpy.linalg import eigh
from datetime import datetime


# [baseline-stats] 基线组合统计栏（fail-safe，任何异常返回空值）
def _bs(key, kind='text'):
    try:
        import sys as _b
        _d = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/shared'
        if _d not in _b.path:
            _b.path.insert(0, _d)
        import report_stats as _r
        return _r.segment(key, kind)
    except Exception:
        return [] if kind in ('card', 'lines') else ''


# ---- Feishu ----
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/6d34534a-af18-48ba-be77-a30c524734a8"
APP_ID = "cli_a97b60337b7bdbd5"
APP_SECRET = "CwiWINsJk6Zzu0uO5pdmCd00y3HEQwPB"
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"

# ---- Fonts ----
_SH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SimHei.ttf')
_ZH = _SH if os.path.exists(_SH) else '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
_EN = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

def F(sz, lang='zh'):
    return ImageFont.truetype(_ZH if lang == 'zh' else _EN, sz)

def TS(draw, text, font):
    bbox = draw.textbbox((0,0), text, font=font)
    return bbox[2]-bbox[0], bbox[3]-bbox[1]

def DM(draw, x, y, text, zh_f, en_f, fill='black', anchor='la'):
    runs=[]; cl=None; cr=''
    for ch in text:
        is_zh = '\u4e00'<=ch<='\u9fff' or '\u3000'<=ch<='\u303f' or '\uff00'<=ch<='\uffef'
        lang = 'zh' if is_zh else 'en'
        if lang==cl: cr+=ch
        else:
            if cr: runs.append((cl,cr))
            cl=lang; cr=ch
    if cr: runs.append((cl,cr))
    tw=0; th=0
    for lang,run in runs:
        w,h=TS(draw, run, zh_f if lang=='zh' else en_f)
        tw+=w; th=max(th,h)
    if anchor=='ma': x-=tw//2
    elif anchor=='ra': x-=tw
    cx=x
    for lang,run in runs:
        font=zh_f if lang=='zh' else en_f
        w,h=TS(draw,run,font)
        dy=(th-h)//2
        draw.text((cx,y+dy), run, fill=fill, font=font)
        cx+=w
    return tw,th

# ---- Config ----
ETFS = [
    ('sh510300', '沪深300', '宽基'), ('sh510500', '中证500', '宽基'), ('sh512100', '中证1000', '宽基'),
    ('sh588000', '科创50', '宽基'), ('sh510050', '上证50', '宽基'), ('sz159915', '创业板', '宽基'),
    ('sh512010', '医药', '行业'), ('sh512480', '半导体', '行业'), ('sh515030', '新能源车', '行业'),
    ('sh515790', '光伏', '行业'), ('sh512660', '军工', '行业'), ('sh512800', '银行', '行业'),
    ('sh512000', '券商', '行业'), ('sh515050', '5G', '行业'), ('sh512690', '酒', '行业'),
    ('sz159992', '创新药', '行业'),
]
LOOKBACK, DATA_LEN = 40, 1200
SPD_W, SPD_REF = 60, 60
HODGE_W, HODGE_T = 30, 0.3
W1_WINDOWS, W1_WEIGHTS = [40, 60], [0.6, 0.4]
OT_QUANTILE = 0.70
MA_PERIOD = 40
# 真实资金流：相对口径(主力净流入占比 r0_ratio, 消除行业规模偏差), OT W1 窗口=LOOKBACK
FUND_WINDOW = 5    # 有效数据最少天数(判定数据可用)
FUND_NUM = 60      # 资金流拉取条数(需覆盖LOOKBACK=40日窗口+对齐缓冲)

# ---- 指数级 ETF 总盘子: 组内固化清单(同花顺全名人工归集2026-09-18, 已剔除低波/A500/港股/沪港深/主题等误归; 16主ETF全覆盖) ----
FALLBACK_GROUPS = {
    '沪深300': [
        ('sh510300', '华泰柏瑞沪深300ETF'),
        ('sh510310', '易方达沪深300ETF'),
        ('sh510330', '华夏沪深300ETF'),
        ('sz159919', '嘉实沪深300ETF'),
        ('sh515330', '天弘沪深300ETF'),
        ('sh510350', '沪深300ETF工银'),
        ('sh510360', '广发沪深300ETF'),
        ('sz159925', '南方沪深300ETF'),
        ('sh515390', '华安沪深300ETF'),
        ('sh515660', '国联安沪深300ETF'),
        ('sz159673', '鹏华沪深300ETF'),
        ('sh510380', '国寿安保沪深300ETF'),
        ('sz159300', '富国沪深300ETF'),
        ('sh510320', '中金沪深300ETF'),
        ('sh510390', '平安沪深300ETF'),
        ('sz159330', '沪深300ETF基金'),
        ('sh563520', '沪深300ETF永赢'),
        ('sz159393', '万家沪深300ETF'),
        ('sh515310', '添富沪深300ETF'),
        ('sh561930', '招商沪深300ETF'),
        ('sh515360', '方正富邦沪深300ETF'),
        ('sh515130', '博时沪深300ETF'),
        ('sh510370', '兴业沪深300ETF'),
        ('sh515350', '民生加银沪深300ETF'),
    ],
    '中证500': [
        ('sh510500', '南方中证500ETF'),
        ('sz159922', '嘉实中证500ETF'),
        ('sh512500', '华夏中证500ETF'),
        ('sh510580', '易方达中证500ETF'),
        ('sh510510', '广发中证500ETF'),
        ('sz159820', '天弘中证500ETF'),
        ('sh512510', '华泰柏瑞中证500ETF'),
        ('sh510590', '平安中证500ETF'),
        ('sz159968', '博时中证500ETF'),
        ('sz159337', '中证500ETF基金'),
        ('sh515190', '中银证券中证500ETF'),
        ('sz159982', '中证500'),
        ('sh510530', '中证500ETF工银'),
        ('sh563750', '汇添富中证500ETF'),
        ('sz159500', '富国中证500ETF'),
        ('sh561350', '国泰中证500ETF'),
        ('sh510560', '国寿安保中证500ETF'),
        ('sz159935', '景顺长城中证500ETF'),
        ('sh510570', '兴业中证500ETF'),
        ('sh510550', '方正富邦中证500ETF'),
        ('sh515550', '国联中证500ETF'),
    ],
    '中证1000': [
        ('sh512100', '南方中证1000ETF'),
        ('sz159845', '华夏中证1000ETF'),
        ('sh560010', '广发中证1000ETF'),
        ('sz159633', '易方达中证1000ETF'),
        ('sz159629', '富国中证1000ETF'),
        ('sh560110', '汇添富中证1000ETF'),
        ('sh516300', '华泰柏瑞中证1000ETF'),
    ],
    '科创50': [
        ('sh588000', '华夏上证科创板50成份ETF'),
        ('sh588080', '易方达上证科创板50ETF'),
        ('sh588050', '科创50ETF工银'),
        ('sh588060', '广发科创50ETF'),
        ('sh588090', '华泰柏瑞上证科创板50成份ETF'),
        ('sh588940', '富国上证科创板50成份ETF'),
        ('sh589360', '国泰上证科创板50成份ETF'),
        ('sh588870', '汇添富上证科创板50成份ETF'),
        ('sh589850', '科创50ETF东财'),
        ('sh588950', '景顺长城上证科创板50成份ETF'),
        ('sh589150', '平安上证科创板50成份ETF'),
        ('sh588280', '华安上证科创板50ETF'),
        ('sh588150', '南方上证科创板50成份ETF'),
        ('sh588720', '中银上证科创板50ETF'),
        ('sh588840', '万家上证科创板50成份ETF'),
    ],
    '上证50': [
        ('sh510050', '华夏上证50ETF'),
        ('sh510100', '易方达上证50ETF'),
        ('sh530000', '天弘上证50ETF'),
        ('sh510710', '博时上证50ETF'),
        ('sh510800', '建信上证50ETF'),
        ('sh510850', '上证50ETF工银'),
        ('sh530050', '上证50ETF东财'),
        ('sh510950', '广发上证50ETF'),
        ('sh510190', '华安上证50ETF'),
        ('sh510680', '万家上证50ETF'),
        ('sh510600', '申万菱信上证50ETF'),
    ],
    '创业板': [
        ('sz159915', '易方达创业板ETF'),
        ('sz159952', '广发创业板ETF'),
        ('sz159977', '天弘创业板ETF'),
        ('sz159948', '南方创业板ETF'),
        ('sz159957', '华夏创业板ETF'),
        ('sz159971', '富国创业板ETF'),
        ('sz159908', '博时创业板ETF'),
        ('sz159205', '创业板ETF东财'),
        ('sz159247', '汇添富创业板ETF'),
        ('sz159958', '创业板ETF工银'),
        ('sz159964', '平安创业板ETF'),
        ('sz159956', '建信创业板ETF'),
        ('sz159810', '浦银创业板ETF'),
        ('sz159808', '融通创业板ETF'),
    ],
    '医药': [
        ('sh512170', '华宝中证医疗ETF'),
        ('sh512010', '易方达沪深300医药ETF'),
        ('sz159938', '广发中证全指医药卫生ETF'),
        ('sz159859', '天弘国证生物医药ETF'),
        ('sh512290', '国泰中证生物医药ETF'),
        ('sz159929', '汇添富中证医药卫生ETF'),
        ('sz159828', '国泰中证医疗ETF'),
        ('sz159847', '易方达中证医疗ETF'),
        ('sh516820', '平安中证医疗创新ETF'),
        ('sh560260', '广发中证医疗ETF'),
        ('sz159839', '汇添富国证生物医药ETF'),
        ('sh515950', '富国中证医药50ETF'),
        ('sz158010', '嘉实中证医疗ETF'),
        ('sz159838', '博时医药50ETF'),
        ('sh510660', '华夏医药ETF'),
        ('sz159760', '医疗健康'),
        ('sz159508', '华安国证生物医药ETF'),
    ],
    '半导体': [
        ('sz159995', '华夏国证半导体芯片ETF'),
        ('sh512480', '国联安半导体ETF'),
        ('sh512760', '国泰CES半导体芯片行业ETF'),
        ('sz159813', '芯片'),
        ('sz159801', '广发国证半导体芯片ETF'),
        ('sz159599', '芯片ETF基金'),
        ('sh589350', '科芯片指'),
    ],
    '新能源车': [
        ('sh515030', '华夏中证新能源汽车ETF'),
        ('sh515700', '平安中证新能源汽车产业ETF'),
        ('sh516520', '华泰柏瑞中证智能汽车主题ETF'),
        ('sz159806', '国泰中证新能源汽车ETF'),
        ('sh515250', '富国中证智能汽车主题ETF'),
        ('sh516390', '汇添富中证新能源汽车产业ETF'),
        ('sh516660', '华安中证新能源汽车ETF'),
        ('sz159824', '博时新能源汽车ETF'),
        ('sz159888', '华夏中证智能汽车主题ETF'),
        ('sz159183', '招商中证新能源汽车ETF'),
        ('sz159889', '国泰中证智能汽车主题ETF'),
        ('sz159795', '汇添富中证智能汽车主题ETF'),
    ],
    '光伏': [
        ('sh515790', '华泰柏瑞中证光伏产业ETF'),
        ('sz159857', '天弘中证光伏产业ETF'),
        ('sh515370', '华夏中证光伏产业ETF'),
        ('sh516880', '光伏50'),
        ('sh516230', '招商中证光伏产业ETF'),
        ('sh516290', '汇添富中证光伏产业ETF'),
        ('sh562970', '易方达中证光伏产业ETF'),
        ('sz159123', '嘉实中证光伏产业ETF'),
        ('sz159864', '国泰中证光伏产业ETF'),
        ('sh561410', '光伏ETF工银'),
        ('sz159863', '光伏产业'),
        ('sz159609', '浦银中证光伏产业ETF'),
        ('sh560310', '博时中证光伏产业ETF'),
        ('sh516180', '平安中证光伏产业ETF'),
        ('sh560230', '富国中证光伏产业ETF'),
        ('sz159618', '华安中证光伏产业ETF'),
    ],
    '军工': [
        ('sh512660', '国泰中证军工ETF'),
        ('sh512680', '广发中证军工ETF'),
        ('sh512670', '国防ETF'),
        ('sh512560', '易方达中证军工ETF'),
        ('sh512810', '华宝中证军工ETF'),
    ],
    '银行': [
        ('sh512800', '华宝中证银行ETF'),
        ('sh515290', '天弘中证银行ETF'),
        ('sh516310', '易方达中证银行ETF'),
        ('sh512700', '南方中证银行ETF'),
        ('sz159887', '富国中证800银行ETF'),
        ('sh515020', '华夏中证银行ETF'),
        ('sh512820', '中证银行ETF'),
        ('sz159253', '博时中证银行ETF'),
        ('sh516210', '华安中证银行ETF'),
        ('sh512730', '银行FUND'),
        ('sh561530', '银华银行'),
        ('sh512410', '广发中证银行ETF'),
        ('sh560670', '华泰柏瑞中证银行ETF'),
    ],
    '券商': [
        ('sh512880', '国泰中证全指证券公司ETF'),
        ('sh512000', '华宝中证全指证券公司ETF'),
        ('sz159841', '天弘中证全指证券公司ETF'),
        ('sz159842', '券商ETF'),
        ('sh512900', '南方中证全指证券公司ETF'),
        ('sh512570', '易方达中证全指证券公司ETF'),
        ('sh560090', '汇添富中证全指证券公司ETF'),
        ('sh515010', '华夏中证全指证券公司ETF'),
        ('sz159692', '证券ETF东财'),
        ('sz159260', '博时证券公司ETF'),
        ('sh515850', '富国中证全指证券公司ETF'),
        ('sh562870', '嘉实中证全指证券公司ETF'),
        ('sh516200', '华安中证全指证券公司ETF'),
        ('sh515560', '建信中证全指证券公司ETF'),
        ('sh510200', '上证券商'),
        ('sh560400', '华泰柏瑞中证全指证券公司ETF'),
        ('sz159848', '国联安证券ETF'),
        ('sz159008', '景顺长城中证全指证券公司ETF'),
        ('sz159058', '大成中证全指证券公司ETF'),
    ],
    '5G': [
        ('sh515050', '华夏中证5G通信主题ETF'),
        ('sz159994', '5GETF'),
        ('sz159811', '博时5G50ETF'),
    ],
    '酒': [
        ('sh512690', '酒ETF'),
    ],
    '创新药': [
        ('sz159992', '创新药'),
        ('sh515120', '广发创新药ETF'),
        ('sh516080', '易方达中证创新药产业ETF'),
        ('sz159858', '南方中证创新药产业ETF'),
        ('sz159835', '建信中证创新药产业ETF'),
        ('sh560900', '摩根中证创新药产业ETF'),
        ('sh516060', '创新药ETF工银'),
    ],
}
INDEX_GROUP_RULES = {
 '沪深300': (['沪深300'], ['红利','价值','增强','指增','现金流','ESG','全收益','沪港深','龙头','自由','质量','低波','高贝','民企','优选','智选','均衡','量化','期权','AH','央','国企','农业','养老','食品','文娱','主题','低碳','医药','非银','消费','金融','信息','健康','科技','算力','软件','新能源','AI','互联网','成长','风格','180','50','沪深300A','沪深300C','沪深300联接','联接']),
 '中证500': (['中证500'], ['增强','指增','价值','红利','现金流','质量','低波','成长','等权','沪港深','A500','国证','全收益','期权','量化','优选','稳','AH','央','国企','信息','科技','行业','高贝','smart','联接','500ETFFOF','500ETF联接']),
 '中证1000': (['中证1000'], ['增强','指增','红利','成长','价值','现金流','低波','质量','优选','量化','全收益','稳','A500','联接','1000指增','1000ETF联接']),
 '科创50': (['科创50','科创板50成份','上证科创板50'], ['增强','指增','期权','联接','成长','价值']),
 '上证50': (['上证50'], ['增强','指增','红利','价值','AH','沪港深','优选','量化','全收益','期权','央','国企','龙头','联接','50增强','50红利','50价值']),
 '创业板': (['创业板'], ['增强','指增','成长','价值','创业板50','创业板200','创业板300','大盘','综','宽基','质量','红利','低波','龙头','人工智能','AI','科创','全收益','优选','量化','算力','新能源','软件','科技','制造','医药','金融','互联','机器人','数字','云','数据','能源','材料','通信','电子','信息','创业50','创业200','创业300','创业大盘','创业综','联接','创业板50','创业板指增','创业板增强']),
 '医药': (['医药','医疗'], ['创新药','器械','卫生','生物','100','50','细分','港股','恒生','沪港深','科创','ESG','全收益','龙头','健康','服务','商','流通','设备','估值','优选','AH','主题','前沿','食品','医美','养老','生殖','疫苗','中药','800','医疗创新','医疗器械','医药商业','医药生物','生物医药','医疗ETF联接','医药ETF联接','科创','创业板','中证医疗','中证医药']),
 '半导体': (['半导体','芯片'], ['中韩','港股','恒生','产业','设备','科创','汽车','设计','材料','集成电路','龙头','全收益','细分','制造','基建','第三代','IGBT','数字经济','AI','算力','智能','车','键','增强','精选','50','联接','半导体设备','芯片设备','科创芯片','汽车芯片','半导体产业','芯片产业','半导体ETF联接']),
 '新能源车': (['新能源车','新能源汽车','智能汽车'], ['电池','零部件','充电','整车','科创','港股','全收益','龙头','车联网','无人驾驶','混合','锂','氢','汽车电子','智能驾驶','细分','联接','新能源车ETF联接']),
 '光伏': (['光伏'], ['龙头','设备','玻璃','储能','建筑','绿色电力','硅','科创','全收益','一体化','港股','细分','智能','联接','光伏ETF联接']),
 '军工': (['军工','国防'], ['龙头','空天','高端装备','期权','全收益','港股','细分','央企','国企','兵器','航海','信息化','增强','国证','联接','中证军工ETF联接','高端装备','军民融合']),
 '银行': (['银行'], ['AH','港股','跨境','联接','优选','绿色','金融科技','银行间','恒生','全收益','低波','价值','中证银行ETF联接','银行ETF联接','龙头']),
 '券商': (['证券','券商'], ['保险','非银','龙头','证券保险','A500','沪港深','港股','互联网','科创','跨境','先锋','主题','全收益','精选','金融科技','证券ETF联接','期权']),
 '5G': (['5G','通信'], ['港股','信息技术','智能','科创','龙头','设备','互联网','云计算','数据','半导体','AI','光模块','主题','全收益','联接','通信ETF联接']),
 '酒': (['酒'], ['白酒','联接','港股']),
 '创新药': (['创新药','创新药产业'], ['港股','恒生','AH','沪港深','科创','龙头','生物科技','CRO','器械','医疗','全收益','国证','精选','联接']),
}


# ---- Data ----
def dl(code, retries=3, backoff=1.2):
    """新浪日线(带退避重试): 单次失败按 1.2/2.4/3.6s 退避重试, 全部失败返回 None(由调用方记录)"""
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            u='https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
            p={'symbol':code,'scale':'240','datalen':str(DATA_LEN),'ma':'no'}
            r=json.loads(requests.get(u,params=p,timeout=20).text)
            if not r:
                last_err='empty'; time.sleep(backoff*(attempt+1)); continue
            df=pd.DataFrame([{'date':pd.to_datetime(i['day']),'open':float(i['open']),'high':float(i['high']),'low':float(i['low']),'close':float(i['close']),'volume':float(i['volume'])} for i in r]).set_index('date').sort_index()
            df['returns']=df['close'].pct_change()  # 原始收盘收益(仅留档; 收益类指标统一改用 get_adj_returns 前复权口径)
            return df
        except Exception as e:
            last_err=e; time.sleep(backoff*(attempt+1))
    print(f"  [行情取数失败] {code} 重试{retries}次仍失败: {last_err}")
    return None

def dl_all(etf_list):
    """拉取全部ETF日线并按共同交易日对齐.
    返回 (data, dates, last_dates, failed, all_dates): 每个成功标的的最后交易日、失败标的名单,
    以及各标的日期并集(交集截断前), 供数据完整性正确标注(哪项数据落后几个交易日)."""
    data={}; last_dates={}; failed=[]; idx_all=[]
    for code,name,_ in etf_list:
        df=dl(code)
        if df is not None and len(df)>120:
            data[name]=df; last_dates[name]=df.index[-1]; idx_all.append(df.index)
        else:
            failed.append(name)
    all_dates = idx_all[0] if idx_all else pd.DatetimeIndex([])
    for ix in idx_all[1:]:
        all_dates = all_dates.union(ix)
    dates=None
    for name,df in data.items():
        dates=df.index if dates is None else dates.intersection(df.index)
    for name in data: data[name]=data[name].loc[dates].copy()
    return data,dates,last_dates,failed,all_dates

# ---- 主力资金流（固定优先级: AKShare → TuShare → 网页[新浪→腾讯→同花顺]）----
FUND_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs"

def dl_flow_akshare(code):
    """优先级1 AKShare: 东财个股资金流历史(主力净流入-净占比, %→小数)"""
    try:
        import akshare as ak
        market = 'sh' if code.lower().startswith('sh') else 'sz'
        df = ak.stock_individual_fund_flow(stock=code[2:], market=market)
        if df is None or len(df) == 0 or '主力净流入-净占比' not in df.columns:
            return None
        ratio = pd.Series(
            pd.to_numeric(df['主力净流入-净占比'], errors='coerce').values / 100.0,
            index=pd.to_datetime(df['日期'])).dropna().sort_index()
        # 无 r0_net/turnover 字段 → 占比口径直接作为相对资金强度得分
        return pd.DataFrame({'r0_ratio': ratio.values}, index=ratio.index)
    except Exception:
        return None

def dl_flow_tushare(code):
    """优先级2 TuShare: moneyflow 个股资金流(无占比/换手字段 → 返回None降级)"""
    try:
        import tushare as ts
        pro = ts.pro_api()
        ts_code = code[2:] + ('.SH' if code.lower().startswith('sh') else '.SZ')
        df = pro.moneyflow(ts_code=ts_code)
        return None
    except Exception:
        return None

def dl_flow_sina(code):
    """优先级3 网页·新浪: MoneyFlow.ssl_qsfx_zjlrqs
    返回 DataFrame[r0_net(元), r0_ratio(无量纲小数), turnover(换手率%, 原始值/100),
                   close(收盘价)], 按日期升序"""
    last_err = None
    for attempt in range(2):  # 限频重试 ≤1 次
        try:
            p = {'page': 1, 'num': str(FUND_NUM), 'sort': 'opendate', 'asc': 0, 'daima': code}
            r = requests.get(FUND_URL, params=p, timeout=20)
            arr = r.json()
            if not isinstance(arr, list) or not arr:
                last_err = 'empty'; time.sleep(0.5); continue
            df = pd.DataFrame([{
                'r0_net': float(x['r0_net']),
                'r0_ratio': float(x['r0_ratio']),
                'turnover': float(x['turnover']) / 100.0,   # 原始值264.471 → 2.645%
                'close': float(x['trade']),
            } for x in arr], index=[pd.to_datetime(x['opendate']) for x in arr])
            df.index.name = 'date'
            return df.sort_index()
        except Exception as e:
            last_err = e; time.sleep(0.5)
    print(f"  [新浪限频] {code} 重试后仍失败: {last_err}")
    return None

def dl_flow_tencent(code):
    """优先级4 网页·腾讯: 资金流历史接口(沙箱实测不可用, 返回None自动降级)"""
    try:
        return None
    except Exception:
        return None

def dl_flow_ths(code):
    """优先级5 网页·同花顺: 个股资金流历史(需hexin-v加密token且无语义字段, 返回None降级)"""
    try:
        return None
    except Exception:
        return None

FUND_FETCHERS = [('AKShare', dl_flow_akshare), ('TuShare', dl_flow_tushare),
                 ('新浪', dl_flow_sina), ('腾讯', dl_flow_tencent), ('同花顺', dl_flow_ths)]

def dl_flow(code):
    """按固定优先级 AKShare→TuShare→网页(新浪→腾讯→同花顺) 探测主力资金流;
    返回 (DataFrame[r0_net,r0_ratio,turnover,close], 实际数据源名)"""
    for src_name, fn in FUND_FETCHERS:
        df = fn(code)
        if df is not None and len(df) > FUND_WINDOW:
            return df, src_name
    return None, '无'

def dl_flows_all(etf_list, dates):
    """拉取全部ETF主力资金流, 对齐日线dates索引;
    返回 (dict[name->DataFrame], dict[数据源->只数])"""
    ff = {}; src_cnt = {}
    for code, name, _ in etf_list:
        df, src = dl_flow(code)
        if df is not None and len(df) > FUND_WINDOW:
            ff[name] = df.reindex(dates)
            src_cnt[src] = src_cnt.get(src, 0) + 1
    return ff, src_cnt

# ---- 行情快照（腾讯实时）: ETF基金规模≈自由流通市值, 上证+深证=全市场口径 ----
QUOTE_URL = "https://qt.gtimg.cn/q={codes}"

def dl_quote_tencent(etf_list):
    """腾讯实时行情: 每只ETF的基金规模(亿≈自由流通市值)与当日成交额(亿);
    全市场(上证+深证)成交额(亿)/流通市值(亿)。
    返回 (dict[name->{'mv_yi','amt_yi'}], 全市场dict)"""
    codes = [c for c, _, _ in etf_list] + ['sh000001', 'sz399001']
    try:
        r = requests.get(QUOTE_URL.format(codes=','.join(codes)), timeout=20)
        r.encoding = 'gbk'
        quotes = {}
        for line in r.text.strip().split('\n'):
            if '=' not in line: continue
            key = line.split('=')[0].replace('v_', '')
            f = line.split('"')[1].split('~')
            if len(f) < 45: continue
            q = {
                'mv_yi': float(f[44]) if f[44] else 0.0,   # ETF: 基金规模(亿); 指数: 流通市值(亿)
                'amt_wan': float(f[37]) if f[37] else 0.0, # 成交额(万元)
                'turn': float(f[38]) if f[38] else 0.0,    # 换手率(%)
                'close': float(f[3]) if f[3] else 0.0,
            }
            quotes[key] = q
        name_map = {c: n for c, n, _ in etf_list}
        mv = {name_map[c]: {'mv_yi': quotes[c]['mv_yi'],
                            'amt_yi': quotes[c]['amt_wan'] / 1e4} for c in name_map if c in quotes}
        mkt_amt_yi = (quotes.get('sh000001', {}).get('amt_wan', 0) +
                      quotes.get('sz399001', {}).get('amt_wan', 0)) / 1e4  # 万→亿
        mkt_mv_yi = (quotes.get('sh000001', {}).get('mv_yi', 0) +
                     quotes.get('sz399001', {}).get('mv_yi', 0))
        mkt_turn = mkt_amt_yi / mkt_mv_yi * 100 if mkt_mv_yi > 0 else 0.0
        return mv, {'amt_yi': mkt_amt_yi, 'mv_yi': mkt_mv_yi, 'turn': mkt_turn}
    except Exception:
        return {}, {'amt_yi': 0.0, 'mv_yi': 0.0, 'turn': 0.0}

# ---- 份额反推: 份额_t = volume_t / (turnover%_t / 100) ----
def etf_share_history(ff_df, kline_df, dates):
    """用新浪资金流换手率(%)+日线成交量(份)反推每日份额;
    返回 (份额Series对齐dates, 有效天数)"""
    shares = pd.Series(np.nan, index=dates)
    if 'turnover' not in getattr(ff_df, 'columns', []):
        return shares, 0          # 资金流降级源(无turnover列)无法反推份额, 保持缺失而非报错
    for d in dates:
        if d in ff_df.index and d in kline_df.index:
            turn = ff_df.at[d, 'turnover']
            vol = kline_df.at[d, 'volume']
            if not np.isnan(turn) and turn > 0 and not np.isnan(vol):
                shares.at[d] = vol / (turn / 100.0)
    return shares, int((~shares.isna()).sum())

def build_index_groups():
    """指数级ETF组合清单: 直接使用固化清单(2026-09-18 同花顺全名人工归集+多轮修正,
    已排除低波/A500/港股/沪港深/主题/联接等误归; 16组全覆盖)。
    固化清单经实测校准: 沪深300总盘2619亿/中证500=760亿/中证1000=407亿/科创50=1775亿/
    上证50=287亿/创业板=994亿。返回 {目标: [(code, 全名), ...]}"""
    return {k: list(v) for k, v in FALLBACK_GROUPS.items()}


def sym_code(code):
    c = str(code)
    if c.startswith(('5','6')): return 'sh' + c
    if c.startswith(('1','0')): return 'sz' + c
    return c

def dl_group_quote(groups):
    """腾讯批量行情: 组内全部ETF的基金规模(亿)与当日成交额(亿)。
    返回 (dict[目标->{mv_yi, amt_yi, n}], dict[code->规模亿])"""
    all_codes = []
    for v in groups.values(): all_codes += [c for c, _ in v]
    all_codes = list(dict.fromkeys(all_codes))
    mv_c = {}
    amt_c = {}
    for i in range(0, len(all_codes), 50):
        batch = all_codes[i:i+50]
        try:
            r = requests.get(QUOTE_URL.format(codes=','.join(batch)), timeout=25)
            r.encoding = 'gbk'
            for line in r.text.strip().split('\n'):
                if '=' not in line: continue
                key = line.split('=')[0].replace('v_', '')
                f = line.split('"')[1].split('~')
                if len(f) < 46: continue
                mv_c[key] = float(f[44]) if f[44] else 0.0
                amt_c[key] = (float(f[37]) / 1e4) if f[37] else 0.0  # 万元 -> 亿
        except Exception:
            pass
        time.sleep(0.25)
    gq = {}
    for target, items in groups.items():
        mv_yi = sum(mv_c.get(c, 0.0) for c, _ in items)
        amt_yi = sum(amt_c.get(c, 0.0) for c, _ in items)
        gq[target] = {'mv_yi': mv_yi, 'amt_yi': amt_yi, 'n': len(items)}
    return gq, mv_c

def dl_group_flows(groups, gq, mv_c, lb=5, mv_min=0.5):
    """组内每只ETF主力资金流: 直连新浪(组内ETF AKShare/TuShare无数据, 且已实测全量覆盖),
    失败再走完整优先级链 dl_flow。汇总窗口内净流入合计(元)。
    覆盖规模口径: 只统计 规模>=mv_min亿 且资金流拉取成功的ETF,
    返回 cov_mv(覆盖规模合计,亿) 供分子分母严格同口径。
    返回 dict[目标 -> {'net_yuan': 合计, 'cov': 只数, 'n': 组内总数, 'cov_mv': 覆盖规模亿,
                        'skip_small': 因规模过小跳过只数, 'fail': 拉取失败只数}]"""
    out = {}
    for target, items in groups.items():
        net_tot = 0.0; cov = 0; cov_mv = 0.0; skip_small = 0; fail = 0
        for code, _ in items:
            mv_yi = mv_c.get(code, 0.0)
            if mv_yi < mv_min:
                skip_small += 1
                continue
            df = None
            try:   # 直连新浪优先(组级目标全为场内ETF, 铭链前三档无ETF数据)
                df = dl_flow_sina(code)
            except Exception:
                df = None
            if df is None or len(df) < 2:
                df2, _ = dl_flow(code)   # 全链降级兜底
                if df2 is None or len(df2) < 2:
                    fail += 1
                    continue
                df = df2
            vals = df['r0_net'].values
            idx = len(vals) - 1
            win = vals[max(0, idx - lb):idx]
            win = win[~np.isnan(win)]
            if len(win) < 1:
                tail = vals[:idx]; tail = tail[~np.isnan(tail)]
                if len(tail) < 1:
                    fail += 1
                    continue
                win = tail[-1:]
            net_tot += float(np.sum(win))
            cov += 1
            cov_mv += mv_yi
            time.sleep(0.2)
        out[target] = {'net_yuan': net_tot, 'cov': cov, 'n': len(items),
                       'cov_mv': cov_mv, 'skip_small': skip_small, 'fail': fail}
    return out

# ---- W1 ----
def adjust_splits(s, jump=0.25):
    """前复权: 检测ETF份额折算/拆分造成的假跳空(|单日收益|>jump), 将该日之前全部价格等比缩放消除跳空.
    用于均价/动量/区间收益(趋势维度)与 W1 距离: 避免拆分伪跳空污染趋势判断与漂移距离计算."""
    s = pd.Series(s).astype(float).copy()
    i = 1
    while i < len(s):
        prev, cur = s.iloc[i - 1], s.iloc[i]
        if prev > 0:
            r = cur / prev - 1.0
            if abs(r) > jump:
                factor = cur / prev
                s.iloc[:i] = s.iloc[:i] * factor
                i = 1
                continue
        i += 1
    return s

def get_adj_returns(data, name):
    """前复权收益序列(消除ETF份额拆分伪跳空): 供 W1 / SPD / Hodge / AC1 与趋势维度共用同一口径.
    以 data[name]['adj_returns'] 缓存, 避免同一标的重复计算 adjust_splits.
    注意: 必须用前复权价而非 data[name]['returns'](原始close的pct_change), 否则拆分伪跳空会污染
          相关性/自相关/漂移距离等全部收益类指标."""
    if 'adj_returns' not in data[name].columns:
        s = adjust_splits(data[name]['close'].values).pct_change()
        s.index = data[name].index
        data[name] = data[name].assign(adj_returns=s)
    return data[name]['adj_returns']

def compute_w1_1d(returns, window):
    n=len(returns); w1=np.full(n,np.nan); direction=np.full(n,0.0)
    vals=returns.values if hasattr(returns,'values') else returns
    for i in range(window*2,n):
        prev=vals[i-window*2:i-window]; curr=vals[i-window:i]
        mask_p=~np.isnan(prev); mask_c=~np.isnan(curr)
        if mask_p.sum()<5 or mask_c.sum()<5: continue
        prev=np.sort(prev[mask_p]); curr=np.sort(curr[mask_c])
        ml=min(len(prev),len(curr))
        w1[i]=np.mean(np.abs(curr[:ml]-prev[:ml]))
        direction[i]=np.sign(np.mean(curr)-np.mean(prev))
    return w1,direction

def compute_individual_w1(data,name):
    """W1 距离口径: 基于前复权价格序列的收益(消除ETF份额拆分伪跳空), 与80日动量/MA40/160趋势维度口径一致.
    注意: 不直接用 data[name]['returns'](原始close的pct_change), 否则拆分伪跳空会放大漂移距离.
    收益序列复用 get_adj_returns()(与 SPD/Hodge/AC1 同一口径)。"""
    adj_ret = get_adj_returns(data, name)
    w1_list=[]; dir_list=[]
    for w in W1_WINDOWS:
        w1,d=compute_w1_1d(adj_ret,w)
        w1_list.append(w1); dir_list.append(d)
    n=len(w1_list[0]); combined=np.full(n,np.nan); direction=np.full(n,0.0)
    for i in range(n):
        vals=[]; dirs=[]
        for j in range(len(w1_list)):
            if not np.isnan(w1_list[j][i]):
                vals.append((w1_list[j][i],W1_WEIGHTS[j])); dirs.append(dir_list[j][i])
        if len(vals)>0:
            tw=sum(w for _,w in vals)
            if tw>0:
                combined[i]=sum(v*w/tw for v,w in vals)
                direction[i]=np.sign(sum(d for d in dirs))
    return combined,direction

# ---- OT Flow v3 (宽基: 净流入/自由流通市值比率; 行业: 四因子加权评分) ----
def pct_rank_100(series):
    """截面标准化: 转为0-100百分位排名"""
    s = pd.Series(series)
    return s.rank(pct=True).fillna(0.5).values * 100.0

def f_net5(ff_df, idx, lb=5, mv_yi=None):
    """近lb个连续交易日主力净流入累计(元); 窗口内有效数据不足时退化为近1日。
    mv_yi给出时返回 累计净流入/自由流通市值 比率。
    返回 (累计, 比率)"""
    if ff_df is None: return 0.0, 0.0
    vals = ff_df['r0_net'].values
    win = vals[max(0, idx - lb):idx]
    win = win[~np.isnan(win)]
    if len(win) < 1:
        tail = vals[:idx]
        tail = tail[~np.isnan(tail)]
        if len(tail) < 1: return 0.0, 0.0
        win = tail[-1:]   # 退化: 近1日(最后一个有效交易日)
    net = float(np.sum(win))
    ratio = net / (float(mv_yi) * 1e8) if mv_yi and mv_yi > 0 else 0.0
    return net, ratio

def wide_flow_ratio(gflows, gq, names):
    """宽基: 组内近5日主力净流入合计 ÷ 指数级ETF总规模(覆盖口径cov_mv)
    → 指数级资金流入比率排序 (用户口径: 流入60亿/总盘6000亿=1%)"""
    flow = np.zeros(len(names))
    for k, name in enumerate(names):
        g = gflows.get(name, {})
        cov_mv = g.get('cov_mv', 0.0)
        net = g.get('net_yuan', 0.0)
        flow[k] = net / (cov_mv * 1e8) if cov_mv > 0 else 0.0
    return flow

def industry_four_factor(ff_data, data, names, mv_map, mkt, idx, gflows, gq, lb=5):
    """行业: 四因子加权评分(取消拥挤度, 无过热剔除)
    因子1 资金强度(31.25%) = 指数级净流入/指数级ETF总规模(组内合计, 覆盖口径cov_mv)
    因子2 换手率偏离(18.75%) = 行业日均换手率 - 全市场换手率 (主ETF口径)
    因子3 ETF资金(31.25%) = ETF份额变化率(%) (主ETF口径), <0且|.|>5% 综合扣10分
    因子4 机构关注度(18.75%) = 日均成交额/自由流通市值 (主ETF口径)
    各因子0-100百分位标准化; 窗口优先近lb日, 不足退近1日。
    返回 (dict[name->{F..得分,综合,备注,inactive}], 按综合降序的名字列表[数据不足排末尾])"""
    mkt_turn = mkt.get('turn', 0.0)       # 全市场当日换手率(%)

    raw = {}
    for name in names:
        ff = ff_data.get(name)
        q = mv_map.get(name, {})
        mv = q.get('mv_yi', 0.0) if isinstance(q, dict) else float(q or 0)
        kl = data.get(name)
        g = gflows.get(name, {})
        cov_mv = g.get('cov_mv', 0.0)     # 指数级覆盖规模(组内ETF规模合计, 亿)
        row = {'mv_yi': mv, 'index_mv': cov_mv, 'inactive': False, 'note': '',
               'f1': 0.0, 'f2': 0.0, 'f3': 0.0, 'f4': 0.0, 'score': 0.0, 'deduct': 0.0}
        if ff is None or kl is None or mv <= 0:
            row['inactive'] = True
            row['note'] = '数据不足'
            raw[name] = row; continue
        # 因子1 资金强度: 组内近5日净流入合计(元) ÷ 指数级ETF总规模(元)
        f1 = g.get('net_yuan', 0.0) / (cov_mv * 1e8) if cov_mv > 0 else 0.0
        row['f1_raw'] = f1
        row['index_net'] = g.get('net_yuan', 0.0)
        # 5日成交额(亿) (主ETF口径, 用于因子4)
        close_v = kl['close'].values; vol_v = kl['volume'].values
        amt5_yi = float(np.nansum(close_v[max(0, idx - lb):idx] * vol_v[max(0, idx - lb):idx]) / 1e8)
        # 因子2 换手率偏离: 行业窗口内日均换手率(新浪turnover均值) - 全市场
        # 资金流走 AKShare/TuShare 降级时其DataFrame无turnover列: 换手维度不可得, 取全市场均值作中性(偏离0)
        if 'turnover' in getattr(ff, 'columns', []):
            turn_win = ff['turnover'].values[max(0, idx - lb):idx]
            turn_win = turn_win[~np.isnan(turn_win)]
            if len(turn_win) < 1:
                t_all = ff['turnover'].values[:idx]
                t_all = t_all[~np.isnan(t_all)]
                turn_win = t_all[-1:] if len(t_all) >= 1 else np.array([0.0])
            ind_turn = float(np.mean(turn_win))
        else:
            ind_turn = float(mkt_turn)
        row['ind_turn'] = ind_turn
        row['f2_raw'] = ind_turn - mkt_turn
        # 因子3 ETF份额变化率(%): 份额=vol/(turnover%/100)
        win_idx = kl.index[max(0, idx - lb):idx + 1]
        sh, n_ok = etf_share_history(ff, kl, win_idx)
        sv = sh.dropna().values
        chg = (sv[-1] / sv[0] - 1.0) * 100.0 if len(sv) >= 2 else 0.0
        row['f3_raw'] = chg
        # 因子4 机构关注度: 窗口内日均成交额/自由流通市值 (主ETF口径)
        amt_daily_yi = amt5_yi / max(lb, 1)
        row['f4_raw'] = amt_daily_yi / mv if mv > 0 else 0.0
        raw[name] = row

    # 截面标准化(0-100百分位) —— 数据完整行业
    active = [n for n in names if not raw[n]['inactive']]
    if active:
        f1v = pct_rank_100([raw[n]['f1_raw'] for n in active])
        f2v = pct_rank_100([raw[n]['f2_raw'] for n in active])
        f3v = pct_rank_100([raw[n]['f3_raw'] for n in active])
        f4v = pct_rank_100([raw[n]['f4_raw'] for n in active])
        for i, n in enumerate(active):
            r = raw[n]
            r['f1'] = f1v[i]
            r['f2'] = f2v[i]
            r['f3'] = f3v[i]
            r['f4'] = f4v[i]
            s = (0.3125 * r['f1'] + 0.1875 * r['f2'] +
                 0.3125 * r['f3'] + 0.1875 * r['f4'])
            # ETF份额变化率<0 且 |.|>5% → 综合扣10分
            if r['f3_raw'] < 0 and abs(r['f3_raw']) > 5.0:
                s -= 10.0
                r['deduct'] = 10.0
                r['note'] = (r['note'] + ';' if r['note'] else '') + \
                    'ETF份额%.1f%%扣10分' % r['f3_raw']
            r['score'] = s
    # 排序: 数据完整按综合降序, 数据不足排末尾
    ranked = sorted([n for n in names if not raw[n]['inactive']],
                    key=lambda x: raw[x]['score'], reverse=True)
    ranked += [n for n in names if raw[n]['inactive']]
    return raw, ranked

# ---- SPD ----
def spd_flow(data,names,idx,w=SPD_W,ref=SPD_REF):
    """SPD 流形几何漂移: 基于 TDA-SPD 策略核心算法。
    对每个行业i:
    1. 计算它与等权市场组合的相关系数 rho_i;
    2. Fisher z变换: z_i = atanh(rho_i) (SPD(2)流形上的测地坐标);
    3. 漂移 = |z_i(t) - z_i(t_ref)| (SPD(2)上的测地线距离);
    4. 乘以动量方向因子 (s_i+1)^2/4, s_i为当前窗口累计收益率百分位排名;
    5. 返回 signed score: 正值=强势漂移(流入侧机会), 负值=弱势漂移(流出侧风险)。
    注: 收益率矩阵取前复权收益(get_adj_returns), 与 W1/趋势维度口径统一, 避免拆分伪跳空污染相关系数。"""
    n=len(names)
    # 当前窗口收益率矩阵 T×n (前复权)
    rets={k:get_adj_returns(data,nm).values[max(0,idx-w):idx] for k,nm in enumerate(names)}
    rets={k:v[~np.isnan(v)] for k,v in rets.items()}
    ml=min(len(v) for v in rets.values())
    mat=np.zeros((ml,n))
    for k in range(n): mat[:,k]=rets[k][:ml]
    # 参考窗口 (前复权)
    ref_start=max(0,idx-ref-w)
    rr={k:get_adj_returns(data,nm).values[ref_start:ref_start+w] for k,nm in enumerate(names)}
    rr={k:v[~np.isnan(v)] for k,v in rr.items()}
    ml2=min(len(v) for v in rr.values())
    mat_ref=np.zeros((ml2,n))
    for k in range(n): mat_ref[:,k]=rr[k][:ml2]
    # 等权市场组合(含自身)
    market_current=mat.mean(axis=1)
    market_ref=mat_ref.mean(axis=1)
    # 每个行业与等权市场的相关系数漂移 (SPD(2)测地距离, 绝对值)
    drift=np.zeros(n)
    for k in range(n):
        r_now=np.corrcoef(mat[:,k],market_current)[0,1] if np.std(mat[:,k])>1e-10 else 0
        r_ref=np.corrcoef(mat_ref[:,k],market_ref)[0,1] if np.std(mat_ref[:,k])>1e-10 else 0
        r_now=np.clip(r_now,-0.999,0.999)
        r_ref=np.clip(r_ref,-0.999,0.999)
        z_now=np.arctanh(r_now)
        z_ref=np.arctanh(r_ref)
        drift[k]=abs(z_now-z_ref)
    # 动量方向因子 (替代夏普比)
    curr_rets=np.mean(mat,axis=0)*ml
    s=(np.argsort(np.argsort(curr_rets))/(max(n-1,1))*2.0)-1.0
    signed=drift*s
    return signed

# ---- Hodge ----
HODGE_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ot_hodge_state.json')

def _load_hodge_state():
    """读取 Hodge 符号锚定状态(上一交易日各池的 Fiedler 向量); 文件缺失/损坏时返回空 dict。"""
    try:
        with open(HODGE_STATE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_hodge_state(key, names, v):
    """落盘当前池的已锚定 Fiedler 向量, 供下一交易日做符号对齐。"""
    if not key: return
    st = _load_hodge_state()
    st[key] = {'names': list(names), 'vec': [round(float(x), 10) for x in v]}
    try:
        with open(HODGE_STATE, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception as e:
        print(f"  [warn] Hodge 符号锚定状态写入失败({key}): {e}")

def _anchor_sign(v, key, names):
    """符号锚定: eigh 返回的特征向量整体符号在数学上不唯一, 同一结构在相邻交易日可能整体翻号。
    先与上一交易日落盘的向量做内积对齐(内积<0则整体翻号); 无状态或标的名录变化时用固定约定兜底
    ——取最大绝对分量为正。此处刻意不用"分量和为正"作约定: 有符号拉普拉斯的 Fiedler 向量分量和可能
    恰为 0(全为正相关边时 L·1=0, v2⊥1), 浮点噪声(±1e-17)会随机决定正负, 造成逐日无谓翻号。
    只固定符号表示, 不改变各标的取值相对大小与结构含义。
    返回 (已锚定向量, 来源标签: 'prev' 上一交易日对齐 / 'fallback' 固定约定)。"""
    v = np.array(v, dtype=float)
    if not np.any(np.abs(v) > 0):
        return v, 'fallback'
    rec = _load_hodge_state().get(key) if key else None
    if rec and list(rec.get('names', [])) == list(names):
        pv = np.array(rec.get('vec', []), dtype=float)
        if pv.shape == v.shape and np.any(np.abs(pv) > 0):
            dp = float(np.dot(v, pv))
            if dp > 0: return v, 'prev'
            if dp < 0: return -v, 'prev'
    i = int(np.argmax(np.abs(v)))
    return (-v if v[i] < 0 else v), 'fallback'

def hodge_flow(data,names,idx,cw=HODGE_W,th=HODGE_T,anchor_key=None):
    """Hodge 1-调和(图论): 基于前复权收益的相关图「有符号拉普拉斯」Fiedler 向量(30日窗口, 相关系数阈值 HODGE_T).
    收益取前复权(get_adj_returns), 与 W1/趋势维度口径统一, 避免拆分伪跳空污染相关系数.
    权重正负处理: 边权重保留相关符号(正=同向联动, 负=反向联动), 度矩阵按 |权重| 求和(D=diag(Σ|w_ij|)),
    即标准有符号拉普拉斯 L=D-A_有符号, 恒为半正定; 避免此前 D=diag(Σw_ij) 让负相关当负权重导致 L 非半正定
    (第二特征值恒为 0、Fiedler 向量退化为常数向量、各标的输出全相同).
    符号锚定: 特征向量整体符号不定(同一结构在相邻交易日可能整体翻号, 导致「X -> Y」方向与桑基图箭头抖动),
    以 ot_hodge_state.json 落盘的上一交易日向量做内积对齐消除抖动, 无状态时用固定约定兜底; anchor_key 为池标识
    ('industry'/'wide', 缺省 None 时不锚定也不落盘), 只固定符号表示, 不改变各标的取值的相对大小。"""
    n=len(names)
    rets={k:get_adj_returns(data,nm).values[max(0,idx-cw):idx] for k,nm in enumerate(names)}
    rets={k:v[~np.isnan(v)] for k,v in rets.items()}
    ml=min(len(v) for v in rets.values())
    mat=np.zeros((ml,n))
    for k in range(n): mat[:,k]=rets[k][:ml]
    corr=np.corrcoef(mat.T)
    corr=np.nan_to_num(corr,nan=0.0)
    adj=(np.abs(corr)>th).astype(float)
    np.fill_diagonal(adj,0)
    adj_w=corr*adj                       # 边权重保留相关符号: 正=同向, 负=反向
    D=np.diag(np.abs(adj_w).sum(axis=1)) # 有符号拉普拉斯: 度按 |权重| 计, 保证 L 半正定
    L=D-adj_w
    ev,evec=eigh(L)
    si=np.argsort(ev)
    ev,evec=ev[si],evec[:,si]
    if len(ev)>=2:
        v,_src=_anchor_sign(evec[:,1], anchor_key, names)
        _save_hodge_state(anchor_key, names, v)
        return v,ev[1]
    return np.zeros(n),0.0

# ---- AC1 ----
def ac1_flow(data,names,dates,w=60):
    """AC1 一阶自相关(市场状态): 等权组合前复权收益的 60 日滞后一阶自相关.
    收益取前复权(get_adj_returns), 与 W1/趋势维度口径统一, 避免拆分伪跳空污染自相关."""
    n=len(dates)
    port=np.zeros(n); valid=np.zeros(n)
    for name in names:
        r=get_adj_returns(data,name).values
        m=~np.isnan(r)
        port[m]+=r[m]; valid[m]+=1
    port=port/np.maximum(valid,1)
    ac1=np.full(n,np.nan)
    for i in range(w,n):
        wi=port[i-w:i]
        m=~np.isnan(wi)
        if m.sum()<20 or np.std(wi[m][:-1])<1e-10: continue
        r=wi[m]
        ac1[i]=np.corrcoef(r[:-1],r[1:])[0,1]
    return ac1

# ---- Sankey Drawing ----
def draw_curved_arrow(draw, x1, y1, x2, y2, color, width):
    n_segments = 20
    mid_x = (x1 + x2) // 2
    mid_y = (y1 + y2) // 2
    curve = abs(y2 - y1) * 0.3
    if y2 > y1: cy = mid_y - curve
    else: cy = mid_y + curve
    points = []
    for t in range(n_segments + 1):
        t_norm = t / n_segments
        bx = (1-t_norm)**2 * x1 + 2*(1-t_norm)*t_norm * mid_x + t_norm**2 * x2
        by = (1-t_norm)**2 * y1 + 2*(1-t_norm)*t_norm * cy + t_norm**2 * y2
        points.append((int(bx), int(by)))
    for i in range(len(points) - 1):
        for w in range(-width//2, width//2 + 1):
            draw.line([(points[i][0], points[i][1] + w), (points[i+1][0], points[i+1][1] + w)], fill=color, width=1)
    if len(points) >= 2:
        last = points[-1]; prev = points[-2]
        angle = np.arctan2(last[1] - prev[1], last[0] - prev[0])
        arrow_len = 12; arrow_angle = 0.5
        p1x = int(last[0] - arrow_len * np.cos(angle - arrow_angle))
        p1y = int(last[1] - arrow_len * np.sin(angle - arrow_angle))
        p2x = int(last[0] - arrow_len * np.cos(angle + arrow_angle))
        p2y = int(last[1] - arrow_len * np.sin(angle + arrow_angle))
        draw.polygon([(last[0], last[1]), (p1x, p1y), (p2x, p2y)], fill=color)

def get_flows(scores, names, top_n=5):
    flows = []
    sorted_idx = np.argsort(scores)
    for i in range(min(top_n, len(sorted_idx))):
        for j in range(max(0, len(sorted_idx) - top_n), len(sorted_idx)):
            if i >= j: continue
            src = sorted_idx[i]; dst = sorted_idx[j]
            amount = abs(scores[dst] - scores[src])
            if amount > 0:
                flows.append((src, dst, amount))
    flows.sort(key=lambda x: x[2], reverse=True)
    return flows[:top_n]

def draw_sankey(draw, x, y, w, h, names, ot_scores, spd_scores, hodge_scores,
                title, zh_f, en_f):
    n = len(names)
    left_x = x + 80
    right_x = x + w - 80
    node_w = 100
    node_h = 26
    start_y = y + 20
    end_y = y + h - 40
    
    n_etf = len(names)
    gap = (end_y - start_y - n_etf * node_h) // max(n_etf - 1, 1)
    
    left_nodes = {}
    right_nodes = {}
    for i, name in enumerate(names):
        ny = start_y + i * (node_h + gap)
        # Left
        draw.rectangle([left_x - node_w//2, ny, left_x + node_w//2, ny + node_h], fill='#f8f9fa', outline='#666666', width=1)
        DM(draw, left_x, ny + node_h//2, name, zh_f, en_f, fill='#333333', anchor='mm')
        left_nodes[i] = (left_x + node_w//2, ny + node_h//2)
        # Right
        draw.rectangle([right_x - node_w//2, ny, right_x + node_w//2, ny + node_h], fill='#f8f9fa', outline='#666666', width=1)
        DM(draw, right_x, ny + node_h//2, name, zh_f, en_f, fill='#333333', anchor='mm')
        right_nodes[i] = (right_x - node_w//2, ny + node_h//2)
    
    DM(draw, left_x, start_y - 12, '← 弱势', zh_f, en_f, fill='#666666', anchor='ma')
    DM(draw, right_x, start_y - 12, '强势 →', zh_f, en_f, fill='#666666', anchor='ma')
    
    max_ot = max(abs(ot_scores)) if max(abs(ot_scores)) > 0 else 1
    max_spd = max(abs(spd_scores)) if max(abs(spd_scores)) > 0 else 1
    max_hodge = max(abs(hodge_scores)) if max(abs(hodge_scores)) > 0 else 1
    
    ot_flows = get_flows(ot_scores, names, top_n=5)
    spd_flows = get_flows(spd_scores, names, top_n=5)
    hodge_flows = get_flows(hodge_scores, names, top_n=5)
    
    for src, dst, amount in ot_flows:
        x1, y1 = left_nodes[src]
        x2, y2 = right_nodes[dst]
        width = max(1, int(amount / max_ot * 6))
        draw_curved_arrow(draw, x1, y1 - 3, x2, y2 - 3, '#2ecc71', width)
    for src, dst, amount in spd_flows:
        x1, y1 = left_nodes[src]
        x2, y2 = right_nodes[dst]
        width = max(1, int(amount / max_spd * 6))
        draw_curved_arrow(draw, x1, y1, x2, y2, '#3498db', width)
    for src, dst, amount in hodge_flows:
        x1, y1 = left_nodes[src]
        x2, y2 = right_nodes[dst]
        width = max(1, int(amount / max_hodge * 6))
        draw_curved_arrow(draw, x1, y1 + 3, x2, y2 + 3, '#e74c3c', width)
    
    DM(draw, x + w // 2, y - 20, title, zh_f, en_f, fill='#1a1a1a', anchor='ma')
    
    # Legend
    ly = y + h - 25
    lx = x + w // 2 - 200
    draw.rectangle([lx, ly, lx + 20, ly + 3], fill='#2ecc71')
    DM(draw, lx + 26, ly - 3, 'OT(5日净流入→强势)', zh_f, en_f, fill='#333333')
    DM(draw, lx + 106, ly - 3, '→', zh_f, en_f, fill='#666666')
    DM(draw, lx + 206, ly - 3, 'SPD(60日漂移→强势)', zh_f, en_f, fill='#333333')
    DM(draw, lx + 286, ly - 3, '→', zh_f, en_f, fill='#666666')
    DM(draw, lx + 366, ly - 3, 'Hodge(30日领涨→强势)', zh_f, en_f, fill='#333333')

# ---- Feishu ----
def get_token():
    return requests.post(TOKEN_URL, json={'app_id':APP_ID,'app_secret':APP_SECRET}, timeout=15).json().get('tenant_access_token','')

def upload(path, token):
    with open(path,'rb') as f:
        return requests.post(UPLOAD_URL,headers={'Authorization':f'Bearer {token}'},data={'image_type':'message'},files={'image':f},timeout=30).json().get('data',{}).get('image_key','')

def push_text(content, title='最优传输OT每日多空观点日报 | 来自 DoMate'):
    card={'config':{'wide_screen_mode':True},'header':{'title':{'tag':'plain_text','content':title},'template':'blue'},'elements':[{'tag':'div','text':{'tag':'lark_md','content':content}}]}
    r=requests.post(WEBHOOK_URL,json={'msg_type':'interactive','card':card},timeout=15)
    return r.json()

def push_img_text(content, img_keys, title='最优传输OT每日多空观点日报 | 来自 DoMate'):
    elements=[{'tag':'div','text':{'tag':'lark_md','content':content}}]
    for ik in img_keys:
        elements.append({'tag':'img','img_key':ik,'alt':{'tag':'plain_text','content':'桑基图'}})
    card={'config':{'wide_screen_mode':True},'header':{'title':{'tag':'plain_text','content':title},'template':'blue'},'elements':elements}
    r=requests.post(WEBHOOK_URL,json={'msg_type':'interactive','card':card},timeout=15)
    return r.json()

# ---- Main ----
def main():
    report_date = datetime.now().strftime('%Y-%m-%d')
    print(f"=== OT Daily Report {report_date} ===")
    
    wn = [n for _,n,c in ETFS if c=='宽基']
    in_ = [n for _,n,c in ETFS if c=='行业']
    an = wn + in_
    
    print("Downloading...")
    data, dates, last_dates, failed, all_dates = dl_all(ETFS)
    if len(dates) == 0:
        print("[DATA-GATE] 无任何日线数据，拒绝生成/发送报告")
        return
    # 数据新鲜度闸门：最新交易日必须为最近交易日（周末/节假日容忍4天）
    idx_last_date = dates[-1].date()
    today_d = datetime.now().date()
    days_gap = (today_d - idx_last_date).days
    print(f"  日线最新日期: {idx_last_date} (距今 {days_gap} 天, {len(dates)} 交易日)")
    # 数据完整性核对: 以全体成功标的的最新交易日为基准, 标记落后标的与落后交易日数
    # 注意: 落后天数按各标的日期并集 all_dates 计算(交集对齐会把所有序列截断到最短序列,
    #       直接用对齐后的索引会低估落后天数, 出现"落后0个交易日"的假完整提示)
    baseline = max(last_dates.values()) if last_dates else idx_last_date
    lag_items = []
    for nm, d in last_dates.items():
        if d < baseline:
            lag_td = sum(1 for x in all_dates if x > d) if len(all_dates) else (baseline - d).days
            lag_items.append(f"{nm}(最新{d.date()}, 落后{lag_td}个交易日)")
    integ_parts = []
    if failed:
        integ_parts.append("未取到行情: " + ",".join(failed))
    if lag_items:
        integ_parts.append("数据不全: " + "; ".join(lag_items))
    if integ_parts:
        data_integrity = ("⚠️ 数据不全 | 基准交易日 " + str(baseline) + " | " + " | ".join(integ_parts))
    else:
        data_integrity = "数据完整(基准交易日 " + str(baseline) + ")"
    print(f"  数据完整性: {data_integrity}")
    if days_gap > 4:
        print(f"[DATA-GATE] 数据最新日期 {idx_last_date} 距今 {days_gap} 天，超过4天容忍阈值，拒绝发送")
        return
    n, idx_last = len(dates), len(dates) - 1
    ff_data, ff_src = dl_flows_all(ETFS, dates)  # 资金流: AKShare→TuShare→新浪→腾讯→同花顺 降级
    print(f"  fund-flow covered: {len(ff_data)}/{len(ETFS)} 数据源分布: {ff_src}")
    
    print("Computing signals...")
    w1_signals = {}
    for code, name, cat in ETFS:
        w1, dir_ = compute_individual_w1(data, name)
        w1_val = w1[idx_last]; dir_val = dir_[idx_last]
        w1_valid = w1[~np.isnan(w1)]
        if len(w1_valid) > 20:
            q30, q70, q90 = np.percentile(w1_valid, [30, 70, 90])
        else: q30 = q70 = q90 = 0
        closes = data[name]['close'].values
        adj = adjust_splits(closes).values          # 前复权: 消除ETF份额拆分伪跳空(趋势/收益口径)
        ma40 = pd.Series(adj).rolling(MA_PERIOD).mean().values
        ma160 = pd.Series(adj).rolling(160).mean().values
        above_ma40 = adj[idx_last] > ma40[idx_last] if not np.isnan(ma40[idx_last]) else False
        above_ma160 = adj[idx_last] > ma160[idx_last] if not np.isnan(ma160[idx_last]) else False
        ret_5d = (adj[idx_last] / adj[idx_last-5] - 1) * 100 if idx_last >= 5 else 0
        ret_20d = (adj[idx_last] / adj[idx_last-20] - 1) * 100 if idx_last >= 20 else 0
        # 80日动量(趋势维度): 仅在动量转负时启用趋势优先 —— 方向判定优先于"观望(高漂移)", 高漂移降级为备注
        mom80 = (adj[idx_last] / adj[idx_last-80] - 1) * 100 if idx_last >= 80 else 0.0
        bear_trend = mom80 < 0
        if w1_val is not None and not np.isnan(w1_val):
            if bear_trend:
                if dir_val > 0: signal = '看多'
                elif dir_val < 0: signal = '看空'
                else: signal = '观望'
                if w1_val > q90: signal += '(高漂移)'
            else:
                if w1_val > q90: signal = '观望(高漂移)'
                elif dir_val > 0: signal = '看多'
                elif dir_val < 0: signal = '看空'
                else: signal = '观望'
        else: signal = 'N/A'; w1_val = 0
        w1_signals[name] = {'w1': w1_val, 'dir': dir_val, 'signal': signal,
                            'ma40': above_ma40, 'ma160': above_ma160,
                            'ret5': ret_5d, 'ret20': ret_20d, 'mom80': mom80}
    
    # Flows
    mv_map, mkt = dl_quote_tencent(ETFS)      # 腾讯快照: ETF规模(自由流通市值)+全市场口径
    # 指数级总盘子: 跟踪该指数的全部场内ETF(同花顺动态归集, 失败回退至固化清单)
    print("  [组级] 构建指数级ETF组合并拉取组内行情/资金流...")
    groups = build_index_groups()
    gq, mv_c = dl_group_quote(groups)         # 组总规模(亿)+组内成交额(亿)
    print("  [组级] 组总规模(亿):", {k: round(v['mv_yi'], 0) for k, v in gq.items()})
    gflows = dl_group_flows(groups, gq, mv_c, lb=5)  # 组内近5日主力净流入合计(元)
    print("  [组级] 组净流入(亿):", {k: round(v['net_yuan'] / 1e8, 2) for k, v in gflows.items()})
    w_ot = wide_flow_ratio(gflows, gq, wn)
    ind_raw, ind_ranked = industry_four_factor(ff_data, data, in_, mv_map, mkt, idx_last, gflows, gq, lb=5)
    f_ot = np.array([ind_raw[n]['score'] for n in in_])
    w_spd = spd_flow(data, wn, idx_last)
    f_spd = spd_flow(data, in_, idx_last)
    w_hodge, _ = hodge_flow(data, wn, idx_last, anchor_key='wide')
    f_hodge, _ = hodge_flow(data, in_, idx_last, anchor_key='industry')
    
    # AC1
    ac1_wide = ac1_flow(data, wn, dates, 60)
    ac1_full = ac1_flow(data, an, dates, 60)
    ac1_w = ac1_wide[idx_last] if not np.isnan(ac1_wide[idx_last]) else 0
    ac1_f = ac1_full[idx_last] if not np.isnan(ac1_full[idx_last]) else 0
    def al(v):
        if v > 0.1: return '熊市/趋势下行', '减仓'
        elif v < -0.1: return '牛市/均值回归', '满仓'
        return '中性', '正常仓位'
    awl, awp = al(ac1_w)
    afl, afp = al(ac1_f)
    
    # Build OT text report
    lines = []
    lines.append(f'**最优传输 OT 每日报告 | {dates[idx_last].date()} | 来自 DoMate**')
    lines.append('')
    lines.append(f'【数据完整性】 {data_integrity}')
    lines.append('')
    lines.append('【OT 单ETF信号 (W1+方向+80日动量+MA趋势)】')
    lines.append('说明: W1距离、80日动量/MA40/160、以及 SPD/Hodge/AC1 收益类指标均基于前复权价(已消除ETF份额拆分伪跳空); 仅80日动量转负时启用趋势优先(方向优先于高漂移观望)')
    lines.append('')
    lines.append('宽基:')
    for name in wn:
        s = w1_signals[name]
        up40 = '↑' if s['ma40'] else '↓'
        up160 = '↑' if s['ma160'] else '↓'
        d = '↑' if s['dir']>0 else ('↓' if s['dir']<0 else '-')
        _mk = "**<font color='red'>☾</font>** " if s['signal'].startswith('看多') else ("<text_tag color='red'>★</text_tag> " if s['signal'].startswith('看空') else "")
        lines.append(f"{_mk}{name} W1={s['w1']:.4f} Dir={d} 5d={s['ret5']:+.2f}% 20d={s['ret20']:+.2f}% 80d={s['mom80']:+.2f}% MA40{up40} MA160{up160} -> {s['signal']}")
    lines.append('')
    lines.append('行业:')
    for name in in_:
        s = w1_signals[name]
        up40 = '↑' if s['ma40'] else '↓'
        up160 = '↑' if s['ma160'] else '↓'
        d = '↑' if s['dir']>0 else ('↓' if s['dir']<0 else '-')
        _mk = "**<font color='red'>☾</font>** " if s['signal'].startswith('看多') else ("<text_tag color='red'>★</text_tag> " if s['signal'].startswith('看空') else "")
        lines.append(f"{_mk}{name} W1={s['w1']:.4f} Dir={d} 5d={s['ret5']:+.2f}% 20d={s['ret20']:+.2f}% 80d={s['mom80']:+.2f}% MA40{up40} MA160{up160} -> {s['signal']}")
    lines.append('')
    lines.append('【OT 宽基资金流入比率排名 (近5日指数级净流入÷指数级ETF总盘)】')
    for i, k in enumerate(np.argsort(w_ot)[::-1]):
        top1 = ' ▲TOP1' if i==0 else ''
        g = gflows.get(wn[k], {})
        pool = g.get('cov_mv', 0.0)
        lines.append(f"{i+1}. {wn[k]} {w_ot[k]*100:+.4f}% (总盘{pool:.0f}亿){top1}")
    lines.append('')
    lines.append('【OT 行业四因子加权评分排名】')
    lines.append('权重: 资金强度31.25% 换手偏离18.75% ETF份额31.25% 机构关注18.75%')
    lines.append('')
    for i, nm in enumerate(ind_ranked):
        r = ind_raw[nm]
        if r['inactive']:
            lines.append(f"[数据不足] {nm}  {r['note']}")
            continue
        lines.append(f"{i+1}. {nm}: 强度{r['f1']:.0f} 换手{r['f2']:.0f} "
                     f"ETF份额{r['f3']:.0f} 机构{r['f4']:.0f} = 综合{r['score']:.1f} "
                     f"[指数净流入{r.get('index_net',0)/1e8:+.2f}亿/总盘{r.get('index_mv',0):.0f}亿]"
                     f"{(' [' + r['note'] + ']') if r['note'] else ''}")
    lines.append('')
    lines.append(f'【方法说明】OT宽基:近5日指数级主力净流入合计(元)/指数级ETF总规模(跟踪该指数的全部场内ETF规模合计, 覆盖口径; 数据源:{"|".join(sorted(ff_src.keys())) or "无"}+组内腾讯/新浪); '
                 f'OT行业:四因子加权评分(资金强度31.25%=指数级净流入/指数级总盘+换手偏离18.75%+ETF份额31.25%+机构关注18.75%, 0-100百分位), '
                 f'ETF份额变化率<-5%扣10分; 窗口优先近5交易日, 数据不足退近1日; '
                 f'总盘=同花顺归集跟踪该指数的全部场内ETF, 沪深300≈{gq.get("沪深300",{}).get("mv_yi",0):.0f}亿 | '
f'SPD:SPD(2)流形相关性漂移(60日当前窗口 vs 60日参考窗口, ρ_i=行业与等权市场组合相关系数, Fisher-z测地漂移=|atanh(ρ_now)-atanh(ρ_ref)|, 加权动量方向s_i); 正值=强势漂移(结构性流入机会), 负值=弱势漂移(流出风险); 收益基于前复权价 | '
                   f'Hodge:Fiedler领涨(30日窗口, 相关系数阈值{HODGE_T}, 有符号拉普拉斯: 边权重保留相关符号、度按|corr|计, 收益基于前复权价; 符号锚定: 特征向量整体符号与上一交易日向量内积对齐(状态落盘 ot_hodge_state.json), 消除eigh整体翻号导致的跨日方向抖动, 仅固定符号表示、不改相对大小) | '
                   f'AC1:等权组合前复权收益60日滞后一阶自相关')
    lines.append('')
    lines.append('【桑基图流转方向汇总】')
    lines.append(f"宽基 OT: {wn[np.argmin(w_ot)]}(比率最低) -> {wn[np.argmax(w_ot)]}(比率最高)")
    # SPD流转文案: 负分=流出侧, 正分=流入侧, 全同向=同向漂移
    def _spd_txt(names, scores):
        pos=[(n,s) for n,s in zip(names,scores) if s>0]; neg=[(n,s) for n,s in zip(names,scores) if s<0]
        if pos and neg:
            return f"{neg[-1][0]}(流出侧 {neg[-1][1]:+.5f}) -> {pos[-1][0]}(流入侧 {pos[-1][1]:+.5f})"
        elif pos:
            return f"同向漂移(均强势), 最强: {pos[-1][0]}({pos[-1][1]:+.5f})"
        elif neg:
            return f"同向漂移(均弱势), 最弱: {neg[0][0]}({neg[0][1]:+.5f})"
        return "无数据"
    lines.append(f"宽基 SPD: {_spd_txt(wn, w_spd)}")
    lines.append(f"宽基 Hodge: {wn[np.argmin(w_hodge)]} -> {wn[np.argmax(w_hodge)]}")
    nm_max = max(in_, key=lambda x: ind_raw[x]['score'], default=in_[0])
    nm_min = min(in_, key=lambda x: ind_raw[x]['score'], default=in_[0])
    lines.append(f"行业 OT: {nm_min}(评分最低) -> {nm_max}(评分最高)")
    lines.append(f"行业 SPD: {_spd_txt(in_, f_spd)}")
    lines.append(f"行业 Hodge: {in_[np.argmin(f_hodge)]} -> {in_[np.argmax(f_hodge)]}")
    
    report_text = '\n'.join(lines) + _bs('ot', 'text')
    print(report_text, flush=True)
    
    # Generate images
    print("Generating wide-base sankey...")
    W, H = 1400, 900
    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)
    z20 = F(20, 'zh'); z16 = F(16, 'zh'); z14 = F(14, 'zh'); e16 = F(16, 'en')
    DM(draw, W//2, 15, f'宽基ETF资金流转桑基图 | {dates[idx_last].date()}', z20, e16, fill='#1a1a1a', anchor='ma')
    DM(draw, W//2, 40, '三种算法对比：绿色=OT | 蓝色=SPD | 红色=Hodge', z14, e16, fill='#666666', anchor='ma')
    draw_sankey(draw, 50, 60, W-100, H-110, wn, w_ot, w_spd, w_hodge, '', z16, e16)
    wide_path = os.path.join(os.path.dirname(__file__), 'ot_sankey_wide.png')
    img.save(wide_path, 'PNG')
    print(f"  Saved: {wide_path}")
    
    print("Generating industry sankey...")
    img2 = Image.new('RGB', (W, H), 'white')
    draw2 = ImageDraw.Draw(img2)
    DM(draw2, W//2, 15, f'行业ETF资金流转桑基图 | {dates[idx_last].date()}', z20, e16, fill='#1a1a1a', anchor='ma')
    DM(draw2, W//2, 40, '三种算法对比：绿色=OT | 蓝色=SPD | 红色=Hodge', z14, e16, fill='#666666', anchor='ma')
    draw_sankey(draw2, 50, 60, W-100, H-110, in_, f_ot, f_spd, f_hodge, '', z16, e16)
    ind_path = os.path.join(os.path.dirname(__file__), 'ot_sankey_ind.png')
    img2.save(ind_path, 'PNG')
    print(f"  Saved: {ind_path}")
    
    # Upload and push
    print("Pushing to Feishu...")
    tok = get_token()
    if tok:
        ik_wide = upload(wide_path, tok)
        ik_ind = upload(ind_path, tok)
        print(f"  Image keys: wide={ik_wide}, ind={ik_ind}")
        
        _nb = sum(1 for n in an if w1_signals[n]['signal'].startswith('看空'))
        _nu = sum(1 for n in an if (w1_signals[n]['signal'].startswith('看多') or w1_signals[n]['signal'].startswith('观望(高漂移)')))
        _mark = ('注意风险 | ' if _nb > 0 else '') + ('注意机会 | ' if _nu > 0 else '')
        _title = f'最优传输OT每日多空观点日报 | {dates[idx_last].date()} | {_mark}来自 DoMate'
        content = f'**{_title}**\n\n{report_text}'
        img_keys = []
        if ik_wide: img_keys.append(ik_wide)
        if ik_ind: img_keys.append(ik_ind)
        
        if img_keys:
            resp = push_img_text(content, img_keys, _title)
        else:
            resp = push_text(content, _title)
        print(f"  Feishu: {resp}")
    else:
        print("Token failed")

    # 写信号 JSON（供每日汇总 send_daily_summary.py 使用，含数据日期供新鲜度校验）
    try:
        from collections import OrderedDict
        sig_json = OrderedDict()
        sig_json['date'] = str(pd.Timestamp(dates[idx_last]).date())
        sig_json['data_integrity'] = data_integrity
        sig_json['data_baseline'] = str(baseline)
        sig_json['signals'] = []
        for name in an:
            s = w1_signals[name]
            sig_json['signals'].append({
                'name': name,
                'direction': 1 if s['dir'] > 0 else (-1 if s['dir'] < 0 else 0),
                'w1': round(float(s['w1']), 6),
                'mom80': round(float(s['mom80']), 2),
                'signal': s['signal'],
                'bull_status': '触发' if (s['signal'].startswith('看多') or s['signal'].startswith('观望(高漂移)')) else '',
                'bear_status': '触发' if s['signal'].startswith('看空') else '',
            })
        sig_json['summary'] = {
            'bull_triggered': sum(1 for s in sig_json['signals'] if s['bull_status']),
            'bear_triggered': sum(1 for s in sig_json['signals'] if s['bear_status']),
            'rotation_pairs': 0,
        }
        sig_json['rotations'] = []
        sig_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ot_signal.json')
        with open(sig_path, 'w', encoding='utf-8') as f:
            json.dump(sig_json, f, ensure_ascii=False, indent=2)
        print(f"  Signal saved: {sig_path} (date={sig_json['date']})")
    except Exception as e:
        print(f"  WARN: 写入 ot_signal.json 失败: {e}")

    print("Done.")

if __name__ == '__main__':
    main()
