"""Content extraction from HTML pages."""
from __future__ import annotations

import logging
import re
from typing import Dict, List

from daily_tech.config import (
    ARTICLE_BLOCK_PATTERNS,
    MAX_CONTENT_LENGTH,
    MIN_CONTENT_LENGTH,
    TARGET_CONTENT_LENGTH,
)
from daily_tech.image_processor import extract_best_image
from daily_tech.utils import (
    clean_text,
    compact_text,
    fetch_html,
    split_sentences,
    strip_noise_lines,
)


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


def choose_canonical_title(source_title: str, page_title: str) -> str:
    from daily_tech.utils import title_similarity
    source_title = compact_text(source_title)
    page_title = compact_text(page_title)
    if not page_title:
        return source_title
    if not source_title:
        return page_title
    if title_similarity(source_title, page_title) >= 0.35:
        return page_title if len(page_title) >= len(source_title) else source_title
    return source_title


def fetch_page_data(session, url: str) -> Dict[str, str]:
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
    from daily_tech.utils import compact_text, has_concrete_news_elements
    from daily_tech.config import GENERIC_CONTENT_PATTERNS
    # 图片不是必须的，但标题和内容必须有
    if not title or not content:
        return False
    if len(compact_text(content)) < MIN_CONTENT_LENGTH:
        return False
    if any(re.search(pattern, content) for pattern in GENERIC_CONTENT_PATTERNS):
        return False
    return True
