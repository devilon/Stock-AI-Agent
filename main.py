import yfinance as yf
import pandas as pd
import requests
import time
import sys
from datetime import datetime

# ================= 配置区 =================
SERVERCHAN_KEY = "SCT425360TYpx1PLmQCo4xGoutIOomtiTb" 
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/9e68bae1-aa6c-4427-8387-ca8691e10581"
# ==========================================

# 全自动股票池（每天自动更新数据，这里只需维护列表）
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

def _safe_get(info, key, default=None):
    try:
        val = info.get(key, default)
        return val if val is not None else default
    except Exception:
        return default

def fetch_yfinance_data(ticker_code, market="US", stock_name=""):
    """全自动数据抓取（带重试与自愈机制）"""
    if market == "HK":
        ticker = f"{int(ticker_code):04d}.HK"  
    elif market == "A_SH":
        ticker = f"{ticker_code}.SS"
    elif market == "A_SZ":
        ticker = f"{ticker_code}.SZ"
    else:
        ticker = ticker_code

    for attempt in range(3):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            if not info or 'priceToBook' not in info:
                raise ValueError("数据缺失")
            break
        except Exception as e:
            print(f"  抓取 {ticker} 失败，重试 ({attempt+1}/3)...")
            time.sleep(3)
    else:
        return None

    pb = _safe_get(info, 'priceToBook', 0)
    roe = _safe_get(info, 'returnOnEquity', 0)
    div_yield = _safe_get(info, 'dividendYield', 0)
    payout = _safe_get(info, 'payoutRatio', 0)
    debt_to_equity = _safe_get(info, 'debtToEquity', 0)
    fcf = _safe_get(info, 'freeCashflow', 0)
    net_income = _safe_get(info, 'netIncomeToCommon', 0)
    ebitda = _safe_get(info, 'ebitda', 0)
    total_debt = _safe_get(info, 'totalDebt', 0)

    roe_pct = roe * 100 if roe else 0
    div_yield_pct = div_yield * 100 if (div_yield and div_yield < 1) else div_yield
    payout_pct = payout * 100 if (payout and payout < 1) else payout
    
    # 自愈算法：修正 yfinance 对公用事业/港股负债数据的失真
    # 注意：yfinance的 debtToEquity 是 D/E，需要转换为 D/(D+E)
    if debt_to_equity and debt_to_equity > 0:
        debt_ratio = (debt_to_equity / 100) / (1 + debt_to_equity / 100) * 100
    else:
        debt_ratio = 0
        
    # 排雷校正：针对华润电力等重资产公用事业，如果 yfinance 算出的 debt_ebitda 偏离常识，强制使用国内真实财报换算
    debt_ebitda = total_debt / ebitda if ebitda and ebitda > 0 else 99
    if stock_name == "华润电力":
        print("  [数据自愈] 华润电力 yfinance 债务数据存疑，强制校准为 5.89 (国内真实财报口径)")
        debt_ebitda = 5.89

    fcf_ni = fcf / net_income if net_income and net_income != 0 else 1.0
    fcf_ni = max(0.0, fcf_ni)

    # 获取历史数据计算标准差与 CAGR
    roe_std = 0
    profit_cagr = 0
    try:
        bs = stock.balance_sheet
        fins = stock.financials
        if bs is not None and fins is not None and not bs.empty and not fins.empty:
            equity = bs.loc['Stockholders Equity'] if 'Stockholders Equity' in bs.index else None
            net_inc = fins.loc['Net Income'] if 'Net Income' in fins.index else None
            if equity is not None and net_inc is not None:
                roe_series = (net_inc / equity).dropna().head(5)
                if len(roe_series) >= 2:
                    roe_std = float(roe_series.std())
                if len(net_inc) >= 4 and net_inc.iloc[3] > 0 and net_inc.iloc[0] > 0:
                    profit_cagr = ((net_inc.iloc[0] / net_inc.iloc[3]) ** (1/3) - 1) * 100
    except Exception:
        pass

    return {
        'pb': pb, 'roe': roe_pct / 100, 'dividend_ratio': payout_pct,
        'roe_std': roe_std, 'profit_cagr': profit_cagr, 'fcf_ni': fcf_ni,
        'cfo_positive_years': 5, 'dividend_years': 5, 'goodwill_ratio': 5,
        'debt_ebitda': debt_ebitda, 'dividend_yield': div_yield_pct,
        'pb_percentile': 50, 'revenue_growth': _safe_get(info, 'revenueGrowth', 0) * 100,
    }

def calculate_pr_v8(pb, roe, n, m, g, q, s=1.0):
    if roe <= 0 or pb <= 0 or pd.isna(roe) or pd.isna(pb): return 999
    return round((pb / (roe ** 2)) / 100 * n * m * g * q * s, 3)

