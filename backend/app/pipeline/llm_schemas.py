"""Strict schemas for every LLM output. Anything that does not validate is rejected (and retried once)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Stage 1: identity -------------------------------------------------------
class Evidence(Strict):
    source: int = Field(description="Index of the source in the provided list")
    quote: str = Field(description="Verbatim sentence from that source supporting this candidate")


class Candidate(Strict):
    full_name: str
    current_company: str = Field(description="Company the sources say they work at NOW ('' if unknown)")
    current_role: str = Field(description="Current title per sources ('' if unknown)")
    at_input_company: Literal["current", "former", "never", "unclear"]
    role_matches_input: Literal["yes", "no", "unknown"]
    distinguisher: str = Field(description="What distinguishes this person from others with the same name "
                                           "(team, location, prior company) - professional facts only")
    is_fictional: bool = Field(default=False, description="True only if the sources describe this as a fictional "
                                                          "character (film, TV, book, game, comic), not a real person")
    fiction_source: str = Field(default="", description="The work of fiction it comes from, if fictional; else ''")
    evidence: list[Evidence]


class CompanyFinding(Strict):
    name: str = Field(description="Company name as the sources write it")
    what_it_does: str = Field(description="One short phrase, from the sources (e.g. 'fire-fighting vehicle "
                                          "maker'); '' if unknown")
    short_label: str = Field(description="2-4 words that tell it apart from same-named companies "
                                         "(e.g. 'fintech bank, San Francisco')")
    is_fictional: bool = Field(description="True only if the sources describe it as a fictional company from a "
                                           "film, TV show, book or game")
    fiction_source: str = Field(description="The work of fiction, if fictional; else ''")
    evidence: list[Evidence]


class IdentityExtraction(Strict):
    candidates: list[Candidate]
    companies: list[CompanyFinding] | None = Field(
        default=None, description="Distinct organisations in the sources whose name matches the company the rep "
                                  "entered (real or fictional). Empty list if none.")
    conflicting_sources: bool = Field(description="True if sources disagree about who this is or where they work")
    summary: str


# --- Stage 3: filter -----------------------------------------------------------
class FilterJudgment(Strict):
    sid: str
    about: Literal["person", "company", "other_person", "other_company", "industry", "unclear"]
    signal_type: Literal["funding", "hiring", "press", "interview", "product_launch", "company_news", "post",
                         "earnings", "partnership", "award", "other"]
    sensitive_category: Literal["none", "layoff", "lawsuit", "breach", "executive_departure", "personal_event",
                                "other_negative"]
    contains_personal_details: bool = Field(description="Family, health, home address/location, religion, "
                                                        "politics or other non-professional personal info")
    reason: str = Field(description="One short sentence")


class FilterBatch(Strict):
    judgments: list[FilterJudgment]


# --- Stage 4: rank -----------------------------------------------------------------
class RankJudgment(Strict):
    sid: str
    specificity: int = Field(description="0-3. 3 = by/about this person directly; 2 = their team or function; "
                                         "1 = company-wide; 0 = generic/industry")
    relevance: int = Field(description="0-3. How plausibly this creates a problem the seller solves. 3 = direct "
                                       "and obvious; 0 = none")
    key_fact: str = Field(description="The single most useful fact, copied VERBATIM from the source text")
    seller_problem: str = Field(description="Which seller problem it connects to ('' if none)")
    why: str = Field(description="One sentence explaining both scores")


class RankBatch(Strict):
    judgments: list[RankJudgment]


# --- Stage 6: draft -------------------------------------------------------------------
class DraftOut(Strict):
    subject: str
    body: str
    hook_signal_id: str
    problem_addressed: str
    ask: str


# --- Stage 7: verify ------------------------------------------------------------------
class Claim(Strict):
    claim: str = Field(description="One atomic statement from the draft")
    kind: Literal["prospect_fact", "company_fact", "seller_fact", "opinion_or_framing", "ask"]
    source_id: str = Field(description="Signal id like 's3', or 'seller.<field>' for seller-profile facts, "
                                       "or '' if none")
    supporting_quote: str = Field(description="Verbatim text from that source that supports the claim, or ''")
    entailed: bool = Field(description="Does the quote fully support the claim as written?")


class ClaimBatch(Strict):
    claims: list[Claim]


# --- Stage 8: genericness --------------------------------------------------------------
class DecoyCheck(Strict):
    decoy: str
    still_makes_sense: bool
    why: str


class GenericnessOut(Strict):
    decoys: list[DecoyCheck]
    specific_elements: list[str] = Field(description="Phrases that only make sense for this prospect")
