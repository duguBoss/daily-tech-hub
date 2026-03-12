import hashlib
import html
import json
import logging
import mimetypes
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


WORKDIR = Path(__file__).resolve().parent
OUTPUT_DIR = WORKDIR / "data"
OUTPUT_FILE = OUTPUT_DIR / "daily_ai_news.json"
IMAGE_DIR = WORKDIR / "assets" / "news_images"

AI_BOT_URL = "https://ai-bot.cn/daily-ai-news/"
AIBASE_DAILY_URL = "https://www.aibase.com/zh/daily"
AIBASE_NEWS_URL = "https://www.aibase.com/zh/news/{news_id}"

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "").strip()
GITHUB_REF_NAME = os.environ.get("GITHUB_REF_NAME", "").strip()
GITHUB_SHA = os.environ.get("GITHUB_SHA", "").strip()

REQUEST_TIMEOUT = 30
MIN_CONTENT_LENGTH = 80
MAX_CONTENT_LENGTH = 360
TARGET_CONTENT_LENGTH = 300
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
}
LATIN_STOPWORDS = {
    "with",
    "from",
    "that",
    "this",
    "into",
    "over",
    "more",
    "than",
    "will",
    "have",
    "has",
}
GENERIC_CONTENT_PATTERNS = [
    r"^每日.*?(快讯|资讯|新闻)",
    r"^点击.*?查看",
    r"^原标题[:：]",
    r"^本文.*?(转载|来源)",
    r"欢迎.*?(关注|订阅)",
]
ARTICLE_BLOCK_PATTERNS = [
    r'<article\b[^>]*>(.*?)</article>',
    r'<div\b[^>]+class="[^"]*(?:entry-content|article-content|post-content|single-content|content-body|news-content)[^"]*"[^>]*>(.*?)</div>',
    r'<section\b[^>]+class="[^"]*(?:entry-content|article-content|post-content|single-content|content-body|news-content)[^"]*"[^>]*>(.*?)</section>',
]
NEWS_ACTION_PATTERN = re.compile(
    r"(发布|推出|上线|升级|更新|接入|支持|开放|完成|获批|融资|收购|开源|测试|合作|回应|发布会|发布了|宣布|启动|新增)"
)


def build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(REQUEST_HEADERS)
    return session


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def require_gemini_api_key() -> None:
    if not GEMINI_API_KEY:
        raise RuntimeError("缺少 GEMINI_API_KEY。当前任务要求 AI 中文改写和语义去重。")


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<.*?>", "", value, flags=re.S)
    value = value.replace("\u200b", "").replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n+", "\n", value)
    return value.strip()


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", clean_text(value))


def normalize_url(base_url: str, raw_url: str) -> str:
    raw_url = html.unescape((raw_url or "").strip())
    if not raw_url:
        return ""
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return urljoin(base_url, raw_url)


def fetch_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def parse_target_dates(days: int = 2) -> List[datetime.date]:
    today = datetime.now().date()
    return [today - timedelta(days=offset) for offset in range(days)]


def infer_year(month: int, today: datetime) -> int:
    if today.month == 1 and month == 12:
        return today.year - 1
    return today.year


def parse_ai_bot_date(label: str, today: datetime) -> Optional[datetime.date]:
    match = re.search(r"(\d{1,2})月(\d{1,2})", label)
    if not match:
        return None
    month = int(match.group(1))
    day = int(match.group(2))
    try:
        return datetime(infer_year(month, today), month, day).date()
    except ValueError:
        return None


def extract_json_string(raw_text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw_text or "", re.S | re.I)
    if fenced:
        return fenced.group(1).strip()
    matched = re.search(r"\{.*\}|\[.*\]", raw_text or "", re.S)
    return matched.group(0).strip() if matched else (raw_text or "").strip()


def call_gemini_json(prompt: str) -> Dict:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    response = requests.post(url, json=payload, timeout=90)
    response.raise_for_status()
    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(extract_json_string(text))


