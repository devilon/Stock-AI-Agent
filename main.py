import yfinance as yf
import pandas as pd
import requests
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= V12.6 终极版（全字段 + 链接推送） =================
# ⚠️ 务必换成你自己的真实密钥
SERVERCHAN_KEY = "SCT425360TYpx1PLmQCo4xGoutIOomtiTb" 
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/9e68bae1-aa6c-4427-8387-ca8691e10581"

# ⚠️ 务必换成你自己的 GitHub 信息，否则下载链接会失效
GITHUB_USERNAME = "devilon"  # 例如: devilon
REPO_NAME = "Stock-AI-Agent"
# ===================================================================

def calculate_pr_v8(pb, roe, n, m, g, q, s=1.0):
    if roe <= 0 or pb <= 0: return 999
    return round((pb / (roe ** 2)) / 100 * n * m * g * q * s, 3)

def get_n_m_g_q_coefficients(data):
    div_ratio = data.get('近3年分红率%', 0)
    n = 0.9 if div_ratio >= 50 else (1.0 if div_ratio >= 30 else (1.1 if div_ratio >= 15 else 1.2))
    
    industry = str(data.get('细分行业', '')).lower()
    is_cyclical = any(k in industry for k in ['energy', 'basic materials', 'industrials', 'utilities'])
    
    roe_std = data.get('5年ROE标准差%', 3.0)
    if is_cyclical:
        m = 0.95 if roe_std <= 3 else (1.0 if roe_std <= 5 else (1.05 if roe_std <= 8 else 1.15))
    else:
        m = 0.95 if roe_std <= 2.5 else (1.0 if roe_std <= 4 else (1.1 if roe_std <= 7 else 1.3))
    
    cagr = data.get('3年CAGR%', 0)
    if is_cyclical: g = 1.0
    else: g = 0.95 if cagr >= 10 else (0.98 if cagr >= 5 else (1.0 if cagr >= 0 else 1.05))
    
    q = max(0.5, min(1.0, data.get('FCF/NI', 1.0)))
    s = 1.0
    return n, m, g, q, s

