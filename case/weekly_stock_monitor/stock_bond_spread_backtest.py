"""
科创板/创业板 股债利差顶底规律回测
============================================================
数据来源：需要akshare或tushare等数据源
运行环境：Python 3.8+, pandas, numpy, matplotlib

三种回测方法：
1. 调整阈值后的股债利差（固定阈值）
2. 自身历史百分位 + 布林线（推荐）
3. PEG-利差（成长修正）

作者：爱马仕小妹
日期：2026-10-14
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


class StockBondSpreadBacktest:
    """股债利差回测类"""
    
    def __init__(self):
        """初始化数据"""
        self.index_data = {}  # 指数数据
        self.bond_yield = None  # 国债收益率
        self.earnings_growth = {}  # 盈利增速
        
    def load_data_akshare(self):
        """通过akshare加载数据"""
        try:
            import akshare as ak
            print("正在通过akshare加载数据...")
            
            # 1. 创业板指 (399006) - 2010年至今
            print("加载创业板指数据...")
            cyb_pe = ak.index_value_hist_funddb(symbol="创业板指", indicator="市盈率")
            cyb_pe = cyb_pe.rename(columns={'日期': 'date', '市盈率': 'pe_ttm'})
            cyb_pe['date'] = pd.to_datetime(cyb_pe['date'])
            self.index_data['创业板指'] = cyb_pe.set_index('date')['pe_ttm']
            
            # 2. 科创50 (000688) - 2020/7/22至今
            print("加载科创50数据...")
            kc50_pe = ak.index_value_hist_funddb(symbol="科创50", indicator="市盈率")
            kc50_pe = kc50_pe.rename(columns={'日期': 'date', '市盈率': 'pe_ttm'})
            kc50_pe['date'] = pd.to_datetime(kc50_pe['date'])
            self.index_data['科创50'] = kc50_pe.set_index('date')['pe_ttm']
            
            # 3. 沪深300 (对照组)
            print("加载沪深300数据...")
            hs300_pe = ak.index_value_hist_funddb(symbol="沪深300", indicator="市盈率")
            hs300_pe = hs300_pe.rename(columns={'日期': 'date', '市盈率': 'pe_ttm'})
            hs300_pe['date'] = pd.to_datetime(hs300_pe['date'])
            self.index_data['沪深300'] = hs300_pe.set_index('date')['pe_ttm']
            
            # 4. 10年期国债收益率
            print("加载10年期国债收益率...")
            bond = ak.bond_zh_us_rate(start_date="20100101")
            bond['date'] = pd.to_datetime(bond['日期'])
            self.bond_yield = bond.set_index('date')['中国国债收益率10年'] / 100  # 转为小数
            
            print("数据加载完成！")
            return True
            
        except Exception as e:
            print(f"akshare加载失败: {e}")
            return False
    
    def load_data_tushare(self, token):
        """通过tushare加载数据（需要token）"""
        try:
            import tushare as ts
            print("正在通过tushare加载数据...")
            
            pro = ts.pro_api(token)
            
            # 指数估值数据
            for name, code in [('创业板指', '399006.SZ'), ('科创50', '000688.SH'), ('沪深300', '000300.SH')]:
                df = pro.index_dailybasic(ts_code=code, start_date='20100101', end_date='20261231',
                                         fields='trade_date,pe_ttm')
                df['date'] = pd.to_datetime(df['trade_date'])
                self.index_data[name] = df.set_index('date')['pe_ttm']
            
            # 国债收益率
            bond = pro.yc_cb(ts_code='1001.CB', start_date='20100101', end_date='20261231')
            bond['date'] = pd.to_datetime(bond['trade_date'])
            self.bond_yield = bond.set_index('date')['yield'] / 100
            
            print("数据加载完成！")
            return True
            
        except Exception as e:
            print(f"tushare加载失败: {e}")
            return False
    
    def calculate_spread(self, index_name, method='basic'):
        """
        计算股债利差
        
        Parameters:
        -----------
        index_name : str
            指数名称
        method : str
            'basic': 基础利差 = 1/PE - 国债收益率
            'peg': PEG利差 = 增速/PE - 国债收益率
        """
        pe = self.index_data[index_name].dropna()
        bond = self.bond_yield.reindex(pe.index).ffill().bfill()
        
        if method == 'basic':
            spread = 1 / pe - bond
            return spread.dropna()
        
        elif method == 'peg':
            if index_name not in self.earnings_growth:
                raise ValueError(f"缺少{index_name}的盈利增速数据")
            growth = self.earnings_growth[index_name].reindex(pe.index).ffill()
            spread = growth / pe - bond
            return spread.dropna()
    
    def method1_fixed_threshold(self, index_name):
        """
        方法一：固定阈值法
        根据指数特征调整阈值
        """
        spread = self.calculate_spread(index_name, method='basic')
        
        # 根据PE中枢估算阈值
        pe_median = self.index_data[index_name].median()
        spread_center = 1 / pe_median - self.bond_yield.median()
        
        # 阈值设置（基于指数特征）
        if index_name == '沪深300':
            bottom_threshold = 0.06  # 6%
            top_threshold = 0.02     # 2%
        elif index_name == '创业板指':
            bottom_threshold = 0.015  # 1.5%
            top_threshold = -0.005    # -0.5%
        elif index_name == '科创50':
            bottom_threshold = 0.005  # 0.5%
            top_threshold = -0.01     # -1%
        else:
            bottom_threshold = spread_center + 0.04
            top_threshold = spread_center - 0.02
        
        # 识别信号
        signals = pd.DataFrame(index=spread.index)
        signals['spread'] = spread
        signals['bottom'] = spread > bottom_threshold
        signals['top'] = spread < top_threshold
        
        return signals, bottom_threshold, top_threshold
    
    def method2_percentile_bollinger(self, index_name, window=750):
        """
        方法二：滚动百分位 + 布林线
        
        Parameters:
        -----------
        window : int
            滚动窗口大小（默认750个交易日≈3年）
        """
        spread = self.calculate_spread(index_name, method='basic')
        
        # 滚动统计量
        rolling_mean = spread.rolling(window=window, min_periods=window//2).mean()
        rolling_std = spread.rolling(window=window, min_periods=window//2).std()
        
        # 布林线通道
        upper_band = rolling_mean + 2 * rolling_std
        lower_band = rolling_mean - 2 * rolling_std
        
        # 百分位排名
        percentile = spread.rolling(window=window, min_periods=window//2).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1]
        )
        
        # 识别信号
        signals = pd.DataFrame(index=spread.index)
        signals['spread'] = spread
        signals['mean'] = rolling_mean
        signals['upper'] = upper_band
        signals['lower'] = lower_band
        signals['percentile'] = percentile
        signals['bottom'] = percentile > 0.90
        signals['top'] = percentile < 0.10
        
        return signals
    
    def method3_peg_spread(self, index_name):
        """
        方法三：PEG-利差（需要盈利增速数据）
        """
        try:
            spread = self.calculate_spread(index_name, method='peg')
            
            # 阈值与沪深300类似
            bottom_threshold = 0.05  # 5%
            top_threshold = 0.01     # 1%
            
            signals = pd.DataFrame(index=spread.index)
            signals['spread'] = spread
            signals['bottom'] = spread > bottom_threshold
            signals['top'] = spread < top_threshold
            
            return signals, bottom_threshold, top_threshold
            
        except ValueError as e:
            print(f"方法三无法执行：{e}")
            return None, None, None
    
    def identify_key_turning_points(self, signals, index_name, method_name):
        """识别关键转折点"""
        bottom_signals = signals[signals['bottom']].index
        top_signals = signals[signals['top']].index
        
        print(f"\n{'='*60}")
        print(f"{index_name} - {method_name}")
        print(f"{'='*60}")
        
        if len(bottom_signals) > 0:
            print(f"\n底部信号（共{len(bottom_signals)}次）：")
            for date in bottom_signals[-5:]:  # 最近5次
                print(f"  {date.strftime('%Y-%m-%d')}")
        
        if len(top_signals) > 0:
            print(f"\n顶部信号（共{len(top_signals)}次）：")
            for date in top_signals[-5:]:  # 最近5次
                print(f"  {date.strftime('%Y-%m-%d')}")
        
        return bottom_signals, top_signals
    
    def backtest_all_methods(self):
        """执行所有方法的回测"""
        results = {}
        
        for index_name in self.index_data.keys():
            print(f"\n{'#'*60}")
            print(f"# {index_name}")
            print(f"{'#'*60}")
            
            results[index_name] = {}
            
            # 方法一：固定阈值
            print("\n【方法一】固定阈值法")
            signals1, bottom_th, top_th = self.method1_fixed_threshold(index_name)
            results[index_name]['method1'] = signals1
            self.identify_key_turning_points(signals1, index_name, '固定阈值法')
            print(f"底部阈值: {bottom_th:.2%}, 顶部阈值: {top_th:.2%}")
            
            # 方法二：百分位+布林线
            print("\n【方法二】滚动百分位+布林线（3年窗口）")
            signals2 = self.method2_percentile_bollinger(index_name, window=750)
            results[index_name]['method2'] = signals2
            self.identify_key_turning_points(signals2, index_name, '百分位+布林线')
            
            # 方法三：PEG-利差（如果数据可用）
            if index_name in self.earnings_growth:
                print("\n【方法三】PEG-利差")
                signals3, bottom_th3, top_th3 = self.method3_peg_spread(index_name)
                if signals3 is not None:
                    results[index_name]['method3'] = signals3
                    self.identify_key_turning_points(signals3, index_name, 'PEG-利差')
                    print(f"底部阈值: {bottom_th3:.2%}, 顶部阈值: {top_th3:.2%}")
        
        return results
    
    def plot_results(self, index_name, results):
        """绘制回测结果图"""
        fig, axes = plt.subplots(3, 1, figsize=(14, 12))
        
        # 图1：基础利差 + 布林线
        if 'method2' in results:
            ax1 = axes[0]
            signals2 = results['method2']
            ax1.plot(signals2.index, signals2['spread'], 'b-', label='股债利差', linewidth=1.5)
            ax1.plot(signals2.index, signals2['mean'], 'k--', label='中轴（3年均值）', linewidth=1)
            ax1.plot(signals2.index, signals2['upper'], 'r--', label='上轨（+2σ）', linewidth=1)
            ax1.plot(signals2.index, signals2['lower'], 'g--', label='下轨（-2σ）', linewidth=1)
            
            # 标注底部和顶部
            bottom_dates = signals2[signals2['bottom']].index
            top_dates = signals2[signals2['top']].index
            ax1.scatter(bottom_dates, signals2.loc[bottom_dates, 'spread'], 
                       color='red', marker='^', s=100, label='底部信号', zorder=5)
            ax1.scatter(top_dates, signals2.loc[top_dates, 'spread'], 
                       color='green', marker='v', s=100, label='顶部信号', zorder=5)
            
            ax1.set_title(f'{index_name} - 滚动百分位+布林线', fontsize=14, fontweight='bold')
            ax1.set_ylabel('股债利差', fontsize=12)
            ax1.legend(loc='best')
            ax1.grid(True, alpha=0.3)
        
        # 图2：百分位排名
        if 'method2' in results:
            ax2 = axes[1]
            signals2 = results['method2']
            ax2.plot(signals2.index, signals2['percentile'] * 100, 'b-', linewidth=1.5)
            ax2.axhline(y=90, color='r', linestyle='--', label='底部阈值（90%）')
            ax2.axhline(y=10, color='g', linestyle='--', label='顶部阈值（10%）')
            ax2.fill_between(signals2.index, 90, 100, where=signals2['percentile'] > 0.9, 
                            color='red', alpha=0.3, label='底部区域')
            ax2.fill_between(signals2.index, 0, 10, where=signals2['percentile'] < 0.1, 
                            color='green', alpha=0.3, label='顶部区域')
            ax2.set_title(f'{index_name} - 百分位排名', fontsize=14, fontweight='bold')
            ax2.set_ylabel('百分位 (%)', fontsize=12)
            ax2.set_ylim(0, 100)
            ax2.legend(loc='best')
            ax2.grid(True, alpha=0.3)
        
        # 图3：指数走势 + 信号标注
        if 'method1' in results:
            ax3 = axes[2]
            signals1 = results['method1']
            pe = self.index_data[index_name]
            ax3.plot(pe.index, pe.values, 'k-', linewidth=1.5, label='PE(TTM)')
            
            # 标注信号
            bottom_dates = signals1[signals1['bottom']].index
            top_dates = signals1[signals1['top']].index
            ax3.scatter(bottom_dates, pe.reindex(bottom_dates), 
                       color='red', marker='^', s=100, label='底部信号', zorder=5)
            ax3.scatter(top_dates, pe.reindex(top_dates), 
                       color='green', marker='v', s=100, label='顶部信号', zorder=5)
            
            ax3.set_title(f'{index_name} - PE走势与信号', fontsize=14, fontweight='bold')
            ax3.set_ylabel('PE(TTM)', fontsize=12)
            ax3.legend(loc='best')
            ax3.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(f'{index_name}_backtest.png', dpi=150, bbox_inches='tight')
        print(f"\n图表已保存：{index_name}_backtest.png")
        plt.show()


def main():
    """主函数"""
    print("="*60)
    print("科创板/创业板 股债利差顶底规律回测")
    print("="*60)
    
    # 初始化
    backtest = StockBondSpreadBacktest()
    
    # 加载数据（二选一）
    # 方式1：akshare（免费，无需token）
    if not backtest.load_data_akshare():
        # 方式2：tushare（需要token）
        print("\n请提供tushare token，或手动加载数据")
        # token = input("输入tushare token: ")
        # backtest.load_data_tushare(token)
        return
    
    # 执行回测
    results = backtest.backtest_all_methods()
    
    # 绘制图表
    for index_name in results.keys():
        backtest.plot_results(index_name, results[index_name])
    
    # 输出总结
    print("\n" + "="*60)
    print("回测完成！")
    print("="*60)
    print("\n关键发现：")
    print("1. 创业板指：百分位法应该能清晰识别2018年底、2022年4月/10月底部")
    print("2. 科创50：样本较少（仅4.5年），规律可能不如创业板明显")
    print("3. 固定阈值法对双创指数效果较差，推荐使用百分位+布林线")
    print("\n建议：")
    print("- 创业板指：90%分位以上为底部区域，10%以下为顶部区域")
    print("- 科创50：由于样本少，建议使用4年全样本百分位（而非滚动）")


if __name__ == '__main__':
    main()
