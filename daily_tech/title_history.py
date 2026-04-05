"""History-backed title generation for daily tech headlines."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from daily_tech.config import SHANGHAI_TZ, WORKDIR
from daily_tech.utils import compact_text, title_similarity


TITLE_HISTORY_FILE = WORKDIR / "data" / "title_history.json"
MAX_HISTORY_ITEMS = 120


def _normalize_title(value: str) -> str:
    return compact_text(value).replace(" ", "")


def load_title_history(path: Path = TITLE_HISTORY_FILE) -> List[Dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def save_title_history(history: List[Dict], path: Path = TITLE_HISTORY_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history[-MAX_HISTORY_ITEMS:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_similar_to_history(candidate: str, history: List[Dict]) -> bool:
    normalized_candidate = _normalize_title(candidate)
    if not normalized_candidate:
        return False
    for item in history:
        previous = _normalize_title(str(item.get("title", "")))
        if not previous:
            continue
        if normalized_candidate == previous:
            return True
        if title_similarity(normalized_candidate, previous) >= 0.72:
            return True
    return False


def generate_unique_title(items: List[Dict], history: List[Dict]) -> str:
    if not items:
        return ""

    today_label = datetime.now(SHANGHAI_TZ).strftime("%m月%d日")
    top_titles = [compact_text(item.get("资讯标题", "")) for item in items if compact_text(item.get("资讯标题", ""))]
    first_title = top_titles[0]
    second_title = top_titles[1] if len(top_titles) > 1 else ""

    candidates = [
        first_title,
        f"{today_label}AI科技日报：{first_title}",
        f"{today_label}AI科技速览：{first_title}",
        f"{today_label}今日AI焦点：{first_title}",
    ]

    if second_title:
        candidates.extend(
            [
                f"{today_label}AI科技日报：{first_title}；{second_title}",
                f"{today_label}AI要闻：{first_title}，{second_title}",
            ]
        )

    for candidate in candidates:
        if candidate and not _is_similar_to_history(candidate, history):
            return candidate

    return f"{today_label}AI科技日报第{len(history) + 1}期：{first_title}"


def record_title(title: str, history: List[Dict]) -> List[Dict]:
    if not compact_text(title):
        return history
    new_item = {
        "date": datetime.now(SHANGHAI_TZ).date().isoformat(),
        "title": compact_text(title),
    }
    return [*history, new_item][-MAX_HISTORY_ITEMS:]
