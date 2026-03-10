import os
import json
import re
import time
import logging
import requests
from playwright.sync_api import sync_playwright

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 配置项
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SOURCES = ["https://ai-bot.cn/daily-ai-news/", "https://www.aibase.com/zh/daily"]
OUTPUT_FILE = "data/daily_ai_news.json"

def call_gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    try:
        response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=90)
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logging.error(f"Gemini API Error: {e}")
        return ""

def clean_json_string(raw_str):
    """提取并清洗 JSON 字符串，修复解析崩溃问题"""
    # 提取 [ ... ] 或 { ... }
    match = re.search(r'\[.*\]|\{.*\}', raw_str, re.DOTALL)
    if not match: return None
    json_str = match.group()
    # 清理 Markdown 符号和多余换行
    return json_str.replace('```json', '').replace('```', '').strip()

def get_list_data(url):
    """提取页面所有链接的标题和地址，简化传给 AI 的内容"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        # 只提取链接文本和 href，减小体积
        data = page.evaluate('''() => Array.from(document.querySelectorAll('a')).map(a => ({
            title: a.innerText.trim(),
            url: a.href
        })).filter(item => item.title.length > 5 && item.url.includes('http'))''')
        browser.close()
        return data

def process_article(title, url):
    """处理详情页"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            content = page.evaluate("document.body.innerText")
            images = page.evaluate('''() => Array.from(document.querySelectorAll('img')).map(i => i.src)''')
        except: 
            browser.close()
            return None
        browser.close()

    if not content or len(content) < 300: return None

    # AI 筛选配图
    img_prompt = f"从这30个链接中，选出最适合做正文头图的一张（排除Logo、icon）。只返回链接本身，不要引号和任何说明: {json.dumps(images[:30])}"
    best_img = call_gemini(img_prompt).strip()

    # AI 重写
    rewrite_prompt = f"重写这篇AI新闻，专业、极客风格。JSON格式: {{\"资讯标题\": \"...\", \"内容\": \"...\"}} \n内容: {content[:8000]}"
    ai_res = call_gemini(rewrite_prompt)
    
    clean_res = clean_json_string(ai_res)
    if clean_res:
        try:
            data = json.loads(clean_res)
            data["配图"] = [best_img] if best_img.startswith("http") else []
            return data
        except: return None
    return None

def main():
    if not os.path.exists("data"): os.makedirs("data")
    
    # 1. 获取所有链接数据
    all_links = []
    for src in SOURCES:
        try:
            logging.info(f"🌐 抓取列表页: {src}")
            all_links.extend(get_list_data(src))
        except Exception as e:
            logging.error(f"抓取列表页 {src} 失败: {e}")
    
    # 2. AI 筛选新闻列表 (放宽限制，改为提取最新的15条)
    logging.info(f"🤖 AI 正在从 {len(all_links)} 个链接中筛选 AI 新闻...")
    list_prompt = f"""
    你是新闻筛选专家。从以下链接中，选出15条与【AI、人工智能、大模型、AI应用】最相关的新闻。
    请忽略日期限制，只要是前沿AI资讯即可。
    返回纯 JSON 数组: [{{'title': '...', 'url': '...'}}]。
    链接列表: {json.dumps(all_links[:120])}
    """
    
    list_res = call_gemini(list_prompt)
    clean_list = clean_json_string(list_res)
    
    if not clean_list:
        logging.error(f"无法解析新闻列表，AI 回复: {list_res[:200]}")
        return
        
    try:
        candidates = json.loads(clean_list)
        logging.info(f"✅ AI 推荐了 {len(candidates)} 条新闻进行处理")
    except Exception as e:
        logging.error(f"JSON解析错误: {e}")
        return

    # 3. 循环处理 (增加重试)
    final_data = []
    for item in candidates:
        if len(final_data) >= 8: break # 目标是8条
        
        try:
            res = process_article(item['title'], item['url'])
            if res:
                final_data.append(res)
                logging.info(f"✅ 处理成功 [{len(final_data)}/8]: {item['title']}")
            else:
                logging.warning(f"⚠️ 处理失败或无效内容: {item['title']}")
        except Exception as e:
            logging.error(f"处理 {item['title']} 时发生意外: {e}")
            
        time.sleep(3) # 增加延迟，防止被反爬
        
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=4)
    logging.info(f"🚀 任务完成，最终保存 {len(final_data)} 条数据")

if __name__ == "__main__":
    main()
