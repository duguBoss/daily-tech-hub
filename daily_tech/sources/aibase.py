"""AIbase news source parser - fetch from news list page."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

import requests

from daily_tech.config import SHANGHAI_TZ
from daily_tech.content_extractor import (
    choose_canonical_title,
    fetch_page_data,
    is_valid_item,
)
from daily_tech.utils import clean_text, compact_text, fetch_html, normalize_url

AIBASE_NEWS_LIST_URL = "https://news.aibase.com/zh/news"
AIBASE_NEWS_URL = "https://www.aibase.com/zh/news/{news_id}"


def extract_news_list_from_html(html_text: str) -> List[Dict]:
    """Extract news list from the news list page HTML."""
    # Try to find JSON data in the HTML
    # Look for patterns like: {"id":123,"title":"...","thumb":"...","addtime":"..."}
    
    # Pattern 1: Look for initial state or data in script tags
    patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*({.+?});',
        r'window\.__DATA__\s*=\s*({.+?});',
        r'"newsList":\s*(\[.+?\])',
        r'"list":\s*(\[.+?\])',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html_text, re.S)
        if match:
            try:
                data = json.loads(match.group(1))
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    # Try common keys
                    for key in ['newsList', 'list', 'data', 'items']:
                        if key in data and isinstance(data[key], list):
                            return data[key]
            except json.JSONDecodeError:
                continue
    
    # Pattern 2: Extract from HTML structure
    # Look for news items in the page
    news_items = []
    
    # Try to find news cards with various patterns
    card_patterns = [
        r'<a[^>]+href="/zh/news/(\d+)"[^>]*>.*?<h[1-6][^>]*>(.*?)</h[1-6]>.*?<img[^>]+src="([^"]+)"',
        r'<div[^>]+class="[^"]*news[^"]*"[^>]*>.*?<a[^>]+href="/zh/news/(\d+)"[^>]*>.*?<img[^>]+src="([^"]+)"[^>]*>.*?<h[1-6][^>]*>(.*?)</h[1-6]>',
    ]
    
    for pattern in card_patterns:
        matches = re.finditer(pattern, html_text, re.S | re.I)
        for match in matches:
            try:
                news_id = match.group(1)
                # Try to determine which group is title and which is image
                group2, group3 = match.group(2), match.group(3)
                if group2.startswith('http'):
                    image, title = group2, group3
                else:
                    title, image = group2, group3
                
                news_items.append({
                    'id': int(news_id),
                    'title': clean_text(title),
                    'thumb': normalize_url(AIBASE_NEWS_LIST_URL, image),
                })
            except (ValueError, IndexError):
                continue
    
    return news_items


def parse_aibase_addtime(value: str) -> Optional[datetime.date]:
    """Parse date string to date object."""
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
    """Fetch news from AIbase news list page."""
    logging.info("抓取 AIbase 新闻列表: %s", AIBASE_NEWS_LIST_URL)
    
    try:
        html_text = fetch_html(session, AIBASE_NEWS_LIST_URL)
    except Exception as exc:
        logging.error("获取 AIbase 新闻列表失败: %s", exc)
        return []
    
    # Extract news items from the page
    news_items = extract_news_list_from_html(html_text)
    
    if not news_items:
        logging.warning("未能从 AIbase 新闻列表提取到数据，尝试备用方法")
        # Fallback: try to find news links directly
        news_links = re.findall(r'href="/zh/news/(\d+)"', html_text)
        news_links = list(set(news_links))[:20]  # Get first 20 unique news IDs
        news_items = [{'id': int(nid)} for nid in news_links]
    
    logging.info("从 AIbase 新闻列表获取到 %s 条新闻", len(news_items))
    
    results: List[Dict] = []
    today = datetime.now(SHANGHAI_TZ).date()
    
    for item in news_items[:15]:  # Process first 15 news items
        news_id = item.get('id')
        if not news_id:
            continue
            
        news_url = AIBASE_NEWS_URL.format(news_id=news_id)
        
        try:
            page_data = fetch_page_data(session, news_url)
        except Exception as exc:
            logging.warning("跳过抓取失败的 AIbase 详情页: %s - %s", news_url, exc)
            continue
        
        # Use list title if available, otherwise use page title
        list_title = compact_text(item.get('title', ''))
        page_title = page_data.get('title', '')
        title = choose_canonical_title(list_title, page_title) if list_title else page_title
        
        if not title:
            continue
            
        content = page_data.get('content', '')
        
        # Use list image if available, otherwise use page image
        list_image = item.get('thumb', '')
        page_image = page_data.get('image', '')
        image = list_image or page_image
        
        if not is_valid_item(title, content, image):
            continue
        
        # Try to extract date from page, default to today
        article_text = page_data.get('article_text', '')
        pub_date = parse_aibase_addtime(article_text) or today
        
        # Only include if date is in target_dates (or if no target_dates specified)
        if target_dates and pub_date not in target_dates:
            # If news is from today or yesterday, include it anyway
            if (today - pub_date).days > 1:
                continue
        
        results.append(
            {
                "资讯标题": title,
                "内容": compact_text(content),
                "来源站点": "AIbase",
                "来源": "AIbase",
                "发布日期": pub_date.isoformat(),
                "原文链接": news_url,
                "原始配图链接": image,
                "原始标题": list_title or title,
                "详情页标题": page_title,
                "详情页正文": compact_text(article_text),
            }
        )
    
    logging.info("AIbase 有效数据 %s 条", len(results))
    return results
