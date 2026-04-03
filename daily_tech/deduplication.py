"""Deduplication logic for news items."""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from daily_tech.utils import (
    compact_text,
    content_similarity,
    fingerprint_text,
    named_tokens,
    title_similarity,
)


def quality_score(item: Dict) -> Tuple[int, int, int]:
    return (
        len(item.get("内容", "")),
        len(item.get("资讯标题", "")),
        1 if item.get("详情页标题") else 0,
    )


def heuristic_dedupe(items: List[Dict]) -> List[Dict]:
    if not items:
        return items
    by_url: Dict[str, Dict] = {}
    for item in items:
        current = by_url.get(item["原文链接"])
        if not current or quality_score(item) > quality_score(current):
            by_url[item["原文链接"]] = item
    unique_items = list(by_url.values())
    unique_items.sort(key=quality_score, reverse=True)
    kept: List[Dict] = []
    seen_fingerprints = set()
    for item in unique_items:
        fp = fingerprint_text(item["资讯标题"], item["内容"])
        if fp and fp in seen_fingerprints:
            continue
        duplicated = False
        for kept_item in kept:
            same_title = title_similarity(item["资讯标题"], kept_item["资讯标题"]) >= 0.3
            same_content = content_similarity(item, kept_item) >= 0.42
            source_title_match = title_similarity(
                item.get("原始标题", item["资讯标题"]),
                kept_item.get("原始标题", kept_item["资讯标题"]),
            ) >= 0.3
            shared_named_tokens = named_tokens(item["资讯标题"]) & named_tokens(kept_item["资讯标题"])
            same_named_event = len(shared_named_tokens) >= 2
            if (same_title and same_content) or (source_title_match and same_content) or same_named_event:
                duplicated = True
                break
        if duplicated:
            continue
        if fp:
            seen_fingerprints.add(fp)
        kept.append(item)
    logging.info("规则去重后剩余 %s 条", len(kept))
    return kept


def chunked(sequence: List[Dict], size: int) -> List[List[Dict]]:
    return [list(sequence[index:index + size]) for index in range(0, len(sequence), size)]


def dedupe_items_with_ai(items: List[Dict]) -> List[Dict]:
    if not items:
        return items
    from daily_tech.ai_client import call_gemini_json
    kept_indices = set()
    for batch in chunked(items, 20):
        payload = [
            {
                "index": index,
                "title": item["资讯标题"],
                "content": item["内容"],
                "source": item["来源站点"],
                "date": item["发布日期"],
                "original_title": item.get("原始标题", ""),
            }
            for index, item in enumerate(batch)
        ]
        prompt = (
            "你是科技新闻去重编辑。请从下面新闻数组中删除语义重复、主体相同、只是换了表述的重复报道。"
            "判断时同时参考标题、原始标题和内容，保留信息更完整的一条。不同公司、不同产品、不同投融资主体、不同功能发布，不算重复。返回 JSON："
            '{"keep_indices":[0,2,5]}'
            "\n输入："
            f"{__import__('json').dumps(payload, ensure_ascii=False)}"
        )
        try:
            result = call_gemini_json(prompt)
            batch_keep = {
                index
                for index in result.get("keep_indices", [])
                if isinstance(index, int) and 0 <= index < len(batch)
            }
        except Exception as exc:
            logging.warning("AI 去重失败，保留当前批次全部新闻: %s", exc)
            batch_keep = set(range(len(batch)))
        if not batch_keep:
            batch_keep = set(range(len(batch)))
        for index in batch_keep:
            kept_indices.add(id(batch[index]))
    deduped = [item for item in items if id(item) in kept_indices]
    if not deduped:
        logging.warning("AI 去重结果为空，回退为规则去重结果。")
        return items
    logging.info("AI 去重后剩余 %s 条", len(deduped))
    return deduped
