# A股周度资金流向简报_2026W41 研究维度

research_question: 模仿国金策略《资金跟踪系列》框架，制作2026年第41周（国庆后首周10月6-10日）A股周度资金流向简报，覆盖五大类资金（ETF、两融、北上、主动基金、龙虎榜），含行业共识矩阵和季节性分析。

## Scope
time_scope: 2026年10月6日-10月10日（第41周），扩展窗口为近10日/近20日滚动，季节性对照为2010年至今历年国庆后首周
geographic_scope: 中国A股市场（含陆股通/北上资金）
target_object: A股五大类资金流向（ETF申赎、两融、北上/陆股通、主动偏股基金仓位、龙虎榜）
decision_intent: 为投资决策提供资金面全景图，识别行业资金共识/分歧方向
task_type: 投资研究/资金面分析
exclusions: 不涉及个股基本面分析、不涉及债券/商品/海外市场、不涉及具体投资建议
ambiguities: 2026Q3基金季报可能尚未完全披露，需用最近一期近似；北上资金实时明细披露受限，需代理变量估算行业流向

### dim_01: ETF资金流向
Entity: 全市场股票型ETF
Must-have: yes
Description: 全市场股票ETF周度净申购额、按宽基/行业/主题拆分、TOP10净申购/赎回单品、按ETF跟踪指数行业权重分摊到申万一级行业
Sources hint: AKShare ETF数据、上交所/深交所基金披露

### dim_02: 两融资金
Entity: A股融资融券
Must-have: yes
Description: 两融余额周度变化、融资净买入、两融成交占比、历史分位数、行业拆分TOP5/Bottom5、季节性对比
Sources hint: AKShare融资融券数据、stock-data-skill

### dim_03: 两融季节性分析
Entity: A股融资融券历史季节性
Must-have: yes
Description: 2010年至今历年国庆后首周两融余额变化统计，判断当前是否偏离常态
Sources hint: AKShare历史数据回溯

### dim_04: 北上资金/陆股通
Entity: 北向资金/陆股通
Must-have: yes
Description: 周度累计净流入/流出额、活跃成交股行业分布、中小盘代理变量估算行业流向
Sources hint: AKShare沪深港通数据、港交所披露

### dim_05: 主动偏股基金仓位
Entity: 主动偏股公募基金
Must-have: yes
Description: 基于最新季报/中报拆解行业仓位变化，剔除涨跌幅因素估算真实加减仓幅度
Sources hint: 基金季报数据、wind/同花顺iFinD

### dim_06: 龙虎榜资金
Entity: A股龙虎榜
Must-have: yes
Description: 周度买卖总额、净买卖额、成交占比、环比变动、TOP10净买入个股及行业
Sources hint: AKShare龙虎榜数据

### dim_07: 市场总量与分位数
Entity: A股全市场
Must-have: yes
Description: 全市场成交额、换手率、两融成交占比、期权IV/VIX的历史分位数
Sources hint: AKShare市场数据、期权数据

### dim_08: 资金共识矩阵与行业排序
Entity: 综合
Must-have: yes
Description: 五大类资金在申万一级行业的方向一致性矩阵、综合加权净流入/流出TOP10行业
Sources hint: 以上各维度综合
