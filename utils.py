import re
import json

def detect_language(text: str) -> str:
    arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
    return "ar" if arabic_chars > 2 else "en"


def strip_cjk_leakage(text: str) -> str:
    text = re.sub(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", "", text)
    return text.strip()


def parse_first_json(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        pass

    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(raw[start : i + 1])
                except Exception:
                    pass
    return None
