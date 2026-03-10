import os
import time
import json
import re
import requests
import logging
from datetime import datetime, timedelta

# 配置日志：将输出打印到控制台，方便实时观察抓取和匹配过程
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ================= 全局配置 ================= #
OPENROUTER_MODEL = "stepfun/step-3.5-flash:free"
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SOURCES = ["https://ai-bot.cn/daily-ai-news/", "https://www.aibase.com/zh/daily"]
OUTPUT_DIR = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "daily_ai_news.json")
NEWS_COUNT = 10

def get_date_info():
    now = datetime.utcnow() + timedelta(hours=8)
    return {
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
        "date_str": now.strftime("%Y-%m-%d")
    }

def filter_valid_images(image_list, date_info):
    """严格过滤图片并打印匹配过程"""
    year, month = date_info['year'], date_info['month']
    regex_aibot = rf"https://ai-bot\.cn/wp-content/uploads/{year}/{month}/"
    regex_chinaz = rf"https://upload\.chinaz\.com/{year}/{month}"
    
    valid_images = []
    logging.info(f"🔍 [图片处理] 收到待处理列表: {image_list}")
    
    for img in image_list:
        if re.search(regex_aibot, img) or re.search(regex_chinaz, img):
            valid_images.append(img)
            logging.info(f"  ✅ [命中规则] {img}")
        else:
            logging.info(f"  ❌ [剔除图片] {img}")
            
    return valid_images

def call_ai_api(prompt):
    """AI API 分发器"""
    # 优先尝试 OpenRouter
    if OPENROUTER_API_KEY:
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", 
                                 headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                                 json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}]}, timeout=45)
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content']
        except Exception as e:
            logging.warning(f"OpenRouter 调用失败: {e}")

    # 备用 Gemini
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
            resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=45)
            if resp.status_code == 200:
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logging.warning(f"Gemini 调用失败: {e}")
    return ""

def fetch_jina_content(url):
    try:
        resp = requests.get(f"https://r.jina.ai/{url}", headers={"X-Return-Format": "markdown"}, timeout=40)
        return resp.text if resp.status_code == 200 else ""
    except: return ""

def rewrite_article(title, url, date_info):
    logging.info(f"📝 [正在重写] {title}")
    detail_md = fetch_jina_content(url)
    
    prompt = f"""
    请重写这篇AI新闻，并提取文中所有的图片URL。
    规则：
    1. 图片链接需包含 /{date_info['year']}/{date_info['month']}/ 路径。
    2. 返回 JSON 格式：{{ "资讯标题": "...", "内容": "...", "候选图片": ["url1", "url2"] }}
    
    文章内容:
    {detail_md[:8000]}
    """
    
    raw_res = call_ai_api(prompt)
    try:
        # 清洗 JSON
        json_str = re.search(r'\{.*\}', raw_res, re.DOTALL).group()
        data = json.loads(json_str)
        
        # 调试输出
        raw_imgs = data.get("候选图片", [])
        final_images = filter_valid_images(raw_imgs, date_info)
        
        return {
            "资讯标题": data.get("资讯标题", title),
            "内容": data.get("内容", ""),
            "配图": final_images[:1] if final_images else []
        }
    except Exception as e:
        logging.error(f"解析文章失败: {e}")
        return None

def main():
    date_info = get_date_info()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # ... 后续循环处理逻辑 ...
    logging.info("🚀 任务启动...")
    # (此处执行您的列表抓取与循环逻辑)

if __name__ == "__main__":
    main()
