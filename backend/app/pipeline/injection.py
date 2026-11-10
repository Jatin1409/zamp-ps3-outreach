"""Prompt-injection defences for retrieved web text and rep-pasted notes.

Layered:
1. Detection (here): pattern match on instruction-like text aimed at an AI. A hit quarantines the source: it is
   stored and shown in the UI, but never reaches the drafting model.
2. Isolation (prompts.py): every untrusted text is wrapped in <untrusted_source> tags, with the system prompt
   telling the model it is data, and angle brackets inside the text are escaped so it cannot close the tag.
3. Containment: the drafting model only sees the chosen hook and supporting signals (never quarantined text),
   and each hook's key fact must be a verbatim quote found in the stored page before it can be used.
4. Schema: model outputs are parsed into strict Pydantic schemas; free-form tool use is never offered.
"""
from __future__ import annotations

import re

_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|messages|rules)",
    r"disregard (all |any |the )?(previous|prior|above|earlier|your) (instructions|prompts|rules|guidelines)",
    r"forget (all |your |the )?(previous |prior )?(instructions|rules|guidelines)",
    r"\byou are now (a|an|the|my|acting|in \w+ mode|free|unrestricted|jailbroken)\b",
    r"\bnew instructions\s*:",
    r"\bsystem prompt\b",
    r"\b(as an|to the|attention,?) (ai|llm|language model|assistant|chatbot)\b",
    r"\b(ai|llm|assistant|chatbot)s? (reading|processing|summari[sz]ing) this\b",
    r"<\s*/?\s*(system|instructions?|untrusted_source)\s*>",
    r"\b(do not|don't) (tell|inform|mention to) the (user|rep|human)\b",
    r"\boverride (your|the) (instructions|rules|guidelines)\b",
    r"\b(include|insert|add) (the following|this) (link|url|text) in (your|the|every) (email|message|draft|reply)",
    r"\bjailbreak\b",
]
_RX = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]


def detect_injection(text: str | None) -> list[str]:
    """Return the matched snippets (empty list = clean)."""
    if not text:
        return []
    hits = []
    for rx in _RX:
        m = rx.search(text)
        if m:
            start = max(0, m.start() - 40)
            hits.append(text[start:m.end() + 40].replace("\n", " "))
    return hits


def escape_for_prompt(text: str) -> str:
    """Neutralise anything that could close or open our delimiter tags."""
    return (text or "").replace("<", "‹").replace(">", "›")
