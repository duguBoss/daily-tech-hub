"""AI Bot news source parser."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

import requests

from daily_tech.config import AI_BOT_URL, SHANGHAI_TZ
from daily_tech.content_extractor import (
    choose_canonical_title,
    fetch_page_data,
    is_valid_item,
)
from daily_tech.utils import clean_text, compact_text, normalize_url, parse_ai_bot_date


def parse_ai_bot(session: requests.Session, target_dates: List[datetime.date]) -> List[Dict]:
    logging.info("抓取 AI工具集日报: %s", AI_BOT_URL)
    from daily_tech.utils import fetch_html
    html_text = fetch_html(session, AI_BOT_URL)
    today = datetime.now(SHANGHAI_TZ)
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
