"""Content selection - pick top 5 most valuable news items."""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from daily_tech.ai_client import call_gemini_json
from daily_tech.config import MAX_CONTENT_LENGTH, MIN_CONTENT_LENGTH
from daily_tech.utils import compact_text


def calculate_news_value_score(item: Dict) -> float:
    """Calculate a value score for a news item based on multiple factors."""
    score = 0.0
    title = item.get("资讯标题", "")
    content = item.get("内容", "") or item.get("详情页正文", "")
    
    # 1. 内容长度分数 (0-20分)
    content_len = len(content)
    if content_len >= 300:
        score += 20
    elif content_len >= 200:
        score += 15
    elif content_len >= 100:
        score += 10
    else:
        score += 5
    
    # 2. 标题质量分数 (0-20分)
    title_len = len(title)
    if 15 <= title_len <= 40:
        score += 20
    elif 10 <= title_len < 15 or 40 < title_len <= 50:
        score += 15
    else:
        score += 10
    
    # 3. 关键词加分 (0-30分)
    high_value_keywords = [
        "发布", "推出", "上线", "开源", "融资", "收购", "合作",
        "GPT", "AI", "大模型", "LLM", "API", "Agent",
        "Google", "OpenAI", "Microsoft", "Meta", "Apple", "NVIDIA",
        "百度", "阿里", "腾讯", "字节", "华为", "小米",
        "Claude", "Gemini", "Llama", "GPT-4", "GPT-5"
    ]
    text = f"{title} {content}".lower()
    keyword_matches = sum(1 for kw in high_value_keywords if kw.lower() in text)
    score += min(keyword_matches * 3, 30)
    
    # 4. 信息密度分数 (0-15分)
    # 检查是否包含具体信息：数字、时间、产品名等
    has_numbers = any(c.isdigit() for c in content)
    has_specific_info = any(marker in content for marker in ["月", "日", "年", "版", "模型", "产品", "功能"])
    if has_numbers:
        score += 8
    if has_specific_info:
        score += 7
    
    # 5. 时效性分数 (0-15分)
    # 优先选择包含最新时间信息的新闻
    time_markers = ["今日", "今天", "刚刚", "最新", "昨日", "昨天", "本周", "本月"]
    if any(marker in title or marker in content for marker in time_markers):
        score += 15
    
    return score


def select_top_items_by_score(items: List[Dict], top_n: int = 5) -> List[Dict]:
    """Select top N items based on calculated value scores."""
    if len(items) <= top_n:
        return items
    
    # 计算每个项目的分数
    scored_items = [(item, calculate_news_value_score(item)) for item in items]
    
    # 按分数降序排序
    scored_items.sort(key=lambda x: x[1], reverse=True)
    
    # 选择前N个
    selected = [item for item, score in scored_items[:top_n]]
    
    logging.info("从 %d 条新闻中筛选出 %d 条高价值新闻", len(items), len(selected))
    for i, (item, score) in enumerate(scored_items[:top_n], 1):
        logging.info("  第%d条 [分数:%.1f]: %s", i, score, item.get("资讯标题", "")[:50])
    
    return selected


def select_top_items_with_ai(items: List[Dict], top_n: int = 5) -> List[Dict]:
    """Use AI to select top N most valuable news items."""
    if len(items) <= top_n:
        return items
    
    # 准备新闻摘要供AI评估
    news_summaries = []
    for idx, item in enumerate(items):
        summary = {
            "index": idx,
            "title": item.get("资讯标题", ""),
            "content_preview": (item.get("内容", "") or item.get("详情页正文", ""))[:200],
            "date": item.get("发布日期", "")
        }
        news_summaries.append(summary)
    
    prompt = (
        "你是资深科技新闻编辑。请从以下新闻列表中挑选出最有价值的5条新闻。"
        "评估标准（按重要性排序）：\n"
        "1. 行业影响力：是否涉及大厂(Google/OpenAI/Microsoft/Meta/Apple/NVIDIA/百度/阿里/腾讯等)\n"
        "2. 技术重要性：是否涉及重大技术突破、新产品发布、重要开源\n"
        "3. 实用性：对开发者或用户是否有直接价值\n"
        "4. 时效性：是否最新、最热门的话题\n"
        "5. 信息完整性：内容是否详实、有具体数据和细节\n\n"
        "请返回一个JSON数组，包含选中的5条新闻的index值（从0开始）。"
        "格式：[0, 3, 5, 8, 12]\n\n"
        "新闻列表：\n"
        f"{json.dumps(news_summaries, ensure_ascii=False, indent=2)}"
    )
    
    try:
        result = call_gemini_json(prompt)
        selected_indices = result if isinstance(result, list) else result.get("indices", [])
        
        # 验证并限制选择数量
        selected_indices = [int(i) for i in selected_indices if 0 <= int(i) < len(items)][:top_n]
        
        if len(selected_indices) < top_n:
            # 如果AI返回不足，补充分数最高的
            remaining = [i for i in range(len(items)) if i not in selected_indices]
            scored_remaining = [(i, calculate_news_value_score(items[i])) for i in remaining]
            scored_remaining.sort(key=lambda x: x[1], reverse=True)
            selected_indices.extend([i for i, _ in scored_remaining[:top_n - len(selected_indices)]])
        
        selected = [items[i] for i in selected_indices[:top_n]]
        logging.info("AI从 %d 条新闻中筛选出 %d 条高价值新闻", len(items), len(selected))
        
        return selected
        
    except Exception as exc:
        logging.warning("AI筛选失败，回退到分数筛选: %s", exc)
        return select_top_items_by_score(items, top_n)


def select_top_news(items: List[Dict], top_n: int = 5, use_ai: bool = True) -> List[Dict]:
    """
    Select top N most valuable news items.
    
    Args:
        items: List of news items
        top_n: Number of items to select (default 5)
        use_ai: Whether to use AI for selection (default True)
    
    Returns:
        List of selected news items
    """
    if not items:
        return []
    
    if len(items) <= top_n:
        logging.info("新闻数量(%d)不足%d条，全部保留", len(items), top_n)
        return items
    
    if use_ai:
        return select_top_items_with_ai(items, top_n)
    else:
        return select_top_items_by_score(items, top_n)
