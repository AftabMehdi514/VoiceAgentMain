import re
import json

_RESPONSE_KEY_RE = re.compile(r'"response"\s*:\s*"')


def detect_language(text: str) -> str:
    arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
    return "ar" if arabic_chars > 2 else "en"


def strip_cjk_leakage(text: str) -> str:
    text = re.sub(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", "", text)
    return text.strip()


def extract_partial_response(raw: str):
    """
    Extract the in-progress JSON string value of "response" for UI streaming.
    Returns None until the key/opening quote appears; otherwise the decoded
    prefix so far (may be incomplete while tokens are still arriving).
    """
    if not raw:
        return None
    m = _RESPONSE_KEY_RE.search(raw)
    if not m:
        return None
    i = m.end()
    out = []
    while i < len(raw):
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= len(raw):
                break
            nxt = raw[i + 1]
            simple = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if nxt == "u":
                if i + 5 >= len(raw):
                    break
                try:
                    out.append(chr(int(raw[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    break
            break
        if ch == '"':
            break
        out.append(ch)
        i += 1
    return "".join(out)


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
