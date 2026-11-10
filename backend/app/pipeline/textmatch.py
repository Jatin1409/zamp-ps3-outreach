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


_DOB = re.compile(r"\b(date of birth|born on|born in|d\.o\.b\.?|dob)\s*[:\-]?\s*[^\n.;|]{0,60}", re.IGNORECASE)


def redact_personal(text: str | None) -> str:
    """Strip date-of-birth / birthplace fragments. Applied to raw search results before they are stored."""
    return _DOB.sub("[personal detail removed]", text or "")


_LEGAL = {"inc", "incorporated", "ltd", "limited", "llc", "llp", "plc", "gmbh", "ag", "sa", "sas", "bv", "nv", "co",
          "corp", "corporation", "company", "group", "holdings", "the", "and", "&", "pvt", "private", "se", "oy", "ab",
          "kg", "srl", "spa", "pte", "pty"}


def company_tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", normalize(name)) if t not in _LEGAL]


def company_matches(entered: str, found: str) -> bool:
    """Every significant word the rep typed must appear in the found name (legal suffixes ignored).

    'Rosenbauer International' ~ 'Rosenbauer International AG' (yes); 'Mercury' ~ 'Mercury Technologies Inc.' (yes);
    'Brightwater Fasteners GmbH' ~ 'Brightwater Ltd' (NO - a fuzzy token-set ratio called this a perfect match)."""
    a, b = company_tokens(entered), company_tokens(found)
    if not a or not b:
        return False
    def same(t: str, u: str) -> bool:  # short words must match exactly ('canva' is not 'canvas')
        return t == u if len(t) <= 5 else fuzz.ratio(t, u) >= 90
    return all(any(same(t, u) for u in b) for t in a)


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text or ""))


def levenshtein(a: str, b: str) -> int:
    from rapidfuzz.distance import Levenshtein

    return Levenshtein.distance(a or "", b or "")
