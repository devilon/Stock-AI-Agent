import akshare as ak
import yfinance as yf
import pandas as pd
import requests
import time
import sys
from datetime import datetime

# ================= 配置区 =================
SERVERCHAN_KEY = "SCT425360TYpx1PLmQCo4xGoutIOomtiTb" # 微信推送
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/9e68bae1-aa6c-4427-8387-ca8691e10581" # 飞书推送

INDEX_POOL = [
    ("000300", "A股", "沪深300", 0.75),
    ("000905", "A股", "中证500", 0.75),
]
# ==========================================

# 股票池：手动维护一批优质标的，每天自动更新数据
STOCK_POOL = [
    # A股（上交所 .SS，深交所 .SZ）
    {"code": "600519", "name": "贵州茅台", "market": "A_SH", "industry": "消费/品牌轻资产", "threshold": 0.75},
    {"code": "000651", "name": "格力电器", "market": "A_SZ", "industry": "消费/品牌轻资产", "threshold": 0.75},
    {"code": "600036", "name": "招商银行", "market": "A_SH", "industry": "银行金融", "threshold": 1.0},
    {"code": "601088", "name": "中国神华", "market": "A_SH", "industry": "周期能源", "threshold": 0.70},
    {"code": "600941", "name": "中国移动", "market": "A_SH", "industry": "港口/电信/公用", "threshold": 1.0},
    # 港股（.HK）
    {"code": "00836", "name": "华润电力", "market": "HK", "industry": "公用事业", "threshold": 1.0},
    {"code": "00883", "name": "中国海洋石油", "market": "HK", "industry": "周期能源", "threshold": 0.70},
    {"code": "00700", "name": "腾讯控股", "market": "HK", "industry": "消费/品牌轻资产", "threshold": 0.75},
    {"code": "00941", "name": "中国移动", "market": "HK", "industry": "港口/电信/公用", "threshold": 1.0},
    {"code": "01186", "name": "中国铁建", "market": "HK", "industry": "周期基建", "threshold": 0.70},
    # 美股
    {"code": "AAPL", "name": "苹果", "market": "US", "industry": "消费/品牌轻资产", "threshold": 0.75},
    {"code": "MSFT", "name": "微软", "market": "US", "industry": "消费/品牌轻资产", "threshold": 0.75},
    {"code": "CVX", "name": "雪佛龙", "market": "US", "industry": "周期能源", "threshold": 0.70},
    {"code": "PFE", "name": "辉瑞", "market": "US", "industry": "医药制造", "threshold": 0.85},
    {"code": "BAC", "name": "美国银行", "market": "US", "industry": "银行金融", "threshold": 1.0},
]


def main():
    print(f"===== V9.3 yfinance真实数据版 | {datetime.now()} =====")

    results = []
    for stock in STOCK_POOL:
        print(f"正在分析: {stock['name']} ({stock['code']})")
        try:
            data = fetch_yfinance_data(stock['code'], stock['market'])
            if not data:
                print(f"  ⚠️ {stock['name']} 数据获取失败，跳过")
                continue

            data['industry'] = stock['industry']
            n, m, g, q = get_n_m_g_q_coefficients(data)
            final_pr = calculate_pr_v8(data['pb'], data['roe'], n, m, g, q)
            passed = check_elimination(data)
            threshold = stock['threshold']
            is_buy = passed and (final_pr <= threshold)

            results.append({
                '股票名称': stock['name'],
                '代码': stock['code'],
                '市场': stock['market'],
                '当前PB': round(data['pb'], 2),
                '正常化ROE(%)': round(data['roe'] * 100, 2),
                '股息率(%)': round(data['dividend_yield'], 2),
                '终极PR': final_pr,
                '行业阈值': threshold,
                '排雷通过': '✅' if passed else '❌',
                '最终判定': '✅ 买入' if is_buy else '❌ 淘汰'
            })
            print(f"  PB={data['pb']:.2f}, ROE={data['roe']*100:.1f}%, PR={final_pr}, 判定={'买入' if is_buy else '淘汰'}")
            time.sleep(1.5)  # 关键：防止yfinance限流

        except Exception as e:
            print(f"  ❌ {stock['name']} 处理出错: {e}")
            continue

    # 生成表格与推送（保持原有逻辑）
    df = pd.DataFrame(results)
    columns = ['股票名称', '代码', '市场', '当前PB', '正常化ROE(%)', '股息率(%)', '终极PR', '行业阈值', '排雷通过', '最终判定']
    if df.empty:
        df = pd.DataFrame(columns=columns)
    else:
        df = df.reindex(columns=columns)

    filename = f"动态选股报告_{datetime.now().strftime('%Y%m%d')}.csv"
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"\n表格已生成：{filename}")
    print(df.to_string())

    # 推送逻辑保持不变...

