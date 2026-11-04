"""Code-level checks on a draft's style rules (the LLM is asked to follow them; code makes sure)."""
from __future__ import annotations

import re

from .textmatch import word_count

MAX_WORDS = 120
BANNED = [
    "hope this finds you", "hope you're well", "hope you are well", "i came across", "i noticed", "i saw that",
    "congrats", "congratulations", "impressive", "amazing", "incredible", "game-changer", "game changer",
    "synergy", "revolutionize", "revolutionise", "touch base", "circle back", "quick call", "just following up",
    "i'd love to", "i would love to", "pick your brain", "low-hanging fruit", "best-in-class", "world-class",
    "cutting-edge", "as we discussed", "great to connect", "following our conversation", "we spoke",
    "exciting times", "thrilled", "kudos",
]


def lint_draft(subject: str, body: str) -> dict:
    issues: list[str] = []
    text = f"{subject}\n{body}".lower()
    wc = word_count(body)
    if wc > MAX_WORDS:
        issues.append(f"Body is {wc} words; limit is {MAX_WORDS}.")
    if word_count(subject) > 10:
        issues.append("Subject is longer than 10 words.")
    found = [p for p in BANNED if p in text]
    if found:
        issues.append("Contains banned phrases: " + ", ".join(f"'{p}'" for p in found) + ".")
    if "!" in subject + body:
        issues.append("Contains an exclamation mark.")
    q = body.count("?")
    if q > 1:
        issues.append(f"Contains {q} questions; use exactly one ask.")
    if re.search(r"[\U0001F300-\U0001FAFF]", subject + body):
        issues.append("Contains an emoji.")
    return {"ok": not issues, "issues": issues, "word_count": wc}
