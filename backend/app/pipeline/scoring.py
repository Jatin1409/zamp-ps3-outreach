"""Signal ranking. The LLM supplies 0-3 judgements; this module turns them into a transparent score in code."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from urllib.parse import urlparse

WEIGHTS = {"specificity": 0.30, "relevance": 0.30, "recency": 0.25, "credibility": 0.15}
MIN_ELIGIBLE_SCORE = 0.30        # below this nothing is worth drafting on
PERSON_LEVEL_MIN = 0.40          # a person-level hook must clear this to count as "personalised"
STALE_DAYS = 548                 # ~18 months: filtered out before ranking

TIER1 = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com", "techcrunch.com", "businesswire.com",
    "prnewswire.com", "globenewswire.com", "forbes.com", "fortune.com", "axios.com", "theinformation.com",
    "nytimes.com", "economictimes.indiatimes.com", "livemint.com", "business-standard.com", "sec.gov",
    "apnews.com", "bbc.co.uk", "bbc.com", "theguardian.com", "venturebeat.com", "crunchbase.com",
}
TRADE = {
    "cfo.com", "cfodive.com", "paymentsdive.com", "finextra.com", "pymnts.com", "fintechfutures.com",
    "accountingtoday.com", "siliconangle.com", "zdnet.com", "inc42.com", "yourstory.com", "entrackr.com",
    "thesaasnews.com", "fintech.global", "americanbanker.com", "retaildive.com", "supplychaindive.com",
    "constructiondive.com", "healthcaredive.com", "builtin.com", "geekwire.com", "sifted.eu", "tech.eu",
}
LOW = {"medium.com", "substack.com", "quora.com", "reddit.com", "facebook.com", "x.com", "twitter.com",
       "zoominfo.com", "rocketreach.co", "signalhire.com", "theorg.com", "apollo.io", "lusha.com"}


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def credibility_score(url: str, company_domain: str | None = None, scope: str = "company") -> tuple[float, str]:
    if scope == "rep" or url.startswith("rep://"):
        return 0.7, "Rep-provided note (not independently verified)"
    host = domain_of(url)
    if company_domain and (host == company_domain or host.endswith("." + company_domain)):
        return 0.9, f"Company's own site ({host})"
    if _matches(host, TIER1):
        return 1.0, f"Wire service / major publication ({host})"
    if _matches(host, TRADE):
        return 0.75, f"Trade or industry press ({host})"
    if _matches(host, LOW) or "linkedin.com" in host:
        return 0.4, f"User-generated or data-broker site ({host})"
    return 0.5, f"Other web source ({host})"


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    v = value.strip()
    if len(v) >= 10 and v[4] == "-" and v[7] == "-":
        try:
            return dt.date.fromisoformat(v[:10])
        except ValueError:
            pass
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(v).date()
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def age_days(published: str | None, today: dt.date) -> int | None:
    d = parse_date(published)
    return None if d is None else max(0, (today - d).days)


def recency_score(published: str | None, today: dt.date, scope: str = "company") -> tuple[float, str]:
    if scope == "rep":
        return 0.8, "Rep-provided (assumed recent)"
    days = age_days(published, today)
    if days is None:
        return 0.3, "Publication date unknown"
    if days <= 30:
        return 1.0, f"{days} days old"
    if days >= 365:
        return 0.1, f"{days} days old"
    return round(1.0 - 0.9 * (days - 30) / (365 - 30), 3), f"{days} days old"


@dataclass
class ScoredSignal:
    sid: str
    specificity: float
    relevance: float
    recency: float
    credibility: float
    total: float
    eligible: bool
    ineligible_reason: str | None = None
    notes: dict = field(default_factory=dict)

    def components(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in WEIGHTS}


def combine(specificity_0_3: int, relevance_0_3: int, recency: float, credibility: float) -> float:
    spec = max(0, min(3, specificity_0_3)) / 3
    rel = max(0, min(3, relevance_0_3)) / 3
    total = (WEIGHTS["specificity"] * spec + WEIGHTS["relevance"] * rel + WEIGHTS["recency"] * recency
             + WEIGHTS["credibility"] * credibility)
    return round(total, 4)


def score_signal(*, sid: str, specificity: int, relevance: int, published: str | None, url: str, scope: str,
                 today: dt.date, company_domain: str | None, key_fact_verified: bool,
                 sensitive: bool) -> ScoredSignal:
    rec, rec_note = recency_score(published, today, scope)
    cred, cred_note = credibility_score(url, company_domain, scope)
    notes = {"recency": rec_note, "credibility": cred_note}
    if not key_fact_verified:
        specificity, relevance = 0, 0
        notes["key_fact"] = "Key fact not found verbatim in source text - judgements zeroed"
    total = combine(specificity, relevance, rec, cred)
    eligible, reason = True, None
    if sensitive:
        eligible, reason = False, "Sensitive topic - never used as an upbeat hook"
    elif not key_fact_verified:
        eligible, reason = False, "No verifiable fact in the source"
    return ScoredSignal(sid=sid, specificity=round(max(0, min(3, specificity)) / 3, 3),
                        relevance=round(max(0, min(3, relevance)) / 3, 3), recency=rec, credibility=cred,
                        total=total, eligible=eligible, ineligible_reason=reason, notes=notes)


def explain_ranking(scored: list[ScoredSignal]) -> dict:
    """Why the winner won and why each other signal lost - in words a rep can read."""
    ranked = sorted(scored, key=lambda s: s.total, reverse=True)
    eligible = [s for s in ranked if s.eligible]
    if not eligible:
        return {"winner": None, "why_winner": "No signal was eligible as a hook.",
                "losers": {s.sid: s.ineligible_reason or "Not eligible" for s in ranked}}
    win = eligible[0]
    comps = win.components()
    top = sorted(comps, key=lambda k: WEIGHTS[k] * comps[k], reverse=True)[:2]
    runner = eligible[1] if len(eligible) > 1 else None
    margin = round(win.total - runner.total, 3) if runner else None
    why = (f"Highest combined score ({win.total:.2f}); strongest on {top[0]} and {top[1]}."
           + (f" Beat the runner-up by {margin:.2f}." if runner else " It was the only eligible signal."))
    losers = {}
    for s in ranked:
        if s.sid == win.sid:
            continue
        if not s.eligible:
            losers[s.sid] = s.ineligible_reason or "Not eligible"
            continue
        gaps = {k: WEIGHTS[k] * (comps[k] - getattr(s, k)) for k in WEIGHTS}
        k = max(gaps, key=gaps.get)
        losers[s.sid] = (f"Scored {s.total:.2f} vs {win.total:.2f}; biggest gap was {k} "
                         f"({getattr(s, k):.2f} vs {comps[k]:.2f}).")
    return {"winner": win.sid, "why_winner": why, "losers": losers, "margin": margin}
