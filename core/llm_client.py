"""
llm_client.py
====================================================================
Local LM Studio API client for Qwen3-14B.

This replaces the Hugging Face API client, routing the request
to the local LM Studio instance running on port 1234.
====================================================================
"""

import requests
import json
from core.telemetry import Span

def qwen_chat(messages, max_new_tokens=400, temperature=0.7):
    """
    messages: list of {"role": "system"/"user"/"assistant", "content": str}
    
    Sends request to local LM Studio server.
    """
    url = "http://127.0.0.1:1234/v1/chat/completions"
    
    payload = {
        "model": "local-model",
        "messages": messages,
        "max_tokens": max_new_tokens,
        "temperature": temperature,
        "stream": False
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    with Span("llm_inference", metadata={"payload": payload}) as span:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=180)
            response.raise_for_status()
            
            result = response.json()
            content = result['choices'][0]['message']['content']
            span.exit_metadata = {"response": content, "usage": result.get("usage", {})}
            return content
            
        except Exception as e:
            print(f"Error communicating with local LLM server: {e}")
            span.exit_metadata = {"response": "{}", "error": str(e)}
            return "{}"

if __name__ == "__main__":
    # Quick manual sanity check: python llm_client.py
    test_messages = [
        {"role": "system", "content": "You are a helpful assistant. /no_think"},
        {"role": "user", "content": "Say hello in one short sentence."},
    ]
    print(qwen_chat(test_messages, max_new_tokens=50))
