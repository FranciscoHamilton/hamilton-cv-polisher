# gpt_formatter.py

import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

# Step 1: Extract all content (100% capture)
def extract_full_cv_content(raw_text):
    system_prompt = (
        "You are a CV parser that extracts all available content from unstructured resumes. "
        "Your goal is to preserve 100% of the information. Do NOT summarize or drop anything. "
        "Return sections and bullet points as-is, organized under headers like 'Summary', 'Work Experience', 'Education', etc."
    )

    response = openai.ChatCompletion.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_text}
        ]
    )

    return response.choices[0].message.content

# Step 2: Format extracted content into Hamilton CV structure
def format_to_hamilton_style(extracted_text):
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

    response = openai.ChatCompletion.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": format_prompt},
            {"role": "user", "content": extracted_text}
        ]
    )

    return response.choices[0].message.content
