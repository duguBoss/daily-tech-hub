"""AI content rewriting functionality."""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from daily_tech.ai_client import call_gemini_json
from daily_tech.config import MAX_CONTENT_LENGTH, MIN_CONTENT_LENGTH
from daily_tech.utils import (
    compact_text,
    has_concrete_news_elements,
    has_source_attribution,
    rewrite_overlap_score,
    split_sentences,
)


def build_fallback_rewrite(item: Dict) -> Optional[Dict]:
    title = compact_text(item["资讯标题"])
    source_text = compact_text(item.get("详情页正文") or item["内容"])
    if not title or len(source_text) < MIN_CONTENT_LENGTH:
        return None
    sentences = split_sentences(source_text)
    merged_parts: List[str] = []
    current_length = 0
    for sentence in sentences:
        extra = len(sentence) + (1 if merged_parts else 0)
        if current_length + extra > MAX_CONTENT_LENGTH:
            break
        merged_parts.append(sentence)
        current_length += extra
        if current_length >= 300:
            break
    if not merged_parts:
        merged_parts = [source_text[:MAX_CONTENT_LENGTH].rstrip("，,；; ")]
    content = "。".join(part.strip("。") for part in merged_parts if part).strip()
    if content and not content.endswith(("。", "！", "？")):
        content += "。"
    if not has_concrete_news_elements(title, content):
        return None
    if has_source_attribution(title) or has_source_attribution(content):
        return None
    new_item = dict(item)
    new_item["资讯标题"] = title
    new_item["内容"] = content[:MAX_CONTENT_LENGTH]
    return new_item


def rewrite_items_to_chinese(items: List[Dict]) -> List[Dict]:
    if not items:
        return items
    rewritten_items: List[Dict] = []
    for item in items:
        source_text = compact_text(item.get("详情页正文") or item["内容"])
        prompt = (
            "你是资深中文科技产业记者。请根据下面这条新闻的标题和详情页正文，输出一条面向行业读者的专业中文科技资讯。"
            "必须严格基于输入事实，不允许编造，不允许串到别的新闻。"
            "标题必须直接说明新闻事件本身，写清主体、动作和事件类型，禁止抽象总结。"
            "内容必须写成约300字的高信息密度摘要，优先覆盖：主体是谁、发生了什么、涉及什么产品/模型/技术、关键时间点或参数、官方怎么说、对用户或行业有什么直接影响。"
            "内容尽量保留详情页中的关键细节，避免口号化和泛泛结论。"
            "如果原文是产品更新、发布、开源、融资、测试、接入、合作，标题和内容里必须明确出现对应事件。"
            "专业术语、产品名、模型名、协议名、技术缩写（例如 API、GPU、LLM、Agent）优先保留原文，不要强行翻译。"
            "不要出现来源归因表达，例如'据XX报道''来源：''原文链接''转载自'。"
            "标题控制在18到34个中文字符。"
            "内容控制在220到360个中文字符。"
            "内容首句必须直接交代核心新闻事实。"
            '返回 JSON：{"title":"...","content":"..."}'
            "\n输入标题："
            f"{json.dumps(item['资讯标题'], ensure_ascii=False)}"
            "\n输入正文："
            f"{json.dumps(source_text, ensure_ascii=False)}"
        )
        try:
            result = call_gemini_json(prompt)
            title = compact_text(str(result.get("title", "")))
            content = compact_text(str(result.get("content", "")))
            if not title or not content:
                raise ValueError("missing title or content")
            if has_source_attribution(title) or has_source_attribution(content):
                raise ValueError("contains source attribution")
            if not has_concrete_news_elements(title, content):
                raise ValueError("rewritten item is too abstract")
            if rewrite_overlap_score(item["资讯标题"], source_text, title, content) < 0.12:
                raise ValueError("rewritten item drifted from source")
            new_item = dict(item)
            new_item["资讯标题"] = title
            new_item["内容"] = content[:MAX_CONTENT_LENGTH]
            rewritten_items.append(new_item)
        except Exception as exc:
            logging.warning("单条新闻 AI 改写失败，回退规则摘要: %s - %s", item["原文链接"], exc)
            fallback_item = build_fallback_rewrite(item)
            if fallback_item:
                rewritten_items.append(fallback_item)
            else:
                logging.warning("单条新闻回退失败，跳过: %s", item["原文链接"])
    return rewritten_items
