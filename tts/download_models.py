import os
import urllib.request

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODELS_DIR, exist_ok=True)

# Define known URLs for the models (Update if the OpenVoiceOS URLs are different)
MODELS_TO_DOWNLOAD = {
    "kareem.onnx": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx",
    "kareem.onnx.json": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx.json",
    
    # Placeholders for Miro and Dii. (Community models)
    # The user can replace these URLs with the exact HuggingFace raw download links if they differ.
    "miro.onnx": "https://huggingface.co/OVI/ovos-tts-plugin-piper/resolve/main/models/pipertts_ar-SA_miro.onnx",
    "miro.onnx.json": "https://huggingface.co/OVI/ovos-tts-plugin-piper/resolve/main/models/pipertts_ar-SA_miro.onnx.json",
    
    "dii.onnx": "https://huggingface.co/OVI/ovos-tts-plugin-piper/resolve/main/models/pipertts_ar-SA_dii.onnx",
    "dii.onnx.json": "https://huggingface.co/OVI/ovos-tts-plugin-piper/resolve/main/models/pipertts_ar-SA_dii.onnx.json",
}

def download_file(url, dest):
    print(f"Downloading {os.path.basename(dest)}...")
    try:
        urllib.request.urlretrieve(url, dest)
        print("Success!")
    except Exception as e:
        print(f"Failed to download {os.path.basename(dest)}: {e}")
        print(f"-> Please download manually from HuggingFace and place it in {MODELS_DIR}")

if __name__ == "__main__":
    for filename, url in MODELS_TO_DOWNLOAD.items():
        dest = os.path.join(MODELS_DIR, filename)
        if not os.path.exists(dest):
            download_file(url, dest)
        else:
            print(f"{filename} already exists, skipping.")
