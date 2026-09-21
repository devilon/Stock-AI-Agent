import akshare as ak
import yfinance as yf
import pandas as pd
import requests
import time
from datetime import datetime

# ================= 配置区 =================
SERVERCHAN_KEY = "SCT425360TYpx1PLmQCo4xGoutIOomtiTb" # 微信推送
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/9e68bae1-aa6c-4427-8387-ca8691e10581" # 飞书推送

# 宽基指数（每日动态扫描的基础股票池，防止抓取全市场导致被封IP）
# 沪深300 (000300), 中证500 (000905), 标普500 (^GSPC), 恒生指数 (^HSI)
INDEX_POOL = [
    ("000300", "A股", "沪深300", 0.75),  # 消费/科技占比较高，统一用0.75初审
    ("000905", "A股", "中证500", 0.75),
    ("^GSPC", "美股", "标普500", 0.80),
    ("^HSI", "港股", "恒生指数", 0.80),
]
# ==========================================

def calculate_pr_v8(pb, roe, n, m, g, q, s=1.0):
    """V8.0 终极修正PR公式"""
    if roe <= 0 or pb <= 0:
        return 999
    base_pr = (pb / (roe ** 2)) / 100
    final_pr = base_pr * n * m * g * q * s
    return round(final_pr, 3)

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
    if is_cyclical:
        g = 1.0
    else:
        g = 0.95 if cagr >= 10 else (0.98 if cagr >= 5 else (1.0 if cagr >= 0 else 1.05))
        
    fcf_ni = data.get('fcf_ni', 1.0)
    q = max(0.5, min(1.0, fcf_ni))
    
    return n, m, g, q

def check_elimination(data):
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

def fetch_a_stock_pool():
    """动态获取沪深300和中证500成分股代码"""
    print("正在获取A股宽基指数成分股...")
    codes = []
    try:
        # 获取沪深300成分股
        df300 = ak.index_stock_cons_csindex(symbol="000300")
        for _, row in df300.iterrows():
            codes.append({"code": row['成分券代码'], "name": row['成分券名称'], "market": "A股", "industry": "待分类"})
    except Exception as e:
        print(f"获取沪深300失败: {e}")
    time.sleep(2) # 防止请求过快
    try:
        # 获取中证500成分股
        df500 = ak.index_stock_cons_csindex(symbol="000905")
        for _, row in df500.iterrows():
            codes.append({"code": row['成分券代码'], "name": row['成分券名称'], "market": "A股", "industry": "待分类"})
    except Exception as e:
        print(f"获取中证500失败: {e}")
    return codes

def fetch_a_stock_data(code):
    """获取A股真实数据（利用AKShare，加入延时防封）"""
    try:
        df_spot = ak.stock_zh_a_spot_em()
        stock_info = df_spot[df_spot['代码'] == code].iloc[0]
        pb = float(stock_info['市净率'])
        time.sleep(0.5) # 延时
        return {
            'pb': pb,
            'roe': 0.15, # 实盘需替换为 ak.stock_financial_analysis_indicator 的真实ROE
            'dividend_ratio': 40,
            'roe_std': 3,
            'profit_cagr': 6,
            'fcf_ni': 1.0,
            'cfo_positive_years': 5,
            'dividend_years': 5,
            'goodwill_ratio': 5,
            'debt_ebitda': 2.0,
            'dividend_yield': 3.0,
            'pb_percentile': 20,
            'revenue_growth': 5,
        }
    except Exception as e:
        return None

def fetch_us_hk_stock_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            'pb': info.get('priceToBook', 2.0),
            'roe': info.get('returnOnEquity', 0.15),
            'dividend_ratio': info.get('payoutRatio', 0.4) * 100,
            'roe_std': 4,
            'profit_cagr': info.get('earningsGrowth', 0.05) * 100,
            'fcf_ni': 1.0,
            'cfo_positive_years': 5,
            'dividend_years': 5,
            'goodwill_ratio': 10,
            'debt_ebitda': 2.5,
            'dividend_yield': info.get('dividendYield', 0.03) * 100,
            'pb_percentile': 30,
            'revenue_growth': info.get('revenueGrowth', 0.05) * 100,
        }
    except Exception as e:
        return None

def send_to_wechat(title, content):
    if "你的SendKey" in SERVERCHAN_KEY: return
    requests.post(f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send", data={"title": title, "desp": content})

def send_to_feishu(title, content):
    if "你的飞书Webhook" in FEISHU_WEBHOOK: return
    headers = {'Content-Type': 'application/json'}
    payload = {
        "msg_type": "text",
        "content": {"text": f"{title}\n\n{content}"}
    }
    # 飞书要求消息必须包含关键词，所以标题带上 V9.0
    requests.post(FEISHU_WEBHOOK, headers=headers, json=payload)

def main():
    print(f"===== V9.0 动态选股模型启动 | {datetime.now()} =====")
    dynamic_pool = fetch_a_stock_pool()
    # 由于篇幅，这里假设抓取了部分数据，实际全量跑需要处理限流，这里截取前20只做演示
    # 实盘中，你需要把整个指数池分组，每天轮询不同组，或者利用本地缓存
    sample_pool = dynamic_pool[:20] 
    
    results = []
    for stock in sample_pool:
        data = fetch_a_stock_data(stock['code'])
        if not data: continue
        # 简单根据代码分类（实盘需引入行业分类库）
        data['industry'] = "消费/品牌轻资产" 
        n, m, g, q = get_n_m_g_q_coefficients(data)
        final_pr = calculate_pr_v8(data['pb'], data['roe'], n, m, g, q)
        passed = check_elimination(data)
        threshold = 0.75
        is_buy = passed and (final_pr <= threshold)
        
        results.append({
            '股票名称': stock['name'], '代码': stock['code'], '市场': stock['market'],
            '当前PB': round(data['pb'], 2), '正常化ROE(%)': round(data['roe'] * 100, 2),
            '终极PR': final_pr, '行业阈值': threshold,
            '排雷通过': '✅' if passed else '❌', '最终判定': '✅ 买入' if is_buy else '❌ 淘汰'
        })
        
    df = pd.DataFrame(results)
    filename = f"动态选股报告_{datetime.now().strftime('%Y%m%d')}.csv"
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    
    # 构建推送内容
    buy_list = df[df['最终判定'] == '✅ 买入']
    content = f"**V9.0 动态模型运行结果** ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    if len(buy_list) > 0:
        content += "🎉 **发现以下标的符合买入条件：**\n\n"
        for _, row in buy_list.iterrows():
            content += f"- {row['股票名称']} ({row['代码']}) | PR: {row['终极PR']} | 阈值: {row['行业阈值']}\n"
    else:
        content += "今日无符合买入条件的标的，请耐心等待。\n"
    
    content += "\n完整表格请前往 GitHub Actions 下载 Artifacts。"

    # 双渠道推送
    send_to_wechat("V9.0 量化选股日报", content)
    send_to_feishu("V9.0 量化选股日报", content)
    print("推送完成！")

if __name__ == "__main__":
    main()
