"""Deterministic decision rules. No LLM calls here: every branch is explainable and unit-tested."""
from __future__ import annotations

from dataclasses import dataclass, field

from .scoring import MIN_ELIGIBLE_SCORE, PERSON_LEVEL_MIN

DRAFT = "draft"
DRAFT_WITH_WARNING = "draft_with_warning"
ASK_REP = "ask_rep"
DO_NOT_DRAFT = "do_not_draft"

SENSITIVE_UNAVOIDABLE_DAYS = 90
SENSITIVE_DOMINANCE_MARGIN = 0.10
SENSITIVE_FRESH_DAYS = 30


# --------------------------------------------------------------------------- identity gate (decision A)
@dataclass
class CandidateView:
    id: str
    full_name: str
    current_company: str
    current_role: str
    at_input_company: str      # current | former | never | unclear
    role_matches_input: str    # yes | no | unknown
    verified_sources: int      # distinct source URLs whose evidence quote was found in the text
    name_matches: bool


@dataclass
class IdentityDecision:
    status: str                # resolved | ambiguous | role_conflict | not_found
    confidence: str            # high | medium | low
    reasons: list[str]
    chosen: str | None = None  # candidate id
    candidates: list[str] = field(default_factory=list)
    conflict_kind: str | None = None  # company_change | role_change


def decide_identity(cands: list[CandidateView], has_input_role: bool, conflicting_sources: bool) -> IdentityDecision:
    plausible = [c for c in cands if c.name_matches and c.verified_sources > 0]
    unverified = [c for c in cands if c.name_matches and c.verified_sources == 0]
    reasons: list[str] = []
    if unverified:
        reasons.append(f"{len(unverified)} candidate(s) ignored because their evidence quotes were not found in "
                       "the retrieved text")
    if not plausible:
        return IdentityDecision("not_found", "low",
                                reasons + ["No public source links this name to this company. Continuing with "
                                           "company-level research only."])

    at_company = [c for c in plausible if c.at_input_company in ("current", "unclear")]
    former = [c for c in plausible if c.at_input_company == "former"]

    if len(at_company) >= 2:
        return IdentityDecision("ambiguous", "low", reasons + [
            f"{len(at_company)} different people match this name at this company: "
            + "; ".join(f"{c.full_name} ({c.current_role or 'role unknown'})" for c in at_company)],
            candidates=[c.id for c in at_company])

    if not at_company and former:
        c = max(former, key=lambda x: x.verified_sources)
        return IdentityDecision("role_conflict", "low", reasons + [
            f"Sources say {c.full_name} has left the company you entered; now "
            f"{c.current_role or 'role unknown'} at {c.current_company or 'an unknown company'}."],
            chosen=c.id, candidates=[c.id], conflict_kind="company_change")

    if not at_company:
        # name matches but evidence ties them to a different company only
        c = max(plausible, key=lambda x: x.verified_sources)
        if len(plausible) >= 2:
            return IdentityDecision("ambiguous", "low", reasons + [
                "Several people with this name were found, none clearly at the company you entered."],
                candidates=[x.id for x in plausible])
        return IdentityDecision("role_conflict", "low", reasons + [
            f"The only matching person found is at {c.current_company or 'a different company'}, "
            "not the company you entered."], chosen=c.id, candidates=[c.id], conflict_kind="company_change")

    c = at_company[0]
    if has_input_role and c.role_matches_input == "no":
        return IdentityDecision("role_conflict", "low", reasons + [
            f"{c.full_name} is at the company, but sources give a different role: {c.current_role}."],
            chosen=c.id, candidates=[c.id], conflict_kind="role_change")

    if conflicting_sources and former:
        return IdentityDecision("ambiguous", "low", reasons + [
            "Sources conflict on whether this person is still at the company."],
            candidates=[c.id] + [f.id for f in former])

    if c.verified_sources >= 2 and (c.role_matches_input == "yes" or not has_input_role) \
            and c.at_input_company == "current":
        conf = "high"
        reasons.append(f"{c.verified_sources} independent sources confirm {c.full_name} at the company"
                       + (" in the stated role." if has_input_role else "."))
    else:
        conf = "medium"
        if c.verified_sources < 2:
            reasons.append("Only one source confirms the person at the company.")
        if c.at_input_company == "unclear":
            reasons.append("Sources do not say clearly whether the role is current.")
        if has_input_role and c.role_matches_input == "unknown":
            reasons.append("Role could not be confirmed from sources.")
    return IdentityDecision("resolved", conf, reasons, chosen=c.id, candidates=[c.id])


# --------------------------------------------------------------------------- route (decision B)
@dataclass
class SignalView:
    sid: str
    total: float
    eligible: bool
    about: str                 # person | company
    sensitive_category: str    # none | layoff | ...
    age_days: int | None
    specificity_0_3: int


