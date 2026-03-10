import os
import time
import json
import re
import requests
import logging
from datetime import datetime, timedelta

# 配置日志
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
        "year": now.strftime("%Y"), "month": now.strftime("%m"), "day": now.strftime("%d"),
        "date_str": now.strftime("%Y-%m-%d")
    }

def fetch_jina_content(url):
    try:
        logging.info(f"🌐 正在抓取: {url}")
        resp = requests.get(f"https://r.jina.ai/{url}", headers={"X-Return-Format": "markdown"}, timeout=40)
        return resp.text if resp.status_code == 200 else ""
    except Exception as e:
        logging.error(f"抓取失败 {url}: {e}")
        return ""

def call_ai_api(prompt):
    # 优先尝试 OpenRouter
    if OPENROUTER_API_KEY:
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", 
                                 headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                                 json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}]}, timeout=45)
            if resp.status_code == 200: return resp.json()['choices'][0]['message']['content']
        except: pass

    # 备用 Gemini
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
            resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=45)
            if resp.status_code == 200: return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except: pass
    return ""

def filter_valid_images(image_list, date_info):
    year, month = date_info['year'], date_info['month']
    regex_aibot = rf"https://ai-bot\.cn/wp-content/uploads/{year}/{month}/"
    regex_chinaz = rf"https://upload\.chinaz\.com/{year}/{month}"
    
    valid = []
    logging.info(f"🔍 检查图片 ({len(image_list)} 张)")
    for img in image_list:
        if re.search(regex_aibot, img) or re.search(regex_chinaz, img):
            valid.append(img)
            logging.info(f"  ✅ 命中: {img}")
        else:
            logging.info(f"  ❌ 剔除: {img}")
    return valid

def rewrite_article(title, url, date_info):
    detail_md = fetch_jina_content(url)
    prompt = f"重写这篇新闻，并提取所有图片URL。返回JSON: {{\"资讯标题\": \"...\", \"内容\": \"...\", \"候选图片\": [\"url\"]}}。文章: {detail_md[:8000]}"
    
    raw_res = call_ai_api(prompt)
    try:
        json_match = re.search(r'\{.*\}', raw_res, re.DOTALL)
        data = json.loads(json_match.group())
        valid_imgs = filter_valid_images(data.get("候选图片", []), date_info)
        return {"资讯标题": data.get("资讯标题", title), "内容": data.get("内容"), "配图": valid_imgs[:1]}
    except: return None

def main():
    date_info = get_date_info()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 1. 抓取列表
    full_text = ""
    for url in SOURCES:
        full_text += fetch_jina_content(url) + "\n"
        time.sleep(1)
    
    # 2. 提取候选任务
    list_prompt = f"分析内容提取{date_info['date_str']}的10条AI新闻标题和URL。返回JSON格式: [{{'title':'','url':''}}]\n内容: {full_text[:10000]}"
    candidates_raw = call_ai_api(list_prompt)
    try:
        candidates = json.loads(re.search(r'\[.*\]', candidates_raw, re.DOTALL).group())
    except:
        logging.error("无法提取新闻列表")
        return

    # 3. 循环处理
    results = []
    for item in candidates:
        if len(results) >= NEWS_COUNT: break
        res = rewrite_article(item['title'], item['url'], date_info)
        if res and res.get("配图"):
            results.append(res)
            logging.info(f"🎉 成功处理: {res['资讯标题']}")
    
    # 4. 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    logging.info(f"🚀 任务完成，共生成 {len(results)} 条数据")

if __name__ == "__main__":
    main()
