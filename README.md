# Outreach Desk — prospect → personalised, evidence-grounded draft

Zamp AI Solutions Analyst case study, **PS-3: Personalised outreach — from prospect to draft**.

A rep types a prospect's name and company. The process researches public sources, confirms it has the right person,
picks the most relevant hook (its key fact must be a quote that code finds in the stored source), writes a short
email, tests whether the email is generic, and hands it to the rep for review. **Nothing is ever sent** — approval only marks a
draft ready to copy.

- **Live app:** https://outreach-desk-lmgu.onrender.com (access code shared separately; free hosting sleeps after
  15 idle minutes, so the first load can take ~50 s)
- **Process map:** [docs/PROCESS_MAP.md](docs/PROCESS_MAP.md) · **Decisions:** [docs/DECISIONS.md](docs/DECISIONS.md) ·
  **Eval:** [docs/EVAL_RESULTS.md](docs/EVAL_RESULTS.md) · **Demo script:** [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) ·
  **Interview prep:** [docs/INTERVIEW_PREP.md](docs/INTERVIEW_PREP.md)

## What it does

| # | Stage | What happens | Who decides |
|---|---|---|---|
| 1 | Resolve identity | Searches the person; the model extracts candidates with quoted evidence | **Code** checks quotes and assigns resolved / ambiguous / role conflict / not found + confidence |
| 2 | Gather signals | Person searches (interviews, talks) + company news (funding, hiring, launches) + rep notes | — |
| 3 | Filter | Dedupe, drop stale, quarantine prompt injection; model labels who it's about, sensitive, personal | **Code** drops |
| 4 | Rank | Model scores specificity + relevance (0–3) and quotes a key fact | **Code**: `0.30·specific + 0.30·relevant + 0.25·recent + 0.15·credible` |
| 5 | Route | Rules R1–R7 | **Code**: draft / draft with warning / ask rep / do not draft |
| 6 | Draft | ≤ 120 words, one ask, no flattery or clichés | Model writes, **code** lints |
| 7 | Genericness | Swap test against two decoy prospects | regenerate once, then flag |
| 8 | Review | Approve / edit / reject / regenerate | **The rep** |

**Also:** multiple **seller profiles** (create / edit / duplicate / delete / switch; runs and metrics are kept per
profile), a prominent **Stop** that aborts a run mid-stage, a hard **90-second budget** per run that returns partial
results clearly marked as incomplete, an explicit **"What we could verify"** checklist on every run, detection of
**fictional characters / companies**, **ambiguous company names** and **people or companies that can't be found**, and
an **About** page.

