import pandas as pd
import numpy as np
import pywt
import base64

# Read data
df = pd.read_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", encoding='utf-8-sig')
df['date'] = pd.to_datetime(df['日期'])
df = df.sort_values('date').reset_index(drop=True)

volume = df['成交额'].values
close = df['收盘'].values
dates = df['date'].values

# Wavelet decomposition
wavelet = 'sym4'
level = 3

pad_len = (2**level) - (len(volume) % (2**level))
if pad_len == 2**level:
    pad_len = 0

if pad_len > 0:
    volume_padded = np.pad(volume, (0, pad_len), mode='symmetric')
else:
    volume_padded = volume

coeffs = pywt.wavedec(volume_padded, wavelet, level=level)

coeffs_long = [coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]]
coeffs_medium = [np.zeros_like(coeffs[0])] + [np.zeros_like(c) for c in coeffs[1:]]
coeffs_short = [np.zeros_like(coeffs[0])] + [np.zeros_like(c) for c in coeffs[1:]]

coeffs_medium[1] = coeffs[1]
coeffs_medium[2] = coeffs[2]
coeffs_short[3] = coeffs[3]

volume_long = pywt.waverec(coeffs_long, wavelet)[:len(volume)]
volume_medium = pywt.waverec(coeffs_medium, wavelet)[:len(volume)]
volume_short = pywt.waverec(coeffs_short, wavelet)[:len(volume)]

# Statistics
long_mean = np.mean(volume_long)
medium_mean = np.mean(volume_medium)
short_mean = np.mean(volume_short)
long_std = np.std(volume_long)
medium_std = np.std(volume_medium)
short_std = np.std(volume_short)

recent_idx = -60
recent_long = volume_long[recent_idx:]
recent_medium = volume_medium[recent_idx:]
recent_short = volume_short[recent_idx:]

