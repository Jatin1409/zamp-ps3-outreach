# Interview prep

## 1. The 30-second pitch (non-technical buyer)

> Your reps either spend 20 minutes researching each prospect or send something generic that gets deleted. This
> does the research in about a minute: it confirms it has the right person, finds the one real thing worth
> mentioning, writes a short email around it, and then checks every fact in that email against the source it came
> from. If it can't find anything real, it tells the rep that instead of making something up. A person always
> approves before anything goes out — the tool can't send email at all.

What the buyer gets: research time per prospect drops from ~20 minutes to ~1 minute of review; every claim is
traceable to a link; the awkward failures (wrong person, someone who left, writing the day after layoffs) are caught
and put in front of the rep instead of going out.

## 2. Plain-English walkthrough of every component

**The form.** Name and company are required. Role helps tell people apart and spot job changes. A LinkedIn URL is
kept as a reference but never opened (we don't scrape LinkedIn). Notes or a pasted post are treated like any other
source — and checked for hidden instructions.

**Stage 1 — Who is this?** Three web searches. The AI reads the results and lists the people they could be, with
a quote supporting each. Code then checks each quote is really on that page (no quote, no evidence), and decides:
one clear match → continue; two different people → pause and ask; the person seems to have moved → pause and ask;
nothing found → continue at company level with low confidence.

**Stage 2 — Collect.** Five searches: interviews/talks/quotes by the person, recent mentions, and company news from
the last 12 months (funding, hiring, launches, partnerships, finance). LinkedIn and people-data brokers are excluded
at the search level. Every result is saved with its URL, date, full text and type — that saved copy is what later
proves or disproves each claim, and what replay mode reuses.

**Stage 3 — Clean up.** Code removes duplicates and anything older than 18 months, and quarantines any page that
contains instructions aimed at an AI. The AI labels each remaining item: about this person, about this company, or
about someone/something else? Is it sensitive (layoffs, lawsuits, breaches, departures, personal events)? Does it
contain personal details? Code drops the off-topic and personal ones — and shows why for each.

**Stage 4 — Rank.** For each item, the AI scores two things it is good at judging: how specific it is to this person
(0–3) and how plausibly it connects to a problem the seller solves (0–3), and copies the single key sentence.
Code adds two things it can compute: how recent it is, and how credible the source is. The final score is a fixed
weighted sum, so anyone can see why item A beat item B. If the "key sentence" isn't actually on the page, the AI's
scores for that item are zeroed.

**Stage 5 — Decide.** Seven written rules, checked in order. Nothing usable → don't draft, and say what's missing.
The headline news is sensitive and recent → draft on a safe hook, soften tone, require the rep to tick an
acknowledgement. Only company-level news → draft, labelled "lower personalisation". Unsure who this is → draft
with a warning. Otherwise → normal draft.

**Stage 6 — Write.** The AI writes ≤ 120 words: the hook as a plain fact, one plausible problem phrased as a
hypothesis, at most one proof point copied from the seller profile, one low-friction ask. Code checks length,
banned phrases ("I came across", "congrats", "quick call"…), exclamation marks and number of questions.

**Stage 7 — Fact-check.** The AI splits the email into individual claims and, for each, names its source and copies
the supporting sentence. Code checks the sentence is really in the saved source. Anything unsupported → the AI
rewrites without it → check again (twice at most) → anything left is shown in red for the rep.

**Stage 8 — Is it generic?** If no verified fact about this prospect survives, it's generic by definition.
Otherwise names are blanked out and the AI is asked whether the email would still work for two unrelated decoy
prospects. If yes → regenerate once → still yes → flagged.

**Stage 9 — The rep.** Approve (ready to copy), edit (original kept, edit size recorded), reject, or regenerate with an
optional style instruction. Sensitive drafts can't be approved without the acknowledgement tick.

**Around it.** A job queue in Postgres runs each request in the background so the page can be closed; each stage
streams to the screen as it happens; a dashboard shows every run with route, outcome, time, cost, approval and edit
rates. An access code and rate limits stop strangers running up costs.

## 3. Likely questions and answers

**Why not just ask one big AI prompt to do everything?**
Because I couldn't explain or test what it did. The AI does the reading; plain code makes the decisions. Every route
the system takes can be traced to a numbered rule, and the rules are unit-tested. It also makes runs reproducible:
I can replay a recorded live run and get the same route.

**How do you stop it making things up?**
Three ways. The AI only ever sees saved source text, not its own memory. Every factual claim must come with a quote,
and code — not the AI — checks the quote is on the page. And claims that fail are removed or shown in red. The seller's
own proof points can only be quoted word-for-word from the seller profile.

**What if the AI hallucinates the quote too?**
That's exactly what the code check catches: the quote has to be found in the stored page text (near-exact match).
A made-up quote isn't on the page, so the claim fails.

**What's the weakest part?**
Person-level signal. Without LinkedIn, many mid-level prospects have nothing public, so the thin-signal route is common.
Second: the thresholds were tuned on a handful of live runs. Third: the free Gemini tier — quotas and somewhat weaker
judgement than a frontier model (the code-level checks don't depend on the model, which limits the damage).

**Why ask the rep instead of picking the most likely person?**
Asymmetric cost: a pause costs one click; emailing the wrong person costs the prospect and some reputation.

**How did you choose the edge cases?**
They're the four ways real outreach goes wrong: wrong person, stale data, nothing real to say, saying the wrong thing
at the wrong time. Each changes what the rep has to do, which is why each has its own screen.

**Give an example where testing changed the design.**
The sensitive-news rule went through three versions. The second was beaten by a live run: Canva had announced a
1,200-person restructuring the day before; the system correctly refused to use it as a hook, but didn't soften the
tone because a person-level item scored higher. It was still the headline company news. Now "headline company news"
counts — and a later live run on Canva's CFO showed a June funding story outranking the same layoff, so now any
sensitive news under 30 days old always triggers the careful route. Others: a newsletter footer ("You are now
subscribed") tripped the injection detector — narrowed the pattern; a UK company-registry page and a Wikipedia
"early life" section carried birth details — registries are excluded and birth details are redacted before anything
is stored; Gemini's free tier turned out to allow 20 requests per day per Flash model — added a per-tier model
fallback chain; two different former-Deloitte "Michael Browns" were being treated as one person who'd moved —
namesakes are now checked before "has this person moved?".

**How is it protected against prompt injection?**
Detector quarantines pages with instructions aimed at an AI; all web text is fenced and escaped so it can't break out
of its "this is data" wrapper; outputs are strict schemas with no tools; and the fact-check means an injected "say you
met at a conference" can't survive into a verified draft. There's a test for each layer.

**What happens if search or the AI is down during the demo?**
The stage turns red with the error — it never pretends to succeed. Search failures offer "Retry in replay mode",
which re-uses the sources saved from an earlier live run of that prospect; the run is labelled "Replay" everywhere.

**How much does a run cost?**
On the free tiers I'm using, $0 — the app shows tokens and duration per run, and cost at published prices
(currently zero). With a paid model the same app shows the estimated dollars. Most runs make ~6–8 model calls and
~8 searches.

**How would this scale to 200 prospects a day?**
Batch input (CSV), a separate worker process (already supported: `python -m app.worker`), a paid model tier,
LISTEN/NOTIFY instead of polling for events, and per-user auth.

**Why Server-Sent Events and not WebSockets?**
The run view only needs server → browser updates; SSE is simpler, reconnects automatically, and passes through
proxies. Events are stored in the database, so a reconnecting browser catches up from the last event it saw.

**Why is the score weighted 30/30/25/15?**
Specificity and relevance are what make a message worth reading, so they carry most weight; recency matters but a
3-month-old interview beats yesterday's generic press release; credibility is a tie-breaker. They're hand-set — with
real data I'd fit them to reply rates.

**Does it actually work? What did you measure?**
On the deployed app, with live search and the live model: 8 of 8 scenarios took their designed route, 8 of 8 flagged
the intended edge case, 0 unsupported claims remained across 19 factual claims in 6 drafts, 0 drafts were left
generic (2 were regenerated once by the genericness loop), 0 runs failed. Median run ~99 s on free tiers. Each of
those runs was recorded and replays offline to the identical route in the test suite. Details: `docs/EVAL_RESULTS.md`.

**How did you get runs under 90 seconds?** (iteration 2)
I measured first. Per-call timings showed the model itself was rarely slow - the time went into fallbacks: every call
rediscovering an overloaded free-tier model with backoff sleeps, and 8 web searches run one after another. So: a
circuit breaker (an overloaded model is skipped by every call for 20 s), *hedged* requests (if a model hasn't answered
in 4 s, ask the next one too and take the first answer), classification never falls back to the slow 20-a-day Flash
models, searches and scoring batches run in parallel, the swap test runs alongside verification, and identical
searches are cached for 6 hours. Only then the hard limit: every call gets the remaining time as its deadline, optional
steps only start if they can finish, and if the limit is still hit you get what's done, clearly marked incomplete.

**What happens when you press Stop?**
The run's background task is cancelled, which aborts the HTTP calls it's waiting on; a guard stops anything new from
starting; the stage that was running is marked "stopped", the rest "not run", and everything found so far is kept.
You can resume from that stage or start fresh. Stopping twice, or stopping a finished run, is harmless.

**What if someone types a fictional character?**
The identity step asks the model whether sources describe the person or company as fictional, with a quote - and code
checks the quote is really on the page. If so, nothing is drafted and the screen shows the fiction (with sources)
separately from what exists in the real world. Example from a live run: "Tony Stark, Stark Industries" - fictional,
but two real companies called Stark Industries exist; the page says exactly that.

**How do multiple seller profiles stay separate?**
Each run records which profile it was made under and a copy of that profile's content, so editing or deleting a
profile never changes history. The dashboard and metrics filter by profile. The selected profile lives in your
browser and is sent with each request, so two reviewers can use different profiles at the same time.

## 4. Known weaknesses (say them before they're found)

- Sensitive tagging depends on the classifier: in one live run a "city ponders lawsuit against fire-truck makers"
  item wasn't tagged (it wasn't the hook, so no harm — but the safety net is only as good as that judgement).
- Runs take 1–3 minutes on free tiers (call spacing, fallbacks, regenerations).
- Thin person-level signal without LinkedIn (by design).
- Quote matching can reject a correct paraphrase (errs toward removing claims).
- Thresholds tuned on a small set; no learning from rep edits yet.
- Free-tier constraints: cold start ~50 s, Gemini quotas, Google may use free-tier prompts, free Postgres expires 2026-12-04.
- One shared access code; no per-user accounts or audit of who approved.
- Edited text isn't re-fact-checked.
- English only; no CRM integration; no sending (deliberate).

## 5. What I'd build next

1. **Learn from the rep:** use approve/edit/reject and edit distance to tune weights and prompts; show "drafts like
   this were usually edited here".
2. **Batch mode:** CSV of prospects → queue → morning review list sorted by confidence.
3. **CRM in, CRM out:** pull prospect + history from the CRM (with consent) and write the approved draft back as a task
   — still no auto-send.
4. **Re-verify edits** and highlight any new unsupported claim the rep typed.
5. **Per-user auth + audit trail** (who approved what, when).
6. **Paid model tier for verification** if eval shows the free model missing claims.
