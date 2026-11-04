"""Deterministic text checks used to keep the LLM honest: a quote only counts if it is really in the source."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\"'“”‘’`´]")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _PUNCT.sub("", text)
    text = text.replace("–", "-").replace("—", "-").replace("…", "...")
    return _WS.sub(" ", text).strip().lower()


def quote_in_text(quote: str | None, text: str | None, threshold: float = 90.0) -> tuple[bool, float]:
    """True if `quote` appears (near-verbatim) in `text`.

    Short quotes (<12 chars) must match exactly - fuzzy matching on tiny strings is meaningless.
    For long quotes we use partial_ratio, which finds the best-aligned window of `text`.
    """
    q, t = normalize(quote or ""), normalize(text or "")
    if not q or not t:
        return False, 0.0
    if q in t:
        return True, 100.0
    if len(q) < 12:
        return False, 0.0
    if len(q) > len(t):
        return False, 0.0
    score = fuzz.partial_ratio(q, t)
    return score >= threshold, float(score)


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text or ""))


def levenshtein(a: str, b: str) -> int:
    from rapidfuzz.distance import Levenshtein

    return Levenshtein.distance(a or "", b or "")
