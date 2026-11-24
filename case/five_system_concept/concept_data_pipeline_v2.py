#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扩展版概念级指数数据管道 (50概念版)
在原20概念基础上新增30个概念, 拉取成分股日K线 -> 等权合成概念级收盘指数 -> 计算日收益率矩阵
输出: concept_returns.pkl (日期 x ~50概念 收益率)
      concept_close.pkl   (日期 x ~50概念 收盘指数)
"""
import requests
import pandas as pd
import numpy as np
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
})

OUT_DIR = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f96134a09ffe1paMAFrct6rV4w'

# ---- datahub 统一取数弱引入 (失败自动回退腾讯/新浪原通路) ----
# ---- DataHub 股票模块（三级体系）：个股日线与概念成分统一入口 ----
HUB_ROOT = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/projects/datahub-three-tier'
HUB_DEPS = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/py_deps'
for _p in (HUB_DEPS, HUB_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from data_hub.collectors.stocks import get_stock_daily
    _dh = None          # 旧版 workspace/datahub 已停用
except Exception:
    get_stock_daily = None

# ===================== 手工定义概念 (原6个 + 新增18个 = 24个) =====================
MANUAL_CONCEPTS = {
    # --- 原6个 ---
    '液冷概念': {
        '002837': '英维克', '603019': '中科曙光', '300499': '高澜股份',
        '300017': '网宿科技', '301018': '申菱环境', '000887': '中鼎股份',
        '688668': '鼎通科技', '301128': '强瑞技术', '603516': '淳中科技',
        '002975': '博杰股份', '603269': '海鸥股份', '000811': '冰轮环境',
        '000530': '冰山冷热', '002639': '雪人股份', '603912': '佳力图',
        '300249': '依米康', '600510': '黑牡丹', '000977': '浪潮信息',
        '601138': '工业富联', '000063': '中兴通讯', '002909': '集泰股份',
        '301176': '鸿富瀚', '300602': '飞荣达', '688146': '精研科技',
        '300831': '科创新源', '002418': '康盛股份', '002328': '新朋股份',
        '002600': '领益智造', '603270': '金帝股份', '002536': '飞龙股份',
        '002050': '三花智控', '002011': '盾安环境', '301521': '思泉新材',
        '600160': '巨化股份', '688181': '八亿时空', '300990': '同飞股份',
    },
    'AI算力': {
        '000977': '浪潮信息', '601138': '工业富联', '603019': '中科曙光',
        '000938': '紫光股份', '000063': '中兴通讯', '688256': '寒武纪',
        '300017': '网宿科技', '300454': '深信服', '603881': '数据港',
        '688345': '优刻得', '688041': '海光信息', '600845': '宝信软件',
    },
    '光模块CPO': {
        '300308': '中际旭创', '300502': '新易盛', '300394': '天孚通信',
        '002281': '光迅科技', '600487': '亨通光电', '600522': '中天科技',
        '002792': '通宇通讯', '300570': '太辰光', '002463': '沪电股份',
        '300353': '东土科技',
    },
    '服务器IDC': {
        '000977': '浪潮信息', '601138': '工业富联', '603019': '中科曙光',
        '000938': '紫光股份', '603881': '数据港', '600845': '宝信软件',
        '300017': '网宿科技', '300383': '光环新网', '688345': '优刻得',
        '000063': '中兴通讯',
    },
    '人工智能': {
        '002230': '科大讯飞', '300418': '昆仑万维', '300364': '中文在线',
        '002415': '海康威视', '002236': '大华股份', '688111': '海天瑞声',
        '300369': '绿盟科技', '300295': '三六五网', '300133': '华策影视',
        '002413': '雷柏科技',
    },
    '半导体芯片': {
        '688981': '中芯国际', '688256': '寒武纪', '603501': '韦尔股份',
        '300474': '景嘉微', '002049': '紫光国微', '603986': '兆易创新',
        '002371': '北方华创', '688012': '中微公司', '002916': '深南电路',
        '688041': '海光信息', '300666': '江丰电子', '002407': '多氟多',
    },
    # --- 新增18个手工定义概念 ---
    '数据中心': {
        '300017': '网宿科技', '300383': '光环新网', '603881': '数据港',
        '600845': '宝信软件', '688345': '优刻得', '000938': '紫光股份',
        '300454': '深信服', '002410': '广联达', '600856': '中天能源',
        '600803': '新奥股份', '600522': '中天科技', '002369': '卓翼科技',
    },
    'PCB电路板': {
        '002916': '深南电路', '002463': '沪电股份', '300479': '神思电子',
        '002475': '立讯精密', '300666': '江丰电子', '688183': '生益科技',
        '002281': '光迅科技', '300502': '新易盛', '300308': '中际旭创',
        '300394': '天孚通信', '300570': '太辰光', '002792': '通宇通讯',
    },
    '消费电子': {
        '002475': '立讯精密', '002241': '歌尔股份', '002236': '大华股份',
        '002415': '海康威视', '600183': '生益科技', '002463': '沪电股份',
        '002916': '深南电路', '600522': '中天科技', '002475': '立讯精密',
        '300433': '蓝思科技', '002635': '安洁科技', '300115': '长盈精密',
    },
    '储能': {
        '300274': '阳光电源', '002335': '科华数据', '300438': '鹏辉能源',
        '002709': '天赐材料', '002460': '赣锋锂业', '002340': '格林美',
        '300750': '宁德时代', '002466': '天齐锂业', '300014': '亿纬锂能',
        '300207': '欣旺达', '300957': '贝泰妮', '300037': '新宙邦',
    },
    '稀土永磁': {
        '600392': '盛和资源', '600259': '广晟有色', '000795': '英洛华',
        '600549': '厦门钨业', '002056': '横店东磁', '600366': '宁波韵升',
        '002345': '潮宏基', '000970': '中科三环', '600980': '北矿科技',
        '600585': '方大特钢',
    },
    '军工电子': {
        '002049': '紫光国微', '600990': '四创电子', '002439': '启明星辰',
        '300034': '钢研高纳', '600879': '航天电子', '002446': '盛路通信',
        '300624': '中电讯飞', '000547': '航天发展', '300527': '中船应急',
        '300567': '精测电子',
    },
    '白酒': {
        '000858': '五粮液', '600519': '贵州茅台', '000568': '泸州老窖',
        '002304': '洋河股份', '600809': '山西汾酒', '000596': '古井贡酒',
        '600702': '舍得酒业', '002568': '百润股份', '603369': '今世缘',
        '603589': '口子窖',
    },
    '医药CRO': {
        '603259': '药明康德', '300122': '智飞生物', '300347': '泰格医药',
        '300759': '康龙化成', '002821': '凯莱英', '603456': '九州药业',
        '300147': '香雪制药', '002422': '科伦药业', '300261': '雅本化学',
        '603392': '万泰生物',
    },
    '医美': {
        '300896': '爱美客', '688363': '华熙生物', '002970': '锐科激光',
        '300595': '欧普康视', '688233': '春立医疗', '300633': '开立医疗',
        '300629': '新产业', '002432': '九安医疗', '300244': '迪安诊断',
        '300326': '凯利泰',
    },
    '预制菜': {
        '603517': '绝味食品', '002548': '金健米业', '603345': '安井食品',
        '002726': '金龙鱼', '603043': '广州酒家', '002971': '和远气体',
        '600872': '中炬高新', '600195': '中牧股份', '002568': '百润股份',
        '600298': '安琪酵母',
    },
    '跨境电商': {
        '300591': '万里马', '300464': '星徽精密', '002098': '浔兴股份',
        '300397': '天和防务', '002464': '众应互联', '300514': '友讯达',
        '002084': '海鸥住工', '300579': '德艺文创', '300848': '美瑞新材',
        '300502': '新易盛',
    },
    '元宇宙': {
        '300133': '华策影视', '300364': '中文在线', '002413': '雷柏科技',
        '300418': '昆仑万维', '300088': '长信科技', '300468': '四方精创',
        '002555': '三七互娱', '002425': '凯撒文化', '300133': '华策影视',
        '600633': '浙数文化', '300059': '东方财富', '300459': '汤臣倍健',
    },
    '直播电商': {
        '300133': '华策影视', '600633': '浙数文化', '002555': '三七互娱',
        '300418': '昆仑万维', '002425': '凯撒文化', '300088': '长信科技',
        '300502': '新易盛', '300570': '太辰光', '300394': '天孚通信',
        '002792': '通宇通讯',
    },
    '固态电池': {
        '300750': '宁德时代', '002460': '天齐锂业', '002466': '赣锋锂业',
        '300014': '亿纬锂能', '300207': '欣旺达', '002709': '天赐材料',
        '300438': '鹏辉能源', '300037': '新宙邦', '002335': '科华数据',
        '300274': '阳光电源',
    },
    '机器人': {
        '002747': '埃斯顿', '002097': '山河智能', '300024': '机器人',
        '300435': '华自科技', '300024': '机器人', '002527': '新时达',
        '300756': '金马游乐', '300552': '苏州科达', '300840': '酷特智能',
        '300022': '吉峰科技', '300894': '火星人', '002293': '罗莱生活',
    },
    '低空经济': {
        '300006': '精测电子', '300474': '景嘉微', '002446': '盛路通信',
        '002465': '海格通信', '600879': '航天电子', '300101': '振芯科技',
        '300024': '机器人', '002097': '山河智能', '300023': '宝塔实业',
        '300527': '中船应急',
    },
    '数据要素': {
        '300017': '网宿科技', '300454': '深信服', '600845': '宝信软件',
        '300017': '网宿科技', '688345': '优刻得', '603881': '数据港',
        '300383': '光环新网', '000938': '紫光股份', '300226': '上海钢联',
        '600588': '用友网络', '002410': '广联达', '300229': '拓尔思',
    },
    '纺织服装': {
        '002503': '搜于特', '002562': '步森股份', '002083': '孚日股份',
        '600851': '海欣股份', '600400': '红豆股份', '002193': '山东如意',
        '002486': '嘉麟杰', '600398': '海澜之家', '002327': '孚日股份',
        '600177': '雅戈尔',
    },
    '白酒2': {
        '000858': '五粮液', '600519': '贵州茅台', '000568': '泸州老窖',
        '002304': '洋河股份', '600809': '山西汾酒', '000596': '古井贡酒',
        '600702': '舍得酒业', '603369': '今世缘', '603589': '口子窖',
        '600197': '伊力特',
    },
    '算力设备': {
        '000977': '浪潮信息', '601138': '工业富联', '603019': '中科曙光',
        '688256': '寒武纪', '688041': '海光信息', '000938': '紫光股份',
        '300017': '网宿科技', '603881': '数据港', '688345': '优刻得',
        '300454': '深信服',
    },
    '铜缆高速': {
        '002231': '恒宝股份', '002792': '通宇通讯', '300502': '新易盛',
        '300308': '中际旭创', '300394': '天孚通信', '002281': '光迅科技',
        '300570': '太辰光', '002463': '沪电股份', '600487': '亨通光电',
        '600522': '中天科技',
    },
    '黄酒': {
        '600059': '古越龙山', '601579': '会稽山', '600616': '金枫酒业',
    },
}

# ===================== 新浪概念 (原14个 + 新增12个 = 26个) =====================
SINA_CONCEPTS = {
    # --- 原14个 ---
    'gn_hwgn': '华为概念', 'gn_5Ggn': '5G概念', 'gn_gf': '光伏',
    'gn_xny': '新能源', 'gn_gfjg': '国防军工', 'gn_gcrj': '国产软件',
    'gn_cxy': '创新药', 'gn_cdz': '充电桩', 'gn_znjq': '智能机器',
    'gn_wxdh': '卫星导航', 'gn_zjtx': '专精特新', 'gn_yjys': '业绩预升',
    'gn_dtjj': '低碳经济', 'gn_qcdz': '汽车电子',
    # --- 新增12个 ---
    'gn_yyjq': '有色金属', 'gn_spl': '食品饮料', 'gn_ylq': '医药制造',
    'gn_jr': '金融', 'gn_dichan': '房地产', 'gn_jianzhu': '建筑建材',
    'gn_jiaotong': '交通运输', 'gn_hb': '环保', 'gn_lvyou': '旅游酒店',
    'gn_jiaju': '家居家具', 'gn_dianqi': '电气设备',
}

MAX_STOCKS_PER_CONCEPT = 12

# ===================== 腾讯API =====================
def get_prefix(code):
    if code.startswith('6') or code.startswith('5') or code.startswith('11'):
        return 'sh'
    elif code.startswith('8') or code.startswith('4'):
        return 'bj'
    return 'sz'

def fetch_kline(code, count=60):
    """个股前复权日线：DataHub 股票模块 get_stock_daily。返回 DataFrame[close, volume]，索引为日期。"""
    if get_stock_daily is None:
        return None
    ts_code = f"{code}.{'SH' if get_prefix(code) == 'sh' else ('BJ' if get_prefix(code) == 'bj' else 'SZ')}"
    start_d = (pd.Timestamp.now() - pd.Timedelta(days=int(count * 2.2) + 30)).strftime('%Y%m%d')
    end_d = pd.Timestamp.now().strftime('%Y%m%d')
    try:
        df = get_stock_daily(ts_code, start_date=start_d, end_date=end_d)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    dcol = 'trade_date' if 'trade_date' in df.columns else 'date'
    out = pd.DataFrame({
        'date': pd.to_datetime(df[dcol].astype(str).str.replace('-', ''), format='%Y%m%d', errors='coerce'),
        'close': pd.to_numeric(df['close'], errors='coerce'),
        'volume': pd.to_numeric(df['vol'] if 'vol' in df.columns else df.get('volume'), errors='coerce'),
    }).dropna(subset=['close']).sort_values('date')
    if len(out) == 0:
        return None
    out = out.set_index('date')
    return out[['close', 'volume']].tail(count)


# ===================== 新浪概念API =====================
def get_sina_concept_stocks(node, max_n=12):
    """概念成分：DataHub 股票模块 fetch_concept_members（按概念名匹配同花顺概念板块）。

    入参沿用原新浪概念节点键（gn_xxx），映射到 SINA_CONCEPTS 中文概念名再查成分。
    返回 {6位代码: 名称}，取不到返回空 dict（由调用方按缺口处理）。
    """
    name = SINA_CONCEPTS.get(node, node)
    try:
        from data_hub.collectors.stocks.concept_members import fetch_concept_members
        df = fetch_concept_members(name=name)
    except Exception as e:
        print(f"[概念] {name} DataHub 成分取数失败: {type(e).__name__}")
        return {}
    if df is None or len(df) == 0:
        return {}
    return dict(zip(df['con_code'].astype(str).head(max_n),
                    df['con_name'].astype(str).head(max_n)))


def main():
    print("=" * 100, file=sys.stderr)
    print("扩展版概念级指数数据管道: ~50概念 -> 成分股K线 -> 等权合成 -> 收益率矩阵", file=sys.stderr)
    print("=" * 100, file=sys.stderr)

    # Step 1: 构建概念成分股
    print("[1/4] 构建概念成分股...", file=sys.stderr)
    all_concepts = {}
    all_stock_names = {}

    for cname, stocks in MANUAL_CONCEPTS.items():
        all_concepts[cname] = dict(stocks)
        all_stock_names.update(stocks)
        print(f"  {cname}: {len(stocks)}只", file=sys.stderr)

    for node, cname in SINA_CONCEPTS.items():
        stocks = get_sina_concept_stocks(node, MAX_STOCKS_PER_CONCEPT)
        if stocks:
            all_concepts[cname] = stocks
            all_stock_names.update(stocks)
            print(f"  {cname}: {len(stocks)}只", file=sys.stderr)
        else:
            print(f"  {cname}: 获取失败!", file=sys.stderr)
        time.sleep(0.12)

    all_codes = list(all_stock_names.keys())
    print(f"\n  共 {len(all_concepts)} 个概念, {len(all_codes)} 只去重个股", file=sys.stderr)

    # Step 2: 并发拉取全部个股K线
    print(f"[2/4] 并发拉取K线({len(all_codes)}只, 60日, 4并发)...", file=sys.stderr)
    stock_data = {}  # {code: pd.DataFrame(close, volume)}
    fail = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_kline, code): code for code in all_codes}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  K线进度: {done}/{len(all_codes)} (成功{len(stock_data)} 失败{fail})", file=sys.stderr)
            code = futures[f]
            df = f.result()
            if df is not None and len(df) > 0:
                stock_data[code] = df
            else:
                fail += 1

    print(f"  成功: {len(stock_data)}/{len(all_codes)}, 失败: {fail}", file=sys.stderr)

    # Step 3: 等权合成概念级收盘指数 + 资金流
    print("[3/4] 等权合成概念级收盘指数...", file=sys.stderr)
    concept_close = {}
    concept_volume = {}

    for cname, stocks in all_concepts.items():
        codes = list(stocks.keys())
        s_close = {}
        s_vol = {}
        for code in codes:
            if code in stock_data:
                s_close[code] = stock_data[code]['close']
                s_vol[code] = stock_data[code]['volume']

        if not s_close:
            print(f"  [SKIP] {cname}: 无有效数据", file=sys.stderr)
            continue

        df_stocks = pd.DataFrame(s_close).sort_index()
        df_norm = df_stocks.div(df_stocks.ffill().iloc[0])
        concept_idx = df_norm.mean(axis=1)
        concept_close[cname] = concept_idx

        # 资金流: 成交量加权
        df_vol = pd.DataFrame(s_vol).reindex(df_stocks.index).fillna(0)
        concept_volume[cname] = df_vol.mean(axis=1)

        valid = len(s_close)
        print(f"  {cname}: {valid}/{len(codes)}只有效, {len(concept_idx)}个交易日", file=sys.stderr)

    df_close = pd.DataFrame(concept_close).sort_index()
    df_volume = pd.DataFrame(concept_volume).reindex(df_close.index).fillna(0)
    df_close = df_close.tail(250)
    df_volume = df_volume.tail(250)

    # Step 4: 计算日收益率矩阵
    print("[4/4] 计算日收益率矩阵 + 资金流...", file=sys.stderr)
    df_returns = df_close.pct_change().dropna(how='all')

    last_date = df_close.index[-1]
    print(f"  数据截止: {last_date.strftime('%Y-%m-%d')}", file=sys.stderr)
    print(f"  概念数: {len(df_close.columns)}", file=sys.stderr)
    print(f"  交易日数: {len(df_close)}", file=sys.stderr)

    nan_cols = df_returns.columns[df_returns.isna().all()].tolist()
    if nan_cols:
        print(f"  [WARN] 全NaN列: {nan_cols}", file=sys.stderr)
    else:
        print(f"  无全NaN列", file=sys.stderr)

    # 落盘
    returns_path = f'{OUT_DIR}/concept_returns.pkl'
    close_path = f'{OUT_DIR}/concept_close.pkl'
    volume_path = f'{OUT_DIR}/concept_volume.pkl'
    df_returns.to_pickle(returns_path)
    df_close.to_pickle(close_path)
    df_volume.to_pickle(volume_path)

    # 输出摘要
    print(f"\n{'='*100}")
    print(f"扩展版概念级数据管道完成")
    print(f"  收益率: {returns_path} ({df_returns.shape[0]}日 x {df_returns.shape[1]}概念)")
    print(f"  收盘指数: {close_path} ({df_close.shape[0]}日 x {df_close.shape[1]}概念)")
    print(f"  成交量: {volume_path} ({df_volume.shape[0]}日 x {df_volume.shape[1]}概念)")
    print(f"  数据截止: {last_date.strftime('%Y-%m-%d')}")
    print(f"\n  概念列表({len(df_close.columns)}个): {', '.join(df_close.columns.tolist())}")

    # 20日动量排名
    print(f"\n{'='*100}")
    print("20日动量排名 (概念级, Top15):")
    if len(df_close) >= 21:
        mom20 = (df_close.iloc[-1] / df_close.iloc[-21] - 1) * 100
        mom20 = mom20.sort_values(ascending=False)
        print(f"{'排名':<5} {'概念':<14} {'20日涨幅%':<10}")
        print("-" * 40)
        for i, (cname, val) in enumerate(mom20.head(15).items()):
            if pd.isna(val):
                continue
            print(f"{i+1:<5} {cname:<14} {val:<10.2f}")

    print(f"\n{'='*100}")

if __name__ == '__main__':
    main()
