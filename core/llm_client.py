"""
llm_client.py
====================================================================
OpenAI-compatible local LLM client (LM Studio by default).

Streams SSE chat.completions tokens; optional on_token callback for UI.
====================================================================
"""

import json
import requests
from core.config import LLM_BASE_URL, LLM_MODEL_ID
from core.telemetry import Span

# Back-compat alias
LM_MODEL_ID = LLM_MODEL_ID


def qwen_chat(messages, max_new_tokens=400, temperature=0.7, on_token=None):
    """
    messages: list of {"role": "system"/"user"/"assistant", "content": str}
    on_token: optional callable(str) invoked for each content delta

    Streams from the local OpenAI-compatible server; returns full text.
    """
    url = f"{LLM_BASE_URL}/chat/completions"

    payload = {
        "model": LLM_MODEL_ID,
        "messages": messages,
        "max_tokens": max_new_tokens,
        "temperature": temperature,
        "stream": True,
    }

    headers = {"Content-Type": "application/json"}

    with Span("llm_inference", metadata={"payload": payload}) as span:
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=180, stream=True
            )
            response.raise_for_status()

            parts = []
            usage = {}
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                token = delta.get("content")
                if token is None and choice.get("message"):
                    token = choice["message"].get("content")
                if not token:
                    continue
                parts.append(token)
                if on_token:
                    try:
                        on_token(token)
                    except Exception:
                        pass

            content = "".join(parts)
            span.exit_metadata = {
                "response": content,
                "usage": usage,
                "streamed": True,
            }
            return content

        except Exception as e:
            print(f"Error communicating with local LLM server: {e}")
            span.exit_metadata = {"response": "{}", "error": str(e), "streamed": True}
            return "{}"


if __name__ == "__main__":
    test_messages = [
        {"role": "system", "content": "You are a helpful assistant. /no_think"},
        {"role": "user", "content": "Say hello in one short sentence."},
    ]
    print(qwen_chat(test_messages, max_new_tokens=50, on_token=lambda t: print(t, end="", flush=True)))
    print()
