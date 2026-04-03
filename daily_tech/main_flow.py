"""Main workflow orchestration."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, List

import requests

from daily_tech.ai_client import require_gemini_api_key
from daily_tech.config import (
    FETCH_DAYS,
    OUTPUT_FILE,
    SHANGHAI_TZ,
    WEIXIN_TEMPLATE_FILE,
    parse_target_dates,
)
from daily_tech.content_selector import select_top_news
from daily_tech.deduplication import dedupe_items_with_ai, heuristic_dedupe
from daily_tech.image_processor import attach_downloaded_images
from daily_tech.rewriter import build_fallback_rewrite, rewrite_items_to_chinese
from daily_tech.sources import parse_ai_bot, parse_aibase
from daily_tech.templates import build_weixin_payload
from daily_tech.utils import build_session, ensure_dirs, cleanup_images_if_saturday


def collect_raw_items(session: requests.Session, target_dates: List[datetime.date]) -> List[Dict]:
    items: List[Dict] = []
    items.extend(parse_ai_bot(session, target_dates))
    items.extend(parse_aibase(session, target_dates))
    return items


def sort_items(items: List[Dict]) -> List[Dict]:
    return sorted(
        items,
        key=lambda item: (item["发布日期"], item["资讯标题"]),
        reverse=True,
    )


def main() -> None:
    ensure_dirs()
    cleanup_images_if_saturday()
    require_gemini_api_key()
    target_dates = parse_target_dates(days=FETCH_DAYS)
    session = build_session()
    all_items = collect_raw_items(session, target_dates)
    if not all_items and FETCH_DAYS < 7:
        fallback_days = 7
        logging.warning("近 %s 天无数据，自动扩大抓取窗口到近 %s 天重试。", FETCH_DAYS, fallback_days)
        all_items = collect_raw_items(session, parse_target_dates(days=fallback_days))
    if not all_items:
        logging.warning("没有抓取到可验证的完整新闻数据，保留现有输出并退出。")
        if not OUTPUT_FILE.exists():
            with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
                json.dump([], file, ensure_ascii=False, indent=2)
            logging.info("已写入空结果文件: %s", OUTPUT_FILE)
        if not WEIXIN_TEMPLATE_FILE.exists():
            with open(WEIXIN_TEMPLATE_FILE, "w", encoding="utf-8") as file:
                json.dump(build_weixin_payload([]), file, ensure_ascii=False, indent=2)
            logging.info("已写入空模板文件: %s", WEIXIN_TEMPLATE_FILE)
        return
    filtered_items = heuristic_dedupe(all_items)
    
    # 从抓取的内容中挑选5条最有价值的新闻
    selected_items = select_top_news(filtered_items, top_n=5, use_ai=True)
    
    rewritten_items = rewrite_items_to_chinese(selected_items)
    if not rewritten_items:
        logging.warning("AI 中文改写阶段没有产出有效数据，回退到规则摘要结果。")
        rewritten_items = [item for item in (build_fallback_rewrite(source) for source in filtered_items) if item]
    if not rewritten_items:
        raise RuntimeError("改写与回退后都没有留下有效数据。")
    deduped_items = heuristic_dedupe(rewritten_items)
    deduped_items = dedupe_items_with_ai(deduped_items)
    downloaded_items = attach_downloaded_images(session, deduped_items)
    final_items = sort_items(downloaded_items)
    if not final_items:
        raise RuntimeError("图片下载后没有留下完整有效数据。")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(final_items, file, ensure_ascii=False, indent=2)
    with open(WEIXIN_TEMPLATE_FILE, "w", encoding="utf-8") as file:
        json.dump(build_weixin_payload(final_items), file, ensure_ascii=False, indent=2)
    logging.info("完成，最终输出 %s 条新闻到 %s", len(final_items), OUTPUT_FILE)
    logging.info("微信模板字段 wexinhtml/wexinhtml1 已写入 %s", WEIXIN_TEMPLATE_FILE)


if __name__ == "__main__":
    main()
