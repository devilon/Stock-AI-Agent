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

def calculate_pr_v8(pb, roe, n, m, g, q, s=1.0):
    try:
        if roe <= 0 or pb <= 0 or pd.isna(roe) or pd.isna(pb):
            return 999
        base_pr = (pb / (roe ** 2)) / 100
        return round(base_pr * n * m * g * q * s, 3)
    except Exception:
        return 999

def fetch_a_stock_pool():
    print("正在获取A股宽基指数成分股...")
    codes = []
    try:
        df300 = ak.index_stock_cons_csindex(symbol="000300")
        for _, row in df300.iterrows():
            codes.append({"code": row['成分券代码'], "name": row['成分券名称'], "market": "A股", "industry": "待分类"})
        print(f"成功获取沪深300成分股：{len(codes)} 只")
    except Exception as e:
        print(f"获取沪深300失败: {e}")
    return codes

def fetch_a_stock_data(code):
    try:
        # 获取实时行情（加入重试机制）
        for _ in range(2):
            try:
                df_spot = ak.stock_zh_a_spot_em()
                stock_info = df_spot[df_spot['代码'] == code].iloc[0]
                pb = float(stock_info['市净率']) if '市净率' in stock_info else 0
                break
            except Exception:
                time.sleep(2)
        else:
            return None
            
        # 模拟真实的财务数据获取（这里为了跑通流程用模拟值）
        # 实战中需要调用 ak.stock_financial_analysis_indicator(symbol=code) 获取真实ROE
        return {
            'pb': pb if pb > 0 else 1.0,
            'roe': 0.15, 
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
        print(f"处理股票 {code} 时发生数据错误: {e}")
        return None

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
    print(f"===== V9.0 动态选股模型启动 | {datetime.now()} =====")
    
    # 如果你只想测试流程，可以取消下面这行的注释，使用静态池子
    # dynamic_pool = [{"code": "000651", "name": "格力电器", "market": "A股", "industry": "消费/品牌轻资产"}]
    
    dynamic_pool = fetch_a_stock_pool()

    # ================= 新增备胎机制 =================
    if not dynamic_pool:
        print("⚠️ 警告：动态股票池获取失败（海外服务器被反爬或超时），启用备用静态测试池...")
        # 如果动态抓取失败，使用一个固定的测试池，确保流程能跑完
        dynamic_pool = [
            {"code": "000651", "name": "格力电器", "market": "A股", "industry": "消费/品牌轻资产"},
            {"code": "600036", "name": "招商银行", "market": "A股", "industry": "银行金融"},
            {"code": "00836", "name": "华润电力", "market": "港股", "industry": "公用事业"},
        ]
    # ================================================

    sample_pool = dynamic_pool # 测试阶段直接全跑，不限制数量
    
    results = []
    for stock in sample_pool:
        print(f"正在分析: {stock['name']} ({stock['code']})")
        data = fetch_a_stock_data(stock['code'])
        if not data: 
            continue
            
        data['industry'] = "消费/品牌轻资产" # 实战需根据行业接口自动分类
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
        time.sleep(0.5) # 防封控
        
    df = pd.DataFrame(results)
       # ================= 新增防弹补丁 =================
    # 定义标准列名，防止因为数据抓取为空导致 KeyError
    columns = ['股票名称', '代码', '市场', '当前PB', '正常化ROE(%)', '终极PR', '行业阈值', '排雷通过', '最终判定']
    
    if df.empty:
        print("警告：今日未获取到任何有效股票数据，可能被反爬或网络超时。")
        df = pd.DataFrame(columns=columns)  # 生成带列名的空表
    else:
        # 如果数据没空，确保列顺序一致
        df = df.reindex(columns=columns)
    # ================================================
        
    filename = f"动态选股报告_{datetime.now().strftime('%Y%m%d')}.csv"
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"表格已生成：{filename}")
    
    buy_list = df[df['最终判定'] == '✅ 买入']
    content = f"**V9.0 动态模型运行结果** ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    if len(buy_list) > 0:
        content += "🎉 **发现以下标的符合买入条件：**\n\n"
        for _, row in buy_list.iterrows():
            content += f"- {row['股票名称']} ({row['代码']}) | PR: {row['终极PR']} | 阈值: {row['行业阈值']}\n"
    else:
        content += "今日无符合买入条件的标的，请耐心等待。\n"
    content += "\n完整表格请前往 GitHub Actions 下载 Artifacts。"

    send_to_wechat("V9.0 量化选股日报", content)
    send_to_feishu("V9.0 量化选股日报", content)
    print("程序运行完毕！")

if __name__ == "__main__":
    main()
