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
- **Decision:** the top sensitive item about the person/company counts as "the strongest news" if it scores within
  0.10 of the best safe hook, **or** it outranks every safe company-level item (it is the headline company news).
  It is unavoidable if ≤ 30 days old, or ≤ 90 days old and specific to the person/their team. Namesakes and
  unrelated companies never trigger it.
- **Why (two iterations, both from evidence):** v1 fired only when the sensitive item out-scored everything; in an
  integration test a 7-day-old layoff lost to an interview by 0.06 and went through as a normal draft. v2 added the
  0.10 margin; then a **live run** (Sarah Riley → Canva, 2026-11-05) found Canva had announced a 1,200-person
  restructuring the day before. It was correctly excluded as a hook but, at 0.17 below a person-level hook, didn't
  trigger tone handling — though it was the top Canva news item. v3 added "outranks every safe company item".
  Then a live run on Canva's CFO showed a June funding story (0.76) outranking the same 1-day-old layoff (0.53):
  the ranking measures relevance to the seller, not newsworthiness. v4 (current): **any sensitive item about the
  person/company ≤ 30 days old always triggers R3**; older ones use the dominance test.
- **Tradeoff:** a small, fresh negative item (e.g. a minor lawsuit filed last week) also forces acknowledgement.
  Acceptable: it costs one tick, and the rep sees exactly which item triggered it.
- **Tradeoff:** more drafts require acknowledgement; occasionally for news the rep would consider minor.

### D5. Ask the rep rather than guess on identity
Pausing costs a click; writing to the wrong person costs a prospect. The run sits in `awaiting_rep` with candidates
and quoted evidence; it resumes from stage 2 when the rep decides. A rep can also *keep the original details*, which
is allowed but forces a warning (rule R6).

### D6. "Do not draft" is a first-class outcome
If nothing usable and safe exists, we say so and list what is missing, rather than produce a polite template. This is
counted in the dashboard as an outcome, not a failure.

