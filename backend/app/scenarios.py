"""One-click sample scenarios. They run the REAL pipeline (live search + live LLM) on real public business people.

`expected_route` / `edge_case` record what each scenario was designed and verified to trigger
(docs/EVAL_RESULTS.md). Public facts drift, so re-run `python -m scripts.eval` before a demo.
`eval_decision` is how the eval script answers an identity pause.
"""

SCENARIOS: list[dict] = [
    {
        "key": "happy_zerodha",
        "title": "Happy path — Zerodha's founder-CEO",
        "description": "One of India's best-known fintech founders, with plenty of public interviews and company "
                       "coverage to hook on.",
        "expected_route": "draft", "edge_case": None,
        "prospect": {"name": "Nithin Kamath", "company": "Zerodha", "role": "Founder & CEO"},
    },
    {
        "key": "happy_nykaa",
        "title": "Happy path — Nykaa's founder-CEO",
        "description": "Founder-CEO of a large Indian beauty retailer selling online and through stores - a business "
                       "with many suppliers.",
        "expected_route": "draft", "edge_case": None,
        "prospect": {"name": "Falguni Nayar", "company": "Nykaa", "role": "Founder & CEO"},
    },
    {
        "key": "happy_mahindra",
        "title": "Happy path — Mahindra Group's chairman",
        "description": "Chairman of one of India's largest business groups; widely covered, with group-level facts "
                       "to anchor on.",
        "expected_route": "draft", "edge_case": None,
        "prospect": {"name": "Anand Mahindra", "company": "Mahindra Group", "role": "Chairman"},
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
        "key": "ambiguous_deloitte",
        "title": "Edge case — ambiguous identity",
        "description": "A common name at a firm with thousands of partners. Public sources show several different "
                       "Michael Browns linked to Deloitte; the run pauses and asks which one you mean.",
        "expected_route": "do_not_draft", "edge_case": "ambiguous_identity",
        "eval_decision": {"choice": "stop"},
        "prospect": {"name": "Michael Brown", "company": "Deloitte", "role": "Partner"},
        "demo_note": "In the demo, show the candidates and evidence, then either pick one (research follows that "
                     "person's current company) or choose 'None of these - stop'. The eval stops, so its expected "
                     "route is do_not_draft (stopped by rep).",
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
        "key": "thin_nothing",
        "title": "Edge case — nothing usable (do not draft)",
        "description": "A prospect at a company with no public footprint. Rather than invent a reason to write, "
                       "the process recommends not sending and lists what is missing.",
        "expected_route": "do_not_draft", "edge_case": "company_not_found",
        "prospect": {"name": "Tomas Lindqvist", "company": "Brightwater Fasteners GmbH", "role": "Finance Director"},
        "demo_note": "Invented company; verified that search finds neither the person nor the company, so the run "
                     "stops at identity and says exactly what it checked.",
    },
    {
        "key": "fictional_stark",
        "title": "Edge case — fictional character",
        "description": "A famous fictional character at a fictional company. Identified as fiction, nothing drafted; "
                       "facts from fiction are kept apart from the real world (real companies share the name).",
        "expected_route": "do_not_draft", "edge_case": "fictional_entity",
        "prospect": {"name": "Tony Stark", "company": "Stark Industries", "role": "CEO"},
    },
    {
        "key": "sensitive_canva",
        "title": "Edge case — sensitive news",
        "description": "Canva announced a 1,200-person restructuring on 3-4 Nov 2026. It must not be used as a hook; "
                     "the tone is adapted and approval needs an explicit acknowledgement.",
        "expected_route": "draft_with_warning", "edge_case": "sensitive_signal",
        "prospect": {"name": "Kelly Steckelberg", "company": "Canva", "role": "CFO"},
    },
]
