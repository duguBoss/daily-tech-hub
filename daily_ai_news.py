import os
import json
import re
import time
import logging
from playwright.sync_api import sync_playwright
import requests

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
        response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logging.error(f"Gemini API 错误: {e}")
        return ""

def get_data_with_playwright(url, is_list_page=False):
    """利用 Playwright 渲染网页获取内容和图片"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # 模拟真实浏览器
        page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
        
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            
            if is_list_page:
                # 提取列表页面的HTML内容，让AI去识别新闻列表
                content = page.content()
            else:
                # 详情页：获取正文文本和所有图片
                content = page.evaluate("document.body.innerText")
                images = page.evaluate('''() => Array.from(document.querySelectorAll('img')).map(i => i.src)''')
                browser.close()
                return content, images
        except Exception as e:
            logging.error(f"抓取页面出错: {e}")
            browser.close()
            return None, []
        browser.close()
        return content, []

def process_article(title, url):
    """处理单篇新闻"""
    logging.info(f"🔎 正在进入详情页: {title}")
    content, images = get_data_with_playwright(url)
    
    if not content or len(content) < 300:
        return None

    # 1. AI 智能选图
    img_prompt = f"从这些图片链接中，选出最像新闻正文配图的一张（排除Logo、icon、广告、占位图）。只返回图片URL，不要Markdown符号: {json.dumps(images[:30])}"
    best_img = call_gemini(img_prompt).strip()
    if not best_img.startswith("http"): best_img = ""

    # 2. AI 重写内容
    rewrite_prompt = f"""
    你是科技新闻主编，请重写以下AI报道，风格专业、极客。
    文章内容: {content[:8000]}
    要求：返回标准的JSON: {{"资讯标题": "...", "内容": "..."}}
    """
    ai_res = call_gemini(rewrite_prompt)
    try:
        data = json.loads(re.search(r'\{.*\}', ai_res, re.DOTALL).group())
        data["配图"] = [best_img] if best_img else []
        return data
    except: return None

def main():
    if not os.path.exists("data"): os.makedirs("data")
    
    # 1. 抓取列表并提取任务
    all_content = ""
    for src in SOURCES:
        logging.info(f"🌐 抓取列表页: {src}")
        html, _ = get_data_with_playwright(src, is_list_page=True)
        all_content += html[:20000] # 截取一部分防止超长
    
    # 让 AI 从原始HTML中提取新闻
    list_prompt = f"分析以下HTML源码，提取今天或昨天的前8条AI新闻。以JSON数组格式返回: [{{'title': '...', 'url': '...'}}]\n源码: {all_content}"
    list_res = call_gemini(list_prompt)
    try:
        candidates = json.loads(re.search(r'\[.*\]', list_res, re.DOTALL).group())
    except:
        logging.error("无法解析新闻列表")
        return

    # 2. 循环处理
    final_data = []
    for item in candidates:
        res = process_article(item['title'], item['url'])
        if res and res.get("配图"):
            final_data.append(res)
            logging.info(f"✅ 完成: {item['title']}")
        time.sleep(2)
        
    # 3. 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=4)
    logging.info(f"🚀 任务完成，共保存 {len(final_data)} 条新闻")

if __name__ == "__main__":
    main()
