"""
core/planner.py
===============
The brain.

Each iteration, the planner hands Claude:
  - the goal
  - a compact summary of what's happened so far (from AgentState)
  - the menu of tools it's allowed to call

...and Claude returns a STRUCTURED decision:
  { "tool": ..., "params": {...}, "reasoning": "..." }

The loop parses that and acts on it. This is the difference between
a pipeline (Python decides the order) and an agent (Claude decides
the order, every iteration, based on what it has seen).

Design choices that matter:
  - We FORCE JSON-only output so the loop can parse reliably. Models
    love to add prose; the system prompt forbids it and we strip
    fences defensively.
  - The TOOL MENU is passed in, not hardcoded here. Add a tool to the
    registry -> the agent can use it. No planner changes needed.
  - If Claude returns garbage, we fail safe to "stop" rather than
    loop blindly.
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

CLAUDE_API_KEY    = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_MAX_TOKENS = 800          # planner replies are small but state summaries can grow
CLAUDE_URL        = "https://api.anthropic.com/v1/messages"

# Default planner model. Override with PLANNER_MODEL env var for dev/test
# (e.g. set PLANNER_MODEL=claude-haiku-4-5-20251001 to cut planner costs).
# WARNING: a weaker model makes worse decisions — only use Haiku to test
# loop wiring, not to evaluate planner quality.
CLAUDE_MODEL = os.environ.get("PLANNER_MODEL", "claude-sonnet-4-6")


# ------------------------------------------------------------
# SYSTEM PROMPT
# ------------------------------------------------------------
# Note: {tool_menu} is filled in at runtime from the tool registry.
PLANNER_SYSTEM = """You are the planning brain of a job-search agent working for Hans Richardson.

Hans has FOUR job tracks:
  1. LoadRunner / Performance Engineering  (his strongest — 14 years expert level)
  2. AI Hybrid  (AI Systems, Agent Engineer, LLM Platform — early/mid, < 2 years)
  3. QA / Test Engineering  (bridge track — SDET, QA Automation, API testing)
  4. COBOL / Mainframe  (bridge track — 7 years early-career experience)

He accepts US-remote only, or hybrid/onsite within ~30 min of Kansas City.

YOUR JOB: given the goal and what has happened so far, decide the SINGLE next action.

PIPELINE — follow this order:
  1. Search each of the 4 tracks (1-2 searches per track is enough; do NOT repeat the
     same track query more than once — check the history before searching). Use
     search_adzuna and search_serper for all tracks; also try search_usajobs for the
     COBOL/Mainframe track (federal agencies run a lot of legacy COBOL) — it yields
     fewer results since most federal jobs aren't fully remote, so don't over-invest
     in it if it comes back thin.
  2. Once you have jobs from at least 2 tracks (or 8+ jobs total), call score_results.
  3. Call analyze_fit after scoring.
  4. Call email_results to send the digest — this is REQUIRED before stop.
  5. Call stop.

AVAILABLE TOOLS:
{tool_menu}

RESPONSE FORMAT — return ONLY valid JSON, no prose, no markdown fences:
{{"tool": "<tool_name>", "params": {{...}}, "reasoning": "<one sentence: why this, now>"}}

Rules:
- "tool" MUST be one of the tool names listed above.
- "params" MUST match what that tool expects (empty object {{}} if none).
- "reasoning" is one honest sentence explaining the choice — this gets logged.
- NEVER repeat a search query you have already run — check the history.
- Return the JSON object and NOTHING else.
"""


def _strip_fences(text: str) -> str:
    """Extract the JSON object from the response, stripping fences and prose."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        t = t.replace("```json", "").replace("```", "").strip()
    # Find the first { and last } to extract just the JSON object,
    # handling cases where the model adds prose before or after.
    start = t.find("{")
    end   = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        t = t[start:end + 1]
    return t


def _parse_decision(raw: str):
    """Try to parse a JSON decision from raw planner output. Returns dict or None."""
    try:
        return json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, ValueError):
        return None


