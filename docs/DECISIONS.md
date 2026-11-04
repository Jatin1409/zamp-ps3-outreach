# Decisions, alternatives and tradeoffs

Each entry: what was decided, what else was considered, why, and what it costs.

## Product decisions

### D1. The LLM judges; code decides
- **Decision:** the model is used only for judgements that need reading comprehension (who is this source about, is it
  sensitive, how specific/relevant is it, what claims does this email make). Identity status, scores, ranking,
  routing, eligibility and pass/fail of verification are computed in plain Python.
- **Alternatives:** one "agent" prompt that researches and writes; LLM-ranked signals.
- **Why:** a buyer (and an interviewer) needs to know *why* a draft took a route. Rules R1–R7 can be read, unit-tested
  and changed without re-prompting. It also makes runs reproducible from recorded model outputs.
- **Tradeoff:** less flexible than an agent; thresholds (0.30, 0.40, 0.10 margin, 90 days) are hand-tuned on a small set.

### D2. Every factual claim must be a verbatim quote that code can find
- **Decision:** the verifier must return a supporting quote per claim; code checks it really appears in the stored
  source (fuzzy ≥ 90, exact for short strings). The same rule applies to identity evidence and to each signal's key fact.
- **Alternatives:** trust the model's "supported: true"; embedding similarity.
- **Why:** the model can hallucinate support as easily as it hallucinates facts. A string match is cheap, auditable, and
  lets the UI link each claim to the exact text.
- **Tradeoff:** a correct paraphrase with no quotable sentence is marked unsupported (false negatives). We accept that:
  it errs towards removing claims, never towards inventing them.

### D3. Four edge cases, each a deliberate route with its own screen
Ambiguous identity (pause + candidate picker), outdated role (pause + conflict view; input never overwritten),
thin signal (company-level fallback labelled "lower personalisation", else do-not-draft with a "what's missing"
list), sensitive signal (never a cheerful hook; avoid it if possible, otherwise adapt tone and require an explicit
acknowledgement before approval). Prompt injection and provider outages are handled too, but as safety behaviour,
not as "edge-case routes".
- **Why these:** they are the four ways personalised outreach actually goes wrong in practice — wrong person, stale
  data, nothing real to say, saying the wrong thing at the wrong time. Each one changes what the rep sees and must do.

### D4. Sensitive = "strongest news", not "highest hook score"
- **Decision:** the sensitive rule fires when the strongest sensitive item scores within 0.10 of the best safe hook (or
  above it). Unavoidable if ≤ 90 days old, about the person/company, specificity ≥ 2.
- **Why:** first version only fired when the sensitive item out-scored everything. In an integration test a layoff
  7 days old lost to an interview by 0.06 and the draft was routed as a normal, no-warning draft. Being a close second is enough to make
  ignoring it tone-deaf.
- **Tradeoff:** more drafts require acknowledgement; occasionally for news the rep would consider minor.

### D5. Ask the rep rather than guess on identity
Pausing costs a click; writing to the wrong person costs a prospect. The run sits in `awaiting_rep` with candidates
and quoted evidence; it resumes from stage 2 when the rep decides. A rep can also *keep the original details*, which
is allowed but forces a warning (rule R6).

### D6. "Do not draft" is a first-class outcome
If nothing usable and safe exists, we say so and list what is missing, rather than produce a polite template. This is
counted in the dashboard as an outcome, not a failure.

### D7. Genericness = deterministic check + swap test
A draft with no verified prospect-specific claim is generic by definition (code). Otherwise names are masked and the
model is asked whether the email would still make sense for two fixed decoys (a logistics finance head with no news, a
hotel ops VP). One regeneration, then a flag. Fixed decoys make the check comparable across runs.

### D8. Approval never sends
Approve = "ready to copy" + clipboard button. Edit, reject, regenerate are all stored with original and final text and
edit distance. No email integration was built, by design (brief requirement and a sensible default for a demo).

## Data and integrity

### D9. No LinkedIn scraping; LinkedIn and people-data brokers excluded from search
LinkedIn URLs are stored as a rep-supplied reference and never fetched. `linkedin.com` and contact-data brokers
(ZoomInfo, RocketReach, Apollo, Lusha, SignalHire, ContactOut, people-finders) are excluded at the search layer.
Rep-pasted posts are the sanctioned way to use LinkedIn content.
- **Tradeoff:** we miss the richest source of person-level signal; thin-signal routes are more common as a result.

### D10. Professional information only
The filter stage drops items whose main content is personal (family, health, home, religion, politics); the prompts
forbid using such details; "personal_event" is treated as sensitive.

### D11. Prompt-injection defence in depth
1. Pattern detector quarantines sources containing instructions aimed at an AI (stored and shown, never sent to the
   drafting model). 2. All untrusted text is wrapped in `<untrusted_source>` tags with angle brackets escaped so it
   cannot close the tag; system prompts say it is data. 3. Strict output schemas, no tools offered to the model.
4. Claim verification: even an injection that slips through can't put an unsupported fact into a verified draft.
Rep notes go through the same detector.
- **Tradeoff:** regex detection is bypassable by a determined attacker; layers 2–4 are the real protection.

### D12. Replay mode reuses stored search results, never stored drafts
Every raw search response is saved. Replay mode serves the latest saved responses for the same prospect (normalised
name + company) and labels the run "Replay" everywhere (banner, badge, stored `mode`, link to the source run). LLM
stages still run live, so the demo still shows real judgement. Live is the default; a live search failure *fails the
stage* and offers "Retry in replay mode" — it never silently falls back.