def image_score(src: str, tag: str, context: str) -> int:
    src_lower = src.lower()
    tag_lower = tag.lower()
    context_lower = context.lower()
    score = 0
    if src_lower.startswith("data:"):
        return -100
    if src_lower.endswith(".svg"):
        score -= 60
    if any(flag in src_lower for flag in ["logo", "icon", "avatar", "favicon", "shape", "gaba"]):
        score -= 80
    if any(flag in src_lower for flag in ["upload.chinaz.com", "wp-content/uploads", "pic.chinaz.com"]):
        score += 35
    if "data-src=" in tag_lower or "data-original=" in tag_lower:
        score += 25
    if any(flag in tag_lower for flag in ["wp-image", "alignnone", "aligncenter", "lazy unfancybox"]):
        score += 15
    if any(flag in context_lower for flag in ["entry-content", "article-content", "site-content", "post-content"]):
        score += 20
    if "object-cover opacity-0" in tag_lower:
        score -= 30
    return score


def extract_best_image(base_url: str, html_text: str) -> str:
    candidates = []
    for match in re.finditer(r"<img\b([^>]+)>", html_text, re.I | re.S):
        attrs = match.group(1)
        tag = match.group(0)
        data_src = re.search(r'data-src="([^"]+)"', attrs, re.I)
        data_original = re.search(r'data-original="([^"]+)"', attrs, re.I)
        src_match = re.search(r'src="([^"]+)"', attrs, re.I)
        raw_src = ""
        if data_src:
            raw_src = data_src.group(1)
        elif data_original:
            raw_src = data_original.group(1)
        elif src_match:
            raw_src = src_match.group(1)
        src = normalize_url(base_url, raw_src)
        if not src:
            continue
        context = html_text[max(0, match.start() - 240): match.end() + 240]
        score = image_score(src, tag, context)
        if score <= 0:
            continue
        candidates.append((score, match.start(), src))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def extract_meta_content(html_text: str, key: str) -> str:
    patterns = [
        rf'<meta[^>]+property="{re.escape(key)}"[^>]+content="([^"]+)"',
        rf'<meta[^>]+content="([^"]+)"[^>]+property="{re.escape(key)}"',
        rf'<meta[^>]+name="{re.escape(key)}"[^>]+content="([^"]+)"',
        rf'<meta[^>]+content="([^"]+)"[^>]+name="{re.escape(key)}"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, re.I | re.S)
        if match:
            return compact_text(match.group(1))
    return ""


def strip_noise_lines(text: str) -> str:
    lines = [line.strip() for line in clean_text(text).splitlines()]
    kept = []
    for line in lines:
        if len(line) < 8:
            continue
        if any(re.search(pattern, line) for pattern in GENERIC_CONTENT_PATTERNS):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def extract_article_text(html_text: str) -> str:
    candidates: List[str] = []
    for pattern in ARTICLE_BLOCK_PATTERNS:
        for match in re.finditer(pattern, html_text, re.I | re.S):
            text = strip_noise_lines(match.group(1))
            if len(text) >= 80:
                candidates.append(text)
    if not candidates:
        paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", html_text, re.I | re.S)
        merged = "\n".join(strip_noise_lines(part) for part in paragraphs)
        merged = strip_noise_lines(merged)
        if len(merged) >= 80:
            candidates.append(merged)
    if not candidates:
        return ""
    candidates.sort(key=len, reverse=True)
    return candidates[0]


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"[。！？!?；;\n]+", clean_text(text))
    return [part.strip(" ，,：:") for part in parts if len(part.strip()) >= 8]


def choose_content_excerpt(description: str, article_text: str) -> str:
    desc = compact_text(description)
    article = clean_text(article_text)
    sentence_parts = split_sentences(article)
    candidates: List[str] = []
    if sentence_parts:
        excerpt_parts: List[str] = []
        current_length = 0
        for part in sentence_parts:
            extra = len(part) + (1 if excerpt_parts else 0)
            if current_length + extra > MAX_CONTENT_LENGTH:
                break
            excerpt_parts.append(part)
            current_length += extra
            if current_length >= TARGET_CONTENT_LENGTH:
                break
        excerpt = "。".join(excerpt_parts).strip()
        if excerpt:
            if not excerpt.endswith(("。", "！", "？")):
                excerpt += "。"
            candidates.append(excerpt)
    if desc:
        candidates.append(desc)
    for candidate in candidates:
        candidate = compact_text(candidate)
        if len(candidate) >= MIN_CONTENT_LENGTH:
            return candidate[:MAX_CONTENT_LENGTH]
    return ""


