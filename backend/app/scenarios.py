"""One-click sample scenarios. They run the REAL pipeline (live search + live LLM) on real public people.

`expected_route` / `expected_edge` record what each scenario was designed and verified to trigger
(see docs/EVAL_RESULTS.md). Public facts drift, so re-run scripts/eval.py before a demo.
"""
SCENARIOS: list[dict] = []