## Architecture

### D13. FastAPI + Postgres-backed job queue + SSE, one service
- **Decision:** a `jobs` table claimed with `FOR UPDATE SKIP LOCKED`, worker loop started inside the web process
  (bounded concurrency), events written to an `events` table and streamed by an SSE endpoint that tails it.
  A standalone `python -m app.worker` exists for splitting later.
- **Alternatives:** Celery/RQ + Redis; FastAPI BackgroundTasks; WebSockets.
- **Why:** durable (a run survives the browser closing; a restart marks interrupted runs failed instead of leaving them
  "running"), no extra infrastructure to pay for or fail during a demo, and SSE through the DB works even with a
  separate worker. SSE is one-way, which is all a run view needs, and passes through proxies.
- **Tradeoff:** polling the events table every 0.4 s per open stream; fine at demo scale, would move to
  LISTEN/NOTIFY or Redis at volume.

### D14. Provider interfaces
`LLMProvider.complete(purpose, system, user, schema, tier)` and `SearchProvider.search(query)`. Implementations:
Anthropic, Fixture (recorded); Tavily, Replay, Fixture. Swapping a model is an env var (`LLM_MODEL`, `LLM_MODEL_FAST`);
swapping a vendor is one class.

### D15. Model choice: Google Gemini free tier (owner's decision), Claude kept as a drop-in alternative
- **Decision:** `LLM_PROVIDER=gemini`. Drafting and verification use `gemini-3.6-flash` (stable); classification and
  scoring use `gemini-3.5-flash-lite` (stable). Thinking level `low` for classification, `medium` for drafting and
  verification. Model IDs, structured-output support and free-tier pricing were checked against Google's docs on
  2026-11-05.
- **Alternatives:** Anthropic Claude (built first and still implemented: `LLM_PROVIDER=anthropic`); Claude Haiku for
  classification at a few cents per run.
- **Why:** the owner wanted no paid model. Gemini Flash models are free on Google's free tier and support schema-
  constrained JSON output, which the pipeline relies on.
- **Tradeoffs, accepted knowingly:**
  - *Data use:* Google's pricing page says free-tier prompts are "used to improve our products". What gets sent is
    public web text, rep notes and the fictional seller profile. Reps should not paste confidential CRM notes while on
    the free tier; the paid tier is not used for training.
  - *Quotas:* free-tier limits are per project and shown only in AI Studio. Calls are spaced (`LLM_MIN_INTERVAL_S`)
    and 429s are retried with the server's suggested delay (up to 4 times); exhausting that fails the stage visibly.
  - *Quality:* expected to be somewhat weaker on subtle judgements than a frontier model; this is measured in
    `docs/EVAL_RESULTS.md`, not assumed. Deterministic safeguards (quote matching, code routing) do not depend on
    which model is used.
  - *Cost display:* LLM cost shows $0.00 because the free tier charges nothing; tokens are still tracked per run.
- **Implementation:** official `google-genai` SDK, `models.generate_content` with `response_json_schema` (Pydantic
  schema with `$ref`s inlined), then strict Pydantic validation of the returned text, one repair retry, then failure.

### D16. Structured outputs via the SDK's `messages.parse` + Pydantic
Every LLM response is parsed into a strict Pydantic model (`extra="forbid"`), with one automatic repair retry quoting
the validation error, then a visible stage failure.

### D17. Search: Tavily
Returns cleaned page text and publication dates in one call (no separate scraping step), supports news-topic and
time-range filters and domain exclusion. Basic depth (1 credit) is enough. Alternatives considered: Exa, Brave,
Serper + a fetcher; any can be dropped in behind `SearchProvider`.

### D18. SQLite locally, Postgres in production
Same SQLAlchemy models; SQLite needs no setup for local dev and tests. Postgres-only code is limited to the job claim
query. Tradeoff: local runs don't exercise the `SKIP LOCKED` path — the deployed app does.

### D19. Abuse protection
Access code (header; query param only for EventSource, which can't send headers), per-network hourly limit, global daily
budget, cap on concurrent runs. Checked before any paid call. Tradeoff: an access code shared with reviewers is a
shared secret, not per-user auth.

### D20. Hosting: Render (Docker web service + managed Postgres)
Long-lived SSE connections and minute-long background jobs rule out serverless. The owner chose Render's **free**
plan. Consequences, accepted knowingly: the service sleeps after 15 minutes without traffic (first request then takes
~50 s — open the link a minute before a demo), and the free Postgres database expires after 30 days. Upgrading is
one line per service in `render.yaml` (`plan: starter`, `plan: basic-256mb`).

## Assumptions
- Prospects are individuals at B2B companies with at least some public footprint; consumer/individual outreach is out of scope.
- The seller is fictional (Ledgerline); its proof points are fictional and labelled as such. Drafts may only quote
  proof points verbatim from the profile.
- "Recent" means ≤ 12 months for news searches; items > 18 months old are dropped as stale; undated items are kept
  with a low recency score.
- One rep, one seller profile, no multi-tenant auth (out of scope for a case study; the profile is snapshotted per run).
- English-language sources.
- Costs shown are estimates from published per-token prices and Tavily's per-credit price, not invoices.
- Sample scenarios use real, public business figures; public facts drift, so scenario expectations must be re-verified
  (`scripts/eval.py`) before a demo.
