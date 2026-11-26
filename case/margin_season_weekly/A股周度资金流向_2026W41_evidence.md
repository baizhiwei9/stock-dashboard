# A股周度资金流向简报_2026W41 证据汇总

## Scope
- 观察窗口：2026年10月6日(周一)-10月10日(周五)，实际交易日为10月8日(周三)-10月10日(周五)，共3个交易日（国庆长假10/1-10/7）
- 注意：10月6日(周一)实际为国庆假期调休，10月8日为节后首个交易日
- 数据来源：AKShare实时接口、国金证券资金跟踪系列之六十六(2026-10-12)、国信证券策略周报(2026-10-10/12)、证券时报数据宝(2026-10-12)、中国基金报(2026-10-12)、东方财富Choice

---

### Claim 1: 两融余额周度下降至25597亿元，连续3周减少
Source: 证券时报·数据宝
URL: (data.eastmoney.com, 东北证券金融世界 2026-10-12)
Date: 2026-10-12
Excerpt: 截至10月9日，深沪北两融余额为25596.56亿元，与前一周末相比减少101.96亿元，为连续第3周减少。其中融资余额25300.55亿元，一周减少104.02亿元；融券余额296.02亿元，一周增加2.06亿元。
Context: 前一周(9/30)两融余额为25698.53亿元(东方财富数据)。9月底融资余额从6月高点约3.00万亿持续下降至2.54万亿。
Dimension: dim_02
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 2: 两融净卖出103.88亿元，活跃度回落至2024年9月以来新低
Source: 国金证券研究所·资金跟踪系列之六十六
URL: https://pdf.dfcfw.com/pdf/H3_AP202610131830379839_1.pdf
Date: 2026-10-12
Excerpt: 上周两融继续净卖出103.88亿元。两融活跃度继续回落至2024年9月下旬以来的新低。行业上，两融主要净买入医药、汽车、农林牧渔等板块，主要净卖出电子、通信、机械等板块。
Context: 两融平均担保比例继续回落。风格上，两融继续净卖出各类风格板块（大盘/中盘/小盘成长/价值）。
Dimension: dim_02
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 3: 两融行业拆分 - 融资净买入前5/后5
Source: 东北证券金融世界·两融周报
URL: (finance.sina.com.cn 2026-10-13)
Date: 2026-10-13
Excerpt: 融资净买入前五的行业为美容护理、综合、纺织服饰、社会服务、商贸零售，分别为-0.21、-0.30、-0.61、-1.28、-1.71亿元。融资净买入额排名后五的行业为电力设备、有色金属、机械设备、通信、电子，分别为-37.94、-44.69、-64.04、-103.72、-284.43亿元。
Context: 所有行业融资净买入均为负值，说明全市场两融资金全面净流出。科技板块（电子、通信）流出最多。
Dimension: dim_02
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 4: 两融成交占A股成交额比8.19%
Source: 东北证券金融世界·两融周报
URL: (finance.sina.com.cn 2026-10-13)
Date: 2026-10-13
Excerpt: 两融成交占比A股成交额比重的8.19%（环比-10.47%）。平均交易额为1339.96亿元，较上周-418.47亿元（环比-23.80%）。
Context: 9月30日数据为两融成交额1148.75亿，占A股成交额7.91%（东方财富）。
Dimension: dim_02, dim_07
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 5: 北上资金活跃度回升，重新小幅净买入A股
Source: 国金证券研究所·资金跟踪系列之六十六
URL: https://pdf.dfcfw.com/pdf/H3_AP202610131830379839_1.pdf
Date: 2026-10-12
Excerpt: 上周北上资金活跃度回升。北上近5日日均买卖总额回升，近5日北上买卖总额占全A成交额之比从14.31%回升至15.31%。上周北上重新小幅净买入A股。
Context: 前一周(9/30周)净流出93亿元(国信证券口径)。
Dimension: dim_04
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 6: 北上资金行业流向
Source: 国金证券研究所·资金跟踪系列之六十六
URL: https://pdf.dfcfw.com/pdf/H3_AP202610131830379839_1.pdf
Date: 2026-10-12
Excerpt: 综合来看：北上可能主要净买入电子、通信、公用事业等板块，主要净卖出非银、化工、机械等板块。基于前10大活跃股口径，北上在电子、通信、家电等板块买卖总额之比上升。
Context: 前十大活跃股：中际旭创(128亿元)、宁德时代(121亿元)、药明康德(52亿元)成交较活跃。
Dimension: dim_04
Scope fit: IN-SCOPE
Confidence: MEDIUM (基于代理变量估算)

### Claim 7: 国信证券估算北向资金净流出14亿元(10/8-10/9)
Source: 国信证券·策略周报
URL: (data.eastmoney.com 2026-10-10/12)
Date: 2026-10-10
Excerpt: 最近一周(2026/10/8-2026/10/9)交易日期间北向资金估算净流出14亿元，前一周(2026/9/27-2026/9/30)估算净流出93亿元。灵活型外资估算净流出8亿元。
Context: 国金证券的测算结论是"重新小幅净买入"，与国信证券"净流出14亿"略有差异，可能源于方法论不同（国金用代理变量+活跃股，国信用银证转账+托管行数据）。
Dimension: dim_04
Scope fit: IN-SCOPE
Confidence: MEDIUM (估算值，两家机构方向略有分歧)

