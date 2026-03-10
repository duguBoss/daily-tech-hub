import html
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


OUTPUT_FILE = "data/daily_ai_news.json"
AI_BOT_URL = "https://ai-bot.cn/daily-ai-news/"
AIBASE_DAILY_URL = "https://www.aibase.com/zh/daily"
AIBASE_NEWS_URL = "https://www.aibase.com/news/{news_id}"
REQUEST_TIMEOUT = 30
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
}


def build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(REQUEST_HEADERS)
    return session


def ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<.*?>", "", value, flags=re.S)
    value = value.replace("\u200b", "").replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


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


def fetch_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def normalize_url(base_url: str, raw_url: str) -> str:
    raw_url = html.unescape((raw_url or "").strip())
    if not raw_url:
        return ""
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return urljoin(base_url, raw_url)


def image_score(src: str, tag: str, context: str) -> int:
    src_lower = src.lower()
    tag_lower = tag.lower()
    context_lower = context.lower()
    score = 0

    if src_lower.startswith("data:"):
        return -100
    if any(flag in src_lower for flag in ["logo", "icon", "avatar", "favicon", "shape", "gaba"]):
        score -= 80
    if src_lower.endswith(".svg"):
        score -= 60
    if any(flag in src_lower for flag in ["banner", "ads", "advert"]):
        score -= 25
    if "data-nimg=\"fill\"" in tag_lower or "object-cover opacity-0" in tag_lower:
        score -= 30
    if any(flag in src_lower for flag in ["upload.chinaz.com", "mmbiz.qpic.cn", "qpic.cn", "wp-content/uploads"]):
        score += 40
    if "data-src=" in tag_lower or "data-original=" in tag_lower:
        score += 25
    if any(flag in tag_lower for flag in ["wp-image", "alignnone", "aligncenter", "lazy unfancybox"]):
        score += 15
    if any(flag in context_lower for flag in ["entry-content", "article-content", "site-content", "post-content", "js_content"]):
        score += 20
    if any(flag in tag_lower for flag in ['width="740"', 'width="750"', 'width="800"', 'height="416"', 'height="496"']):
        score += 10

    return score


def extract_best_image(base_url: str, html_text: str) -> str:
    candidates = []
    pattern = re.compile(r"<img\b([^>]+)>", re.I | re.S)
    for match in pattern.finditer(html_text):
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


def fetch_page_data(session: requests.Session, url: str) -> Dict[str, str]:
    try:
        html_text = fetch_html(session, url)
    except Exception as exc:
        logging.warning("详情页抓取失败: %s - %s", url, exc)
        return {}

    data: Dict[str, str] = {"html": html_text}
    title_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html_text, re.I | re.S)
    desc_match = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', html_text, re.I | re.S)
    image_match = re.search(
        r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"|'
        r'<meta[^>]+name="twitter:image"[^>]+content="([^"]+)"',
        html_text,
        re.I | re.S,
    )

    if title_match:
        data["title"] = clean_text(title_match.group(1))
    if desc_match:
        data["description"] = clean_text(desc_match.group(1))
    if image_match:
        raw_image = next((value for value in image_match.groups() if value), "")
        data["meta_image"] = normalize_url(url, raw_image)

    best_image = extract_best_image(url, html_text)
    if best_image:
        data["image"] = best_image
    elif data.get("meta_image"):
        data["image"] = data["meta_image"]

    return data


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
            title = clean_text(match.group(2))
            summary = clean_text(match.group(3))
            source = clean_text(match.group(4))

            page_data = fetch_page_data(session, url)
            content = page_data.get("description") or summary
            image = page_data.get("image", "")

            results.append(
                {
                    "资讯标题": title,
                    "内容": content,
                    "来源站点": "AI工具集",
                    "来源": source,
                    "发布日期": news_date.isoformat(),
                    "原文链接": url,
                    "配图": [image] if image else [],
                }
            )

    logging.info("AI工具集命中 %s 条", len(results))
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
            logging.warning("无法解析 AIbase 日报日期: %s", card["addtime"])
            continue

        if card_date not in target_dates:
            continue

        for ailog in parse_aibase_ailog_items(card["ailoglist"]):
            news_url = AIBASE_NEWS_URL.format(news_id=ailog["news_id"])
            page_data = fetch_page_data(session, news_url)
            content = page_data.get("description") or card["title"]
            image = page_data.get("image") or card["thumb"]

            results.append(
                {
                    "资讯标题": ailog["title"],
                    "内容": content,
                    "来源站点": "AIbase",
                    "来源": "AIbase",
                    "发布日期": card_date.isoformat(),
                    "原文链接": news_url,
                    "配图": [image] if image else [],
                }
            )

    logging.info("AIbase 命中 %s 条", len(results))
    return results


def dedupe_news(items: List[Dict]) -> List[Dict]:
    seen = set()
    deduped = []
    for item in items:
        key = (item["资讯标题"], item["原文链接"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def sort_news(items: List[Dict]) -> List[Dict]:
    return sorted(
        items,
        key=lambda item: (item["发布日期"], item["来源站点"], item["资讯标题"]),
        reverse=True,
    )


def main() -> None:
    ensure_data_dir()
    target_dates = parse_target_dates(days=2)
    session = build_session()

    all_news: List[Dict] = []
    all_news.extend(parse_ai_bot(session, target_dates))
    all_news.extend(parse_aibase(session, target_dates))

    final_data = sort_news(dedupe_news(all_news))
    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(final_data, file, ensure_ascii=False, indent=2)

    logging.info(
        "抓取完成，目标日期: %s，最终保存 %s 条新闻到 %s",
        ", ".join(date.isoformat() for date in target_dates),
        len(final_data),
        OUTPUT_FILE,
    )


if __name__ == "__main__":
    main()
