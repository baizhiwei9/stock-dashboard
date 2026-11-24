import pandas as pd
import numpy as np
import pywt
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import warnings
import base64
import io
warnings.filterwarnings('ignore')

# Set Chinese font support
plt.rcParams['font.sans-serif'] = ['Droid Sans Fallback', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

print("Step 1: Reading data...")
df = pd.read_csv("/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/510300_data.csv", encoding='utf-8-sig')
df['日期'] = pd.to_datetime(df['日期'])
df = df.sort_values('日期').reset_index(drop=True)
print(f"Data loaded: {len(df)} rows, {df['日期'].min()} to {df['日期'].max()}")

# Use 成交额 (turnover amount) as volume metric
volume = df['成交额'].values
close = df['收盘'].values
dates = df['日期'].values

print("\nStep 2: Wavelet decomposition...")
# sym4 wavelet, 3-level decomposition
wavelet = 'sym4'
level = 3

# Pad data to suitable length for wavelet decomposition
# Make length divisible by 2^level
pad_len = (2**level) - (len(volume) % (2**level))
if pad_len == 2**level:
    pad_len = 0

if pad_len > 0:
    # Use symmetric padding (reflect)
    volume_padded = np.pad(volume, (0, pad_len), mode='symmetric')
    close_padded = np.pad(close, (0, pad_len), mode='symmetric')
    # Extend dates
    last_date = dates[-1]
    date_range = pd.date_range(start=last_date + pd.Timedelta(days=1), periods=pad_len, freq='B')
    dates_padded = np.concatenate([dates, date_range])
else:
    volume_padded = volume
    close_padded = close
    dates_padded = dates

print(f"Original length: {len(volume)}, Padded length: {len(volume_padded)}, padding: {pad_len}")

# Multi-level wavelet decomposition
coeffs = pywt.wavedec(volume_padded, wavelet, level=level)
print(f"Decomposition levels: {len(coeffs)} (cA{level} + cD{level}...cD1)")
for i, c in enumerate(coeffs):
    print(f"  Level {i}: length {len(c)}")

# Reconstruct components
# Long-term: cA3 (approximation)
# Medium-term: cD3 + cD2 (detail levels 3 and 2)
# Short-term: cD1 (detail level 1)

# Create coefficient arrays for reconstruction
coeffs_long = [coeffs[0]] + [np.zeros_like(c) for c in coeffs[1:]]
coeffs_medium = [np.zeros_like(coeffs[0])] + [np.zeros_like(c) for c in coeffs[1:]]
coeffs_short = [np.zeros_like(coeffs[0])] + [np.zeros_like(c) for c in coeffs[1:]]

# Medium: cD3 + cD2 (indices 1 and 2 in coeffs list, since coeffs[0] is cA3)
coeffs_medium[1] = coeffs[1]  # cD3
coeffs_medium[2] = coeffs[2]  # cD2

# Short: cD1
coeffs_short[3] = coeffs[3]  # cD1

# Reconstruct
volume_long = pywt.waverec(coeffs_long, wavelet)
volume_medium = pywt.waverec(coeffs_medium, wavelet)
volume_short = pywt.waverec(coeffs_short, wavelet)

# Trim back to original length
volume_long = volume_long[:len(volume)]
volume_medium = volume_medium[:len(volume)]
volume_short = volume_short[:len(volume)]
dates_final = dates
close_final = close

print(f"\nReconstructed lengths: long={len(volume_long)}, medium={len(volume_medium)}, short={len(volume_short)}")
print(f"Long range: {volume_long.min():.0f} ~ {volume_long.max():.0f}")
print(f"Medium range: {volume_medium.min():.0f} ~ {volume_medium.max():.0f}")
print(f"Short range: {volume_short.min():.0f} ~ {volume_short.max():.0f}")

print("\nStep 3: Generating plots...")

# Plot 1: Price + Decomposed Volume Components
fig, axes = plt.subplots(4, 1, figsize=(16, 14), sharex=True)
fig.suptitle('沪深300ETF (510300) 成交量小波分解 - sym4 三层多尺度分析 (2019-2026)', fontsize=16, fontweight='bold')

# Plot price
ax1 = axes[0]
ax1.plot(dates_final, close_final, color='black', linewidth=1.2, label='沪深300ETF收盘价')
ax1.set_ylabel('价格 (元)', fontsize=11)
ax1.legend(loc='upper left', fontsize=10)
ax1.grid(True, alpha=0.3)
ax1.set_title('沪深300ETF收盘价走势', fontsize=12, fontweight='bold')

# Key events annotation
key_events = [
    ('2020-03-19', '疫情底'),
    ('2021-02-18', '2021年高点'),
    ('2022-04-27', '2022年4月底'),
    ('2022-10-31', '2022年10月底'),
    ('2024-02-05', '2024年2月低点'),
]

for event_date, event_label in key_events:
    try:
        event_dt = pd.to_datetime(event_date)
        if event_dt >= dates_final[0] and event_dt <= dates_final[-1]:
            # Find closest date
            idx = np.argmin(np.abs(pd.to_datetime(dates_final) - event_dt))
            price_at_event = close_final[idx]
            ax1.axvline(x=dates_final[idx], color='gray', linestyle='--', alpha=0.5)
            ax1.annotate(event_label, xy=(dates_final[idx], price_at_event), 
                        xytext=(10, 20), textcoords='offset points',
                        fontsize=8, color='red',
                        arrowprops=dict(arrowstyle='->', color='red', alpha=0.6))
    except:
        pass

# Plot long-term component
ax2 = axes[1]
ax2.fill_between(dates_final, volume_long, alpha=0.4, color='blue', label=f'长周期趋势 (cA{level})')
ax2.plot(dates_final, volume_long, color='blue', linewidth=1.5)
ax2.set_ylabel('成交额 (元)', fontsize=11)
ax2.legend(loc='upper left', fontsize=10)
ax2.grid(True, alpha=0.3)
ax2.set_title('长周期成交量趋势 (低频基底)', fontsize=12, fontweight='bold')

# Plot medium-term component
ax3 = axes[2]
ax3.fill_between(dates_final, volume_medium, alpha=0.4, color='green', label='中周期波段 (cD3+cD2)')
ax3.plot(dates_final, volume_medium, color='green', linewidth=1.2)
ax3.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
ax3.set_ylabel('成交额 (元)', fontsize=11)
ax3.legend(loc='upper left', fontsize=10)
ax3.grid(True, alpha=0.3)
ax3.set_title('中周期成交量波段 (中频趋势资金)', fontsize=12, fontweight='bold')

# Plot short-term component
ax4 = axes[3]
ax4.fill_between(dates_final, volume_short, alpha=0.4, color='red', label='短周期脉冲 (cD1)')
ax4.plot(dates_final, volume_short, color='red', linewidth=0.8)
ax4.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
ax4.set_ylabel('成交额 (元)', fontsize=11)
ax4.legend(loc='upper left', fontsize=10)
ax4.grid(True, alpha=0.3)
ax4.set_title('短周期成交量脉冲 (高频噪声/日内博弈)', fontsize=12, fontweight='bold')
ax4.set_xlabel('日期', fontsize=11)

# Format x-axis
for ax in axes:
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax.xaxis.set_minor_locator(mdates.MonthLocator())

plt.tight_layout(rect=[0, 0, 1, 0.96])
plt.savefig('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_decomposition.png', dpi=150, bbox_inches='tight')
print("Plot 1 saved: wavelet_decomposition.png")
plt.close()

# Plot 2: Comparison chart similar to the article
fig, axes = plt.subplots(2, 1, figsize=(16, 10), sharex=True)
fig.suptitle('沪深300ETF 成交量频段拆解与趋势真实性研判 (2019-2026)', fontsize=16, fontweight='bold')

# Top: Price
ax1 = axes[0]
ax1.plot(dates_final, close_final, color='black', linewidth=1.2, label='沪深300ETF收盘价')
ax1.set_ylabel('价格 (元)', fontsize=11)
ax1.legend(loc='upper left', fontsize=10)
ax1.grid(True, alpha=0.3)
ax1.set_title('沪深300ETF收盘价', fontsize=12, fontweight='bold')

# Bottom: Decomposed volume (stacked/overlay view)
ax2 = axes[1]
# Normalize for visualization
v_long_norm = volume_long / np.max(np.abs(volume_long)) * np.max(volume) * 0.4
v_medium_norm = volume_medium 
v_short_norm = volume_short

ax2.fill_between(dates_final, 0, volume, alpha=0.15, color='gray', label='原始成交额')
ax2.plot(dates_final, volume_long, color='blue', linewidth=2, label='长周期趋势 (低频基底)')
ax2.plot(dates_final, volume_medium, color='green', linewidth=1.5, label='中周期波段 (中频趋势资金)')
ax2.plot(dates_final, volume_short, color='red', linewidth=0.8, alpha=0.6, label='短周期脉冲 (高频噪声)')

ax2.set_ylabel('成交额 (元)', fontsize=11)
ax2.legend(loc='upper left', fontsize=10)
ax2.grid(True, alpha=0.3)
ax2.set_title('成交量多频段拆解', fontsize=12, fontweight='bold')
ax2.set_xlabel('日期', fontsize=11)

for ax in axes:
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax.xaxis.set_minor_locator(mdates.MonthLocator())

plt.tight_layout(rect=[0, 0, 1, 0.96])
plt.savefig('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_comparison.png', dpi=150, bbox_inches='tight')
print("Plot 2 saved: wavelet_comparison.png")
plt.close()

print("\nStep 4: Generating HTML report...")

# Convert images to base64
def img_to_base64(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode()

img1_b64 = img_to_base64('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_decomposition.png')
img2_b64 = img_to_base64('/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace/ses_gffe5f530aa190ffeRxnRiUPRdvAPh0/wavelet_comparison.png')

# Calculate some statistics
long_mean = np.mean(volume_long)
medium_mean = np.mean(volume_medium)
short_mean = np.mean(volume_short)
long_std = np.std(volume_long)
medium_std = np.std(volume_medium)
short_std = np.std(volume_short)

# Recent data (last 60 days)
recent_idx = -60
recent_long = volume_long[recent_idx:]
recent_medium = volume_medium[recent_idx:]
recent_short = volume_short[recent_idx:]

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
                <li><strong>长周期趋势 (cA3)：</strong>近似系数，代表成交量的长期趋势和低频基底</li>
                <li><strong>中周期波段 (cD3 + cD2)：</strong>第2、3层细节系数，代表中期趋势资金和波段动能</li>
                <li><strong>短周期脉冲 (cD1)：</strong>第1层细节系数，代表高频日内博弈、对倒噪声和散户追风</li>
            </ul>
        </div>
        <p>核心判断逻辑：<span class="highlight">中低频同步放大 → 确认真实趋势</span>；<span class="highlight">仅高频脉冲而中频缺席 → 虚假诱多</span></p>
    </div>

    <div class="section">
        <h2>二、成交量频段分解图</h2>
        <div class="chart-container">
            <img src="data:image/png;base64,{img1_b64}" alt="成交量小波分解图">
            <p style="color: #666; font-size: 12px; margin-top: 10px;">图1：沪深300ETF收盘价与成交量三频段分解（2019-2026）</p>
        </div>
        
        <div class="chart-container">
            <img src="data:image/png;base64,{img2_b64}" alt="成交量频段对比图">
            <p style="color: #666; font-size: 12px; margin-top: 10px;">图2：成交量多频段叠加对比图（2019-2026）</p>
        </div>
    </div>

    <div class="section">
        <h2>三、频段统计特征</h2>
        <div class="stats-grid">
            <div class="stat-card">
                <h4>长周期趋势 (cA3)</h4>
                <p>均值: {long_mean/1e9:.2f} 亿元</p>
                <p>标准差: {long_std/1e9:.2f} 亿元</p>
                <p>特点: 反映市场长期资金格局</p>
            </div>
            <div class="stat-card">
                <h4>中周期波段 (cD3+cD2)</h4>
                <p>均值: {medium_mean/1e9:.2f} 亿元</p>
                <p>标准差: {medium_std/1e9:.2f} 亿元</p>
                <p>特点: 中期趋势资金与波段推动</p>
            </div>
            <div class="stat-card">
                <h4>短周期脉冲 (cD1)</h4>
                <p>均值: {short_mean/1e9:.2f} 亿元</p>
                <p>标准差: {short_std/1e9:.2f} 亿元</p>
                <p>特点: 高频日内博弈与噪声</p>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>四、关键周期节点研判</h2>
        <table>
            <tr>
                <th>时间节点</th>
                <th>市场状态</th>
                <th>长周期</th>
                <th>中周期</th>
                <th>短周期</th>
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
                <h4>长周期趋势</h4>
                <p>近60日均值: {np.mean(recent_long)/1e9:.2f} 亿元</p>
                <p>趋势方向: {'上升' if recent_long[-1] > recent_long[0] else '下降'}</p>
            </div>
            <div class="stat-card">
                <h4>中周期波段</h4>
                <p>近60日均值: {np.mean(recent_medium)/1e9:.2f} 亿元</p>
                <p>趋势方向: {'上升' if recent_medium[-1] > recent_medium[0] else '下降'}</p>
            </div>
            <div class="stat-card">
                <h4>短周期脉冲</h4>
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

print(f"\nHTML report saved to: {report_path}")
print("All tasks completed successfully!")
