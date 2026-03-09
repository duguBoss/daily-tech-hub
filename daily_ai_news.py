import os
import time
import json
import re
import requests
import datetime
from urllib.parse import urljoin

# ================= 全局配置 =================
# 1. 模型与 API 配置
AI_MODEL = "gemini-3-flash-preview"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# 注意：API URL 保持与你提供的 curl 格式一致
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent?key={GEMINI_API_KEY}"

# 2. 目标数据源
SOURCES = [
    "https://ai-bot.cn/daily-ai-news/",
    "https://www.aibase.com/zh/daily"
]

# 3. 输出配置
OUTPUT_DIR = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "daily_ai_news.json")
NEWS_COUNT = 10  # 最终保留条数

# ===========================================

def call_gemini_api(prompt):
    """使用原生 REST API 调用 Gemini 3 Flash"""
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.8,
            "topK": 40
        }
    }
    
    try:
        response = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=60)
        res_json = response.json()
        if "candidates" in res_json:
            return res_json["candidates"][0]["content"]["parts"][0]["text"]
        else:
            print(f"❌ API 返回错误: {res_json}")
            return ""
    except Exception as e:
        print(f"❌ 请求 Gemini 出错: {e}")
        return ""

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
    # 尝试匹配 ```json ... ``` 或 ``` ... ```
    match = re.search(r'```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 如果没有代码块，尝试直接寻找最外层的 [ 或 {
    match = re.search(r'(\[.*\]|\{.*\})', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text

def get_news_list(all_markdown):
    """第一步：从混合内容中提取新闻候选"""
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    
    # 使用 {{ 和 }} 来转义 f-string 中的大括号
    prompt = f"""
    分析以下网页内容，筛选出今日({today})最新发布的 AI 行业动态。
    要求：
    1. 严格去除重复：如果多篇文章描述同一个模型发布或事件，只保留一个。
    2. 优先选择重磅消息。
    3. 严格返回以下 JSON 数组格式，不要有其他解释：
    [
        {{"title": "原标题", "url": "链接"}}
    ]
    
    内容来源：
    {all_markdown[:25000]}
    """
    raw_res = call_gemini_api(prompt)
    cleaned = clean_json_string(raw_res)
    try:
        return json.loads(cleaned)[:15]
    except Exception as e:
        print(f"解析新闻列表 JSON 失败: {e}")
        return []

def rewrite_article(title, url, context_md):
    """第二步：AI 深度重写"""
    print(f"  📝 正在处理: {title}")
    
    detail_md = fetch_jina_content(url)
    if not detail_md or len(detail_md) < 500:
        detail_md = f"标题: {title}\n上下文内容: {context_md[:3000]}"
        
    prompt = f"""
    你是一名资深的科技媒体主编。请根据以下素材，撰写一篇具有科技报道风格的新闻简报。
    
    要求：
    1. 重新拟定标题：专业、客观且极客风格。
    2. 重写内容：250字以内，分析其技术意义或行业影响。
    3. 提取配图：找出一个相关的图片 URL（若有）。
    4. 语言：中文。
    
    素材：
    {detail_md[:10000]}
    
    请严格返回 JSON 格式：
    {{
        "资讯标题": "新标题",
        "内容": "报道内容",
        "配图": ["图片URL"]
    }}
    """
    
    raw_res = call_gemini_api(prompt)
    cleaned = clean_json_string(raw_res)
    try:
        return json.loads(cleaned)
    except:
        return None

def main():
    if not GEMINI_API_KEY:
        print("❌ 未检测到 GEMINI_API_KEY")
        return

    # 1. 抓取主页内容
    full_content = ""
    for url in SOURCES:
        content = fetch_jina_content(url)
        if content:
            full_content += f"\n\n--- Source: {url} ---\n{content}"
        time.sleep(2)

    # 2. 获取候选列表
    candidates = get_news_list(full_content)
    print(f"✅ 找到 {len(candidates)} 条候选新闻")

    # 3. 逐条重写
    final_results = []
    seen_titles = set()

    for item in candidates:
        if len(final_results) >= NEWS_COUNT:
            break
            
        rewritten = rewrite_article(item['title'], item['url'], full_content)
        
        if rewritten and rewritten.get("内容"):
            # 二次去重检测
            new_title = rewritten.get("资讯标题")
            if new_title not in seen_titles:
                final_results.append(rewritten)
                seen_titles.add(new_title)
                time.sleep(2)

    # 4. 保存
    if final_results:
        if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(final_results, f, ensure_ascii=False, indent=4)
        print(f"🚀 任务成功，已更新 {len(final_results)} 条报道。")
    else:
        print("❌ 未生成有效内容。")

if __name__ == "__main__":
    main()
