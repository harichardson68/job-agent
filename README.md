# Job-Agent — An Autonomous Agentic Job Search

A job search system where **Claude decides what to do next** — not a fixed
script. Each iteration, the agent observes what it has found, plans its next
move, acts, and observes the result, looping until it decides it has enough.
The result is a system that *adapts its strategy mid-run* instead of marching
through hardcoded steps.

## 🎬 Live Demo

[![Watch the Demo](https://img.shields.io/badge/Watch-Loom%20Demo-orange?logo=loom)](https://www.loom.com/share/8a5bf691b79b4d9abb7379b2e0b771c3)

> End-to-end walkthrough of the agentic loop — observe, plan, act cycle with live tool calls, decision traces, and real output.

Built by a performance engineer bringing reliability, observability, and
evaluation discipline to a domain that needs it: non-deterministic AI systems.

---

## Why this exists (and why it's not just a script)

I already had a deterministic job-search **pipeline** (`job_search.py`): fetch
from every source, score, email — same sequence every run. It works, but it
can't *react*. If a LoadRunner search comes back empty, a pipeline returns
nothing. An agent notices the dry well and broadens the search itself.

That difference — **the LLM choosing the sequence of actions** — is the whole
point. This repo is the agent version: same domain, completely different
architecture.

| | Pipeline (`job_search.py`) | **Job-Agent (this repo)** |
|---|---|---|
| Decides the order | Hardcoded in Python | **Claude, every iteration** |
| Reacts to thin results | No | **Yes — pivots queries/sources** |
| Output | A score | **Ranked + fit-tiered + reasoning trace** |
| Best at | Broad daily sweep | **Adaptive, targeted hunts** |

---

## How it works

```
GOAL ─▶ PLAN ─▶ ACT ─▶ OBSERVE ─┐
         ▲                       │
         └───────────────────────┘
              loop until stop()
```

1. **PLAN** — the planner sends Claude the goal, a compact summary of what's
   happened so far, and a menu of available tools. Claude returns a structured
   decision: *which tool, what parameters, and why.*
2. **ACT** — the loop looks the chosen tool up in the registry and runs it.
3. **OBSERVE** — the result is folded into state and logged. Repeat.
4. **STOP** — Claude decides when the goal is satisfied (or a safety cap trips).

A real run pivoting on its own:

```
Iteration 1: search LoadRunner roles on Adzuna     → 0 results
Iteration 2: results thin — broaden to Serper      → 10 results
Iteration 3: pivot to AI-hybrid track              → +more
Iteration 4: score + dedup gathered jobs
Iteration 5: fit-analyze the top matches
Iteration 6: enough to satisfy the goal — stop
```

No pipeline does iteration 2. That adaptive pivot is the thesis.

---

## Architecture

```
job-agent/
├── agent.py              # the loop — plan → act → observe → repeat
├── core/
│   ├── state.py          # run state: history, accumulated jobs, safety cap
│   ├── planner.py        # calls Claude for the next action (forced JSON)
│   └── logger.py         # streaming reasoning trace → console + UI
├── tools/
│   ├── registry.py       # one dict → generates the planner menu AND the
│   │                     #   function lookup (add a tool in one place)
│   ├── search.py         # Adzuna + Serper, location-aware filtering
│   ├── score.py          # dedup → tiered scoring → rank
│   ├── analyze_fit.py    # LLM fit tiers + honest gap analysis (top-N only)
│   ├── cover_letter.py   # on-demand, for chosen roles only
│   └── email_results.py  # HTML digest — act from anywhere
└── config/
    ├── goals.py          # preset goals
    ├── salary_config.py  # two-track salary floors
    └── watch_vectors.py  # market-intelligence-driven search angles
```

### Design decisions worth calling out

- **The registry is the single source of truth.** Each tool is described once
  in a dict (name, function, params, description). The planner's menu *and* the
  loop's function lookup are both generated from it — so adding a tool is one
  entry, and the menu can never drift out of sync with the real functions.

- **The planner is tool-agnostic.** It never hardcodes tool names; it reads the
  generated menu. New tools drop in with zero planner changes.

- **Observability is first-class.** Every decision is logged as a
  `THINK / ACT / SEE` trace, streamed live and saved to `logs/`. The trace *is*
  the artifact — it shows the agent reasoning, not just its output.

- **Fail-safe by default.** If Claude returns malformed JSON, picks an unknown
  tool, or a tool raises — the loop degrades to a safe stop instead of crashing
  or looping forever. A runaway agent costs real API money; the safety cap and
  graceful failure are deliberate.

- **Cost-aware "test mode."** A dev toggle swaps the planner to a cheaper model,
  trims the iteration cap, and skips the expensive fit-analysis call — so the
  plumbing can be iterated for pennies, with full-cost runs reserved for
  verification. (Performance-engineering instinct: measure cheap, spend
  deliberately.)

---

## The evaluation layer (the performance-engineering crossover)

Finding jobs is the easy part. Turning a messy pile of duplicates into a clean,
ranked, honestly-assessed list is where the discipline shows:

- **Deduplication, two layers.** Across runs (every job the agent evaluates —
  not just the ones that make the final digest — is recorded as seen, so a
  poor-fit posting doesn't get silently re-fetched and re-billed through fit
  analysis tomorrow) and across sources (the same role posted under varying titles
  "Agentic AI Engineer" vs "Agentic AI Platform Engineer" — is collapsed via a
  company-gated, ratio-thresholded title match tuned to avoid false merges).
- **Tiered scoring.** Weighted keyword policy encodes *what matters* as a fixed,
  debuggable rule — not something the LLM re-decides each run.
- **Fit analysis with honest gaps.** The top matches get an LLM-driven tier
  (Excellent → Weak) plus the single most notable gap flagged ("strong skill
  match, but wants Azure you don't have yet"). Track-aware: senior-beyond-reach
  roles are filtered or capped rather than oversold.

This is eval thinking — the same instinct that makes a performance engineer ask
"does this actually hold up?" instead of trusting the happy path.

---

## Running it

```bash
pip install -r requirements.txt

# set keys in .env (not committed):
#   CLAUDE_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY, SERPER_API_KEY

python agent.py "Find LoadRunner and AI hybrid roles, remote or KC, above my floor"
```

The agent streams its reasoning trace to the console and saves a full trace to
`logs/`. It can also be triggered from a desktop GUI (Agent Hub) that streams
the same trace into a chat window.

---
## Running it in the cloud (GitHub Actions)

The agent doesn't need my laptop running. A scheduled workflow
(`.github/workflows/run-agent.yml`) runs it daily on a fresh, disposable Ubuntu
VM:

1. Checks out this repo's code.
2. Checks out a second, private repo (`job-search-hans`) that holds
   `job_decisions.json` — the cross-run dedup history shared with the original
   pipeline — into a subfolder.
3. Installs dependencies and runs `agent.py`, with API keys and the Gmail app
   password injected from GitHub Secrets (never committed to code).
4. Sends the digest, then commits the updated history back to
   `job-search-hans` with a repo-scoped token, so tomorrow's run — cloud or
   local — sees today's decisions, regardless of whether the email send
   itself succeeded.

**Known open risk:** because both a local run and the scheduled cloud run can
write to the same history file, running both inside the same window risks a
lost update on whichever pushes second. Mitigated for now by keeping the
scheduled run at a fixed early-morning hour and avoiding manual local runs
during it; a real fix (pull-then-merge-then-push, or a small server-side
store) is on the roadmap.

## Status & roadmap

**Working today:** adaptive multi-source search, location filtering, two-layer
dedup, tiered scoring, LLM fit analysis with gap-flagging, on-demand cover
letters, HTML email digest, full reasoning-trace observability, dev/prod test
mode, graceful failure handling, scheduled cloud execution via GitHub Actions
(runs daily without a laptop on; decisions persisted cross-repo, independent
of email delivery outcome).

**Next:** harden the cross-repo decisions write against concurrent local +
cloud runs (pull-then-merge-then-push); an outcome-driven learning loop where 
logged decisions feed scoring refinements a human approves (deliberately 
*not* unsupervised self-modification — human in the loop by design).

---

## A note on the human-in-the-loop choice

The agent could be wired to rewrite its own scoring based on patterns in past
decisions. It deliberately isn't — yet. Unsupervised self-modification is hard
to debug and prone to silent drift. The intended design keeps a human approving
each change, with a full audit trail. Knowing *not* to build the autonomous
version yet is part of the engineering.

---

*Built with Python and the Claude API. Part of a broader portfolio bridging 24
years of enterprise performance engineering into AI systems work.*
