import html
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


OUTPUT_FILE = "data/daily_ai_news.json"
AI_BOT_URL = "https://ai-bot.cn/daily-ai-news/"
AIBASE_DAILY_URL = "https://www.aibase.com/zh/daily"
AIBASE_NEWS_URL = "https://www.aibase.com/news/{news_id}"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 30


def build_session() -> requests.Session:
    session = requests.Session()
    # 避免系统代理配置异常导致请求直接失败。
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
    year = today.year
    if today.month == 1 and month == 12:
        return year - 1
    return year


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


def fetch_meta(session: requests.Session, url: str) -> Dict[str, str]:
    try:
        html_text = fetch_html(session, url)
    except Exception as exc:
        logging.warning("详情页抓取失败: %s - %s", url, exc)
        return {}

    meta = {}
    patterns = {
        "title": r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
        "description": r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"',
        "image": (
            r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"|'
            r'<meta[^>]+name="twitter:image"[^>]+content="([^"]+)"'
        ),
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, html_text, re.I | re.S)
        if not match:
            continue
        if key == "image":
            meta[key] = next((value for value in match.groups() if value), "")
        else:
            meta[key] = clean_text(match.group(1))
    return meta


def parse_ai_bot(session: requests.Session, target_dates: List[datetime.date]) -> List[Dict]:
    logging.info("抓取 AI工具集日报: %s", AI_BOT_URL)
    html_text = fetch_html(session, AI_BOT_URL)
    today = datetime.now()
    results: List[Dict] = []

    blocks = html_text.split('<div class="news-list">')[1:]
    for block in blocks:
        date_match = re.search(r'<div class="news-date">(.*?)</div>', block, re.S)
        if not date_match:
            continue

        date_label = clean_text(date_match.group(1))
        news_date = parse_ai_bot_date(date_label, today)
        if news_date not in target_dates:
            continue

        item_pattern = re.compile(
            r'<div class="news-item">.*?'
            r'<h2><a href="([^"]+)"[^>]*>(.*?)</a></h2>.*?'
            r'<p class="text-muted text-sm">(.*?)'
            r'<span class="news-time text-xs">来源：(.*?)</span>',
            re.S,
        )
        for match in item_pattern.finditer(block):
            url = html.unescape(match.group(1).strip())
            title = clean_text(match.group(2))
            summary = clean_text(match.group(3))
            source = clean_text(match.group(4))

            item = {
                "资讯标题": title,
                "内容": summary,
                "来源站点": "AI工具集",
                "来源": source,
                "发布日期": news_date.isoformat(),
                "原文链接": url,
                "配图": [],
            }

            if "ai-bot.cn" in url:
                meta = fetch_meta(session, url)
                if meta.get("description"):
                    item["内容"] = meta["description"]
                if meta.get("image"):
                    item["配图"] = [meta["image"]]

            results.append(item)

    logging.info("AI工具集命中 %s 条", len(results))
    return results


def extract_aibase_segment(html_text: str) -> str:
    match = re.search(
        r'initialDailyList\\":(\[.*?\]),\\"lang\\":',
        html_text,
        re.S,
    )
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
                "thumb": html.unescape(match.group(4).strip()),
                "ailoglist": match.group(5),
            }
        )
    return cards


def parse_aibase_ailog_items(raw_ailoglist: str) -> List[Dict]:
    item_pattern = re.compile(
        r'\{\\"Id\\":(\d+),\\"title\\":\\"(.*?)\\",\\"addtime\\":\\"(.*?)\\"\}',
        re.S,
    )
    items = []
    for match in item_pattern.finditer(raw_ailoglist):
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
            meta = fetch_meta(session, news_url)
            description = meta.get("description") or card["title"]
            image = meta.get("image") or card["thumb"]

            results.append(
                {
                    "资讯标题": ailog["title"],
                    "内容": description,
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