def _safe_get(info, key, default=None):
    """安全获取info字段，避免直接报错"""
    try:
        val = info.get(key, default)
        return val if val is not None else default
    except Exception:
        return default

def fetch_yfinance_data(ticker_code, market="US"):
    """
    通用数据获取函数，支持美股/港股/A股
    ticker_code: 纯代码，如 'AAPL', '00836', '000651'
    market: 'US', 'HK', 'A_SH', 'A_SZ'
    """
    # 1. 代码格式转换
    if market == "HK":
        ticker = f"{int(ticker_code):04d}.HK"  # 港股补足4位
    elif market == "A_SH":
        ticker = f"{ticker_code}.SS"
    elif market == "A_SZ":
        ticker = f"{ticker_code}.SZ"
    else:
        ticker = ticker_code

    print(f"  正在抓取 {ticker} ...")
    stock = yf.Ticker(ticker)
    info = stock.info

    # 2. 基础字段获取（带容错）
    pb = _safe_get(info, 'priceToBook', 0)
    roe = _safe_get(info, 'returnOnEquity', 0)  # 小数形式，0.15 = 15%
    div_yield = _safe_get(info, 'dividendYield', 0)
    payout = _safe_get(info, 'payoutRatio', 0)
    debt_to_equity = _safe_get(info, 'debtToEquity', 0)
    fcf = _safe_get(info, 'freeCashflow', 0)
    net_income = _safe_get(info, 'netIncomeToCommon', 0)
    ebitda = _safe_get(info, 'ebitda', 0)
    total_debt = _safe_get(info, 'totalDebt', 0)

    # 3. 数据清洗与单位统一
    # ROE: yfinance 返回小数，转为百分比
    roe_pct = roe * 100 if roe else 0
    # 股息率: yfinance 可能返回小数(0.03)或百分比(3.0)，统一为百分比
    div_yield_pct = div_yield * 100 if (div_yield and div_yield < 1) else div_yield
    # 分红率: 同上
    payout_pct = payout * 100 if (payout and payout < 1) else payout
    # 资产负债率: yfinance 返回的是比率，转为百分比
    # 但注意：debtToEquity 是"负债/权益"，需要换算为"负债/总资产"
    if debt_to_equity and debt_to_equity > 0:
        # 负债/权益 = D/E，总资产 = D + E，则 负债率 = D/(D+E) = (D/E) / (1 + D/E)
        debt_ratio = (debt_to_equity / 100) / (1 + debt_to_equity / 100) * 100
    else:
        debt_ratio = 0

    # 4. FCF/NI 计算（避免除零）
    fcf_ni = fcf / net_income if net_income and net_income != 0 else 1.0
    # 防止负值，Q系数最低取0.5
    fcf_ni = max(0.0, fcf_ni)

    # 5. 有息负债/EBITDA
    debt_ebitda = total_debt / ebitda if ebitda and ebitda > 0 else 99

    # 6. 计算5年ROE标准差（需要历史数据）
    roe_std = _calc_roe_std(stock)

    # 7. 获取3年利润CAGR
    profit_cagr = _calc_profit_cagr(stock)

    return {
        'pb': pb,
        'roe': roe_pct / 100,  # 保持小数形式，用于公式计算
        'dividend_ratio': payout_pct,
        'roe_std': roe_std,
        'profit_cagr': profit_cagr,
        'fcf_ni': fcf_ni,
        'cfo_positive_years': 5,  # 简化：默认为5，后续可升级
        'dividend_years': 5,      # 简化：默认为5，后续可升级
        'goodwill_ratio': 5,      # 简化：yfinance不直接提供，后续可从balance_sheet计算
        'debt_ebitda': debt_ebitda,
        'dividend_yield': div_yield_pct,
        'pb_percentile': 50,      # 简化：yfinance不直接提供历史PB分位
        'revenue_growth': _safe_get(info, 'revenueGrowth', 0) * 100,
        'debt_ratio': debt_ratio,
    }


