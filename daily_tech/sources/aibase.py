"""AIbase news source parser."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

import requests

from daily_tech.config import AIBASE_DAILY_URL, AIBASE_NEWS_URL, SHANGHAI_TZ
from daily_tech.content_extractor import (
    choose_canonical_title,
    fetch_page_data,
    is_valid_item,
)
from daily_tech.utils import clean_text, compact_text, fetch_html, normalize_url


def extract_aibase_segment(html_text: str) -> str:
    match = re.search(r'initialDailyList\\":(\[.*?\]),\\"lang\\":', html_text, re.S)
    if not match:
        raise ValueError("未找到 initialDailyList 数据")
    return match.group(1)


def parse_aibase_daily_cards(segment: str) -> List[Dict]:
    pattern = re.compile(
        r'\\{\\"Id\\":(\d+),\\"playtime\\":.*?'
        r'\\"addtime\\":\\"(.*?)\\",'
        r'\\"title\\":\\"(.*?)\\",'
        r'.*?\\"thumb\\":\\"(.*?)\\",'
        r'.*?\\"ailoglist\\":\[(.*?)\],'
        r'\\"Pv\\":\\"(.*?)\\"'
        r'\\}',
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
        r'\\{\\"Id\\":(\d+),\\"title\\":\\"(.*?)\\",\\"addtime\\":\\"(.*?)\\"\\}',
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


def parse_aibase_addtime(value: str) -> Optional[datetime.date]:
    text = compact_text(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo:
            return parsed.astimezone(SHANGHAI_TZ).date()
        return parsed.date()
    except ValueError:
        pass
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
    except ValueError:
        return None


def parse_aibase(session: requests.Session, target_dates: List[datetime.date]) -> List[Dict]:
    logging.info("抓取 AIbase 日报: %s", AIBASE_DAILY_URL)
    html_text = fetch_html(session, AIBASE_DAILY_URL)
    segment = extract_aibase_segment(html_text)
    cards = parse_aibase_daily_cards(segment)
    results: List[Dict] = []
    for card in cards:
        card_date = parse_aibase_addtime(card["addtime"])
        if not card_date:
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