### Claim 8: 股票ETF被明显净申购，约383亿元/周
Source: 国信证券·立体投资策略周报 / 中国基金报
URL: (data.eastmoney.com 2026-10-12)
Date: 2026-10-12
Excerpt: 上周ETF净申购现金规模为383亿元。10月9日股票ETF市场总份额增加150.17亿份，当日净流入资金达到231.14亿元。9月28日以来，股票ETF整体以资金净流入为主，合计"吸金"已超700亿元。上周2个交易日净流入资金增加，合计净流入接近400亿元。
Context: 26年累计(截至2026/10/9)ETF净赎回5767亿元。
Dimension: dim_01
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 9: ETF按指数拆分 - 科创50、半导体、创业板指被主要净申购
Source: 国金证券研究所·资金跟踪系列之六十六
URL: https://pdf.dfcfw.com/pdf/H3_AP202610131830379839_1.pdf
Date: 2026-10-12
Excerpt: 从ETF跟踪的指数来看，科创50、半导体、创业板指等ETF被主要净申购。从ETF分拆行业来看，股票ETF主要净买入TMT、医药、电新等板块。
Context: 5日角度观测，近期资金流入科创50指数超201亿元，创业板指超88亿元。以个人ETF为主。
Dimension: dim_01
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 10: 主动偏股基金仓位小幅回升，加仓电子通信军工
Source: 国金证券研究所·资金跟踪系列之六十六
URL: https://pdf.dfcfw.com/pdf/H3_AP202610131830379839_1.pdf
Date: 2026-10-12
Excerpt: 上周主动偏股基金仓位小幅回升，剔除涨跌幅因素后，主要加仓电子、通信、军工等板块，主要减仓有色、化工、电新等板块。与中盘/小盘成长的相关性上升，与大盘/中盘/小盘价值的相关性回落。
Context: 基于二次规划法。7月底以来公募基金在科技股上呈逆势操作（反弹减仓，下跌加仓）。
Dimension: dim_05
Scope fit: IN-SCOPE
Confidence: MEDIUM (基于二次规划法估算)

### Claim 11: 龙虎榜交易热度有所回升
Source: 国金证券研究所·资金跟踪系列之六十六
URL: https://pdf.dfcfw.com/pdf/H3_AP202610131830379839_1.pdf
Date: 2026-10-12
Excerpt: 上周龙虎榜买卖总额有所回升，龙虎榜买卖总额占全A成交额之比同样回升。行业层面，农林牧渔、传媒、家电等板块龙虎榜买卖总额占比相对较高且仍在上升。
Context: AKShare数据显示上周164条龙虎榜记录(10/8-10/10)。
Dimension: dim_06
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 12: 市场成交数据
Source: AKShare stock_zh_index_daily
URL: N/A
Date: 2026-10-12
Excerpt: 上证指数10/8成交482亿股，10/9成交535亿股。10/9两市成交约1.9万亿。9月日均成交额约1.81万亿（东方财富证券报告）。
Context: 9月30日(节前最后交易日)全市场成交仅1.45万亿元。节后首日10/8明显放量。
Dimension: dim_07
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 13: 市场交易热度和波动率
Source: 国金证券研究所·资金跟踪系列之六十六
URL: https://pdf.dfcfw.com/pdf/H3_AP202610131830379839_1.pdf
Date: 2026-10-12
Excerpt: 市场交易热度回升，房地产、医药、化工、农林牧渔、建材等板块的交易热度均在80%分位数以上。通信、电子、建材等板块波动率在80%分位数以上。
Context: 分位数起点为2010年1月第一周。电子行业成交额占比24.14%(近一周)，历史高位。
Dimension: dim_07
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 14: ETF单品数据 - 科创50ETF华夏/创业板ETF等
Source: 中国基金报
URL: (m.21jingji.com 2026-10-12)
Date: 2026-10-12
Excerpt: 10月9日科创50ETF华夏净流入36.72亿元(最新规模969.62亿元)、创业板ETF易方达净流入21.9亿元(规模674.5亿元)、中证1000ETF南方净流入居前。科创50ETF易方达规模421.1亿元，净流入8.0亿元。
Context: 截至10月13日全市场1597只股票ETF总规模达3.36万亿元。
Dimension: dim_01
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 15: 国信证券估算偏股型基金新发行125亿元/周
Source: 国信证券·立体投资策略周报
URL: (data.eastmoney.com 2026-10-12)
Date: 2026-10-12
Excerpt: 上周偏股型基金新发行125亿元，26年累计(截至2026/10/9)发行6417亿元。
Context: 10月近百只新基金定档待售，六成聚焦权益赛道。
Dimension: dim_05
Scope fit: IN-SCOPE
Confidence: HIGH

### Claim 16: 融资余额历史分位数参考
Source: 东方财富证券·策略报告
URL: (m.toutiao.com 2026-10-13)
Date: 2026-10-13
Excerpt: 沪深两市日均成交额由6月约3.11万亿元降至9月约1.81万亿元，融资余额同期由约3.00万亿元降至2.54万亿元。
Context: 融资余额从6月高点3万亿降至10月初约2.53万亿，降幅约15.7%，处于2024年10月以来的低位。
Dimension: dim_02, dim_07
Scope fit: IN-SCOPE
Confidence: HIGH