**Edge cases, each a deliberate route with its own screen:** ambiguous identity (pause, pick a candidate),
outdated role (pause, show the conflict, never overwrite the input), thin signal (company-level fallback labelled
"lower personalisation", else "do not send" with what's missing), sensitive signal (never a cheerful hook; adapt
tone and require acknowledgement when it can't be ignored).

## Architecture

```
React (Vite, TS) ──HTTP/SSE──▶ FastAPI ──▶ Postgres (runs, stages, signals, drafts, reviews, events, jobs,
                                  │                     raw search results, LLM call log)
                                  └─ in-process worker: claims jobs (FOR UPDATE SKIP LOCKED) → pipeline
                                        ├─ LLMProvider:   Gemini (default, free tier) | Anthropic Claude | Fixture
                                        └─ SearchProvider: Tavily (live) | Replay (saved results) | Fixture
```

- Every stage persists input, output, timing, status, plain-English explanation, tokens and cost; every change is an
  event streamed to the browser over Server-Sent Events (with polling fallback).
- **Replay mode** re-uses search results saved from an earlier live run of the same prospect (labelled on screen and
  stored on the run). Live is the default; a live search failure fails the stage and offers "Retry in replay mode".
- **Access code + rate limits** (per network per hour, global per day, concurrent runs) protect the public link.
- **Speed:** searches and scoring batches run in parallel, each search has its own ~18 s cap (8 s per attempt, one quick retry) (one slow search can't hold
  the stage), model calls are hedged (a slow model is raced against the next one) behind a circuit breaker, and identical searches are cached for
  6 h. **Budget:** 90 s of active time per run, enforced on every call; optional steps only start if they can finish.
- **Stop:** cancels the run's task (in-flight provider calls are aborted); results so far are kept.

Code map: `backend/app/pipeline/` (`stages.py` stages, `scoring.py` formula, `routing.py` rules, `textmatch.py` quote
check, `injection.py` defences, `prompts.py`, `llm_schemas.py`), `backend/app/providers/` (LLM + search),
`backend/app/jobs.py` (queue), `backend/app/api.py`, `frontend/src/`.

## Run locally

Needs Python 3.12+ and Node 20+. SQLite is used locally (no database setup).

```bash
cp .env.example .env        # then fill GEMINI_API_KEY and TAVILY_API_KEY
```

```bash
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt   # macOS/Linux: .venv/bin/pip
```

```bash
cd frontend && npm install && npm run build && cd ..
```

```bash
cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000 and enter the `ACCESS_CODE` from `.env` (default `local-dev`).
For frontend hot reload, run `npm run dev` in `frontend/` (proxies `/api` to port 8000).

### Tests and evaluation

```bash
cd backend && ../.venv/Scripts/python -m pytest -q
```

Unit tests (scoring, routing, quote matching, injection, lint), integration tests for every route through the HTTP
API with fixtures, Gemini provider tests with a fake client, and replays of fixtures **recorded from real live runs**
(`backend/fixtures/recorded/`).

```bash
cd backend && ../.venv/Scripts/python -m scripts.eval --base https://outreach-desk-lmgu.onrender.com --code <ACCESS_CODE> --record
```

Runs every sample scenario live, checks it took its designed route, and writes `docs/EVAL_RESULTS.md`.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` \| `anthropic` |
| `GEMINI_API_KEY` | — | Google AI Studio key (free tier) |
| `GEMINI_MODEL` / `GEMINI_MODEL_FAST` | `gemini-3.6-flash` / `gemini-3.5-flash-lite` | drafting / classification+scoring |
| `LLM_EFFORT_FAST` / `LLM_EFFORT_DRAFT` | `low` / `medium` | thinking level per tier |
| `LLM_MIN_INTERVAL_S` | `1.0` | spacing between model calls (free-tier quotas) |
| `GEMINI_FALLBACKS` / `GEMINI_FAST_FALLBACKS` | `gemini-3.8-flash,gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` | models tried in order when one's daily free quota is used up or it is overloaded; the model that answered is shown per stage |
| `ANTHROPIC_API_KEY`, `LLM_MODEL`, `LLM_MODEL_FAST` | — / `claude-opus-5-5` | only if `LLM_PROVIDER=anthropic` |
| `TAVILY_API_KEY` | — | web search |
| `SEARCH_COST_PER_CALL_USD` | `0` | cost shown per search (0 on Tavily free tier) |
| `DATABASE_URL` | local SQLite | `postgresql://…` in production |
| `ACCESS_CODE` | `local-dev` | required for every API call |
| `RUNS_PER_HOUR_PER_CLIENT` / `RUNS_PER_DAY_GLOBAL` / `MAX_CONCURRENT_RUNS` | 15 / 150 / 3 | abuse limits |
| `WORKER_ENABLED` | `true` | set `false` and run `python -m app.worker` to split the worker out |
| `RUN_BUDGET_S` | `90` | hard limit on active execution time per run |
| `LLM_ATTEMPT_TIMEOUT_FAST_S` / `..._DRAFT_S` | `10` / `20` | one model attempt before moving on |
| `LLM_HEDGE_AFTER_FAST_S` / `..._DRAFT_S` | `4` / `9` | when to race a slow model against the next one |
| `MODEL_COOLDOWN_S` | `20` | how long an overloaded model is deprioritised |
| `SEARCH_CACHE_TTL_S` | `21600` | reuse identical searches for this long (0 = off) |

## Deployment (Render)

`render.yaml` is a Blueprint for one Docker web service (API + SSE + built frontend + worker) and one Postgres
database, both on the free plan. Deploy: push to GitHub → Render → New → Blueprint → pick the repo → fill the secret
env vars (`GEMINI_API_KEY`, `TAVILY_API_KEY`, `ACCESS_CODE`). The live instance was created through the Render API
with the same settings, from the public repo URL — so there is no GitHub webhook and pushes do **not** auto-deploy.
Redeploy with `POST https://api.render.com/v1/services/<service-id>/deploys` (or "Manual Deploy" in the dashboard),
or connect the GitHub app in Render to get deploy-on-push.

## Limitations (honest list)

- **Free tiers:** the app sleeps after 15 idle minutes (~50 s cold start); the free Postgres expires
  **2026-12-04**; Gemini free-tier quotas are small (calls are spaced and 429s retried, but a burst of runs can fail
  a stage), and Google may use free-tier prompts to improve its products — don't paste confidential notes.
- **No LinkedIn data** unless the rep pastes it (deliberate); person-level signal is thinner as a result.
- **No separate claim-by-claim fact check** (removed at the owner's request, D26): identity evidence and each hook's
  key fact are still quote-checked by code, but the draft's sentences are not individually linked to sources - the rep
  reviews them against the Signals tab.
- **Thresholds are hand-tuned** on a small set of live runs; they'd need tuning on real rep feedback.
- **Single shared access code**, no per-user accounts.
- **The 90 s budget depends on the free Gemini tier's health.** On bad days some runs reach the limit and return an
  incomplete (clearly flagged) draft; see `docs/EVAL_RESULTS.md` for measured timings.
- **Seller profiles are shared** by everyone with the access code (no per-user ownership).
- **Fiction detection relies on public pages saying so** (e.g. "is a fictional character"); an obscure fictional name
  with no such page is treated as "not found", which also stops the run.
- **Public facts drift:** sample scenarios were verified on the dates in `docs/EVAL_RESULTS.md`; re-run the eval
  before a demo.
- Costs shown are estimates from published prices (currently $0 on free tiers), not invoices.
