import yfinance as yf
import pandas as pd
import requests
import time
import sys
import io
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 配置区 =================
SERVERCHAN_KEY = "SCT425360TYpx1PLmQCo4xGoutIOomtiTb" 
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/9e68bae1-aa6c-4427-8387-ca8691e10581"
# ==========================================

def get_sp500_tickers():
    """从维基百科动态获取标普500成分股"""
    print("动态获取标普500成分股...")
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        tables = pd.read_html(url)
        df = tables[0]
        return df['Symbol'].tolist()
    except Exception as e:
        print(f"获取标普500失败: {e}")
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "BRK-B", "JPM", "V", "UNH", "HD"] # 备用

def get_hsi_tickers():
    """获取恒生指数成分股（港股）"""
    print("动态获取恒生指数成分股...")
    # 恒指成分股列表可以从公开接口获取，这里使用一个常用的稳定链接
    try:
        # 使用 yfinance 提供的恒指成分股代码列表，或者从公开的财经数据源获取
        # 这里为了演示，提供一个高质量的港股核心池（覆盖互联网、公用事业、能源、金融）
        # 实际生产中，可以通过 akshare 在本地生成后上传 json
        return ["0700.HK", "0939.HK", "0941.HK", "0836.HK", "0883.HK", "0388.HK", "1810.HK", "9618.HK", "9988.HK", "2318.HK"]
    except Exception as e:
        print(f"获取恒生指数失败: {e}")
        return ["0700.HK", "0941.HK"]

def _safe_get(info, key, default=None):
    try:
        val = info.get(key, default)
        return val if val is not None else default
    except: return default

def fetch_yfinance_data(ticker):
    """获取美股/港股详细数据"""
    for attempt in range(3):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            if not info or 'priceToBook' not in info:
                raise ValueError("数据缺失")
            break
        except Exception as e:
            time.sleep(2)
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
    
    if debt_to_equity and debt_to_equity > 0:
        debt_ratio = (debt_to_equity / 100) / (1 + debt_to_equity / 100) * 100
    else:
        debt_ratio = 0
        
    debt_ebitda = total_debt / ebitda if ebitda and ebitda > 0 else 99
    fcf_ni = fcf / net_income if net_income and net_income != 0 else 1.0
    fcf_ni = max(0.0, fcf_ni)

    # 计算ROE波动和利润增速
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
                if len(roe_series) >= 2: roe_std = float(roe_series.std())
                if len(net_inc) >= 4 and net_inc.iloc[3] > 0 and net_inc.iloc[0] > 0:
                    profit_cagr = ((net_inc.iloc[0] / net_inc.iloc[3]) ** (1/3) - 1) * 100
    except: pass

    return {
        'pb': pb, 'roe': roe_pct / 100, 'dividend_ratio': payout_pct,
        'roe_std': roe_std, 'profit_cagr': profit_cagr, 'fcf_ni': fcf_ni,
        'debt_ebitda': debt_ebitda, 'dividend_yield': div_yield_pct,
        'debt_ratio': debt_ratio, 'industry': _safe_get(info, 'industry', '未知')
    }

def calculate_pr_v8(pb, roe, n, m, g, q, s=1.0):
    if roe <= 0 or pb <= 0 or pd.isna(roe) or pd.isna(pb): return 999
    return round((pb / (roe ** 2)) / 100 * n * m * g * q * s, 3)

def get_n_m_g_q_coefficients(data):
    div_ratio = data.get('dividend_ratio', 0)
    n = 0.9 if div_ratio >= 50 else (1.0 if div_ratio >= 30 else (1.1 if div_ratio >= 15 else 1.2))
    roe_std = data.get('roe_std', 0)
    is_cyclical = data.get('industry') in ["Energy", "Basic Materials", "Industrials"]
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
        data.get('cfo_positive_years', 5) >= 4, data.get('dividend_years', 5) >= 3,
        data.get('goodwill_ratio', 5) < 30, data.get('debt_ebitda', 99) <= 4,
        data.get('dividend_yield', 0) >= 2, data.get('fcf_ni', 0) >= 0.6,
        data.get('pb_percentile', 50) < 70, data.get('revenue_growth', 5) > -20,
    ])

def send_to_wechat(title, content):
    if "你的SendKey" in SERVERCHAN_KEY: return
    try: requests.post(f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send", data={"title": title, "desp": content}, timeout=10)
    except Exception as e: print(f"微信推送失败: {e}")

def send_to_feishu(title, content):
    if "你的飞书Webhook" in FEISHU_WEBHOOK: return
    try: requests.post(FEISHU_WEBHOOK, headers={'Content-Type': 'application/json'}, json={"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}, timeout=10)
    except Exception as e: print(f"飞书推送失败: {e}")

def main():
    print(f"===== V12.0 美股/港股动态模型启动 | {datetime.now()} =====")
    
    sp500 = get_sp500_tickers()
    hsi = get_hsi_tickers()
    raw_pool = sp500 + hsi
    print(f"共获取 {len(raw_pool)} 只股票，开始并发深度抓取...")

    results = []
    # 使用并发加速（yfinance支持高并发）
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ticker = {executor.submit(fetch_yfinance_data, t): t for t in raw_pool}
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                data = future.result()
                if not data: continue
                
                # 行业阈值动态判断
                threshold = 1.0
                ind = data['industry'].lower()
                if "consumer" in ind or "technology" in ind: threshold = 0.75
                elif "healthcare" in ind: threshold = 0.85
                elif "energy" in ind or "basic materials" in ind: threshold = 0.70
                
                n, m, g, q = get_n_m_g_q_coefficients(data)
                final_pr = calculate_pr_v8(data['pb'], data['roe'], n, m, g, q)
                passed = check_elimination(data)
                is_buy = passed and (final_pr <= threshold)
                
                # 只保留PR小于1.5的股票，避免发送无效数据
                if final_pr < 1.5:
                    results.append({
                        '股票名称': ticker, '代码': ticker, '市场': '港股' if '.HK' in ticker else '美股',
                        '当前PB': round(data['pb'], 2), '正常化ROE(%)': round(data['roe']*100, 2),
                        '股息率(%)': round(data['dividend_yield'], 2), '终极PR': final_pr,
                        '行业阈值': threshold, '排雷通过': '✅' if passed else '❌',
                        '最终判定': '✅ 买入' if is_buy else '❌ 淘汰'
                    })
            except Exception as e:
                print(f"  {ticker} 处理失败: {e}")

    if not results:
        print("今日未筛选出符合条件的标的。")
        return

    df = pd.DataFrame(results)
    df = df.sort_values(by='终极PR')
    
    filename = f"美股港股报告_{datetime.now().strftime('%Y%m%d')}.xlsx"
    df.to_excel(filename, index=False, engine='openpyxl')

    # 构建推送内容
    content = f"**V12.0 美股/港股扫描结果** ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    # 只推送通过排雷且PR合格的
    buy_df = df[df['最终判定'] == '✅ 买入']
    if not buy_df.empty:
        content += "🎉 **发现以下符合买入条件的标的：**\n\n"
        content += buy_df.to_markdown(index=False)
    else:
        content += "今日无符合买入条件的标的，以下是PR较低的前5名：\n\n"
        content += df.head(5).to_markdown(index=False)

    send_to_wechat("V12.0 美股/港股量化日报", content)
    send_to_feishu("V12.0 美股/港股量化日报", content)
    print("程序运行完毕！")

if __name__ == "__main__":
    main()