def _retry_parse(api_key: str, system: str, user_msg: str,
                 bad_raw: str, model: str):
    """
    One retry when the first response wasn't valid JSON.
    Feeds the bad response back and insists on JSON only.
    Returns dict or None.
    """
    retry_msg = (
        f"{user_msg}\n\n"
        f"Your previous response was not valid JSON:\n{bad_raw[:400]}\n\n"
        "Return ONLY the JSON object — no prose, no markdown, no explanation."
    )
    try:
        resp = requests.post(
            CLAUDE_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": CLAUDE_MAX_TOKENS,
                "system": system,
                "messages": [{"role": "user", "content": retry_msg}],
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        return _parse_decision(resp.json()["content"][0]["text"])
    except Exception:
        return None


def plan_next_action(state, tool_menu: str, model: str = None) -> dict:
    """
    Ask Claude for the next action given current state.

    state:      AgentState
    tool_menu:  human-readable list of tools + params (from the registry)

    Returns a decision dict:
        {"tool": str, "params": dict, "reasoning": str}
    On any failure, fails SAFE to stop:
        {"tool": "stop", "params": {}, "reasoning": "<why we bailed>"}
    """
    if not CLAUDE_API_KEY:
        return {"tool": "stop", "params": {},
                "reasoning": "No CLAUDE_API_KEY set — cannot plan."}

    system = PLANNER_SYSTEM.format(tool_menu=tool_menu)
    user_msg = (
        f"{state.summary_for_planner()}\n\n"
        "Decide the next action. Return only the JSON object."
    )

    try:
        resp = requests.post(
            CLAUDE_URL,
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model or CLAUDE_MODEL,
                "max_tokens": CLAUDE_MAX_TOKENS,
                "system": system,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=30,
        )

        if resp.status_code != 200:
            return {"tool": "stop", "params": {},
                    "reasoning": f"Planner API error {resp.status_code}; stopping safely."}

        raw = resp.json()["content"][0]["text"]
        decision = _parse_decision(raw)
        if decision is None:
            # One retry with a blunter instruction before giving up.
            decision = _retry_parse(CLAUDE_API_KEY, system, user_msg, raw,
                                    model or CLAUDE_MODEL)
        if decision is None:
            return {"tool": "stop", "params": {},
                    "reasoning": "Planner returned non-JSON after retry; stopping safely."}

        # Validate shape — fail safe if malformed.
        if not isinstance(decision, dict) or "tool" not in decision:
            return {"tool": "stop", "params": {},
                    "reasoning": "Planner returned malformed decision; stopping safely."}

        decision.setdefault("params", {})
        decision.setdefault("reasoning", "(no reasoning provided)")
        return decision

    except Exception as e:
        return {"tool": "stop", "params": {},
                "reasoning": f"Planner exception: {e}; stopping safely."}


# ------------------------------------------------------------
# QUICK SELF-TEST  (python core/planner.py)
# Needs CLAUDE_API_KEY to hit the live model. Without it, exercises
# the fail-safe path. With it, you'll see a real planning decision.
# ------------------------------------------------------------
if __name__ == "__main__":
    import sys, os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from core.state import AgentState

    DEMO_MENU = (
        "- search_adzuna(query: str): search Adzuna job board for a query.\n"
        "- score_results(): score and filter the jobs gathered so far.\n"
        "- analyze_fit(): write a fit analysis for the top matches.\n"
        "- stop(reason: str): finish the run and report."
    )

    s = AgentState(goal="Find 5 strong LoadRunner roles, remote or KC, above my floor.")

    print("=== Plan #1 (empty state) ===")
    d1 = plan_next_action(s, DEMO_MENU)
    print(json.dumps(d1, indent=2))

    # Simulate that a search happened and was thin, then plan again.
    s.next_iteration()
    s.record(s.iteration, d1.get("tool", "search_adzuna"),
             d1.get("params", {}), d1.get("reasoning", ""),
             result_summary="2 results (thin)")
    s.add_jobs([{"title": "Perf Engineer", "url": "http://x/1"},
                {"title": "LR Consultant", "url": "http://x/2"}])

    print("\n=== Plan #2 (after a thin search) ===")
    d2 = plan_next_action(s, DEMO_MENU)
    print(json.dumps(d2, indent=2))
