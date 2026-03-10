import os
import time
import json
import re
import requests
import logging
from datetime import datetime, timedelta

# 配置日志：在控制台清晰显示每一步进度
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
    """获取日期信息，用于路径匹配"""
    now = datetime.utcnow() + timedelta(hours=8)
    return {
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "today": now.strftime("%Y-%m-%d"),
        "yesterday": (now - timedelta(days=1)).strftime("%Y-%m-%d")
    }

def fetch_jina_content(url):
    """抓取网页 Markdown 内容"""
    try:
        logging.info(f"🌐 正在抓取: {url}")
        resp = requests.get(f"https://r.jina.ai/{url}", headers={"X-Return-Format": "markdown"}, timeout=40)
        return resp.text if resp.status_code == 200 else ""
    except Exception as e:
        logging.error(f"抓取失败 {url}: {e}")
        return ""

def call_ai_api(prompt):
    """AI API 分发器"""
    # 优先使用 OpenRouter
    if OPENROUTER_API_KEY:
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", 
                                 headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                                 json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}]}, timeout=50)
            if resp.status_code == 200: return resp.json()['choices'][0]['message']['content']
        except Exception: pass
    
    # 备用使用 Gemini
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
            resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=50)
            if resp.status_code == 200: return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception: pass
    return ""

def filter_valid_images(image_list, date_info):
    """严格过滤图片并进行调试打印"""
    year, month = date_info['year'], date_info['month']
    # 允许匹配当前月路径
    regex_aibot = rf"https://ai-bot\.cn/wp-content/uploads/{year}/{month}/"
    regex_chinaz = rf"https://upload\.chinaz\.com/{year}/{month}"
    
    valid = []
    logging.info(f"🔍 [图片检查] 发现 {len(image_list)} 张候选图片")
    for img in image_list:
        if re.search(regex_aibot, img) or re.search(regex_chinaz, img):
            valid.append(img)
            logging.info(f"  ✅ [命中] {img}")
        else:
            logging.info(f"  ❌ [过滤] {img}")
    return valid

def rewrite_article(title, url, date_info):
    """重写单篇新闻并获取 JSON"""
    logging.info(f"📝 [正在处理] {title}")
    content = fetch_jina_content(url)
    if not content: return None
    
    prompt = f"""
    请根据以下新闻内容，重写一篇科技新闻。
    要求：
    1. 保持专业、科技感。
    2. 提取文中所有的图片URL。
    3. 严格返回JSON格式: {{"资讯标题": "...", "内容": "...", "候选图片": ["url1", "url2"]}}
    
    文章内容:
    {content[:8000]}
    """
    
    raw_res = call_ai_api(prompt)
    try:
        json_str = re.search(r'\{.*\}', raw_res, re.DOTALL).group()
        data = json.loads(json_str)
        valid_imgs = filter_valid_images(data.get("候选图片", []), date_info)
        return {
            "资讯标题": data.get("资讯标题", title),
            "内容": data.get("内容", ""),
            "配图": valid_imgs[:1] if valid_imgs else []
        }
    except Exception as e:
        logging.error(f"处理失败: {e}")
        return None

def main():
    date_info = get_date_info()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 1. 抓取所有数据源
    full_text = ""
    for url in SOURCES:
        full_text += fetch_jina_content(url) + "\n"
        time.sleep(1)
    
    # 2. 提取近两天的新闻列表
    list_prompt = f"""
    分析以下内容，提取日期为 {date_info['today']} 或 {date_info['yesterday']} 的前10条AI新闻标题和URL。
    直接返回 JSON 数组格式: [{{'title': '标题', 'url': '链接'}}]
    内容: {full_text[:15000]}
    """
    
    logging.info("🤖 AI 正在筛选新闻列表...")
    candidates_raw = call_ai_api(list_prompt)
    try:
        candidates = json.loads(re.search(r'\[.*\]', candidates_raw, re.DOTALL).group())
    except Exception as e:
        logging.error(f"提取新闻列表失败: {e}")
        return

    # 3. 循环处理
    results = []
    for item in candidates:
        if len(results) >= NEWS_COUNT: break
        res = rewrite_article(item['title'], item['url'], date_info)
        if res and res.get("配图"):
            results.append(res)
            logging.info(f"🎉 成功加入: {res['资讯标题']}")
        time.sleep(2)
    
    # 4. 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    logging.info(f"🚀 任务完成，共生成 {len(results)} 条带图资讯。")

if __name__ == "__main__":
    main()
