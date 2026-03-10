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
        logging.info(f"🌐 抓取列表页: {src}")
        all_links.extend(get_list_data(src))
    
    # 2. AI 筛选新闻列表
    logging.info("🤖 AI 正在筛选新闻列表...")
    list_prompt = f"从以下链接中筛选今天或昨天发布的AI新闻标题和URL。返回纯 JSON 数组: [{{'title': '...', 'url': '...'}}]。链接列表: {json.dumps(all_links[:100])}"
    
    list_res = call_gemini(list_prompt)
    clean_list = clean_json_string(list_res)
    if not clean_list:
        logging.error("无法解析新闻列表，原始输出:")
        logging.error(list_res)
        return
        
    candidates = json.loads(clean_list)
    logging.info(f"✅ 找到 {len(candidates)} 条新闻")

    # 3. 循环处理
    final_data = []
    for item in candidates[:8]: # 限制 8 条
        res = process_article(item['title'], item['url'])
        if res:
            final_data.append(res)
            logging.info(f"✅ 处理成功: {item['title']}")
        time.sleep(2)
        
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=4)
    logging.info(f"🚀 任务完成，保存 {len(final_data)} 条新闻")

if __name__ == "__main__":
    main()