def title_tokens(value: str) -> set:
    text = compact_text(value).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s*-\s*[^-]{1,20}$", " ", text)
    return set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text))


def title_similarity(left: str, right: str) -> float:
    left_tokens = title_tokens(left)
    right_tokens = title_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def choose_canonical_title(source_title: str, page_title: str) -> str:
    source_title = compact_text(source_title)
    page_title = compact_text(page_title)
    if not page_title:
        return source_title
    if not source_title:
        return page_title
    if title_similarity(source_title, page_title) >= 0.35:
        return page_title if len(page_title) >= len(source_title) else source_title
    return source_title


def fetch_page_data(session: requests.Session, url: str) -> Dict[str, str]:
    html_text = fetch_html(session, url)
    data: Dict[str, str] = {"html": html_text}
    title_match = re.search(r"<title>(.*?)</title>", html_text, re.I | re.S)
    page_title = (
        extract_meta_content(html_text, "og:title")
        or extract_meta_content(html_text, "twitter:title")
        or (compact_text(title_match.group(1)) if title_match else "")
    )
    description = (
        extract_meta_content(html_text, "og:description")
        or extract_meta_content(html_text, "description")
        or extract_meta_content(html_text, "twitter:description")
    )
    article_text = extract_article_text(html_text)
    if page_title:
        data["title"] = page_title
    if description:
        data["description"] = description
    if article_text:
        data["article_text"] = article_text
        excerpt = choose_content_excerpt(description, article_text)
        if excerpt:
            data["content"] = excerpt
    elif description:
        data["content"] = compact_text(description)[:MAX_CONTENT_LENGTH]
    image = extract_best_image(url, html_text)
    if image:
        data["image"] = image
    return data


def is_valid_item(title: str, content: str, image: str) -> bool:
    if not title or not content or not image:
        return False
    if len(compact_text(content)) < MIN_CONTENT_LENGTH:
        return False
    if any(re.search(pattern, content) for pattern in GENERIC_CONTENT_PATTERNS):
        return False
    return True


def parse_ai_bot(session: requests.Session, target_dates: List[datetime.date]) -> List[Dict]:
    logging.info("抓取 AI工具集日报: %s", AI_BOT_URL)
    html_text = fetch_html(session, AI_BOT_URL)
    today = datetime.now()
    results: List[Dict] = []
    blocks = html_text.split('<div class="news-list">')[1:]
    item_pattern = re.compile(
        r'<div class="news-item">.*?'
        r'<h2><a href="([^"]+)"[^>]*>(.*?)</a></h2>.*?'
        r'<p class="text-muted text-sm">(.*?)'
        r'<span class="news-time text-xs">来源：(.*?)</span>',
        re.S,
    )
    for block in blocks:
        date_match = re.search(r'<div class="news-date">(.*?)</div>', block, re.S)
        if not date_match:
            continue
        date_label = clean_text(date_match.group(1))
        news_date = parse_ai_bot_date(date_label, today)
        if news_date not in target_dates:
            continue
        for match in item_pattern.finditer(block):
            url = normalize_url(AI_BOT_URL, match.group(1))
            if not url or "mp.weixin.qq.com" in url:
                continue
            list_title = compact_text(match.group(2))
            list_summary = compact_text(match.group(3))
            source = compact_text(match.group(4))
            try:
                page_data = fetch_page_data(session, url)
            except Exception as exc:
                logging.warning("跳过抓取失败的 AI工具集详情页: %s - %s", url, exc)
                continue
            title = choose_canonical_title(list_title, page_data.get("title", ""))
            content = page_data.get("content") or list_summary
            image = page_data.get("image", "")
            if not is_valid_item(title, content, image):
                continue
            results.append(
                {
                    "资讯标题": title,
                    "内容": compact_text(content),
                    "来源站点": "AI工具集",
                    "来源": source or "AI工具集",
                    "发布日期": news_date.isoformat(),
                    "原文链接": url,
                    "原始配图链接": image,
                    "原始标题": list_title,
                    "详情页标题": page_data.get("title", ""),
                    "详情页正文": compact_text(page_data.get("article_text", "")),
                }
            )
    logging.info("AI工具集有效数据 %s 条", len(results))
    return results


