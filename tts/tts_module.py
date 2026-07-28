import os
import tempfile
import time
from piper.voice import PiperVoice

TTS_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")

# Define the models we expect to have
AVAILABLE_VOICES = {
    "Miro (ar-SA)": os.path.join(TTS_MODELS_DIR, "miro.onnx"),
    "Dii (ar-SA)": os.path.join(TTS_MODELS_DIR, "dii.onnx"),
    "Kareem (ar-JO)": os.path.join(TTS_MODELS_DIR, "kareem.onnx"),
}

# Cache for the loaded PiperVoice instance
_current_voice_name = None
_voice_instance = None

def get_voice(voice_name):
    global _current_voice_name, _voice_instance
    if _current_voice_name == voice_name and _voice_instance is not None:
        return _voice_instance
    
    model_path = AVAILABLE_VOICES.get(voice_name)
    if not model_path or not os.path.exists(model_path):
        print(f"[TTS] Warning: Model {model_path} not found. Ensure it is downloaded.")
        return None
    
    config_path = model_path + ".json"
    if not os.path.exists(config_path):
        print(f"[TTS] Warning: Config {config_path} not found.")
        return None
        
    print(f"[TTS] Loading {voice_name} from {model_path}...")
    _voice_instance = PiperVoice.load(model_path, config_path=config_path)
    _current_voice_name = voice_name
    print(f"[TTS] {voice_name} loaded successfully!")
    return _voice_instance

def generate_audio(text, voice_name="Miro (ar-SA)"):
    if not text or not text.strip():
        return None
        
    voice = get_voice(voice_name)
    if not voice:
        return None
        
    try:
        # Create a temp wav file
        temp_dir = tempfile.gettempdir()
        out_path = os.path.join(temp_dir, f"tania_tts_{int(time.time())}.wav")
        
        with open(out_path, "wb") as f:
            voice.synthesize(text, f)
            
        return out_path
    except Exception as e:
        print(f"[TTS] Generation error: {e}")
        return None
