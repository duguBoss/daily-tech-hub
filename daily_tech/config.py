"""Configuration constants for daily tech news."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

try:
    from zoneinfo import ZoneInfo
    SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
except ImportError:
    try:
        from pytz import timezone as pytz_timezone
        SHANGHAI_TZ = pytz_timezone("Asia/Shanghai")
    except ImportError:
        SHANGHAI_TZ = timezone(timedelta(hours=8))


WORKDIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = WORKDIR / "data"
OUTPUT_FILE = OUTPUT_DIR / "daily_ai_news.json"
WEIXIN_TEMPLATE_FILE = OUTPUT_DIR / "daily_ai_news_weixin.json"
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
FETCH_DAYS = max(2, int(os.environ.get("FETCH_DAYS", "4")))

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
}

LATIN_STOPWORDS = {
    "with", "from", "that", "this", "into", "over", "more", "than",
    "will", "have", "has",
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


def parse_target_dates(days: int = 2) -> List[datetime.date]:
    today = datetime.now(SHANGHAI_TZ).date()
    return [today - timedelta(days=offset) for offset in range(days)]