# Convert images to base64
def img_to_base64(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode()

img1_b64 = img_to_base64('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_decomposition.png')
img2_b64 = img_to_base64('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_comparison.png')

html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>沪深300ETF 成交量小波分解分析报告</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 28px;
        }}
        .header p {{
            margin: 10px 0 0 0;
            opacity: 0.9;
            font-size: 14px;
        }}
        .section {{
            background: white;
            padding: 25px;
            border-radius: 10px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .section h2 {{
            color: #667eea;
            border-bottom: 2px solid #667eea;
            padding-bottom: 10px;
            margin-top: 0;
        }}
        .chart-container {{
            text-align: center;
            margin: 20px 0;
        }}
        .chart-container img {{
            max-width: 100%;
            height: auto;
            border-radius: 5px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}
        .stat-card {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            border-left: 4px solid #667eea;
        }}
        .stat-card h4 {{
            margin: 0 0 10px 0;
            color: #667eea;
        }}
        .stat-card p {{
            margin: 5px 0;
            font-size: 14px;
        }}
        .methodology {{
            background: #e8f4f8;
            padding: 15px;
            border-radius: 8px;
            margin: 15px 0;
        }}
        .methodology h3 {{
            margin-top: 0;
            color: #2c5282;
        }}
        .methodology ul {{
            margin: 10px 0;
        }}
        .highlight {{
            background: #fff3cd;
            padding: 2px 6px;
            border-radius: 3px;
            font-weight: bold;
        }}
        .footer {{
            text-align: center;
            color: #666;
            font-size: 12px;
            margin-top: 30px;
            padding: 20px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }}
        th, td {{
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background-color: #667eea;
            color: white;
        }}
        tr:hover {{
            background-color: #f5f5f5;
        }}
        .legend {{
            display: flex;
            justify-content: center;
            gap: 20px;
            margin: 15px 0;
            flex-wrap: wrap;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        .legend-color {{
            width: 20px;
            height: 3px;
            border-radius: 2px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>沪深300ETF (510300) 成交量小波分解分析报告</h1>
        <p>基于 sym4 小波三层多尺度分解 | 数据区间: 2019-01-02 至 2026-09-16 | 报告生成日期: 2026-09-17</p>
    </div>

    <div class="section">
        <h2>一、分析概述</h2>
        <p>本报告仿照微信公众号"真实A股小波实战"的分析方法，对<strong>沪深300ETF (510300.SH)</strong>的日成交额进行小波多尺度分解，将成交量拆解为三个频段：</p>
        <div class="methodology">
            <h3>小波分解方法论</h3>
            <ul>
                <li><strong>小波基函数：</strong>sym4（Symlet 4阶，具有近似对称性和紧支撑特性）</li>
                <li><strong>分解层数：</strong>3层（Level 3）</li>
                <li><strong>长周期趋势 (cA3)：</strong>近似系数，代表成交量的长期趋势和低频基底，对应图中<strong>蓝色线</strong></li>
                <li><strong>中周期波段 (cD3 + cD2)：</strong>第2、3层细节系数，代表中期趋势资金和波段动能，对应图中<strong>绿色线</strong></li>
                <li><strong>短周期脉冲 (cD1)：</strong>第1层细节系数，代表高频日内博弈、对倒噪声和散户追风，对应图中<strong>红色线</strong></li>
            </ul>
        </div>
        <p>核心判断逻辑：<span class="highlight">中低频同步放大 → 确认真实趋势</span>；<span class="highlight">仅高频脉冲而中频缺席 → 虚假诱多</span></p>
    </div>

    <div class="section">
        <h2>二、成交量频段分解图</h2>
        <div class="legend">
            <div class="legend-item">
                <div class="legend-color" style="background-color: blue;"></div>
                <span>长周期趋势 (cA3) - 低频基底</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background-color: green;"></div>
                <span>中周期波段 (cD3+cD2) - 中频趋势资金</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background-color: red;"></div>
                <span>短周期脉冲 (cD1) - 高频噪声/日内博弈</span>
            </div>
        </div>
        
        <div class="chart-container">
            <img src="data:image/png;base64,{img1_b64}" alt="成交量小波分解图">
            <p style="color: #666; font-size: 12px; margin-top: 10px;">
                <strong>图1：沪深300ETF收盘价与成交量三频段分解（2019-2026）</strong><br>
                从上到下依次为：收盘价走势、长周期成交量趋势、中周期成交量波段、短周期成交量脉冲
            </p>
        </div>
        
        <div class="chart-container">
            <img src="data:image/png;base64,{img2_b64}" alt="成交量频段对比图">
            <p style="color: #666; font-size: 12px; margin-top: 10px;">
                <strong>图2：成交量多频段叠加对比图（2019-2026）</strong><br>
                上图：收盘价走势；下图：原始成交额（灰色填充）与三频段分量叠加
            </p>
        </div>
    </div>

    <div class="section">
        <h2>三、频段统计特征</h2>
        <div class="stats-grid">
            <div class="stat-card">
                <h4>长周期趋势 (cA3) - 蓝色</h4>
                <p>均值: {long_mean/1e9:.2f} 亿元</p>
                <p>标准差: {long_std/1e9:.2f} 亿元</p>
                <p>特点: 反映市场长期资金格局和流动性环境</p>
            </div>
            <div class="stat-card">
                <h4>中周期波段 (cD3+cD2) - 绿色</h4>
                <p>均值: {medium_mean/1e9:.2f} 亿元</p>
                <p>标准差: {medium_std/1e9:.2f} 亿元</p>
                <p>特点: 中期趋势资金与波段推动力量</p>
            </div>
            <div class="stat-card">
                <h4>短周期脉冲 (cD1) - 红色</h4>
                <p>均值: {short_mean/1e9:.2f} 亿元</p>
                <p>标准差: {short_std/1e9:.2f} 亿元</p>
                <p>特点: 高频日内博弈、对倒与散户噪声</p>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>四、关键周期节点研判</h2>
        <table>
            <tr>
                <th>时间节点</th>
                <th>市场状态</th>
                <th>长周期(蓝)</th>
                <th>中周期(绿)</th>
                <th>短周期(红)</th>
                <th>研判结论</th>
            </tr>
            <tr>
                <td>2019年初</td>
                <td>熊市末期</td>
                <td>低位</td>
                <td>低迷</td>
                <td>脉冲为主</td>
                <td>底部震荡，缺乏趋势资金</td>
            </tr>
            <tr>
                <td>2020年3月</td>
                <td>疫情底</td>
                <td>回升</td>
                <td>放量</td>
                <td>脉冲放大</td>
                <td>恐慌底，三频同步放量</td>
            </tr>
            <tr>
                <td>2021年2月</td>
                <td>阶段高点</td>
                <td>高位</td>
                <td>见顶回落</td>
                <td>脉冲放大</td>
                <td>中频见顶，高频脉冲警示</td>
            </tr>
            <tr>
                <td>2022年10月</td>
                <td>二次探底</td>
                <td>回落</td>
                <td>放量</td>
                <td>脉冲</td>
                <td>中频放量可能为筑底信号</td>
            </tr>
            <tr>
                <td>2024年2月</td>
                <td>极端低点</td>
                <td>新低</td>
                <td>脉冲</td>
                <td>剧烈</td>
                <td>恐慌性抛盘，高频剧烈</td>
            </tr>
            <tr>
                <td>2024年至今</td>
                <td>反弹/震荡</td>
                <td>回升</td>
                <td>活跃</td>
                <td>正常</td>
                <td>三频同步改善，趋势确立</td>
            </tr>
        </table>
    </div>

    <div class="section">
        <h2>五、当前状态分析（近60日）</h2>
        <div class="stats-grid">
            <div class="stat-card">
                <h4>长周期趋势 (蓝)</h4>
                <p>近60日均值: {np.mean(recent_long)/1e9:.2f} 亿元</p>
                <p>趋势方向: {'上升' if recent_long[-1] > recent_long[0] else '下降'}</p>
            </div>
            <div class="stat-card">
                <h4>中周期波段 (绿)</h4>
                <p>近60日均值: {np.mean(recent_medium)/1e9:.2f} 亿元</p>
                <p>趋势方向: {'上升' if recent_medium[-1] > recent_medium[0] else '下降'}</p>
            </div>
            <div class="stat-card">
                <h4>短周期脉冲 (红)</h4>
                <p>近60日均值: {np.mean(recent_short)/1e9:.2f} 亿元</p>
                <p>波动状态: {'活跃' if np.std(recent_short) > np.std(volume_short)*0.5 else '平稳'}</p>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>六、交易机会研判</h2>
        <p>根据成交量小波分解的信号逻辑：</p>
        <ul>
            <li><strong>真实趋势确认信号：</strong>当长周期（蓝色）和中周期（绿色）同步放大时，表明有大级别资金在跨日建仓，趋势真实坚固，可坚定顺势。</li>
            <li><strong>虚假繁荣警示信号：</strong>当成交量创出天量但中低频完全没跟上，放大的量全堆在高频层（红色），说明只是日内对倒或散户追风，这种放量是虚假繁荣，趋势随时夭折。</li>
            <li><strong>底部信号：</strong>长周期趋势在低位企稳，中周期开始从负向转正并放大，可能是大级别趋势的确立信号。</li>
            <li><strong>顶部信号：</strong>中周期有效趋势动能在历史极值见顶，而高频日内噪声冲至历史极值，警示趋势衰竭并即将逆转。</li>
        </ul>
        
        <div class="methodology" style="background: #fff3cd;">
            <h3 style="color: #856404;">当前研判（2026-09）</h3>
            <p style="color: #856404;"><strong>请结合图1和图2观察最近几个月的三频段状态：</strong></p>
            <ul style="color: #856404;">
                <li>若蓝线（长周期）与绿线（中周期）同步上行 → <strong>趋势真实，可顺势持有</strong></li>
                <li>若仅红线（短周期）剧烈脉冲而蓝绿线 flat → <strong>虚假繁荣，警惕回调</strong></li>
                <li>若三频同步下行 → <strong>资金撤离，观望为主</strong></li>
            </ul>
        </div>
    </div>

    <div class="section">
        <h2>七、数据说明</h2>
        <ul>
            <li><strong>数据来源：</strong>akshare - 沪深300ETF (510300.SH) 日K线数据</li>
            <li><strong>数据区间：</strong>2019-01-02 至 2026-09-16（共 {len(df)} 个交易日）</li>
            <li><strong>成交量指标：</strong>日成交额（元）</li>
            <li><strong>小波参数：</strong>sym4 小波，3层分解</li>
            <li><strong>边界处理：</strong>对称延拓（symmetric padding），确保分解完整性</li>
        </ul>
    </div>

    <div class="footer">
        <p>报告生成时间: 2026-09-17 | 分析工具: Python + PyWavelets + Matplotlib</p>
        <p>免责声明：本报告仅供研究参考，不构成投资建议。市场有风险，投资需谨慎。</p>
    </div>
</body>
</html>
'''

# Save HTML report
report_path = '/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/沪深300ETF成交量小波分解分析报告.html'
with open(report_path, 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f"HTML report saved to: {report_path}")
print(f"Report size: {len(html_content)/1024:.1f} KB")
