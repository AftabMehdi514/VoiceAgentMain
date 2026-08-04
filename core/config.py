"""
config.py
====================================================================
Shared configuration for the Tania agent.

IMPORTANT — set your Hugging Face token:
    Option A (recommended): set an environment variable before running
        export HF_API_TOKEN="hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"   # Linux/Mac
        setx HF_API_TOKEN "hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"     # Windows

    Option B (quick/local testing only — do NOT commit this to git):
        Just hardcode it in HF_API_TOKEN below.

Get a token at: https://huggingface.co/settings/tokens
It needs "Read" access (fine-grained tokens: enable "Make calls to
Inference Providers").
====================================================================
"""

import os

# Hugging Face access token — pulled from env var if set, otherwise
# falls back to the placeholder string below (replace it).
HF_API_TOKEN = os.environ.get(
    "HF_API_TOKEN",
    "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",  # <-- replace with your real token
)

# Model repo id on the Hugging Face Hub.
# Verify this exact id is available through Hugging Face Inference
# Providers on the model's page (huggingface.co/Qwen/Qwen3-14B) —
# under "Deploy" -> "Inference Providers" it will list which
# provider(s) currently serve it.
HF_MODEL_ID = "Qwen/Qwen3-14B"
DBPassword = 'aftab'

# Local OpenAI-compatible inference (LM Studio default; swap URL for other servers).
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
LLM_MODEL_ID = os.environ.get("LLM_MODEL_ID", "qwen-3-8b")

# Define the specific folder in the project where the model will be stored
LOCAL_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "LocalModels", HF_MODEL_ID.replace("/", "_"))