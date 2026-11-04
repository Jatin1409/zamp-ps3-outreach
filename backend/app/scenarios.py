"""One-click sample scenarios. They run the REAL pipeline (live search + live LLM) on real public business people.

`expected_route` / `edge_case` record what each scenario was designed and verified to trigger
(docs/EVAL_RESULTS.md). Public facts drift, so re-run `python -m scripts.eval` before a demo.
`eval_decision` is how the eval script answers an identity pause.
"""

SCENARIOS: list[dict] = [
    {
        "key": "happy_rosenbauer",
        "title": "Happy path — new CFO digitising finance",
        "description": "New CFO whose appointment announcement says he will establish digital processes across "
                       "finance. A clean person-level hook.",
        "expected_route": "draft", "edge_case": None,
        "prospect": {"name": "Jörg Schuschnig", "company": "Rosenbauer International", "role": "CFO"},
    },
    {
        "key": "happy_legitscript",
        "title": "Happy path — combined COO/CFO, podcast guest",
        "description": "Finance leader with a recent podcast and press coverage of her dual COO/CFO role.",
        "expected_route": "draft", "edge_case": None,
        "prospect": {"name": "Jaylene Kunze", "company": "LegitScript", "role": "CFO"},
    },
    {
        "key": "happy_oaknorth",
        "title": "Happy path — new CFO at an expanding bank",
        "description": "CFO hired this year while the bank expands into the US.",
        "expected_route": "draft", "edge_case": None,
        "prospect": {"name": "Gergely Zaborszky", "company": "OakNorth", "role": "CFO"},
    },
    {
        "key": "outdated_dbt",
        "title": "Edge case — outdated role",
        "description": "Entered as CFO of dbt Labs; recent sources say she left in May 2026 for Canva. The run "
                       "pauses and shows the conflict.",
        "expected_route": "draft_with_warning", "edge_case": "outdated_role",
        "allowed_extra_edges": ["sensitive_signal"],
        "eval_decision": {"choice": "use_new"},
        "prospect": {"name": "Sarah Riley", "company": "dbt Labs", "role": "CFO"},
        "demo_note": "Choose 'Continue with the new details'. Her new company (Canva) announced a restructuring on "
                     "2026-11-03/04, so the draft also carries the sensitive-signal acknowledgement.",
    },
    {
        "key": "thin_company_fallback",
        "title": "Edge case — thin signal (company fallback)",
        "description": "A name from a list with no public footprint, at a company with real news. Falls back to a "
                       "company-level hook, labelled lower personalisation.",
        "expected_route": "draft_with_warning", "edge_case": "thin_signal",
        "prospect": {"name": "Marta Kowalczyk", "company": "Rosenbauer International",
                     "role": "Head of Accounts Payable"},
        "demo_note": "Illustrative name chosen to have no public footprint (searched 2026-11-05: none found).",
    },
    {
        "key": "sensitive_canva",
        "title": "Edge case — sensitive news",
        "description": "Canva announced a 1,200-person restructuring this week. It must not be used as a hook; "
                     "the tone is adapted and approval needs an explicit acknowledgement.",
        "expected_route": "draft_with_warning", "edge_case": "sensitive_signal",
        "prospect": {"name": "Kelly Steckelberg", "company": "Canva", "role": "CFO"},
    },
]
