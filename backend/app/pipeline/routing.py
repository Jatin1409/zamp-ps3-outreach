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
    is_fictional: bool = False


@dataclass
class IdentityDecision:
    status: str                # resolved | ambiguous | role_conflict | not_found
    confidence: str            # high | medium | low
    reasons: list[str]
    chosen: str | None = None  # candidate id
    candidates: list[str] = field(default_factory=list)
    conflict_kind: str | None = None  # company_change | role_change


def decide_identity(cands: list[CandidateView], has_input_role: bool, conflicting_sources: bool) -> IdentityDecision:
    verified = [c for c in cands if c.name_matches and c.verified_sources > 0]
    unverified = [c for c in cands if c.name_matches and c.verified_sources == 0]
    reasons: list[str] = []
    if unverified:
        reasons.append(f"{len(unverified)} candidate(s) ignored because their evidence quotes were not found in "
                       "the retrieved text")
    fictional = [c for c in verified if c.is_fictional]
    plausible = [c for c in verified if not c.is_fictional]
    if fictional and not [c for c in plausible if c.at_input_company in ("current", "unclear")]:
        f = fictional[0]
        return IdentityDecision("fictional", "high", reasons + [
            f"Sources describe {f.full_name} as a fictional character, and no real person with this name was "
            "found at the company you entered."], chosen=f.id, candidates=[c.id for c in fictional])
    if fictional:
        reasons.append(f"Ignored a fictional character with the same name ({fictional[0].full_name}).")
    unrelated = [c for c in plausible if c.at_input_company == "never"]
    if len(unrelated) >= 2 and len(unrelated) == len(plausible):
        # Several namesakes, none ever linked to the company entered (seen live: 7 unrelated "Tomas Lindqvist"s).
        # They aren't candidates for this prospect - asking "which one?" would only offer wrong people.
        return IdentityDecision("not_found", "low", reasons + [
            f"Found {len(unrelated)} people with this name, but none linked to the company you entered "
            f"({', '.join(sorted({c.current_company for c in unrelated if c.current_company})[:4])}...). "
            "Continuing with company-level research only."])
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

    if not at_company and len(plausible) >= 2:
        # Several distinct people with this name and none clearly at the company now: asking "which one?" comes
        # before "has this person moved?" (seen live: two different former-Deloitte Michael Browns).
        return IdentityDecision("ambiguous", "low", reasons + [
            f"{len(plausible)} different people with this name were found, none clearly at the company you "
            "entered now: " + "; ".join(f"{c.full_name} ({c.current_role or 'role unknown'}"
                                         f"{', ' + c.current_company if c.current_company else ''})"
                                         for c in plausible)],
            candidates=[c.id for c in plausible])

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
    if has_input_role and c.role_matches_input == "no" and len(plausible) >= 2:
        # The one namesake at the company holds a different role and other namesakes exist: we can't tell which
        # person the rep means (seen live: Michael Brown at Deloitte - a Senior Manager plus two former staff,
        # none a Partner). Ask, rather than assume the rep's role is out of date.
        return IdentityDecision("ambiguous", "low", reasons + [
            f"{len(plausible)} different people with this name were found and none holds the role you entered: "
            + "; ".join(f"{x.full_name} ({x.current_role or 'role unknown'}"
                        f"{', ' + x.current_company if x.current_company else ''})" for x in plausible)],
            candidates=[x.id for x in plausible])
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


# --------------------------------------------------------------------------- company (also decision A)
@dataclass
class CompanyView:
    id: str
    name: str
    label: str
    verified_sources: int
    name_matches: bool
    is_fictional: bool = False


@dataclass
class CompanyDecision:
    status: str                # found | not_found | ambiguous | fictional | unchecked
    reasons: list[str]
    chosen: str | None = None
    candidates: list[str] = field(default_factory=list)


def decide_company(comps: list[CompanyView] | None, person_status: str) -> CompanyDecision:
    if comps is None:
        return CompanyDecision("unchecked", ["Company was not checked separately."])
    verified = [c for c in comps if c.name_matches and c.verified_sources > 0]
    real = [c for c in verified if not c.is_fictional]
    fict = [c for c in verified if c.is_fictional]
    if not verified:
        if person_status in ("resolved", "role_conflict", "ambiguous"):
            return CompanyDecision("found", ["The company is identified through the person's own sources."])
        return CompanyDecision("not_found", ["No public source describes a company with this name."])
    if fict and not real:
        return CompanyDecision("fictional", [f"Sources describe {fict[0].name} as a fictional company."],
                               chosen=fict[0].id, candidates=[c.id for c in fict])
    if len(real) >= 2 and person_status != "resolved":
        return CompanyDecision("ambiguous", [
            f"{len(real)} different organisations match this name: "
            + "; ".join(f"{c.name} ({c.label or 'no description'})" for c in real)], candidates=[c.id for c in real])
    reasons = [f"Identified as {real[0].name}" + (f" ({real[0].label})." if real[0].label else ".")]
    if fict:
        reasons.append("A fictional company with the same name was ignored.")
    return CompanyDecision("found", reasons, chosen=real[0].id, candidates=[c.id for c in real])


def identity_gate(person: IdentityDecision, company: CompanyDecision) -> str:
    """Combine person + company checks into what happens next, most important first."""
    if person.status == "fictional" or company.status == "fictional":
        return "fictional"            # never draft to a fictional entity
    if company.status == "ambiguous":
        return "ambiguous_company"    # ask which organisation before researching
    if person.status in ("ambiguous", "role_conflict"):
        return person.status          # ask the rep
    if person.status == "not_found" and company.status == "not_found":
        return "nothing_found"        # neither person nor company exists in public sources
    return person.status              # resolved | not_found -> research continues


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
    fresh = [s for s in sens if s.age_days is not None and s.age_days <= SENSITIVE_FRESH_DAYS]
    if fresh:
        # A sensitive event in the last 30 days is the context the prospect is living in right now, whatever it
        # scores for relevance to the seller (seen live: a 1-day-old 1,200-person layoff ranked below a June
        # funding story). It always triggers sensitive handling.
        top = fresh[0]
    elif sens:
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
