"""
tts_module.py
====================================================================
Local Piper TTS for Tania (Arabic voices).

Uses piper-tts >= 1.0 API:
    wave.open(...) + voice.synthesize_wav(text, wav_file)

Do NOT call voice.synthesize(text, file) — in piper-tts 1.6+ that returns
a generator and never writes audio (produces 0-byte files).
====================================================================
"""

import os
import tempfile
import wave

from piper.voice import PiperVoice

TTS_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")

AVAILABLE_VOICES = {
    "Miro (ar-SA)": os.path.join(TTS_MODELS_DIR, "miro.onnx"),
    "Dii (ar-SA)": os.path.join(TTS_MODELS_DIR, "dii.onnx"),
    "Kareem (ar-JO)": os.path.join(TTS_MODELS_DIR, "kareem.onnx"),
}

DEFAULT_VOICE = "Miro (ar-SA)"

# Minimum valid WAV size: 44-byte header + a little PCM
_MIN_WAV_BYTES = 100

# Cache for the loaded PiperVoice instance
_current_voice_name = None
_voice_instance = None


def list_voices():
    """Return voice labels that have both .onnx and .onnx.json on disk."""
    ready = []
    for name, model_path in AVAILABLE_VOICES.items():
        if os.path.exists(model_path) and os.path.exists(model_path + ".json"):
            ready.append(name)
    return ready


def get_voice(voice_name):
    """Load (or return cached) PiperVoice for the given UI voice label."""
    global _current_voice_name, _voice_instance

    if not voice_name or voice_name not in AVAILABLE_VOICES:
        print(f"[TTS] Unknown voice {voice_name!r}; falling back to {DEFAULT_VOICE}.")
        voice_name = DEFAULT_VOICE

    if _current_voice_name == voice_name and _voice_instance is not None:
        return _voice_instance

    model_path = AVAILABLE_VOICES.get(voice_name)
    if not model_path or not os.path.exists(model_path):
        print(f"[TTS] Warning: Model not found: {model_path}")
        print(f"[TTS] Download models into {TTS_MODELS_DIR} (see tts/download_models.py).")
        return None

    config_path = model_path + ".json"
    if not os.path.exists(config_path):
        print(f"[TTS] Warning: Config not found: {config_path}")
        return None

    try:
        print(f"[TTS] Loading {voice_name} from {model_path}...")
        loaded = PiperVoice.load(model_path, config_path=config_path)
    except Exception as e:
        print(f"[TTS] Failed to load {voice_name}: {e}")
        return None

    _voice_instance = loaded
    _current_voice_name = voice_name
    print(f"[TTS] {voice_name} loaded successfully!")
    return _voice_instance


def _wav_is_valid(path):
    """True if path is a readable WAV with actual PCM frames."""
    try:
        if not path or not os.path.exists(path):
            return False
        if os.path.getsize(path) < _MIN_WAV_BYTES:
            return False
        with wave.open(path, "rb") as wf:
            return wf.getnframes() > 0 and wf.getframerate() > 0
    except Exception:
        return False


def generate_audio(text, voice_name=DEFAULT_VOICE):
    """
    Synthesize `text` with Piper and return a temp .wav path, or None on failure.

    Gradio Audio accepts a filepath string; caller should treat None as TTS skip/fail.
    """
    if not text or not str(text).strip():
        return None

    voice = get_voice(voice_name)
    if not voice:
        return None

    out_path = None
    try:
        fd, out_path = tempfile.mkstemp(prefix="tania_tts_", suffix=".wav")
        os.close(fd)

        with wave.open(out_path, "wb") as wav_file:
            voice.synthesize_wav(str(text).strip(), wav_file)

        if not _wav_is_valid(out_path):
            size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
            print(f"[TTS] Generation produced invalid/empty WAV (size={size}).")
            try:
                os.remove(out_path)
            except OSError:
                pass
            return None

        print(f"[TTS] Wrote {os.path.getsize(out_path)} bytes -> {out_path}")
        return out_path

    except Exception as e:
        print(f"[TTS] Generation error: {e}")
        if out_path:
            try:
                os.remove(out_path)
            except OSError:
                pass
        return None