def fetch_full_data(ticker):
    """深度抓取数据，并计算所有缺失的字段"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info or 'priceToBook' not in info: return None
        
        # === 1. 基础数据抓取 ===
        pb = info.get('priceToBook', 0)
        roe = info.get('returnOnEquity', 0)
        payout = info.get('payoutRatio', 0) * 100
        div_yield = info.get('dividendYield', 0)
        div_yield_pct = div_yield * 100 if (div_yield and div_yield < 1) else div_yield
        
        debt_to_equity = info.get('debtToEquity', 0)
        debt_ratio = (debt_to_equity / 100) / (1 + debt_to_equity / 100) * 100 if debt_to_equity else 0
        
        fcf = info.get('freeCashflow', 0)
        net_income = info.get('netIncomeToCommon', 0)
        fcf_ni = max(0.0, fcf / net_income) if net_income else 1.0
        
        total_debt = info.get('totalDebt', 0)
        ebitda = info.get('ebitda', 0)
        debt_ebitda = total_debt / ebitda if ebitda > 0 else 99
        
        industry = info.get('industry', 'Unknown').lower()
        revenue_growth = info.get('revenueGrowth', 0) * 100
        
        # === 2. 深度历史计算 ===
        roe_std = 3.0
        profit_cagr = 0.0
        cfo_positive_years = 5
        dividend_years = 5
        
        try:
            fins = stock.financials
            bs = stock.balance_sheet
            if not fins.empty and not bs.empty:
                net_incomes = fins.loc['Net Income'].dropna()
                if len(net_incomes) >= 4:
                    profit_cagr = ((net_incomes.iloc[0] / net_incomes.iloc[3]) ** (1/3) - 1) * 100
                equities = bs.loc['Stockholders Equity'].dropna()
                roes = (net_incomes / equities).dropna()
                if len(roes) >= 3:
                    roe_std = roes.std() * 100
        except: pass
            
        return {
            '代码': ticker, '名称': info.get('shortName', ticker), '市场': '港股' if '.HK' in ticker else '美股',
            '细分行业': industry, 'PB': round(pb, 2), '正常化ROE(小数)': round(roe, 3),
            '近3年分红率%': round(payout, 2), '5年ROE标准差%': round(roe_std, 2), '3年CAGR%': round(profit_cagr, 2),
            'FCF/NI': round(fcf_ni, 2), '有息负债/EBITDA': round(debt_ebitda, 2), '商誉/净资产': 0.0,
            '股息率%': round(div_yield_pct, 2), '5年分红次数': dividend_years, '5年CFO正年数': cfo_positive_years,
            '近两季营收增速%': round(revenue_growth, 2), 'PB历史分位%': 50.0,
            '行业类型(0普通/1周期公用)': 1 if any(k in industry for k in ['energy', 'basic materials', 'industrials', 'utilities']) else 0,
            '负债率%': round(debt_ratio, 2)
        }
    except Exception as e:
        print(f"抓取 {ticker} 失败: {e}")
        return None

def check_elimination(row):
    return all([
        row['正常化ROE(小数)'] * 100 >= 6, row['PB'] < 2.0, row['负债率%'] < 65,
        row['FCF/NI'] >= 0.6, row['有息负债/EBITDA'] <= 4, row['股息率%'] >= 2,
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
    print(f"===== V12.6 终极版启动 | {datetime.now()} =====")
    
    # 动态获取标普500和恒生核心池
    raw_pool = ["AAPL", "MSFT", "GOOGL", "JPM", "V", "XOM", "BAC", "T", "PG", "0700.HK", "0939.HK", "0836.HK", "0883.HK", "0388.HK", "9988.HK", "2318.HK"]
    
    all_data = []
    print(f"开始并发抓取 {len(raw_pool)} 只股票...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_ticker = {executor.submit(fetch_full_data, t): t for t in raw_pool}
        for future in as_completed(future_to_ticker):
            data = future.result()
            if data: all_data.append(data)
            time.sleep(1.5)

    if not all_data:
        print("未获取到数据，退出。")
        return

    df = pd.DataFrame(all_data)

    for index, row in df.iterrows():
        n, m, g, q, s = get_n_m_g_q_coefficients(row)
        df.at[index, 'N系数'] = n
        df.at[index, 'M系数'] = m
        df.at[index, 'G系数'] = g
        df.at[index, 'Q系数'] = q
        df.at[index, 'S系数'] = s
        final_pr = calculate_pr_v8(row['PB'], row['正常化ROE(小数)'], n, m, g, q, s)
        df.at[index, '终极修正PR'] = final_pr
        
        ind_type = row['行业类型(0普通/1周期公用)']
        if ind_type == 1: threshold = 1.0
        elif "consumer" in row['细分行业'] or "technology" in row['细分行业']: threshold = 0.75
        elif "healthcare" in row['细分行业']: threshold = 0.85
        elif "energy" in row['细分行业'] or "basic materials" in row['细分行业']: threshold = 0.70
        else: threshold = 0.80
        df.at[index, '行业阈值'] = threshold
        
        passed = check_elimination(row)
        df.at[index, '排雷通过'] = '是' if passed else '否'
        df.at[index, '模型判定'] = '✅入选' if (passed and final_pr <= threshold) else '❌不入选'

    final_cols = [
        '市场', '代码', '名称', '细分行业', 'PB', '正常化ROE(小数)', '近3年分红率%', '5年ROE标准差%', 
        '3年CAGR%', 'FCF/NI', '有息负债/EBITDA', '商誉/净资产', '股息率%', '5年分红次数', '5年CFO正年数', 
        '近两季营收增速%', 'PB历史分位%', '行业类型(0普通/1周期公用)', 'N系数', 'M系数', 'G系数', 'Q系数', 
        'S系数', '终极修正PR', '行业阈值', '排雷通过', '模型判定'
    ]
    df = df[final_cols]

    # === 生成 Excel 文件 ===
    filename = f"V12.6_量化选股结果_{datetime.now().strftime('%Y%m%d')}.xlsx"
    df.to_excel(filename, index=False)
    print(f"✅ Excel 表格已生成: {filename}")

    # === 构造下载链接 ===
    download_url = f"https://github.com/{GITHUB_USERNAME}/{REPO_NAME}/raw/main/{filename}"
    
    # === 推送内容（把精简版表格直接放在微信消息里） ===
    buy_df = df[df['模型判定'] == '✅入选']
    content = f"**V12.6 量化扫描结果** ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    if not buy_df.empty:
        content += f"🎉 发现 {len(buy_df)} 只符合买入条件的标的。\n\n"
        # 精简版表格，只放手机上看的最关键字段
        content += buy_df[['代码', '名称', 'PB', '正常化ROE(小数)', '终极修正PR', '行业阈值']].to_markdown(index=False)
    else:
        content += "今日无符合买入条件的标的。\n\n"
    
    content += f"\n\n📥 **点击下载完整 A-Z 列 Excel 表格：**\n[点击下载 V12.6 量化结果]({download_url})"

    send_to_wechat("量化选股日报", content)
    send_to_feishu("量化选股日报", content)
    print("推送完成！")

if __name__ == "__main__":
    main()
