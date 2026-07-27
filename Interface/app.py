import time
import os
import glob
import sys
import json
import io
import contextlib
import gradio as gr

# Add parent directory to path so we can import tania_agent
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

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
                try:
                    os.add_dll_directory(bin_dir)
                    os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]
                except Exception:
                    pass

from faster_whisper import WhisperModel
from tania_agent import fresh_state, detect_language, process_turn

# ============================================================
# CONFIG & MODEL INIT
# ============================================================

MODEL_NAME = "large-v3-turbo"
DOWNLOAD_ROOT = "E:\\WhisperModels"

print(f"Loading local model '{MODEL_NAME}' from {DOWNLOAD_ROOT}...")
model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16", download_root=DOWNLOAD_ROOT)
print("Model loaded successfully! Launching UI...")

# ============================================================
# LOGIC
# ============================================================

@contextlib.contextmanager
def capture_stdout():
    old_stdout = sys.stdout
    buffer = io.StringIO()
    sys.stdout = buffer
    try:
        yield buffer
    finally:
        sys.stdout = old_stdout

def process_input(audio_path, text_val, state, history, chat_display):
    customer_text = ""
    stt_time = 0.0
    debug_stt = ""
    
    if text_val and text_val.strip():
        customer_text = text_val.strip()
        debug_stt = "Input: Text\n"
    elif audio_path:
        t0 = time.perf_counter()
        segments, info = model.transcribe(audio_path, beam_size=5)
        for segment in segments:
            customer_text += segment.text
        customer_text = customer_text.strip()
        stt_time = time.perf_counter() - t0
        debug_stt = f"STT Latency: {stt_time:.2f}s | Lang: {info.language} ({info.language_probability:.2f})\n"
        
    if not customer_text:
        return state, history, chat_display, "No input provided."

    if state is None:
        state = fresh_state()
    if history is None:
        history = []
        
    state["language"] = detect_language(customer_text)
    history.append({"role": "user", "content": customer_text})

    with capture_stdout() as buf:
        state, agent_reply, decision = process_turn(customer_text, state, history)
        
        # ── Append raw JSON to history BEFORE popping tool_trace ────────────
        # This ensures the LLM always sees its own JSON format in history,
        # preventing the "plain text drift" that caused the amnesia loop.
        history.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
        
        # THEN pop tool_trace for logging only (does not affect history)
        tool_trace = decision.pop("_tool_trace", [])
        
        print(f"\n[LLM Decision]\n{json.dumps(decision, ensure_ascii=False, indent=2)}")
        if tool_trace:
            print(f"\n[Tool Calls]\n{json.dumps(tool_trace, ensure_ascii=False, indent=2)}")
        print(f"\n[State]\n{json.dumps(state, ensure_ascii=False, indent=2)}")
        print(f"\nAgent : {agent_reply}\n{'-'*60}")

    terminal_logs = buf.getvalue()

    # (history already appended above inside capture_stdout block)
    chat_display.append({"role": "user", "content": customer_text})
    chat_display.append({"role": "assistant", "content": agent_reply})

    debug_info = debug_stt + "\n--- Terminal Logs ---\n" + terminal_logs
    
    if state.get("order_step") == "done":
        debug_info += "\n✅ ORDER COMPLETE!\n"
        lang = state.get("language", "ar")
        mobile = state.get("mobile")
        state = fresh_state()
        state["language"] = lang
        state["mobile"] = mobile
        history = []

    return state, history, chat_display, debug_info

def process_text(text_val, state, history, chat_display):
    return process_input(None, text_val, state, history, chat_display)

def process_audio_wrapper(audio_path, state, history, chat_display):
    return process_input(audio_path, None, state, history, chat_display)

def clear_all():
    return fresh_state(), [], [], "Conversation cleared."

# ============================================================
# GRADIO UI
# ============================================================

with gr.Blocks(title="Tania Voice Agent") as demo:
    gr.Markdown("# 🎤 Tania Voice Agent (Live Multi-Modal Demo)")
    
    tania_state = gr.State(fresh_state())
    tania_history = gr.State([])
    
    with gr.Row():
        with gr.Column(scale=2):
            chatbot = gr.Chatbot(label="Conversation", height=500)
            

            
            with gr.Tabs():
                with gr.TabItem("Text Input"):
                    text_input = gr.Textbox(label="Type Here", placeholder="أريد كرتون مياه للمنزل...", lines=2)
                    text_submit_btn = gr.Button("Send Text", variant="primary")
                with gr.TabItem("Audio Input"):
                    audio_input = gr.Audio(sources=["microphone", "upload"], type="filepath", label="Speak Here")
                    audio_submit_btn = gr.Button("Send Audio", variant="primary")
            
            clear_btn = gr.Button("Clear Conversation")
            
        with gr.Column(scale=1):
            debug_box = gr.Textbox(label="Terminal & Debug Logs", lines=35, interactive=False)
            
    # Trigger processing
    text_submit_btn.click(
        fn=process_text,
        inputs=[text_input, tania_state, tania_history, chatbot],
        outputs=[tania_state, tania_history, chatbot, debug_box]
    ).then(
        fn=lambda: "", inputs=None, outputs=text_input
    )
    
    audio_submit_btn.click(
        fn=process_audio_wrapper,
        inputs=[audio_input, tania_state, tania_history, chatbot],
        outputs=[tania_state, tania_history, chatbot, debug_box]
    )
    
    clear_btn.click(
        fn=clear_all,
        inputs=[],
        outputs=[tania_state, tania_history, chatbot, debug_box]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7861, inbrowser=True, share=True)