def extract_aibase_segment(html_text: str) -> str:
    match = re.search(r'initialDailyList\\":(\[.*?\]),\\"lang\\":', html_text, re.S)
    if not match:
        raise ValueError("未找到 initialDailyList 数据")
    return match.group(1)


def parse_aibase_daily_cards(segment: str) -> List[Dict]:
    pattern = re.compile(
        r'\{\\"Id\\":(\d+),\\"playtime\\":.*?'
        r'\\"addtime\\":\\"(.*?)\\",'
        r'\\"title\\":\\"(.*?)\\",'
        r'.*?\\"thumb\\":\\"(.*?)\\",'
        r'.*?\\"ailoglist\\":\[(.*?)\],'
        r'\\"Pv\\":\\"(.*?)\\"'
        r'\}',
        re.S,
    )
    cards = []
    for match in pattern.finditer(segment):
        cards.append(
            {
                "daily_id": int(match.group(1)),
                "addtime": clean_text(match.group(2)),
                "title": clean_text(match.group(3)),
                "thumb": normalize_url(AIBASE_DAILY_URL, match.group(4)),
                "ailoglist": match.group(5),
            }
        )
    return cards


def parse_aibase_ailog_items(raw_ailoglist: str) -> List[Dict]:
    pattern = re.compile(
        r'\{\\"Id\\":(\d+),\\"title\\":\\"(.*?)\\",\\"addtime\\":\\"(.*?)\\"\}',
        re.S,
    )
    items = []
    for match in pattern.finditer(raw_ailoglist):
        items.append(
            {
                "news_id": int(match.group(1)),
                "title": clean_text(match.group(2)),
                "addtime": clean_text(match.group(3)),
            }
        )
    return items


def parse_aibase(session: requests.Session, target_dates: List[datetime.date]) -> List[Dict]:
    logging.info("抓取 AIbase 日报: %s", AIBASE_DAILY_URL)
    html_text = fetch_html(session, AIBASE_DAILY_URL)
    segment = extract_aibase_segment(html_text)
    cards = parse_aibase_daily_cards(segment)
    results: List[Dict] = []
    for card in cards:
        try:
            card_date = datetime.strptime(card["addtime"], "%Y/%m/%d %H:%M:%S").date()
        except ValueError:
            continue
        if card_date not in target_dates:
            continue
        for ailog in parse_aibase_ailog_items(card["ailoglist"]):
            news_url = AIBASE_NEWS_URL.format(news_id=ailog["news_id"])
            try:
                page_data = fetch_page_data(session, news_url)
            except Exception as exc:
                logging.warning("跳过抓取失败的 AIbase 详情页: %s - %s", news_url, exc)
                continue
            list_title = compact_text(ailog["title"])
            title = choose_canonical_title(list_title, page_data.get("title", ""))
            content = page_data.get("content", "")
            image = page_data.get("image", "")
            if not is_valid_item(title, content, image):
                continue
            results.append(
                {
                    "资讯标题": title,
                    "内容": compact_text(content),
                    "来源站点": "AIbase",
                    "来源": "AIbase",
                    "发布日期": card_date.isoformat(),
                    "原文链接": news_url,
                    "原始配图链接": image,
                    "原始标题": list_title,
                    "详情页标题": page_data.get("title", ""),
                    "详情页正文": compact_text(page_data.get("article_text", "")),
                }
            )
    logging.info("AIbase 有效数据 %s 条", len(results))
    return results


def normalize_dedupe_text(value: str) -> str:
    text = compact_text(value).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize_for_dedupe(value: str) -> List[str]:
    text = normalize_dedupe_text(value)
    return re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text)


