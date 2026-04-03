"""Image extraction and downloading functionality."""
from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

import requests
from PIL import Image

from daily_tech.config import (
    GITHUB_REF_NAME,
    GITHUB_REPOSITORY,
    GITHUB_SHA,
    IMAGE_DIR,
    REQUEST_TIMEOUT,
    WORKDIR,
)
from daily_tech.utils import compact_text, normalize_url

MAX_IMAGE_SIZE_MB = 5
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024


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
    import re
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


def download_image_bytes(session: requests.Session, image_url: str, max_retries: int = 3) -> bytes:
    """Download image bytes with retry logic."""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(image_url, timeout=REQUEST_TIMEOUT, stream=True)
            response.raise_for_status()
            content = b""
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    content += chunk
            return content
        except Exception as exc:
            last_error = exc
            logging.warning("图片下载尝试 %d/%d 失败: %s - %s", attempt, max_retries, image_url, exc)
    raise last_error if last_error else RuntimeError(f"下载失败: {image_url}")


def process_image_to_jpeg(image_bytes: bytes, max_size_mb: int = MAX_IMAGE_SIZE_MB) -> bytes:
    """Convert image to JPEG and compress if needed to stay under max_size_mb."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise ValueError(f"无法打开图片: {exc}")

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    quality = 95
    max_size_bytes = max_size_mb * 1024 * 1024

    while True:
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        output.seek(0)
        result_bytes = output.read()

        if len(result_bytes) <= max_size_bytes:
            return result_bytes

        if quality <= 30:
            width, height = img.size
            new_width = int(width * 0.9)
            new_height = int(height * 0.9)
            if new_width < 100 or new_height < 100:
                return result_bytes
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            quality = 95
        else:
            quality -= 10


def download_and_process_image(session: requests.Session, image_url: str) -> Dict[str, str]:
    """Download image, convert to JPG, ensure size < 5MB."""
    raw_bytes = download_image_bytes(session, image_url, max_retries=3)
    processed_bytes = process_image_to_jpeg(raw_bytes, max_size_mb=MAX_IMAGE_SIZE_MB)

    digest = hashlib.md5(image_url.encode("utf-8")).hexdigest()
    file_name = f"{digest}.jpg"
    file_path = IMAGE_DIR / file_name

    with open(file_path, "wb") as file:
        file.write(processed_bytes)

    relative_path = file_path.relative_to(WORKDIR).as_posix()
    return {
        "absolute_path": str(file_path.resolve()),
        "relative_path": relative_path,
        "github_raw_url": build_github_raw_url(relative_path),
        "size_bytes": len(processed_bytes),
    }


def build_github_raw_url(relative_path: str) -> str:
    if not GITHUB_REPOSITORY:
        return ""
    ref = GITHUB_REF_NAME or GITHUB_SHA
    if not ref:
        return ""
    return f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{ref}/{relative_path}"


def attach_downloaded_images(session: requests.Session, items: List[Dict]) -> List[Dict]:
    final_items: List[Dict] = []
    for item in items:
        image_url = item.get("原始配图链接", "")
        if not image_url:
            continue
        try:
            saved = download_and_process_image(session, image_url)
            logging.info("图片处理成功: %s -> %s (%.2f KB)", item["资讯标题"][:30], saved["relative_path"], saved["size_bytes"] / 1024)
        except Exception as exc:
            logging.error("图片处理失败，跳过新闻: %s - %s", item["资讯标题"], exc)
            continue
        final_item = {
            "资讯标题": item["资讯标题"],
            "内容": item["内容"],
            "发布日期": item["发布日期"],
            "原文链接": item["原文链接"],
            "配图": saved["github_raw_url"] or saved["absolute_path"],
            "配图本地路径": saved["absolute_path"],
            "配图仓库路径": saved["relative_path"],
            "原始配图链接": image_url,
        }
        final_items.append(final_item)
    return final_items
