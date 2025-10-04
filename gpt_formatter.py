# gpt_formatter.py

import os

# --- Compatibility layer: supports OpenAI 1.x and legacy 0.28 ---

_OPENAI_V1 = False
_client_v1 = None

try:
    # OpenAI >= 1.x style
    from openai import OpenAI  # type: ignore
    _client_v1 = OpenAI()
    _OPENAI_V1 = True
except Exception:
    # Fallback to legacy 0.28
    import openai  # type: ignore
    openai.api_key = os.getenv("OPENAI_API_KEY", "")

def _chat(messages, *, model=None, temperature=0.0):
    """
    Minimal wrapper that returns assistant message content as a string.
    Works on both OpenAI 1.x and OpenAI 0.28.
    """
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if _OPENAI_V1:
        resp = _client_v1.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()
    else:
        resp = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        return (resp["choices"][0]["message"]["content"] or "").strip()


# --- Public API ---

# Step 1: Extract all content (100% capture)
def extract_full_cv_content(raw_text: str) -> str:
    system_prompt = (
        "You are a CV parser that extracts all available content from unstructured resumes. "
        "Your goal is to preserve 100% of the information. Do NOT summarize or drop anything. "
        "Return sections and bullet points as-is, organized under headers like 'Summary', 'Work Experience', 'Education', etc."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": raw_text},
    ]
    return _chat(messages, temperature=0.0)

# Step 2: Format extracted content into Hamilton CV structure
def format_to_hamilton_style(extracted_text: str) -> str:
    format_prompt = (
        "You are a CV structuring assistant for Hamilton Recruitment. "
        "Format the following CV content using this exact structure:\n\n"
        "- EXECUTIVE SUMMARY\n"
        "- PERSONAL INFORMATION\n"
        "- PROFESSIONAL QUALIFICATIONS\n"
        "- PROFESSIONAL SKILLS\n"
        "- PROFESSIONAL EXPERIENCE\n"
        "- REFERENCES (if provided)\n\n"
        "Do not summarize or omit any content. Include all details. Use bullet points, proper headers, and spacing. "
        "Maintain a neutral, professional tone. Keep 100% of the original content. Begin formatting now:\n\n"
    )
    messages = [
        {"role": "system", "content": format_prompt},
        {"role": "user", "content": extracted_text},
    ]
    return _chat(messages, temperature=0.0)
