# Job-Agent Goals — Working Draft
# Hans Richardson
# ============================================================
# These are the plain-English goals you type into "Run Agent."
# They are written to FORCE adaptive, self-correcting behavior —
# the stuff a fixed pipeline (job_search.py) structurally cannot do.
#
# Add / delete / edit freely. These goals define what tools we build.
# ============================================================


# ------------------------------------------------------------
# TIER 1 — DAILY DRIVERS (the ones you'll actually use most)
# ------------------------------------------------------------

GOAL_01 = """Find me 5 strong LoadRunner or performance engineering matches today,
remote only, rate or salary above my floor. If you can't find 5 that clear the bar,
broaden the search and tell me exactly what you had to relax to get there."""

GOAL_02 = """Search LoadRunner and performance roles first. If results are thin,
pivot to AI hybrid roles (performance + AI, AI Systems, Agent Engineer) and tell me
why you switched and what you found in each track."""

GOAL_03 = """Find the single best match for me today across both my tracks and
justify why it beats every other option you looked at. Flag any gap I'd need to
address before applying."""


# ------------------------------------------------------------
# TIER 2 — TARGETED HUNTS (specific filters / constraints)
# ------------------------------------------------------------

GOAL_04 = """Find remote LoadRunner contract roles posted in the last 7 days.
Skip anything requiring active clearance I don't currently hold or relocation.
Rank by how explicitly they name LoadRunner vs generic 'performance testing'."""

GOAL_05 = """Find AI Systems / Agent / LLM Platform Engineer roles that would value
a performance and reliability background. Avoid generic 'ML Engineer' titles that
want a PhD or years of model training. Tell me honestly which ones are a stretch."""

GOAL_06 = """Find remote roles above my salary floor, posted this week, in either
track. For each one, tell me which of my gaps (Azure, LangGraph, containerization,
years of AI experience) would matter most for that specific posting."""


# ------------------------------------------------------------
# TIER 3 — NEW REACH (sources NOT in job_search.py)
# ------------------------------------------------------------

GOAL_07 = """Use web search to find performance engineering or AI hybrid roles that
my usual aggregators (Adzuna, Serper, USAJobs) are missing. Prioritize company
career pages and fresh postings over reposted aggregator listings."""

GOAL_08 = """Pull openings directly from the careers boards of companies I'd want
to work for. Look for performance, reliability, SRE, or AI systems roles. Tell me
which companies are actively hiring in my space right now."""

GOAL_09 = """Check the latest Hacker News 'Who is Hiring' thread for remote roles
matching either of my tracks. Focus on AI / startup roles that fit my entry-to-mid
AI level and call out anything that explicitly mentions reliability or eval work."""


# ------------------------------------------------------------
# TIER 4 — SELF-CORRECTING / META (the portfolio showpieces)
# ------------------------------------------------------------

GOAL_10 = """Run a broad sweep, then critique your own results. If the matches are
weak or too few, diagnose why (wrong keywords? wrong sources? bar too high?),
adjust, and run again before reporting. Show me your reasoning at each decision."""

GOAL_11 = """Find me roles, but before you finalize, double-check the top 2 by
searching each company and the full job posting. Confirm they're really remote and
the salary is real before you rank them #1 and #2."""


# ============================================================
# NOTES — what each goal forces the agent to DECIDE
# (this is the column that justifies the agent existing)
# ============================================================
#
# GOAL_01  -> evaluate own output count, broaden + report relaxation
# GOAL_02  -> dynamic source/track pivot based on live results
# GOAL_03  -> comparative reasoning, pick + justify a single winner
# GOAL_04  -> apply hard filters, rank by keyword specificity
# GOAL_05  -> honest stretch assessment, title filtering
# GOAL_06  -> per-posting gap analysis (your differentiator)
# GOAL_07  -> open-ended web search (pipeline CAN'T do this)
# GOAL_08  -> ATS board fetch (Greenhouse/Lever) — new reach
# GOAL_09  -> HN Who's Hiring parse — new reach
# GOAL_10  -> self-critique + re-run loop (THE showpiece)
# GOAL_11  -> verification pass / deeper dig on top candidates
#
# ============================================================
# TOOLS THESE GOALS IMPLY WE NEED TO BUILD:
# ============================================================
#   REUSED from job_search.py:
#     - search_adzuna(query)
#     - search_serper(query)
#     - search_usajobs(query)
#     - score_results(jobs)
#     - analyze_fit(job)
#
#   NEW to the agent:
#     - web_search(query)            # open-ended reach + verification
#     - fetch_company_board(company) # Greenhouse / Lever JSON
#     - fetch_hn_hiring()            # latest Who is Hiring thread
#     - stop()                       # agent decides it's done
#
#   AGENT-ONLY behavior (no tool, just loop logic):
#     - evaluate_results_quality()   # the self-critique in GOAL_10
# ============================================================
