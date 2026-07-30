import time
import os
import sys
import json
import io
import uuid
import contextlib
import gradio as gr

# Add parent directory to path so we can import tania_agent, stt, tts
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Fix UnicodeEncodeError when printing non-English characters to Windows console
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from stt.stt_module import transcribe_audio
from tts.tts_module import generate_audio
from core.tania_agent import process_turn
from core.state import fresh_state
from core.utils import detect_language
from core.telemetry import Span, set_context, clear_turn


@contextlib.contextmanager
def capture_stdout():
    old_stdout = sys.stdout
    buffer = io.StringIO()
    sys.stdout = buffer
    try:
        yield buffer
    finally:
        sys.stdout = old_stdout


def _state_public(state):
    if not state:
        return {}
    return {k: v for k, v in state.items() if k != "debug_log"}


def process_input(audio_path, text_val, voice_name, state, history, chat_display, session_id):
    if not session_id:
        session_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    set_context(session_id=session_id, turn_id=turn_id)

    customer_text = ""
    debug_stt = ""
    input_mode = "text"

    if text_val and text_val.strip():
        customer_text = text_val.strip()
        debug_stt = "Input: Text\n"
        input_mode = "text"
    elif audio_path:
        input_mode = "audio"
        with Span("stt", metadata={"audio_path": os.path.basename(str(audio_path))}) as stt_span:
            customer_text, debug_stt = transcribe_audio(audio_path)
            disabled = "stt disabled" in (debug_stt or "").lower()
            stt_span.exit_metadata = {
                "transcript": customer_text,
                "disabled": disabled,
                "debug": debug_stt,
            }

    if not customer_text:
        clear_turn()
        return state, history, chat_display, "No input provided.", None, session_id

    if state is None:
        state = fresh_state()
    if history is None:
        history = []
    if chat_display is None:
        chat_display = []

    state_before = _state_public(state)
    state["language"] = detect_language(customer_text)
    history.append({"role": "user", "content": customer_text})

    audio_output = None
    with capture_stdout() as buf:
        with Span(
            "turn",
            metadata={
                "customer_input": customer_text,
                "input_mode": input_mode,
                "order_step_before": state_before.get("order_step"),
                "language": state.get("language"),
                "state_before": state_before,
            },
        ) as turn_span:
            state, agent_reply, decision = process_turn(customer_text, state, history)

            tool_trace = list(decision.get("_tool_trace") or [])
            guards = list(decision.get("_guard_events") or [])
            state_diff = decision.get("_state_diff") or {}

            with Span(
                "tts",
                metadata={"voice": voice_name, "char_length": len(agent_reply or "")},
            ) as tts_span:
                audio_output = generate_audio(agent_reply, voice_name=voice_name)
                tts_span.exit_metadata = {
                    "voice": voice_name,
                    "char_length": len(agent_reply or ""),
                    "ok": bool(audio_output),
                }

            turn_span.exit_metadata = {
                "decision": {k: v for k, v in decision.items() if not str(k).startswith("_")},
                "state": _state_public(state),
                "state_diff": state_diff,
                "agent_reply": agent_reply,
                "tool_trace": tool_trace,
                "guards": guards,
            }

        history.append({"role": "assistant", "content": json.dumps(
            {k: v for k, v in decision.items() if not str(k).startswith("_")},
            ensure_ascii=False,
        )})

        decision.pop("_tool_trace", None)
        decision.pop("_guard_events", None)
        decision.pop("_state_diff", None)

        print(f"\n[LLM Decision]\n{json.dumps(decision, ensure_ascii=False, indent=2)}")
        if tool_trace:
            print(f"\n[Tool Calls]\n{json.dumps(tool_trace, ensure_ascii=False, indent=2)}")
        print(f"\n[State]\n{json.dumps(state, ensure_ascii=False, indent=2)}")
        print(f"\nAgent : {agent_reply}\n{'-'*60}")

    terminal_logs = buf.getvalue()
    chat_display.append({"role": "user", "content": customer_text})
    chat_display.append({"role": "assistant", "content": agent_reply})

    debug_info = debug_stt + "\n--- Terminal Logs ---\n" + terminal_logs
    debug_info += f"\n[Monitor] session={session_id[:8]}… turn={turn_id[:8]}…\n"

    if state.get("order_step") == "done":
        debug_info += "\n✅ ORDER COMPLETE!\n"
        lang = state.get("language", "ar")
        mobile = state.get("mobile")
        state = fresh_state()
        state["language"] = lang
        state["mobile"] = mobile
        history = []

    clear_turn()
    return state, history, chat_display, debug_info, audio_output, session_id


def process_text(text_val, voice_name, state, history, chat_display, session_id):
    return process_input(None, text_val, voice_name, state, history, chat_display, session_id)


def process_audio_wrapper(audio_path, voice_name, state, history, chat_display, session_id):
    return process_input(audio_path, None, voice_name, state, history, chat_display, session_id)


def clear_all(session_id):
    # Keep session_id so monitor continues the same session after clear
    return fresh_state(), [], [], "Conversation cleared.", None, session_id or str(uuid.uuid4())


def new_session_id():
    return str(uuid.uuid4())


with gr.Blocks(title="Tania Voice Agent") as demo:
    gr.Markdown("# Tania Voice Agent (Live Multi-Modal Demo)")

    tania_state = gr.State(fresh_state())
    tania_history = gr.State([])
    session_id_state = gr.State(new_session_id())

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
                label="Agent Voice (Requires downloaded Piper models)",
            )
            clear_btn = gr.Button("Clear Conversation")

        with gr.Column(scale=1):
            debug_box = gr.Textbox(label="Terminal & Debug Logs", lines=35, interactive=False)

    out_slots = [tania_state, tania_history, chatbot, debug_box, tania_voice, session_id_state]

    text_submit_btn.click(
        fn=process_text,
        inputs=[text_input, voice_selector, tania_state, tania_history, chatbot, session_id_state],
        outputs=out_slots,
    ).then(
        fn=lambda: "", inputs=None, outputs=text_input
    )

    audio_submit_btn.click(
        fn=process_audio_wrapper,
        inputs=[audio_input, voice_selector, tania_state, tania_history, chatbot, session_id_state],
        outputs=out_slots,
    )

    clear_btn.click(
        fn=clear_all,
        inputs=[session_id_state],
        outputs=out_slots,
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7862, inbrowser=True, share=True)