def named_tokens(value: str) -> set:
    tokens = set()
    for token in re.findall(r"[a-z0-9]{3,}", compact_text(value).lower()):
        if token in LATIN_STOPWORDS:
            continue
        tokens.add(token)
    return tokens


def fingerprint_text(title: str, content: str) -> str:
    tokens = tokenize_for_dedupe(title) + tokenize_for_dedupe(content)
    if not tokens:
        return ""
    counts = Counter(tokens)
    common = [token for token, _ in counts.most_common(12)]
    return "|".join(common)


def content_similarity(left: Dict, right: Dict) -> float:
    left_tokens = set(tokenize_for_dedupe(left["资讯标题"] + " " + left["内容"]))
    right_tokens = set(tokenize_for_dedupe(right["资讯标题"] + " " + right["内容"]))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def quality_score(item: Dict) -> Tuple[int, int, int]:
    return (
        len(item.get("内容", "")),
        len(item.get("资讯标题", "")),
        1 if item.get("详情页标题") else 0,
    )


def heuristic_dedupe(items: List[Dict]) -> List[Dict]:
    if not items:
        return items
    by_url: Dict[str, Dict] = {}
    for item in items:
        current = by_url.get(item["原文链接"])
        if not current or quality_score(item) > quality_score(current):
            by_url[item["原文链接"]] = item
    unique_items = list(by_url.values())
    unique_items.sort(key=quality_score, reverse=True)
    kept: List[Dict] = []
    seen_fingerprints = set()
    for item in unique_items:
        fp = fingerprint_text(item["资讯标题"], item["内容"])
        if fp and fp in seen_fingerprints:
            continue
        duplicated = False
        for kept_item in kept:
            same_title = title_similarity(item["资讯标题"], kept_item["资讯标题"]) >= 0.3
            same_content = content_similarity(item, kept_item) >= 0.42
            source_title_match = title_similarity(
                item.get("原始标题", item["资讯标题"]),
                kept_item.get("原始标题", kept_item["资讯标题"]),
            ) >= 0.3
            shared_named_tokens = named_tokens(item["资讯标题"]) & named_tokens(kept_item["资讯标题"])
            same_named_event = len(shared_named_tokens) >= 2
            if (same_title and same_content) or (source_title_match and same_content) or same_named_event:
                duplicated = True
                break
        if duplicated:
            continue
        if fp:
            seen_fingerprints.add(fp)
        kept.append(item)
    logging.info("规则去重后剩余 %s 条", len(kept))
    return kept


def chunked(sequence: Sequence[Dict], size: int) -> List[List[Dict]]:
    return [list(sequence[index:index + size]) for index in range(0, len(sequence), size)]