### D7. Genericness = deterministic check + swap test
Names are masked and the model is asked whether the email would still make sense for two fixed decoys (a logistics finance head with no news, a
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
forbid using such details; "personal_event" is treated as sensitive. Two gaps found in live runs and closed:
a UK company-registry officer page carried a date of birth into the identity sources (registries and people-search
sites are now excluded at the search layer), and a Wikipedia "Early life" section carried a birth date and birthplace
into the stored raw search results (birth details are now redacted **before** any search result is stored or shown,
existing rows are redacted on boot, and the recorded fixtures were scrubbed). Earlier git history of one fixture
file still contains that public Wikipedia sentence.

### D11. Prompt-injection defence in depth
1. Pattern detector quarantines sources containing instructions aimed at an AI (stored and shown, never sent to the
   drafting model). 2. All untrusted text is wrapped in `<untrusted_source>` tags with angle brackets escaped so it
   cannot close the tag; system prompts say it is data. 3. Strict output schemas, no tools offered to the model.
4. Grounding: the drafter only sees the chosen hook (whose key fact code found verbatim in the source), two
   supporting signals and the seller profile. (Until D26 a claim-by-claim check was a fifth layer.)
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
- **Decision:** `LLM_PROVIDER=gemini`. Drafting uses `gemini-3.6-flash` (stable); classification and
  scoring use `gemini-3.5-flash-lite` (stable). Thinking level `low` for classification, `medium` for drafting. Model IDs, structured-output support and free-tier pricing were checked against Google's docs on
  2026-11-05.
- **Alternatives:** Anthropic Claude (built first and still implemented: `LLM_PROVIDER=anthropic`); Claude Haiku for
  classification at a few cents per run.
- **Why:** the owner wanted no paid model. Gemini Flash models are free on Google's free tier and support schema-
  constrained JSON output, which the pipeline relies on.
- **Tradeoffs, accepted knowingly:**
  - *Data use:* Google's pricing page says free-tier prompts are "used to improve our products". What gets sent is
    public web text, rep notes and the fictional seller profile. Reps should not paste confidential CRM notes while on
    the free tier; the paid tier is not used for training.
  - *Quotas:* free-tier limits are per project **per model per day** — measured on 2026-11-05 from the error's
    quota id: `GenerateRequestsPerDayPerProjectPerModel-FreeTier limit=20` for `gemini-3.6-flash`. A run makes
    ~3–5 drafting-tier calls, so one Flash model alone allows only a handful of runs a day. **Model fallback chain:**
    each tier has an ordered list (drafting: 3.6-flash → 3.8-flash → 3.5-flash-lite; classification: 3.5-flash-lite
    → 3.1-flash-lite). A daily-quota 429 marks that model exhausted until its reset and moves on immediately; a
    per-minute 429 waits the server's suggested delay; a 503 backs off then falls back. The model that actually
    answered is stored on every call and shown in the stage's technical details. If every model is exhausted the
    stage fails visibly with "daily free-tier quota used up".
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

## Iteration 2 (2026-11-11): performance, stop, profiles, entity checks

### D21. Performance work came from measurement, not from adding a timeout
Profiling real deployed runs (per-stage durations + per-model-call latencies) showed the model calls themselves were
mostly quick (1-15 s); the time went to (a) **fallback waits** - every call rediscovering the same overloaded or
quota-exhausted Gemini model with backoff sleeps (50-100 s on a single draft call), (b) **sequential I/O** - 3 + 5 web
searches and 2-3 scoring batches run one after another, (c) a 1 s global spacing between model calls, (d) a full
regeneration loop when a draft was generic. Changes, in order of impact:
- **Circuit breaker:** a model that 503s or stalls is deprioritised for every call for 20 s (and a model whose daily
  free quota is used up is skipped until it resets). Earlier each call re-tried it from scratch.
- **Hedged requests:** if a model hasn't answered after 4 s (classification) / 9 s (drafting), the same request is
  sent to the next model and the first answer wins; the loser is cancelled. Targets the long tail directly.
- **Fast-only chains:** classification never falls back to the slower, 20-requests/day Flash models (measured:
  20-45 s lost per call when it did); per-attempt timeouts cut from 20/35 s to 10/20 s.
- **Parallel I/O:** identity (4) and gather (5) searches run concurrently; scoring batches run concurrently; (until D26
  the genericness swap test also ran alongside claim verification).
- **Search cache:** identical searches (same query + filters) are reused for 6 h across runs.
- Result: see `docs/EVAL_RESULTS.md` -> Performance. Under a period of heavy Google free-tier overload, 5/5 live runs
  finished inside 90 s (avg 65.6 s) where 2 of 3 had hit the limit before the hedging/fast-chain changes.
- **Tradeoff:** hedging can spend two requests on one answer (only when the first is slow); the cache can serve
  results up to 6 h old (news queries are already limited to the last year, so this doesn't change routing).

### D22. A hard 90-second budget that degrades gracefully
- Each execution gets a deadline (start: 90 s; resume after a rep decision: what's left; regenerate/retry: a fresh
  90 s - they're new executions the rep asked for). Time spent waiting for the rep never counts.
- The deadline is handed down to every provider call (no attempt or backoff sleep may outlive it), and optional work
  is only started if it can finish: a lint retry or claim rewrite needs >= 22 s left, a genericness regeneration >= 35 s.
  Skipped steps become explicit warnings ("not regenerated because it could not finish within the 90-second limit").
- If the limit is hit anyway: if a draft exists, the run goes to review **marked incomplete** ("claims were NOT
  fact-checked" / "genericness check did not run") and approving requires an explicit acknowledgement; otherwise the run
  ends as `timed_out` with everything found so far kept, and can be resumed or re-run.
- Consistency: one finaliser marks the running stage `timed_out`, later stages `skipped`, discards a half-finished new
  draft if a complete earlier one exists, and never leaves anything `running`.
- **Tradeoff:** on a very slow provider day the rep sometimes gets an unverified draft instead of a verified one; it is
  never presented as verified.

### D23. Stop = cancel the job's task
- `POST /runs/{id}/cancel` sets `cancel_requested` and, if the run is executing in this process, calls
  `RunContext.interrupt("user")`, which cancels the job's asyncio task. In-flight HTTP calls (Tavily, Gemini) are
  awaited inside that task, so they are aborted with it; `ctx.check()` guards every new call so nothing starts after a
  stop. Queued or paused runs are finalised directly; a run executing in another worker process sees the flag within
  ~1 s (the worker polls). Idempotent: repeated stops are harmless; stopping a finished run returns `already_finished`.
- Stopping a *regeneration* keeps the previous complete draft. The browser side uses `AbortController` for its own
  requests so a slow response can't overwrite a newer one.

### D24. Multiple seller profiles: selection travels with the request
- Profiles are rows (CRUD + duplicate + reset-to-example for the default). The selected profile id is stored in the
  viewer's browser and sent as `X-Seller-Profile`, so two reviewers can work in different profiles simultaneously.
- Every run stores `seller_profile_id` **and** a snapshot of the profile's content: editing or deleting a profile never
  rewrites past runs. Dashboard and metrics are filtered by profile (default: the current one; "All profiles" option).
  Runs created before profiles existed have no id and are treated as the default profile's.
- Guardrails: unique names, at least one "problem solved" (relevance scoring needs it), can't delete the last profile
  or one with a run in progress, max 25 profiles.
- **Tradeoff:** no per-user ownership - anyone with the access code sees and can edit every profile.

### D25. Entity checks: person, company, fiction - decided in code, evidenced by quotes
- The identity stage now also searches the company on its own and the model reports, per person and per company,
  whether sources describe it as **fictional**, with a quote. As everywhere else, a claim only counts if its quote is
  found in the page text - the model's say-so alone decides nothing (tested).
- Outcomes (code, `routing.identity_gate`): fictional person or company -> stop, nothing drafted, verified facts from
  the fiction shown separately from what exists in the real world (live example: real companies called "Stark
  Industries" exist; no real Tony Stark works at them); several real companies with the name and the person doesn't
  disambiguate -> pause and ask which; neither person nor company found -> stop with "what we checked";
  person not found but company found -> company-level research only (labelled lower personalisation).
- Every run shows a **"What we could verify"** checklist (person, employer, role, company, person-level signal, company
  news) with verified / not verified / conflicting / fictional for each, so partial information is explicit.

### D26. Claim-by-claim verification removed (owner's decision, iteration 3)
- **Decision:** the Verify stage (model splits the draft into claims + quotes, code finds each quote, up to 2 rewrites)
  is gone. Pipeline: identity → gather → filter → rank → route → draft → genericness → review.
- **Why:** the owner judged it unnecessary; it was also the slowest optional step (1-3 model calls per draft) and the
  usual reason a run brushed the 90 s limit.
- **What still grounds the draft:** identity evidence must be a quote code finds on the page (D2, D25); a signal is only
  eligible as the hook if its key fact is found verbatim in the stored source; the drafter sees only the hook, two
  supporting signals and the seller profile; all web text is fenced as untrusted data (D11).
- **What we lose:** no claim → quote → link list under the draft and no automatic rewrite of an unsupported sentence, so
  a model slip in the email text itself is caught only by the rep. The genericness check loses its "no verified claim =
  generic" shortcut (swap test only). The incomplete-on-timeout flag now only concerns the genericness check.

### D27. Edge-case sweep (iteration 3)
Found by trying unusual inputs against the running app; each fix has a test (`tests/test_edge_inputs.py`).
- Whitespace-only name/company was accepted (length checked before trimming) -> trimmed first; blank optional fields
  become empty.
- A double quote in a name (`Nithin "NK" Kamath`) broke the quoted search phrase -> rep text is sanitised in queries.
- Non-Latin company names (e.g. Devanagari) never matched -> company tokens split on whitespace/punctuation, any script.
- Neither person nor company found but the rep added notes -> previously a hard stop; now drafts from the notes only,
  low confidence, with a warning saying exactly that.
- An edit that emptied the email body was accepted -> rejected.
- Stalled web searches held a stage for 53-58 s (live; httpx timeouts are per phase) -> 8 s hard cap per search attempt, 1 quick retry (~18 s max per search); a failed search is
  skipped and logged on the stage; only all searches failing fails the stage.
- Product listings (live: "Buy Peyush Bansal Shark Tank ... Eyeglasses" ranked top) -> shopping pages are dropped in
  the filter stage by code.
- Michael Brown at Deloitte (eval): one namesake at the firm in another role plus former namesakes was read as
  "role changed" -> now asks which person. Tomas Lindqvist (eval): 7 namesakes never linked to the company were
  offered as candidates -> they are not candidates; with none left the person is not found (and, the company being
  unknown too, the run stops at "nothing found"). Code also marks a company not found when no source names it.
- Checked and already correct: unknown ids (404), double decisions/approvals (409), regenerate twice (409), profile
  deleted while selected (falls back to default), editing a profile after a run (snapshot unchanged).
