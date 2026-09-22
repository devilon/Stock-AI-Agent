import yfinance as yf
import pandas as pd
import requests
import time
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 配置区 =================
SERVERCHAN_KEY = "SCT425360TYpx1PLmQCo4xGoutIOomtiTb" 
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/9e68bae1-aa6c-4427-8387-ca8691e10581"
# ==========================================

def get_sp500_tickers():
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        tables = pd.read_html(url)
        return tables[0]['Symbol'].tolist()
    except Exception as e:
        print(f"获取标普500失败: {e}")
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "BRK-B", "JPM", "V"]

def get_hsi_tickers():
    # 恒生指数核心池（动态抓取容易失败，这里使用代表性核心股）
    return ["0700.HK", "0939.HK", "0941.HK", "0836.HK", "0883.HK", "0388.HK", "1810.HK", "9618.HK", "9988.HK", "2318.HK"]

def _safe_get(info, key, default=None):
    try:
        val = info.get(key, default)
        return val if val is not None else default
    except: return default

def fetch_yfinance_data(ticker):
    for attempt in range(3):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            if not info or 'priceToBook' not in info: raise ValueError("数据缺失")
            break
        except: time.sleep(2)
    else: return None

    pb = _safe_get(info, 'priceToBook', 0)
    roe = _safe_get(info, 'returnOnEquity', 0)
    div_yield = _safe_get(info, 'dividendYield', 0)
    payout = _safe_get(info, 'payoutRatio', 0)
    debt_to_equity = _safe_get(info, 'debtToEquity', 0)
    fcf = _safe_get(info, 'freeCashflow', 0)
    net_income = _safe_get(info, 'netIncomeToCommon', 0)
    total_debt = _safe_get(info, 'totalDebt', 0)
    ebitda = _safe_get(info, 'ebitda', 0)

    roe_pct = roe * 100 if roe else 0
    div_yield_pct = div_yield * 100 if (div_yield and div_yield < 1) else div_yield
    payout_pct = payout * 100 if (payout and payout < 1) else payout
    
    debt_ratio = 0
    if debt_to_equity and debt_to_equity > 0:
        debt_ratio = (debt_to_equity / 100) / (1 + debt_to_equity / 100) * 100
        
    debt_ebitda = total_debt / ebitda if ebitda and ebitda > 0 else 99
    fcf_ni = fcf / net_income if net_income and net_income != 0 else 1.0
    fcf_ni = max(0.0, fcf_ni)

    return {
        'pb': pb, 'roe': roe_pct / 100, 'dividend_ratio': payout_pct,
        'fcf_ni': fcf_ni, 'debt_ebitda': debt_ebitda, 'dividend_yield': div_yield_pct,
        'debt_ratio': debt_ratio, 'industry': _safe_get(info, 'industry', 'Unknown').lower()
    }

def get_n_m_g_q_coefficients(data):
    div_ratio = data.get('dividend_ratio', 0)
    n = 0.9 if div_ratio >= 50 else (1.0 if div_ratio >= 30 else (1.1 if div_ratio >= 15 else 1.2))
    
    industry = data.get('industry', '')
    is_cyclical = any(k in industry for k in ['energy', 'basic materials', 'industrials', 'utilities'])
    
    roe_std = 3.0 # 默认值（yfinance难以直接获取5年标准差，可用预估）
    m = 0.95 if roe_std <= 3 else 1.0 # 简化处理
    g = 1.0 if is_cyclical else 1.0 # 默认中性
    q = max(0.5, min(1.0, data.get('fcf_ni', 1.0)))
    return n, m, g, q

def check_elimination(data):
    return all([
        data.get('roe', 0) * 100 >= 6,
        data.get('pb', 99) < 2.0,
        data.get('debt_ratio', 99) < 65,
        data.get('fcf_ni', 0) >= 0.6,
        data.get('debt_ebitda', 99) <= 4,
        data.get('dividend_yield', 0) >= 2,
    ])

def send_to_wechat(title, content):
    if "你的SendKey" in SERVERCHAN_KEY: return
    try: requests.post(f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send", data={"title": title, "desp": content}, timeout=10)
    except: pass

def send_to_feishu(title, content):
    if "你的飞书Webhook" in FEISHU_WEBHOOK: return
    try: requests.post(FEISHU_WEBHOOK, headers={'Content-Type': 'application/json'}, json={"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}, timeout=10)
    except: pass

def main():
    print(f"===== V7.2 美股/港股动态模型启动 | {datetime.now()} =====")
    raw_pool = get_sp500_tickers() + get_hsi_tickers()
    
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ticker = {executor.submit(fetch_yfinance_data, t): t for t in raw_pool}
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                data = future.result()
                if not data: continue
                
                threshold = 1.0
                ind = data['industry']
                if "consumer" in ind or "technology" in ind: threshold = 0.75
                elif "healthcare" in ind: threshold = 0.85
                elif "energy" in ind or "basic materials" in ind: threshold = 0.70
                
                n, m, g, q = get_n_m_g_q_coefficients(data)
                final_pr = calculate_pr_v8(data['pb'], data['roe'], n, m, g, q)
                passed = check_elimination(data)
                is_buy = passed and (final_pr <= threshold)
                
                if final_pr < 1.5:
                    results.append({
                        '股票名称': ticker, '代码': ticker, '市场': '港股' if '.HK' in ticker else '美股',
                        '当前PB': round(data['pb'], 2), '正常化ROE(%)': round(data['roe']*100, 2),
                        '股息率(%)': round(data['dividend_yield'], 2), '终极PR': final_pr,
                        '行业阈值': threshold, '排雷通过': '✅' if passed else '❌',
                        '最终判定': '✅ 买入' if is_buy else '❌ 淘汰'
                    })
            except: continue

    if not results: return
    df = pd.DataFrame(results)
    df = df.sort_values(by='终极PR')
    
    content = f"**V7.2 美股/港股扫描结果** ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    buy_df = df[df['最终判定'] == '✅ 买入']
    if not buy_df.empty:
        content += "🎉 **发现以下标的符合买入条件：**\n\n" + buy_df.to_markdown(index=False)
    else:
        content += "今日无符合买入条件的标的，以下是PR较低的前5名：\n\n" + df.head(5).to_markdown(index=False)

    send_to_wechat("美股/港股量化日报", content)
    send_to_feishu("美股/港股量化日报", content)
    print("推送完成！")

if __name__ == "__main__":
    main()
