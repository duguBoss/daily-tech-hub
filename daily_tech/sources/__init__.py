"""News source parsers."""
from __future__ import annotations

from daily_tech.sources.ai_bot import parse_ai_bot
from daily_tech.sources.aibase import parse_aibase

__all__ = ["parse_ai_bot", "parse_aibase"]
