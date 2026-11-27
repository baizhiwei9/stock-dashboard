# -*- coding: utf-8 -*-
"""猎杀 70%：全数据 × 全条件组合 × 严格前向
数据源（本机全部）：
  A. 包内 111 维特征面板（期权PCR/偏度/QVIX/技术/宏观） for 5 指数
  B. orth_feats 34 维（行业宽度/离散度/轮动/SHIBOR/中票利差/用电）
  C. 包内 5 种子预测（次日口径） + 我的 46 维（T+4）
  D. 期货主力映射（IC/IF/IM）→ 基差
方法：
  对每个 (指数, horizon, 因子, 分位门槛, 方向) 组合做前向测试：
    前半段(2019-01~2022-12) 挑 → 后半段(2023-01~2026-10) 验
  报告后半段命中率，只保留 n≥25 且 p<0.10 的组合
"""
import os, sys, json, itertools, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from scipy.stats import binomtest

try:
    from wsenv import WS                     # case/_shared/wide_ens_lib/wsenv.py
except ImportError:                              # 单独拷出去跑时退到文件所在仓根
    WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG=os.path.join(WS,'.cache/quark_dl/科创50日频预测包_20261018')
RAW=os.path.join(WS,'.cache/weekly_forecast/raw')
OUT=os.path.join(WS,'.cache/wide_ens')
os.makedirs(OUT,exist_ok=True)

IDX={'000300_SH':('沪深300','idx_hs300.csv'),'000688_SH':('科创50','idx_kc50.csv'),
     '000852_SH':('中证1000','idx_zz1000.csv'),'000905_SH':('中证500','idx_zz500.csv'),
     '399006_SZ':('创业板','idx_cyb.csv')}
HORIZONS={'D':1,'W':5,'M':20}

def load_close(fn):
    d=pd.read_csv(os.path.join(RAW,fn),dtype={'trade_date':str})
    d['trade_date']=pd.to_datetime(d['trade_date'],format='%Y%m%d',errors='coerce')
    d=d.dropna(subset=['trade_date']).set_index('trade_date').sort_index()
    d=d[~d.index.duplicated(keep='last')]
    return pd.to_numeric(d['close'],errors='coerce').astype(float)

def load_panel(code):
    """包内 111 维面板"""
    p=os.path.join(PKG,'data/features/daily_features_options_%s.csv'%code)
    if not os.path.exists(p): return None
    d=pd.read_csv(p)
    d['T']=pd.to_datetime(d['T'],errors='coerce')
    d=d.dropna(subset=['T']).set_index('T').sort_index()
    return d[~d.index.duplicated(keep='last')]

def load_orth():
    d=pd.read_csv(os.path.join(RAW,'orth_feats.csv'))
    d['trade_date']=pd.to_datetime(d['trade_date'],errors='coerce')
    d=d.dropna(subset=['trade_date']).set_index('trade_date').sort_index()
    return d[~d.index.duplicated(keep='last')]

def load_opt():
    d=pd.read_csv(os.path.join(RAW,'opt_feat.csv'))
    # 该文件首列是数据（PCR），日期列名可能是 trade_date/T/date
    dcol=[c for c in d.columns if c.lower() in ('trade_date','t','date')]
    if not dcol:
        return pd.DataFrame()
    d[dcol[0]]=pd.to_datetime(d[dcol[0]],errors='coerce')
    d=d.dropna(subset=[dcol[0]]).set_index(dcol[0]).sort_index()
    d=d[~d.index.duplicated(keep='last')]
    return d

def fwd_ret(c,h):
    return (c.shift(-h)/c-1)*100

def build(code, with_extra_h=None):
    """code + 原始面板。

    with_extra_h：若给定期望周期（1 或 5），则挂上 extra_feats 采纳的外部特征
    （T+1 ← 隔夜跨市场带滚动失效门；T+5 ← 周度成交额动量）。
    不传则保持原行为（只用于纯面板查看/旧脚本）。
    """
    name,fn=IDX[code]
    c=load_close(fn)
    F={}
    pn=load_panel(code)
    if pn is not None:
        for col in pn.columns:
            if col.lower() in ('t','y_next_ret','_grp','code'): continue
            if pn[col].dtype.kind not in 'fi': continue
            F['pkg_'+col]=np.log1p(pn[col].clip(lower=0)) if False else pn[col]
    for nm,loader in (('orth',load_orth),('opt',load_opt)):
        d=loader()
        for col in d.columns:
            if d[col].dtype.kind not in 'fi': continue
            F[nm+'_'+col]=d[col]
    df=pd.DataFrame(F)
    df=df.reindex(df.index.union(c.index)).ffill(limit=5).reindex(c.index)
    if with_extra_h is not None:
        try:
            import extra_feats as _XF
            df=_XF.attach(df, code, with_extra_h)
        except Exception as _e:
            print('[warn] extra_feats 挂载失败: %r'%(_e,), flush=True)
    return c,df