def has_concrete_news_elements(title: str, content: str) -> bool:
    combined = f"{compact_text(title)} {compact_text(content)}"
    action_hits = len(NEWS_ACTION_PATTERN.findall(combined))
    subject_hits = len(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", combined))
    return action_hits >= 1 and subject_hits >= 4


def rewrite_overlap_score(original_title: str, original_content: str, rewritten_title: str, rewritten_content: str) -> float:
    original_tokens = set(tokenize_for_dedupe(f"{original_title} {original_content}"))
    rewritten_tokens = set(tokenize_for_dedupe(f"{rewritten_title} {rewritten_content}"))
    if not original_tokens or not rewritten_tokens:
        return 0.0
    return len(original_tokens & rewritten_tokens) / max(1, len(rewritten_tokens))


def build_fallback_rewrite(item: Dict) -> Optional[Dict]:
    title = compact_text(item["资讯标题"])
    source_text = compact_text(item.get("详情页正文") or item["内容"])
    if not title or len(source_text) < MIN_CONTENT_LENGTH:
        return None
    sentences = split_sentences(source_text)
    merged_parts: List[str] = []
    current_length = 0
    for sentence in sentences:
        extra = len(sentence) + (1 if merged_parts else 0)
        if current_length + extra > MAX_CONTENT_LENGTH:
            break
        merged_parts.append(sentence)
        current_length += extra
        if current_length >= TARGET_CONTENT_LENGTH:
            break
    if not merged_parts:
        merged_parts = [source_text[:MAX_CONTENT_LENGTH].rstrip("，,；; ")]
    content = "。".join(part.strip("。") for part in merged_parts if part).strip()
    if content and not content.endswith(("。", "！", "？")):
        content += "。"
    if not has_concrete_news_elements(title, content):
        return None
    new_item = dict(item)
    new_item["资讯标题"] = title
    new_item["内容"] = content[:MAX_CONTENT_LENGTH]
    return new_item


def rewrite_items_to_chinese(items: List[Dict]) -> List[Dict]:
    if not items:
        return items
    rewritten_items: List[Dict] = []
    for item in items:
        source_text = compact_text(item.get("详情页正文") or item["内容"])
        prompt = (
            "你是中文科技日报编辑。请根据下面这条新闻的标题和详情页正文，输出一条具体、清楚、可读的中文科技新闻。"
            "必须严格基于输入事实，不允许编造，不允许串到别的新闻。"
            "标题必须直接说明新闻事件本身，写清主体和动作，禁止抽象总结。"
            "内容必须写成约300字的详细新闻摘要，优先覆盖：主体是谁、发生了什么、涉及什么产品或功能、官方怎么说、对用户或行业有什么直接影响。"
            "内容尽量保留详情页中的关键细节，不能只写泛泛结论。"
            "如果原文是产品更新、发布、开源、融资、测试、接入、合作，标题和内容里必须明确出现对应事件。"
            "不要出现任何英文字母、英文缩写、英文品牌名、英文模型名，必要时请自然中文意译。"
            "标题控制在18到34个中文字符。"
            "内容控制在220到360个中文字符。"
            "内容首句必须直接交代核心新闻事实。"
            "返回 JSON："
            '{"title":"...","content":"..."}'
            "\n输入标题："
            f"{json.dumps(item['资讯标题'], ensure_ascii=False)}"
            "\n输入正文："
            f"{json.dumps(source_text, ensure_ascii=False)}"
        )
        try:
            result = call_gemini_json(prompt)
            title = compact_text(str(result.get("title", "")))
            content = compact_text(str(result.get("content", "")))
            if not title or not content:
                raise ValueError("missing title or content")
            if re.search(r"[A-Za-z]", title) or re.search(r"[A-Za-z]", content):
                raise ValueError("contains latin chars")
            if not has_concrete_news_elements(title, content):
                raise ValueError("rewritten item is too abstract")
            if rewrite_overlap_score(item["资讯标题"], source_text, title, content) < 0.12:
                raise ValueError("rewritten item drifted from source")
            new_item = dict(item)
            new_item["资讯标题"] = title
            new_item["内容"] = content[:MAX_CONTENT_LENGTH]
            rewritten_items.append(new_item)
        except Exception as exc:
            logging.warning("单条新闻 AI 改写失败，回退规则摘要: %s - %s", item["原文链接"], exc)
            fallback_item = build_fallback_rewrite(item)
            if fallback_item:
                rewritten_items.append(fallback_item)
            else:
                logging.warning("单条新闻回退失败，跳过: %s", item["原文链接"])
    return rewritten_items


def dedupe_items_with_ai(items: List[Dict]) -> List[Dict]:
    if not items:
        return items
    kept_indices = set()
    for batch in chunked(items, 20):
        payload = [
            {
                "index": index,
                "title": item["资讯标题"],
                "content": item["内容"],
                "source": item["来源站点"],
                "date": item["发布日期"],
                "original_title": item.get("原始标题", ""),
            }
            for index, item in enumerate(batch)
        ]
        prompt = (
            "你是科技新闻去重编辑。请从下面新闻数组中删除语义重复、主体相同、只是换了表述的重复报道。"
            "判断时同时参考标题、原始标题和内容，保留信息更完整的一条。不同公司、不同产品、不同投融资主体、不同功能发布，不算重复。返回 JSON："
            '{"keep_indices":[0,2,5]}'
            "\n输入："
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            result = call_gemini_json(prompt)
            batch_keep = {
                index
                for index in result.get("keep_indices", [])
                if isinstance(index, int) and 0 <= index < len(batch)
            }
        except Exception as exc:
            logging.warning("AI 去重失败，保留当前批次全部新闻: %s", exc)
            batch_keep = set(range(len(batch)))
        if not batch_keep:
            batch_keep = set(range(len(batch)))
        for index in batch_keep:
            kept_indices.add(id(batch[index]))
    deduped = [item for item in items if id(item) in kept_indices]
    if not deduped:
        logging.warning("AI 去重结果为空，回退为规则去重结果。")
        return items
    logging.info("AI 去重后剩余 %s 条", len(deduped))
    return deduped


def guess_extension(image_url: str, content_type: str) -> str:
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ".jpg" if ext == ".jpe" else ext
    path = urlparse(image_url).path
    ext = Path(path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def build_github_raw_url(relative_path: str) -> str:
    if not GITHUB_REPOSITORY:
        return ""
    ref = GITHUB_REF_NAME or GITHUB_SHA
    if not ref:
        return ""
    return f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{ref}/{relative_path}"


def download_image(session: requests.Session, image_url: str) -> Dict[str, str]:
    response = session.get(image_url, timeout=REQUEST_TIMEOUT, stream=True)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    extension = guess_extension(image_url, content_type)
    digest = hashlib.md5(image_url.encode("utf-8")).hexdigest()
    file_name = f"{digest}{extension}"
    file_path = IMAGE_DIR / file_name
    with open(file_path, "wb") as file:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                file.write(chunk)
    relative_path = file_path.relative_to(WORKDIR).as_posix()
    return {
        "absolute_path": str(file_path.resolve()),
        "relative_path": relative_path,
        "github_raw_url": build_github_raw_url(relative_path),
    }


def attach_downloaded_images(session: requests.Session, items: List[Dict]) -> List[Dict]:
    final_items: List[Dict] = []
    for item in items:
        image_url = item.get("原始配图链接", "")
        if not image_url:
            continue
        try:
            saved = download_image(session, image_url)
        except Exception as exc:
            logging.warning("跳过图片下载失败的新闻: %s - %s", item["资讯标题"], exc)
            continue
        final_item = {
            "资讯标题": item["资讯标题"],
            "内容": item["内容"],
            "来源站点": item["来源站点"],
            "来源": item["来源"],
            "发布日期": item["发布日期"],
            "原文链接": item["原文链接"],
            "配图": saved["github_raw_url"] or saved["absolute_path"],
            "配图本地路径": saved["absolute_path"],
            "配图仓库路径": saved["relative_path"],
            "原始配图链接": image_url,
        }
        final_items.append(final_item)
    return final_items


def sort_items(items: List[Dict]) -> List[Dict]:
    return sorted(
        items,
        key=lambda item: (item["发布日期"], item["来源站点"], item["资讯标题"]),
        reverse=True,
    )


def main() -> None:
    ensure_dirs()
    require_gemini_api_key()
    target_dates = parse_target_dates(days=2)
    session = build_session()
    all_items: List[Dict] = []
    all_items.extend(parse_ai_bot(session, target_dates))
    all_items.extend(parse_aibase(session, target_dates))
    if not all_items:
        raise RuntimeError("没有抓取到可验证的完整新闻数据。")
    filtered_items = heuristic_dedupe(all_items)
    rewritten_items = rewrite_items_to_chinese(filtered_items)
    if not rewritten_items:
        logging.warning("AI 中文改写阶段没有产出有效数据，回退到规则摘要结果。")
        rewritten_items = [item for item in (build_fallback_rewrite(source) for source in filtered_items) if item]
    if not rewritten_items:
        raise RuntimeError("改写与回退后都没有留下有效数据。")
    deduped_items = heuristic_dedupe(rewritten_items)
    deduped_items = dedupe_items_with_ai(deduped_items)
    downloaded_items = attach_downloaded_images(session, deduped_items)
    final_items = sort_items(downloaded_items)
    if not final_items:
        raise RuntimeError("图片下载后没有留下完整有效数据。")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(final_items, file, ensure_ascii=False, indent=2)
    logging.info("完成，最终输出 %s 条新闻到 %s", len(final_items), OUTPUT_FILE)


if __name__ == "__main__":
    main()