def _calc_roe_std(stock, years=5):
    """计算近5年ROE标准差"""
    try:
        financials = stock.financials
        if financials is None or financials.empty:
            return 0
        net_income = financials.loc['Net Income'] if 'Net Income' in financials.index else None
        # 从balance_sheet获取股东权益
        bs = stock.balance_sheet
        if bs is None or bs.empty:
            return 0
        equity = bs.loc['Stockholders Equity'] if 'Stockholders Equity' in bs.index else None
        if net_income is None or equity is None:
            return 0
        # 取最近years年的数据
        roe_series = (net_income / equity).dropna().head(years)
        if len(roe_series) < 2:
            return 0
        return float(roe_series.std())
    except Exception:
        return 0


def _calc_profit_cagr(stock, years=3):
    """计算近3年净利润复合增速"""
    try:
        financials = stock.financials
        if financials is None or financials.empty:
            return 0
        net_income = financials.loc['Net Income'] if 'Net Income' in financials.index else None
        if net_income is None or len(net_income) < years + 1:
            return 0
        latest = net_income.iloc[0]
        earliest = net_income.iloc[years]
        if earliest and earliest > 0 and latest and latest > 0:
            cagr = ((latest / earliest) ** (1 / years) - 1) * 100
            return round(cagr, 2)
        return 0
    except Exception:
        return 0
def calculate_pr_v8(pb, roe, n, m, g, q, s=1.0):
    try:
        if roe <= 0 or pb <= 0 or pd.isna(roe) or pd.isna(pb):
            return 999
        base_pr = (pb / (roe ** 2)) / 100
        return round(base_pr * n * m * g * q * s, 3)
    except Exception:
        return 999


def get_n_m_g_q_coefficients(data):
    try:
        div_ratio = data.get('dividend_ratio', 0)
        n = 0.9 if div_ratio >= 50 else (1.0 if div_ratio >= 30 else (1.1 if div_ratio >= 15 else 1.2))
        
        roe_std = data.get('roe_std', 0)
        is_cyclical = data.get('industry') in ["周期能源", "周期资源", "周期基建"]
        if is_cyclical:
            m = 0.95 if roe_std <= 3 else (1.0 if roe_std <= 5 else (1.05 if roe_std <= 8 else 1.15))
        else:
            m = 0.95 if roe_std <= 2.5 else (1.0 if roe_std <= 4 else (1.1 if roe_std <= 7 else 1.3))
            
        cagr = data.get('profit_cagr', 0)
        if is_cyclical:
            g = 1.0
        else:
            g = 0.95 if cagr >= 10 else (0.98 if cagr >= 5 else (1.0 if cagr >= 0 else 1.05))
            
        fcf_ni = data.get('fcf_ni', 1.0)
        q = max(0.5, min(1.0, fcf_ni))
        return n, m, g, q
    except Exception:
        return 1.0, 1.0, 1.0, 1.0

def check_elimination(data):
    try:
        return all([
            data.get('cfo_positive_years', 0) >= 4,
            data.get('dividend_years', 0) >= 3,
            data.get('goodwill_ratio', 0) < 30,
            data.get('debt_ebitda', 99) <= 4,
            data.get('dividend_yield', 0) >= 2,
            data.get('fcf_ni', 0) >= 0.6,
            data.get('pb_percentile', 100) < 70,
            data.get('revenue_growth', -99) > -20,
        ])
    except Exception:
        return False

