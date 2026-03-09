import os
import time
import json
import re
import requests
import datetime
import random
from urllib.parse import urljoin

# ================= 全局配置 =================
# 1. 模型配置
OPENROUTER_MODEL = "stepfun/step-3.5-flash:free"
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 2. 目标数据源
SOURCES = [
    "https://ai-bot.cn/daily-ai-news/",
    "https://www.aibase.com/zh/daily"
]

# 3. 输出配置
OUTPUT_DIR = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "daily_ai_news.json")
NEWS_COUNT = 10 

# ===========================================

def get_current_date_info():
    """获取北京时间的年、月、日、年月路径"""
    beijing_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    year = beijing_now.strftime("%Y")
    month = beijing_now.strftime("%m")
    day = beijing_now.strftime("%d")
    return {
        "year": year,
        "month": month,
        "day": day,
        "date_str": beijing_now.strftime("%Y-%m-%d"),
        "path_aibot": f"{year}/{month}",
        "path_chinaz": f"{year}/{month}{day}" # 预判 chinaz 可能是当日日期
    }

def filter_valid_images(image_list, date_info):
    """
    严格过滤图片地址：
    1. https://ai-bot.cn/wp-content/uploads/YYYY/MM/...
    2. https://upload.chinaz.com/YYYY/MM...
    """
    year = date_info['year']
    month = date_info['month']
    
    # 允许 chinaz 匹配当前月开头的任何日期 (如 2025/0309)
    regex_aibot = rf"https://ai-bot\.cn/wp-content/uploads/{year}/{month}/"
    regex_chinaz = rf"https://upload\.chinaz\.com/{year}/{month}"
    
    valid_images = []
    for img in image_list:
        if re.search(regex_aibot, img) or re.search(regex_chinaz, img):
            valid_images.append(img)
    return valid_images

def call_ai_api(prompt):
    """调用 API 分发器（先 OpenRouter 后 Gemini）"""
    # 尝试 OpenRouter
    if OPENROUTER_API_KEY:
        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
            payload = {"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
        except: pass
    
    # 备用 Gemini
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.3}}
            response = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=60)
            if response.status_code == 200:
                return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except: pass
    return ""

def fetch_jina_content(url):
    """读取网页内容"""
    print(f"🌐 正在抓取: {url}")
    headers = {"X-Return-Format": "markdown"}
    try:
        resp = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=40)
        return resp.text if resp.status_code == 200 else ""
    except: return ""

def clean_json_string(text):
    if not text: return ""
    match = re.search(r'```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```', text, re.DOTALL)
    if match: return match.group(1).strip()
    match = re.search(r'(\[.*\]|\{.*\})', text, re.DOTALL)
    return match.group(1).strip() if match else text

def rewrite_article(title, url, date_info):
    """深度重写并严格寻找符合路径的图片"""
    print(f"  📝 正在重写: {title}")
    detail_md = fetch_jina_content(url)
    
    # 告知 AI 具体的图片匹配规则
    prompt = f"""
    作为科技主编，请根据素材重写新闻，并从【文章原始Markdown】中找出符合规则的图片。
    
    规则：
    1. 图片必须以 https://ai-bot.cn/wp-content/uploads/{date_info['year']}/{date_info['month']}/ 开头
    2. 或以 https://upload.chinaz.com/{date_info['year']}/{date_info['month']} 开头
    3. 重写风格：科技媒体、极客、专业。200字左右。
    
    素材内容：
    {detail_md[:10000]}
    
    请严格返回 JSON：
    {{
        "资讯标题": "重写后的标题",
        "内容": "报道内容",
        "候选图片": ["从文中找到的所有图片URL"]
    }}
    """
    raw_res = call_ai_api(prompt)
    try:
        data = json.loads(clean_json_string(raw_res))
        # 在 Python 层进行最终正则过滤，确保万无一失
        final_images = filter_valid_images(data.get("候选图片", []), date_info)
        return {
            "资讯标题": data.get("资讯标题"),
            "内容": data.get("内容"),
            "配图": final_images[:1] # 只取一张最符合的
        }
    except:
        return None

def main():
    date_info = get_current_date_info()
    print(f"📅 当前处理日期路径: {date_info['year']}/{date_info['month']}")

    # 1. 抓取列表
    full_home_content = ""
    for url in SOURCES:
        full_home_content += f"\n{fetch_jina_content(url)}"
        time.sleep(2)

    # 2. 提取新闻列表
    list_prompt = f"分析以下内容，提取今日({date_info['date_str']})最新的15条AI新闻标题和URL。返回 JSON 数组: [{{'title':'','url':''}}]\n内容:\n{full_home_content[:20000]}"
    candidates = []
    try:
        candidates = json.loads(clean_json_string(call_ai_api(list_prompt)))
    except: pass

    # 3. 循环重写
    final_results = []
    seen_titles = set()
    for item in candidates:
        if len(final_results) >= NEWS_COUNT: break
        res = rewrite_article(item['title'], item['url'], date_info)
        if res and res.get("内容"):
            if res["资讯标题"] not in seen_titles:
                final_results.append(res)
                seen_titles.add(res["资讯标题"])
                time.sleep(2)

    # 4. 保存
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=4)
    print(f"🚀 任务完成，成功生成 {len(final_results)} 条带合规图片的报道")

if __name__ == "__main__":
    main()
