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
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=180)
        response.raise_for_status()
        
        result = response.json()
        return result['choices'][0]['message']['content']
        
    except Exception as e:
        print(f"Error communicating with local LLM server: {e}")
        return "{}"

if __name__ == "__main__":
    # Quick manual sanity check: python llm_client.py
    test_messages = [
        {"role": "system", "content": "You are a helpful assistant. /no_think"},
        {"role": "user", "content": "Say hello in one short sentence."},
    ]
    print(qwen_chat(test_messages, max_new_tokens=50))
