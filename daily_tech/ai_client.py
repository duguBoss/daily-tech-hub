"""AI client for Gemini API interactions."""
from __future__ import annotations

import json
import logging
from typing import Dict

import requests

from daily_tech.config import GEMINI_API_KEY, GEMINI_MODEL
from daily_tech.utils import extract_json_string


def require_gemini_api_key() -> None:
    if not GEMINI_API_KEY:
        raise RuntimeError("缺少 GEMINI_API_KEY。当前任务要求 AI 中文改写和语义去重。")


def call_gemini_json(prompt: str) -> Dict:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    response = requests.post(url, json=payload, timeout=90)
    response.raise_for_status()
    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(extract_json_string(text))
