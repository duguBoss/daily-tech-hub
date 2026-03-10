import hashlib
import html
import json
import logging
import mimetypes
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
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


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def require_gemini_api_key() -> None:
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "缺少 GEMINI_API_KEY。当前任务要求 AI 纯中文改写和 AI 语义去重，"
            "未配置该环境变量时不会输出结果。"
        )


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<.*?>", "", value, flags=re.S)
    value = value.replace("\u200b", "").replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


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
        "generationConfig": {"responseMimeType": "application/json"},
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


def fetch_page_data(session: requests.Session, url: str) -> Dict[str, str]:
    html_text = fetch_html(session, url)
    data: Dict[str, str] = {"html": html_text}

    title_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html_text, re.I | re.S)
    desc_match = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', html_text, re.I | re.S)

    if title_match:
        data["title"] = clean_text(title_match.group(1))
    if desc_match:
        data["description"] = clean_text(desc_match.group(1))

    image = extract_best_image(url, html_text)
    if image:
        data["image"] = image
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
            if not url or "mp.weixin.qq.com" in url:
                continue

            title = clean_text(match.group(2))
            summary = clean_text(match.group(3))
            source = clean_text(match.group(4))

            try:
                page_data = fetch_page_data(session, url)
            except Exception as exc:
                logging.warning("跳过抓取失败的 AI工具集详情页: %s - %s", url, exc)
                continue

            image = page_data.get("image", "")
            content = page_data.get("description") or summary
            if not image or not content:
                continue

            results.append(
                {
                    "资讯标题": title,
                    "内容": content,
                    "来源站点": "AI工具集",
                    "来源": source or "AI工具集",
                    "发布日期": news_date.isoformat(),
                    "原文链接": url,
                    "原始配图链接": image,
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

            image = page_data.get("image", "")
            content = page_data.get("description", "")
            if not image or not content:
                continue

            results.append(
                {
                    "资讯标题": ailog["title"],
                    "内容": content,
                    "来源站点": "AIbase",
                    "来源": "AIbase",
                    "发布日期": card_date.isoformat(),
                    "原文链接": news_url,
                    "原始配图链接": image,
                }
            )

    logging.info("AIbase 有效数据 %s 条", len(results))
    return results


def rewrite_items_to_chinese(items: List[Dict]) -> List[Dict]:
    if not items:
        return items

    payload = [
        {"index": index, "title": item["资讯标题"], "content": item["内容"]}
        for index, item in enumerate(items)
    ]
    prompt = (
        "你是中文科技日报编辑。请将下面数组中的每条新闻标题和内容改写为纯中文。"
        "不要出现任何英文字母、英文缩写、英文品牌名、英文模型名。"
        "必要时请用自然的中文意译。"
        "标题控制在 18 到 36 个中文字符。"
        "内容控制在 70 到 120 个中文字符。"
        "返回 JSON："
        '{"items":[{"index":0,"title":"...","content":"..."}]}'
        "\n输入："
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    result = call_gemini_json(prompt)
    rewritten_map = {item["index"]: item for item in result["items"]}

    rewritten_items: List[Dict] = []
    for index, item in enumerate(items):
        rewritten = rewritten_map.get(index)
        if not rewritten:
            continue
        title = clean_text(str(rewritten.get("title", "")))
        content = clean_text(str(rewritten.get("content", "")))
        if not title or not content:
            continue
        if re.search(r"[A-Za-z]", title) or re.search(r"[A-Za-z]", content):
            continue

        new_item = dict(item)
        new_item["资讯标题"] = title
        new_item["内容"] = content
        rewritten_items.append(new_item)

    return rewritten_items


def dedupe_items_with_ai(items: List[Dict]) -> List[Dict]:
    if not items:
        return items

    payload = [
        {
            "index": index,
            "title": item["资讯标题"],
            "content": item["内容"],
            "source": item["来源站点"],
            "date": item["发布日期"],
        }
        for index, item in enumerate(items)
    ]
    prompt = (
        "你是科技新闻去重编辑。请从下面新闻数组中删除语义重复、主体相同、只是换了表述的重复报道。"
        "保留信息最完整的一条。"
        "返回 JSON："
        '{"keep_indices":[0,2,5]}'
        "\n输入："
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    result = call_gemini_json(prompt)
    keep_indices = {
        index
        for index in result.get("keep_indices", [])
        if isinstance(index, int) and 0 <= index < len(items)
    }
    if not keep_indices:
        raise RuntimeError("AI 去重结果为空，停止输出，避免生成无效数据。")
    return [item for index, item in enumerate(items) if index in keep_indices]


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

    rewritten_items = rewrite_items_to_chinese(all_items)
    if not rewritten_items:
        raise RuntimeError("AI 纯中文改写后没有留下有效数据。")

    deduped_items = dedupe_items_with_ai(rewritten_items)
    downloaded_items = attach_downloaded_images(session, deduped_items)
    final_items = sort_items(downloaded_items)

    if not final_items:
        raise RuntimeError("图片下载后没有留下完整有效数据。")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(final_items, file, ensure_ascii=False, indent=2)

    logging.info("完成，最终输出 %s 条新闻到 %s", len(final_items), OUTPUT_FILE)


if __name__ == "__main__":
    main()
