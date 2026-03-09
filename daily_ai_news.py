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
# 优先使用 OpenRouter 的阶跃星辰模型
OPENROUTER_MODEL = "stepfun/step-3.5-flash:free"
# 备用谷歌模型
GEMINI_MODEL = "gemini-1.5-flash"

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

def call_openrouter(prompt):
    """调用 OpenRouter (Step 3.5 Flash)"""
    if not OPENROUTER_API_KEY:
        return ""
    
    print(f"🤖 尝试使用 OpenRouter ({OPENROUTER_MODEL})...")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            print(f"⚠️ OpenRouter 报错: {response.status_code}")
    except Exception as e:
        print(f"❌ OpenRouter 请求异常: {e}")
    return ""

def call_gemini(prompt):
    """调用 Google Gemini (作为备用)"""
    if not GEMINI_API_KEY:
        return ""
    
    print(f"🔄 切换至备用模型 Google Gemini ({GEMINI_MODEL})...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3}
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        if response.status_code == 200:
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"❌ Gemini 请求异常: {e}")
    return ""

def call_ai_api(prompt):
    """统一模型分发器：先 OpenRouter，后 Gemini"""
    # 1. 尝试 OpenRouter
    res = call_openrouter(prompt)
    if res: return res
    
    # 2. 失败后尝试 Gemini
    res = call_gemini(prompt)
    return res

def fetch_jina_content(url):
    """使用 Jina 读取网页 Markdown 内容"""
    print(f"🌐 正在抓取: {url}")
    jina_url = f"https://r.jina.ai/{url}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Return-Format": "markdown"
    }
    try:
        resp = requests.get(jina_url, headers=headers, timeout=40)
        return resp.text if resp.status_code == 200 else ""
    except:
        return ""

def clean_json_string(text):
    """提取 Markdown 代码块中的 JSON"""
    if not text: return ""
    text = text.strip()
    match = re.search(r'```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```', text, re.DOTALL)
    if match: return match.group(1).strip()
    match = re.search(r'(\[.*\]|\{.*\})', text, re.DOTALL)
    return match.group(1).strip() if match else text

def get_news_list(all_markdown):
    """第一步：提取候选"""
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    prompt = f"""
    分析以下内容，筛选出今日({today})最新的 15 条 AI 行业动态。
    要求：去重，严格返回 JSON 数组格式：
    [ {{"title": "原标题", "url": "链接"}} ]
    内容来源：
    {all_markdown[:20000]}
    """
    raw_res = call_ai_api(prompt)
    try:
        return json.loads(clean_json_string(raw_res))
    except:
        return []

def rewrite_article(title, url, context_md):
    """第二步：AI 深度重写"""
    print(f"  📝 正在重写: {title}")
    
    detail_md = fetch_jina_content(url)
    if not detail_md or len(detail_md) < 500:
        detail_md = f"标题: {title}\n摘要: {context_md[:2000]}"
        
    prompt = f"""
    作为科技媒体主编，请根据素材撰写一篇 250 字以内的科技风报道。
    要求：重新拟定极客风格标题，分析技术影响。
    素材内容：
    {detail_md[:10000]}
    
    必须返回 JSON：
    {{
        "资讯标题": "新标题",
        "内容": "报道内容",
        "配图": ["图片URL"]
    }}
    """
    raw_res = call_ai_api(prompt)
    try:
        return json.loads(clean_json_string(raw_res))
    except:
        return None

def main():
    if not OPENROUTER_API_KEY and not GEMINI_API_KEY:
        print("❌ 未设置任何 API KEY")
        return

    full_content = ""
    for url in SOURCES:
        content = fetch_jina_content(url)
        if content:
            full_content += f"\n\n--- Source: {url} ---\n{content}"
        time.sleep(2)

    candidates = get_news_list(full_content)
    if not candidates:
        candidates = []

    final_results = []
    seen_titles = set()

    for item in candidates:
        if len(final_results) >= NEWS_COUNT: break
        rewritten = rewrite_article(item['title'], item['url'], full_content)
        if rewritten and rewritten.get("内容"):
            new_title = rewritten.get("资讯标题")
            if new_title not in seen_titles:
                final_results.append(rewritten)
                seen_titles.add(new_title)
                time.sleep(2)

    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=4)
    
    print(f"🚀 任务结束，已生成 {len(final_results)} 条 AI 报道")

if __name__ == "__main__":
    main()