@dataclass
class RouteDecision:
    route: str
    rule: str
    reason: str
    hook: str | None
    personalisation: str       # person | company | none
    sensitive_mode: str = "none"   # none | avoided | acknowledge
    sensitive_signal: str | None = None
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def decide_route(signals: list[SignalView], identity_confidence: str, rep_stopped: bool = False,
                 continued_despite_conflict: bool = False) -> RouteDecision:
    if rep_stopped:
        return RouteDecision(DO_NOT_DRAFT, "R1", "The rep chose to stop at the identity check.", None, "none")

    ranked = sorted(signals, key=lambda s: s.total, reverse=True)
    eligible = [s for s in ranked if s.eligible and s.total >= MIN_ELIGIBLE_SCORE]
    warnings: list[str] = []

    if not eligible:
        missing = []
        if not any(s.about == "person" for s in signals):
            missing.append("Nothing public and recent about the person themselves (interviews, posts, talks, quotes).")
        if not any(s.about == "company" for s in signals):
            missing.append("No recent company news (funding, hiring, launches, press) from a credible source.")
        if signals and not missing:
            missing.append(f"Signals were found but none scored above {MIN_ELIGIBLE_SCORE:.2f} for relevance to "
                           "the seller - a message would have to be invented.")
        if any(not s.eligible and s.sensitive_category != "none" for s in signals):
            missing.append("The only notable news is sensitive and cannot be used as a hook.")
        return RouteDecision(DO_NOT_DRAFT, "R2", "Nothing usable and safe to anchor a message on. Recommend "
                             "not sending until the rep adds context.", None, "none", missing=missing)

    best = eligible[0]
    sensitive_mode, sensitive_sid = "none", None
    sens = [s for s in ranked if s.sensitive_category != "none" and s.about in ("person", "company")]
    # A sensitive item counts as "the strongest news" if (a) it scores within a small margin of the best safe hook,
    # or (b) it outranks every safe item about the company - i.e. it is the headline company news right now.
    best_company = next((s for s in eligible if s.about == "company"), None)
    top = None
    if sens:
        cand = sens[0]
        if cand.total >= best.total - SENSITIVE_DOMINANCE_MARGIN or \
                (cand.about == "company" and (best_company is None or cand.total > best_company.total)):
            top = cand

    if top is not None:
        # Can't reasonably be ignored: very fresh (<= 30 days), or recent and specific to the person/their team.
        unavoidable = top.age_days is not None and (
            top.age_days <= SENSITIVE_FRESH_DAYS
            or (top.age_days <= SENSITIVE_UNAVOIDABLE_DAYS and top.specificity_0_3 >= 2))
        if unavoidable:
            return RouteDecision(
                DRAFT_WITH_WARNING, "R3",
                f"The most important recent news is sensitive ({top.sensitive_category.replace('_', ' ')}, "
                f"{top.age_days} days old) and too significant to ignore. The draft uses a safe hook, adapts its "
                "tone, and needs the rep's explicit acknowledgement before approval.",
                best.sid, "person" if best.about == "person" else "company", "acknowledge", top.sid,
                warnings=["Sensitive context: review tone carefully and acknowledge before approving."])
        sensitive_mode, sensitive_sid = "avoided", top.sid
        warnings.append(f"Skipped a sensitive signal ({top.sensitive_category.replace('_', ' ')}) in favour of a "
                        "safe alternative.")

    person_hooks = [s for s in eligible if s.about == "person" and s.total >= PERSON_LEVEL_MIN]
    if best.about != "person" and not person_hooks:
        return RouteDecision(DRAFT_WITH_WARNING, "R5",
                             "Nothing usable about the person, so the draft uses a company-level signal. "
                             "Lower personalisation - expect a lower reply rate.",
                             best.sid, "company", sensitive_mode, sensitive_sid,
                             warnings=warnings + ["Lower personalisation: company-level hook only."])
    hook = person_hooks[0] if person_hooks and best.about != "person" and \
        person_hooks[0].total >= best.total - 0.05 else best

    if identity_confidence == "low" or continued_despite_conflict:
        return RouteDecision(DRAFT_WITH_WARNING, "R6",
                             "Identity confidence is low - confirm you have the right person before using this.",
                             hook.sid, "person" if hook.about == "person" else "company", sensitive_mode,
                             sensitive_sid, warnings=warnings + ["Low identity confidence."])

    rule = "R4" if sensitive_mode == "avoided" else "R7"
    reason = ("A sensitive signal was deliberately avoided; drafting on the best safe alternative."
              if rule == "R4" else "A strong, specific, safe signal about this person was found.")
    return RouteDecision(DRAFT, rule, reason, hook.sid, "person" if hook.about == "person" else "company",
                         sensitive_mode, sensitive_sid, warnings=warnings)
