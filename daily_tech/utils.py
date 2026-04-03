"""Utility functions for text processing and common operations."""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from daily_tech.config import (
    GENERIC_CONTENT_PATTERNS,
    REQUEST_HEADERS,
    SHANGHAI_TZ,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def build_session():
    import requests
    session = requests.Session()
    session.trust_env = False
    session.headers.update(REQUEST_HEADERS)
    return session


def ensure_dirs() -> None:
    from daily_tech.config import OUTPUT_DIR, IMAGE_DIR
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_images_if_saturday() -> None:
    from daily_tech.config import IMAGE_DIR
    current_time = datetime.now(SHANGHAI_TZ)
    if current_time.weekday() != 5:
        return

    removed_count = 0
    for path in IMAGE_DIR.iterdir():
        if path.is_file():
            path.unlink()
            removed_count += 1

    logging.info("周六清理历史图片 %s 个文件", removed_count)


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


def fetch_html(session, url: str) -> str:
    from daily_tech.config import REQUEST_TIMEOUT
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


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


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"[。！？!?；;\n]+", clean_text(text))
    return [part.strip(" ，,：:") for part in parts if len(part.strip()) >= 8]


def tokenize_for_dedupe(value: str) -> List[str]:
    text = compact_text(value).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text)


def named_tokens(value: str) -> set:
    from daily_tech.config import LATIN_STOPWORDS
    tokens = set()
    for token in re.findall(r"[a-z0-9]{3,}", compact_text(value).lower()):
        if token in LATIN_STOPWORDS:
            continue
        tokens.add(token)
    return tokens


def fingerprint_text(title: str, content: str) -> str:
    from collections import Counter
    tokens = tokenize_for_dedupe(title) + tokenize_for_dedupe(content)
    if not tokens:
        return ""
    counts = Counter(tokens)
    common = [token for token, _ in counts.most_common(12)]
    return "|".join(common)


def title_tokens(value: str) -> set:
    text = compact_text(value).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s*-\s*[^-]{1,20}$", " ", text)
    return set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text))


def title_similarity(left: str, right: str) -> float:
    left_tokens_set = title_tokens(left)
    right_tokens_set = title_tokens(right)
    if not left_tokens_set or not right_tokens_set:
        return 0.0
    return len(left_tokens_set & right_tokens_set) / len(left_tokens_set | right_tokens_set)


def has_concrete_news_elements(title: str, content: str) -> bool:
    from daily_tech.config import NEWS_ACTION_PATTERN
    combined = f"{compact_text(title)} {compact_text(content)}"
    action_hits = len(NEWS_ACTION_PATTERN.findall(combined))
    subject_hits = len(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", combined))
    return action_hits >= 1 and subject_hits >= 4


def has_source_attribution(text: str) -> bool:
    normalized = compact_text(text)
    return bool(
        re.search(
            r"(来源[:：]|原文(?:链接)?|转载(?:自)?|据[^，。；;\n]{1,24}(?:报道|消息)|据悉)",
            normalized,
            re.I,
        )
    )


def rewrite_overlap_score(
    original_title: str, original_content: str,
    rewritten_title: str, rewritten_content: str
) -> float:
    original_tokens = set(tokenize_for_dedupe(f"{original_title} {original_content}"))
    rewritten_tokens = set(tokenize_for_dedupe(f"{rewritten_title} {rewritten_content}"))
    if not original_tokens or not rewritten_tokens:
        return 0.0
    return len(original_tokens & rewritten_tokens) / max(1, len(rewritten_tokens))


def content_similarity(left: Dict, right: Dict) -> float:
    """Calculate similarity between two news items based on title and content."""
    left_tokens = set(tokenize_for_dedupe(left["资讯标题"] + " " + left["内容"]))
    right_tokens = set(tokenize_for_dedupe(right["资讯标题"] + " " + right["内容"]))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
