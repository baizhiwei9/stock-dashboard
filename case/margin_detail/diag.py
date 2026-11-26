import os, pandas as pd
D=os.path.join(os.path.dirname(os.path.abspath(__file__)),"data")
md=pd.read_csv(os.path.join(D,"margin_detail.csv")); im=pd.read_csv(os.path.join(D,"industry_map.csv")); sb=pd.read_csv(os.path.join(D,"stock_basic.csv"))
latest=md[md.trade_date==md.trade_date.max()]
miss=latest[~latest.ts_code.isin(set(im.ts_code))]
print("missing leaf: none")
print("missing industry codes sample:", miss.ts_code.head(15).tolist())
print("missing suffix dist:", miss.ts_code.str.split('.').str[1].value_counts().to_dict())
print("missing also not in stock_basic:", (~miss.ts_code.isin(set(sb.ts_code))).sum(), "of", len(miss))
