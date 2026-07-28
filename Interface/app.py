import time
import os
import sys
import json
import io
import contextlib
import gradio as gr

# Add parent directory to path so we can import tania_agent, stt, tts
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Fix UnicodeEncodeError when printing non-English characters to Windows console
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from stt.stt_module import transcribe_audio
from tts.tts_module import generate_audio
from core.tania_agent import process_turn
from core.state import fresh_state
from core.utils import detect_language

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

def process_input(audio_path, text_val, voice_name, state, history, chat_display):
    customer_text = ""
    debug_stt = ""
    
    if text_val and text_val.strip():
        customer_text = text_val.strip()
        debug_stt = "Input: Text\n"
    elif audio_path:
        customer_text, debug_stt = transcribe_audio(audio_path)
        
    if not customer_text:
        return state, history, chat_display, "No input provided.", None

    if state is None:
        state = fresh_state()
    if history is None:
        history = []
        
    state["language"] = detect_language(customer_text)
    history.append({"role": "user", "content": customer_text})

    with capture_stdout() as buf:
        state, agent_reply, decision = process_turn(customer_text, state, history)
        
        # ── Append raw JSON to history BEFORE popping tool_trace ────────────
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

    # Generate TTS audio
    audio_output = generate_audio(agent_reply, voice_name=voice_name)

    return state, history, chat_display, debug_info, audio_output

def process_text(text_val, voice_name, state, history, chat_display):
    return process_input(None, text_val, voice_name, state, history, chat_display)

def process_audio_wrapper(audio_path, voice_name, state, history, chat_display):
    return process_input(audio_path, None, voice_name, state, history, chat_display)

def clear_all():
    return fresh_state(), [], [], "Conversation cleared.", None

# ============================================================
# GRADIO UI
# ============================================================

with gr.Blocks(title="Tania Voice Agent") as demo:
    gr.Markdown("# 🎤 Tania Voice Agent (Live Multi-Modal Demo)")
    
    tania_state = gr.State(fresh_state())
    tania_history = gr.State([])
    
    with gr.Row():
        with gr.Column(scale=2):
            chatbot = gr.Chatbot(label="Conversation", height=400)
            tania_voice = gr.Audio(label="Tania's Voice", autoplay=True, interactive=False)
            
            with gr.Tabs():
                with gr.TabItem("Text Input"):
                    text_input = gr.Textbox(label="Type Here", placeholder="أريد كرتون مياه للمنزل...", lines=2)
                    text_submit_btn = gr.Button("Send Text", variant="primary")
                with gr.TabItem("Audio Input"):
                    audio_input = gr.Audio(sources=["microphone", "upload"], type="filepath", label="Speak Here")
                    audio_submit_btn = gr.Button("Send Audio", variant="primary")
            
            voice_selector = gr.Radio(
                choices=["Miro (ar-SA)", "Dii (ar-SA)", "Kareem (ar-JO)"],
                value="Miro (ar-SA)",
                label="Agent Voice (Requires downloaded Piper models)"
            )
            clear_btn = gr.Button("Clear Conversation")
            
        with gr.Column(scale=1):
            debug_box = gr.Textbox(label="Terminal & Debug Logs", lines=35, interactive=False)
            
    # Trigger processing
    text_submit_btn.click(
        fn=process_text,
        inputs=[text_input, voice_selector, tania_state, tania_history, chatbot],
        outputs=[tania_state, tania_history, chatbot, debug_box, tania_voice]
    ).then(
        fn=lambda: "", inputs=None, outputs=text_input
    )
    
    audio_submit_btn.click(
        fn=process_audio_wrapper,
        inputs=[audio_input, voice_selector, tania_state, tania_history, chatbot],
        outputs=[tania_state, tania_history, chatbot, debug_box, tania_voice]
    )
    
    clear_btn.click(
        fn=clear_all,
        inputs=[],
        outputs=[tania_state, tania_history, chatbot, debug_box, tania_voice]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7861, inbrowser=True, share=True)
