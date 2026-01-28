from __future__ import annotations

import logging
import os
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

_llm_callable: Optional[Callable[[str], str]] = None


def register_llm_callable(func: Callable[[str], str]) -> None:
    """
    Allow applications to register a synchronous LLM callable.
    This is kept for backward compatibility with tests.
    """
    global _llm_callable
    _llm_callable = func


def call_llm(prompt: str) -> str:
    """
    Send a prompt to AnvilGPT and return the model's text response.
    
    Uses environment variables:
    - ANVILGPT_URL: The API endpoint (e.g., https://anvilgpt.rcac.purdue.edu/api/chat/completions)
    - ANVILGPT_KEY: The API key (Bearer token)
    - ANVILGPT_MODEL: The model name (default: gpt-oss:120b)
    
    Returns the generated text response from the model.
    Raises RuntimeError on configuration or API errors.
    """
    # Allow test override via register_llm_callable
    if _llm_callable is not None:
        try:
            result = _llm_callable(prompt)
            if isinstance(result, str):
                return result
            logger.warning("LLM callable returned non-string result; coercing to string.")
            return str(result)
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            return "I could not compose an answer due to a generation error."
    
    # Production path: direct AnvilGPT call
    url = os.getenv("ANVILGPT_URL")
    key = os.getenv("ANVILGPT_KEY")
    
    if not url or not key:
        raise RuntimeError(
            "❌ Missing ANVILGPT_URL or ANVILGPT_KEY environment variable. "
            "Please set these in your .env.local file."
        )
    
    model = os.getenv("ANVILGPT_MODEL", "gpt-oss:120b")
    
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }
    
    try:
        logger.debug(f"Calling AnvilGPT at {url} with model {model}")
        response = requests.post(url, headers=headers, json=body, timeout=60)
        
        if response.status_code == 403:
            raise RuntimeError("🚫 403 Forbidden: Invalid or expired AnvilGPT key or endpoint.")
        
        if response.status_code == 401:
            raise RuntimeError("🚫 401 Unauthorized: Invalid AnvilGPT API key.")
        
        if response.status_code != 200:
            error_text = response.text[:200]
            raise RuntimeError(f"⚠️ HTTP {response.status_code}: {error_text}")
        
        data = response.json()
        
        # Extract the response content
        if "choices" in data and len(data["choices"]) > 0:
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content")
            
            # Handle reasoning mode: if content is None but reasoning_content exists, use that
            if content is None:
                reasoning_content = message.get("reasoning_content")
                if reasoning_content:
                    logger.warning(
                        f"AnvilGPT returned None content but reasoning_content exists "
                        f"(finish_reason: {choice.get('finish_reason')}). Using reasoning_content."
                    )
                    content = reasoning_content
                else:
                    # Check provider_specific_fields as fallback
                    provider_fields = message.get("provider_specific_fields", {})
                    reasoning_content = provider_fields.get("reasoning_content")
                    if reasoning_content:
                        logger.warning(
                            f"AnvilGPT returned None content but reasoning_content in provider_specific_fields "
                            f"(finish_reason: {choice.get('finish_reason')}). Using reasoning_content."
                        )
                        content = reasoning_content
            
            if content is None:
                finish_reason = choice.get("finish_reason", "unknown")
                raise RuntimeError(
                    f"⚠️ AnvilGPT returned empty content (finish_reason: {finish_reason}). "
                    f"Response may have been truncated. Full response: {str(data)[:500]}"
                )
            
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                logger.warning(
                    f"⚠️ AnvilGPT response was truncated (hit token limit). "
                    f"Received {len(content)} characters."
                )
            
            logger.debug(f"AnvilGPT response received: {len(content)} characters")
            return content
        else:
            raise RuntimeError(f"⚠️ Unexpected response format from AnvilGPT: {data}")
    
    except requests.RequestException as exc:
        logger.error(f"LLM request failed: {exc}")
        raise RuntimeError(f"🔌 LLM request failed: {exc}")


__all__ = ["call_llm", "register_llm_callable"]
