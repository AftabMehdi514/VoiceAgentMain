import os
import glob
import sys
import time

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

MODEL_NAME = "large-v3-turbo"
DOWNLOAD_ROOT = "E:\\WhisperModels"

print(f"[STT] Loading local model '{MODEL_NAME}' from {DOWNLOAD_ROOT}...")
model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16", download_root=DOWNLOAD_ROOT)
print("[STT] Model loaded successfully!")

def transcribe_audio(audio_path):
    t0 = time.perf_counter()
    segments, info = model.transcribe(audio_path, beam_size=5)
    customer_text = ""
    for segment in segments:
        customer_text += segment.text
    customer_text = customer_text.strip()
    stt_time = time.perf_counter() - t0
    
    debug_str = f"STT Latency: {stt_time:.2f}s | Lang: {info.language} ({info.language_probability:.2f})\n"
    
    return customer_text, debug_str
