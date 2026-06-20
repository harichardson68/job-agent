# NEXT SESSION — job-agent

## CURRENT STATE (as of 2026-06-12 — end of session)

The agent is COMPLETE and RUNNING end-to-end, live in Agent Hub via the
"Run Agent" button. Email digest + cover letters confirmed working — agent
sends automatically at end of every run; Email Results button re-sends
from last_run.json with fresh cover letters.

Pieces, all built + tested:
  core/state.py        spine (state, history, dedup-by-url, safety cap)
  core/logger.py       observability (console + stream_callback to GUI)
  core/planner.py      Claude picks next tool, JSON, fail-safe to stop
                       PLANNER_MODEL env var override for dev/test
  tools/search.py      search_adzuna + search_serper (location-aware,
                       Adzuna location-word strip, Serper junk-parser,
                       max_days_old=7 / tbs=qdr:w stale filters,
                       ONSITE_SIGNALS fix for explicit on-site jobs)
  tools/score.py       dedup -> tiered scoring -> rank
                       hard drops: junior, overseniority, AI-track seniority,
                       heavy travel (20%+ travel in description)
                       ai_seniority_drop() track-aware filter
                       _AI_YEARS_RE + _AI_PROVEN_TRACK_RE desc penalties
                       _parse_salary() + salary floor enforcement
  tools/analyze_fit.py TOP_N=7, fit tiers, gap-flagging
  tools/cover_letter.py TWO-TRACK cover letters (Performance / AI Hybrid)
                        one batched Claude call, templates sent once
                        garbage company fallback -> "Dear Hiring Team"
                        time.sleep(2) before API call (rate limit buffer)
  tools/email_results.py HTML digest via Gmail SMTP — agent calls automatically
                         cover letters generated before send (both paths)
  tools/registry.py    TOOLS dict -> generates menu + function lookup
  agent.py             the loop (plan->act->observe->repeat->report)
                       skip_fit + planner_model params for test mode
                       saves last_run.json after every run
  config/goals.py, config/salary_config.py, config/watch_vectors.py

agent_hub.py (in job-search-hans):
  - TEST MODE checkbox in header (Haiku + 4 iter + no fit)
  - Email Results button (reads last_run.json, generates cover letters,
    re-sends on demand — requires Agent Hub restart to pick up changes)

PROVEN working: GUI trigger, multi-source search, location filter,
across-run dedup, scoring, ranking, fit analysis, cover letters,
email digest auto-send, Email Results button.


## ============================================================
## NEXT UP — priority order
## ============================================================

### 1. REMOTE EXECUTION  (GitHub Actions — runs without Hans's PC)
WHY: With email working, the agent can run in the cloud and reach Hans
anywhere. run_agent() is already GUI-independent (pure Python), so the
same core works from GUI / CLI / GitHub Action / schedule.
PATH:
  - GitHub Actions (trigger from phone via GitHub mobile) OR
    Hugging Face Space (Gradio front end, always-on URL).
  - Headless run + email_results() = agent works while PC is off.
  - Needs API keys as GitHub Secrets / HF Secrets (NOT committed).
PORTFOLIO STORY: "autonomous cloud agent that makes its own search
decisions and emails ranked results with reasoning."

### 2. PLANNER TUNING  (from live run — real inefficiencies)
  a. EMPTY-PARAM REPEAT: planner called search_serper({}) with no query
     THREE times in a row (iters 4,6,7) before supplying one. The guard
     caught it (no crash) but the planner kept repeating the bad call.
     FIX: harden planner prompt — "tools whose menu shows required params
     MUST receive them; never call with empty params."
  b. REPEAT-ACTION GUARD: if the planner tries the SAME failed action
     twice, the loop should nudge it ("you already tried that, do
     something different"). Also fixes the Adzuna repeated-zero-query
     pattern.
  c. ADZUNA SHORT QUERY: planner still sends 3-word Adzuna queries that
     AND-match to 0 (registry steer not always obeyed). Adzuna is a weak
     source for this niche regardless; Serper carries the load. Low
     urgency — monitor, maybe drop Adzuna priority.

### 3. DISPLAY POLISH  (minor)
  - Some report entries show "@ " with a blank company (Serper parser
    fallback to empty — correct behavior, but looks unfinished). Either
    hide the "@" when company is empty, or show "(company not listed)".

### 4. HUGGING FACE DEMO  (portfolio piece — after GitHub Actions)
  - Sanitized public Space on Hugging Face showing the agent running live
  - DEMO_MODE flag: search tools return canned demo_jobs.json instead of live APIs
  - email_results renders HTML to browser instead of sending
  - Planner uses Haiku (cheap) or mocked responses
  - Cover letters show pre-generated samples
  - No personal data, no real API keys exposed
  - Portfolio pitch: live demo a hiring manager can click, not just a GitHub link
  ORDER: GitHub Actions remote run first, then Hugging Face demo Space.

### 5. FEEDBACK LOOP  (human-approved rule updates)
  - Hans flags a bad result ("shouldn't have come through — reason X")
  - Agent surfaces the flag for review; Hans approves
  - Rule is written to a config file (e.g. config/user_filters.py)
  - Score.py reads it next run — no code change needed
  CONSTRAINT: Hans approves every rule before it takes effect.
  Never autonomous. Agent proposes, Hans decides.

### 5. WATCH VECTORS  (deferred feature, config already exists)
  - config/watch_vectors.py holds the OPM/Oracle Federal HR 2.0 intel.
  - Build search_watch_vectors() tool + registry entry so the agent runs
    those high-priority queries. Do AFTER remote execution is in.


## NOTES / KNOWN LIMITATIONS (carry forward)
  - Cross-source merge ([Adzuna+Serper]) never WITNESSED, but logic is
    proven (across-run dedup fires; parser test recovered Akshaya's real
    company). Reason: Adzuna returns so little that overlap with Serper
    is rare. STOP chasing the visual — logic is sound.
  - max_iterations = 15 placeholder cap. Normal runs finish ~8-11. Fine.
  - job_decisions.json read path points at job-search-hans (wired in
    task 1). Working.
  - Fixed _mark_seen to record all evaluated jobs, not just sendable, and
    decoupled it from email-send success — 6/20/26.