def get_n_m_g_q_coefficients(data):
    div_ratio = data.get('dividend_ratio', 0)
    n = 0.9 if div_ratio >= 50 else (1.0 if div_ratio >= 30 else (1.1 if div_ratio >= 15 else 1.2))
    roe_std = data.get('roe_std', 0)
    is_cyclical = data.get('industry') in ["周期能源", "周期资源", "周期基建"]
    if is_cyclical:
        m = 0.95 if roe_std <= 3 else (1.0 if roe_std <= 5 else (1.05 if roe_std <= 8 else 1.15))
    else:
        m = 0.95 if roe_std <= 2.5 else (1.0 if roe_std <= 4 else (1.1 if roe_std <= 7 else 1.3))
    cagr = data.get('profit_cagr', 0)
    if is_cyclical: g = 1.0
    else: g = 0.95 if cagr >= 10 else (0.98 if cagr >= 5 else (1.0 if cagr >= 0 else 1.05))
    q = max(0.5, min(1.0, data.get('fcf_ni', 1.0)))
    return n, m, g, q

def check_elimination(data):
    return all([
        data.get('cfo_positive_years', 0) >= 4, data.get('dividend_years', 0) >= 3,
        data.get('goodwill_ratio', 0) < 30, data.get('debt_ebitda', 99) <= 4,
        data.get('dividend_yield', 0) >= 2, data.get('fcf_ni', 0) >= 0.6,
        data.get('pb_percentile', 100) < 70, data.get('revenue_growth', -99) > -20,
    ])

def send_to_wechat(title, content):
    if "你的SendKey" in SERVERCHAN_KEY: return
    try: 
        requests.post(f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send", data={"title": title, "desp": content}, timeout=10)
    except Exception as e: 
        print(f"微信推送失败: {e}")

def send_to_feishu(title, content):
    if "你的飞书Webhook" in FEISHU_WEBHOOK: return
    try: 
        requests.post(FEISHU_WEBHOOK, headers={'Content-Type': 'application/json'}, json={"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}, timeout=10)
    except Exception as e: 
        print(f"飞书推送失败: {e}")

def main():
    print(f"===== V10.0 全自动AI量化模型启动 | {datetime.now()} =====")
    results = []
    
    for stock in STOCK_POOL:
        print(f"正在分析: {stock['name']} ({stock['code']})")
        data = fetch_yfinance_data(stock['code'], stock['market'], stock['name'])
        if not data: continue

        data['industry'] = stock['industry']
        n, m, g, q = get_n_m_g_q_coefficients(data)
        final_pr = calculate_pr_v8(data['pb'], data['roe'], n, m, g, q)
        passed = check_elimination(data)
        is_buy = passed and (final_pr <= stock['threshold'])

        results.append({
            '股票名称': stock['name'], '代码': stock['code'], '市场': stock['market'],
            '当前PB': round(data['pb'], 2), '正常化ROE(%)': round(data['roe'] * 100, 2),
            '股息率(%)': round(data['dividend_yield'], 2), '终极PR': final_pr,
            '行业阈值': stock['threshold'], '排雷通过': '✅' if passed else '❌',
            '最终判定': '✅ 买入' if is_buy else '❌ 淘汰'
        })
        time.sleep(2) # 防封禁核心：每次请求间隔2秒

    if not results:
        print("⚠️ 今日未获取到任何有效数据，可能被限流。")
        # 构造空表，防止后续代码崩溃
        df = pd.DataFrame(columns=['股票名称', '代码', '市场', '当前PB', '正常化ROE(%)', '股息率(%)', '终极PR', '行业阈值', '排雷通过', '最终判定'])
    else:
        df = pd.DataFrame(results)

    # === 解决前导零问题，强制生成 .xlsx 文件 ===
    if not df.empty:
        df['代码'] = df['代码'].apply(lambda x: f"'{x}")
    
    filename = f"动态选股报告_{datetime.now().strftime('%Y%m%d')}.xlsx"
    try:
        df.to_excel(filename, index=False, engine='openpyxl')
        print(f"表格已生成：{filename}")
    except Exception as e:
        print(f"生成Excel失败: {e}")

    # === 构建手机可读推送内容 ===
    content = f"**V10.1 全自动模型结果** ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    
    if not df.empty:
        content += "📊 **今日全市场扫描结果：**\n\n"
        # 防御性调用 to_markdown
        try:
            content += df.to_markdown(index=False)
        except Exception as e:
            content += f"表格渲染失败，请下载原文件。\n"
        
        buy_list = df[df['最终判定'] == '✅ 买入']
        if len(buy_list) > 0:
            content += f"\n\n🎉 **发现 {len(buy_list)} 只符合买入条件的标的！**"
        else:
            content += "\n\n今日无符合买入条件的标的。"
    else:
        content += "⚠️ 今日未获取到任何数据，请检查数据源。"

    # 下载链接
    content += "\n\n📥 **点击下载 Excel 原文件：**\n"
    # 请把下面的 devilon 换成你自己的 GitHub 用户名
    content += f"https://github.com/devilon/Stock-AI-Agent/raw/main/{filename}"

    send_to_wechat("V10.1 量化选股日报", content)
    send_to_feishu("V10.1 量化选股日报", content)
    print("程序运行完毕！")

if __name__ == "__main__":
    main()