def test_combo(ret, sig, mask, lo, hi, min_n=25):
    """sig: 已在 [lo,hi] 内的信号(+1/-1/0)，mask 有效"""
    s=pd.DataFrame({'sig':sig,'ret':ret}).loc[lo:hi]
    s=s[(s['sig']!=0)].dropna()
    if len(s)<min_n: return None
    pr=s['sig']*s['ret']
    w=int((pr>0).sum()); n=len(s)
    return dict(n=n,hit=w/n,p=float(binomtest(w,n,0.5,alternative='greater').pvalue),
                ret=float(pr.mean()),net=float(pr.mean()-0.2))

def main():
    H=int(os.environ.get('HUNT_H','5'))     # 用哪个 horizon 当标签
    h=HORIZONS.get(os.environ.get('HUNT_CODE','W'),H)
    print('horizon = T+%d'%h, flush=True)
    ORTH=load_orth()   # 用于额外因子
    rows=[]
    for code,(name,fn) in IDX.items():
        c,df=build(code)
        ret=fwd_ret(c,h)
        print('== %s 面板 %d 列, rows=%d, %s~%s'%(name,df.shape[1],len(df),
              df.index.min().date(),df.index.max().date()), flush=True)
        # 每个因子：滚动 250 日分位 → 高/低两端 各 20%/10%
        for col in df.columns:
            v=df[col]
            if v.notna().sum()<400: continue
            q20=v.shift(1).rolling(250,min_periods=120).quantile(0.20)
            q80=v.shift(1).rolling(250,min_periods=120).quantile(0.80)
            q10=v.shift(1).rolling(250,min_periods=120).quantile(0.10)
            q90=v.shift(1).rolling(250,min_periods=120).quantile(0.90)
            conds={
              'HIGH20': v>=q80, 'LOW20': v<=q20,
              'HIGH10': v>=q90, 'LOW10': v<=q10,
            }
            # 双向：条件成立时做多 / 做空
            for cname,cond in conds.items():
                for sgn,sn in ((1,'L'),(-1,'S')):
                    sig=pd.Series(np.where(cond.fillna(False),sgn,0),index=df.index)
                    # 前半挑
                    a=test_combo(ret,sig,None,'2019-01-01','2022-12-31',20)
                    if not a or a['p']>0.20 or a['net']<=0: continue
                    # 后半验
                    b=test_combo(ret,sig,None,'2023-01-01','2026-12-31',25)
                    if not b: continue
                    rows.append(dict(idx=name,code=code,fac=col,cond=cname,dir=sn,
                                     pre_n=a['n'],pre_hit=a['hit'],pre_p=a['p'],pre_net=a['net'],
                                     oos_n=b['n'],oos_hit=b['hit'],oos_p=b['p'],oos_net=b['net']))
    R=pd.DataFrame(rows)
    R.to_csv(os.path.join(OUT,'hunt70_T%d.csv'%h),index=False)
    print('\n候选总数(前半通过)=%d'%len(R), flush=True)
    if not len(R): return
    # 严格筛选：后半 n>=25, hit>=0.70, p<0.10, net>0.2
    strict=R[(R.oos_n>=25)&(R.oos_hit>=0.70)&(R.oos_p<0.10)&(R.oos_net>0.2)]
    print('\n=== 命中率≥70% 且 OOS 显著 (n>=25) ===')
    if len(strict):
        print(strict.sort_values('oos_hit',ascending=False).head(40).to_string(index=False))
    else:
        print('无')
    loose=R[(R.oos_n>=25)&(R.oos_hit>=0.62)&(R.oos_p<0.10)&(R.oos_net>0.2)]
    print('\n=== 放宽到 ≥62% 且 OOS 显著 ===')
    print(loose.sort_values('oos_hit',ascending=False).head(30).to_string(index=False) if len(loose) else '无')

if __name__=='__main__':
    main()
