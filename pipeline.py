"""
pipeline.py
====================================================================
End-to-End Pipeline: Audio -> STT -> Agent
====================================================================
This script reads audio files sequentially (1.mp3, 2.mp3, etc.)
from test_audio/, transcribes them using the local faster-whisper
model, and feeds the transcriptions to the Tania agent to simulate
a conversation.
"""

import time
import os
import glob
import sys

# Fix UnicodeEncodeError when printing non-English characters to Windows console
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Add NVIDIA DLL directories to the DLL search path so CTranslate2 can find cublas64_12.dll
if os.name == "nt":
    import site
    site_packages = site.getsitepackages()
    if site_packages:
        for pkg_dir in site_packages:
            nvidia_bins = glob.glob(os.path.join(pkg_dir, "nvidia", "*", "bin"))
            for bin_dir in nvidia_bins:
                os.add_dll_directory(bin_dir)
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]

import json
from faster_whisper import WhisperModel
from tania_agent import fresh_state, detect_language, extract_order_decision, update_state, generate_response

# ============================================================
# CONFIG
# ============================================================

MODEL_NAME = "large-v3-turbo"
DOWNLOAD_ROOT = "E:\\WhisperModels"

# ============================================================
# PIPELINE
# ============================================================

def transcribe_local(audio_file, model):
    t0 = time.perf_counter()
    segments, info = model.transcribe(audio_file, beam_size=5)

    full_text = ""
    for segment in segments:
        full_text += segment.text

    request_time = time.perf_counter() - t0

    return {
        "text": full_text.strip(),
        "request_time_s": request_time,
        "language": info.language,
        "language_probability": info.language_probability
    }

def run_pipeline():
    print(f"Loading local model '{MODEL_NAME}' from {DOWNLOAD_ROOT}...")
    model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16", download_root=DOWNLOAD_ROOT)
    
    state = fresh_state()
    history = []

    print("\nStarting Automated Pipeline...")
    print("=" * 60)

    for i in range(1, 7):
        audio_file = f"test_audio/{i}.mp3"
        
        if not os.path.exists(audio_file):
            print(f"⚠️ Audio file {audio_file} not found. Skipping...")
            continue
            
        print(f"\nProcessing Audio: {audio_file}")
        
        # 1. Transcribe Audio
        print("Transcribing...")
        stt_result = transcribe_local(audio_file, model)
        customer_input = stt_result["text"]
        
        print(f"[Customer message] (STT Latency: {stt_result['request_time_s']:.2f}s, Lang: {stt_result['language']})")
        print(customer_input)
        
        if not customer_input:
            print("No speech detected.")
            continue
            
        # 2. Agent Processing
        state["language"] = detect_language(customer_input)
        history.append({"role": "user", "content": customer_input})

        decision = extract_order_decision(customer_input, state, history)
        state = update_state(state, decision, customer_message=customer_input)
        agent_reply = generate_response(state, decision)
        history.append({"role": "assistant", "content": agent_reply})

        tool_trace = decision.pop("_tool_trace", [])

        # 3. Output Logging
        print(f"\n[LLM Decision]\n{json.dumps(decision, ensure_ascii=False, indent=2)}")
        if tool_trace:
            print(f"\n[Tool Calls]\n{json.dumps(tool_trace, ensure_ascii=False, indent=2)}")
        print(f"\n[State]\n{json.dumps(state, ensure_ascii=False, indent=2)}")
        print(f"\nAgent : {agent_reply}\n{'-'*60}")

        if state["confirmed"]:
            final = {
                "product": state["product"],
                "quantity": state["quantity"],
                "address": state["address"],
                "language": state["language"],
            }
            print(f"\n✅ FINAL ORDER COMPLETE\n{json.dumps(final, ensure_ascii=False, indent=2)}")
            print("Conversation finished. Exiting pipeline.")
            break

if __name__ == "__main__":
    run_pipeline()