def send_to_wechat(title, content):
    if "你的SendKey" in SERVERCHAN_KEY: 
        print("微信推送Key未配置，跳过微信推送")
        return
    try:
        requests.post(f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send", data={"title": title, "desp": content}, timeout=10)
    except Exception as e:
        print(f"微信推送失败: {e}")

def send_to_feishu(title, content):
    if "你的飞书Webhook" in FEISHU_WEBHOOK: 
        print("飞书Webhook未配置，跳过飞书推送")
        return
    try:
        payload = {"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}
        requests.post(FEISHU_WEBHOOK, headers={'Content-Type': 'application/json'}, json=payload, timeout=10)
    except Exception as e:
        print(f"飞书推送失败: {e}")

def main():
    print(f"===== V9.2 隔离测试模型启动 | {datetime.now()} =====")
    
    # 强制使用静态测试池，彻底跳过抓取A股指数
    dynamic_pool = [
        {"code": "000651", "name": "格力电器", "market": "A股", "industry": "消费/品牌轻资产"},
        {"code": "600036", "name": "招商银行", "market": "A股", "industry": "银行金融"},
        {"code": "00836", "name": "华润电力", "market": "港股", "industry": "公用事业"},
    ]
    
    sample_pool = dynamic_pool
    results = []
    
    for stock in sample_pool:
        print(f"正在分析: {stock['name']} ({stock['code']})")
        
        # 注入模拟的财务数据，让流程能跑通
        data = {
            'pb': 1.44 if stock['name'] == '格力电器' else (0.84 if stock['name'] == '招商银行' else 0.80),
            'roe': 0.18 if stock['name'] == '格力电器' else (0.13 if stock['name'] == '招商银行' else 0.0977),
            'dividend_ratio': 50,
            'roe_std': 3,
            'profit_cagr': 6,
            'fcf_ni': 1.0,
            'cfo_positive_years': 5,
            'dividend_years': 5,
            'goodwill_ratio': 5,
            'debt_ebitda': 1.5 if stock['name'] == '格力电器' else 3.0,
            'dividend_yield': 8.0 if stock['name'] == '格力电器' else 5.2,
            'pb_percentile': 10,
            'revenue_growth': 5,
        }
        
        data['industry'] = stock['industry']
        n, m, g, q = get_n_m_g_q_coefficients(data)
        final_pr = calculate_pr_v8(data['pb'], data['roe'], n, m, g, q)
        passed = check_elimination(data)
        
        threshold = 0.75 if "消费" in stock['industry'] else (1.0 if "银行" in stock['industry'] or "公用" in stock['industry'] else 0.70)
        is_buy = passed and (final_pr <= threshold)
        
        results.append({
            '股票名称': stock['name'], '代码': stock['code'], '市场': stock['market'],
            '当前PB': round(data['pb'], 2), '正常化ROE(%)': round(data['roe'] * 100, 2),
            '终极PR': final_pr, '行业阈值': threshold,
            '排雷通过': '✅' if passed else '❌', '最终判定': '✅ 买入' if is_buy else '❌ 淘汰'
        })
        time.sleep(1.5)

    df = pd.DataFrame(results)
    
    # 防弹补丁
    columns = ['股票名称', '代码', '市场', '当前PB', '正常化ROE(%)', '终极PR', '行业阈值', '排雷通过', '最终判定']
    if df.empty:
        df = pd.DataFrame(columns=columns) 
    else:
        df = df.reindex(columns=columns)

    filename = f"动态选股报告_{datetime.now().strftime('%Y%m%d')}.csv"
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"表格已生成：{filename}")
    
    # 构建推送内容
    buy_list = df[df['最终判定'] == '✅ 买入']
    content = f"**V9.2 隔离测试结果** ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    if len(buy_list) > 0:
        content += "🎉 **发现以下标的符合买入条件：**\n\n"
        for _, row in buy_list.iterrows():
            content += f"- {row['股票名称']} ({row['代码']}) | PR: {row['终极PR']} | 阈值: {row['行业阈值']}\n"
    else:
        content += "今日无符合买入条件的标的。\n"
    content += "\n完整表格请查看 GitHub Actions 的 Artifacts 下载。"

    send_to_wechat("V9.2 量化选股日报", content)
    send_to_feishu("V9.2 量化选股日报", content)
    print("程序运行完毕！")


if __name__ == "__main__":
    main()
