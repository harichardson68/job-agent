"""
agent.py
========
The loop. This is where it all comes together.

THE CYCLE (one iteration):
    1. PLAN     planner asks Claude: "what's the next move?"
    2. ACT      look the chosen tool up in the registry, run it
    3. OBSERVE  record the result into state, log the reasoning
    4. repeat until the agent calls stop() or hits the safety cap

Everything before this file was a part. This file makes them an agent.

RUN:
    python agent.py "Find 5 strong LoadRunner roles, remote or KC, above my floor"
    (or with no arg, uses a default goal)

The same run_agent() function is what Agent Hub's "Run Agent" button
will call — it accepts an optional stream_callback so the live trace
flows into the GUI window.
"""

import sys
import json
import os

# Package imports (running from job-agent/ root) with dev fallback.
try:
    from core.state import AgentState
    from core.planner import plan_next_action
    from core.logger import RunLogger
    from core.decisions_sync import pull_before_run, push_after_run
    from tools.registry import build_menu, get_tool, is_valid_tool, TOOLS
except ImportError:
    from state import AgentState
    from planner import plan_next_action
    from logger import RunLogger
    from decisions_sync import pull_before_run, push_after_run
    from registry import build_menu, get_tool, is_valid_tool, TOOLS


def run_agent(goal: str, max_iterations: int = 15, stream_callback=None,
              skip_fit: bool = False, planner_model: str = None) -> AgentState:
    """
    Run one full agent loop for `goal`.

    goal:            plain-English objective from Hans
    max_iterations:  safety cap (state enforces it too)
    stream_callback: optional fn(text) -> None for live UI streaming
                     (Agent Hub passes this; CLI leaves it None)

    Returns the final AgentState (jobs, history, report all populated).
    """
    state = AgentState(goal=goal, max_iterations=max_iterations)
    log = RunLogger(goal=goal, stream_callback=stream_callback)

    # Pull the shared decisions history before scoring reads it — keeps a
    # local run in sync with whatever the cloud (GitHub Actions) run last
    # decided, so dedup doesn't work off a stale snapshot. No-op when the
    # decisions path isn't a git repo (e.g. already-fresh cloud checkout).
    pull_before_run()

    # Build the active tool set — strip analyze_fit when skip_fit=True so the
    # planner never sees it and can't call it (saves the most expensive call).
    _skip = {"analyze_fit"} if skip_fit else set()
    active_tools = {k: v for k, v in TOOLS.items() if k not in _skip}
    tool_menu = build_menu(active_tools)

    # ---- THE LOOP ----
    while state.should_continue():
        n = state.next_iteration()
        log.iteration(n)

        # 1. PLAN — Claude picks the next tool
        decision = plan_next_action(state, tool_menu, model=planner_model)
        tool_name = decision.get("tool", "stop")
        params    = decision.get("params", {}) or {}
        reasoning = decision.get("reasoning", "")

        log.thinking(reasoning)

        # Guard: did Claude pick a tool that's in the active set?
        if tool_name != "stop" and tool_name not in active_tools:
            log.error(f"Planner chose unknown tool '{tool_name}'. Stopping safely.")
            state.stop(reason=f"Unknown tool '{tool_name}'.")
            break

        log.action(tool_name, params)

        # 2. ACT — special-case stop; otherwise run the tool
        if tool_name == "stop":
            state.record(n, "stop", params, reasoning, "Agent chose to stop.")
            state.stop(reason=reasoning)
            log.observation("Agent decided it has enough. Ending loop.")
            break

        fn = get_tool(tool_name)
        try:
            # Tools that need state get it; simple search tools take params only.
            result = _call_tool(fn, params, state)
        except Exception as e:
            log.error(f"Tool '{tool_name}' raised: {e}")
            state.record(n, tool_name, params, reasoning, f"ERROR: {e}")
            continue   # don't crash the run; let the planner react next iteration

        # 3. OBSERVE — fold the result into state + log it
        summary = _summarize_result(result)
        state.record(n, tool_name, params, reasoning, summary, raw_result=result)

        # If the tool returned jobs, accumulate them (with URL dedup in state)
        new_jobs = result.get("jobs") if isinstance(result, dict) else None
        if new_jobs:
            added = state.add_jobs(new_jobs)
            log.observation(f"{summary}  (+{added} new, {len(state.jobs)} total)")
        else:
            log.observation(summary)

    # ---- REPORT ----
    report = _build_report(state)
    state.final_report = report
    log.finish(report, jobs_count=len(state.jobs))

    # Save last run for GUI-triggered email button
    _save_last_run(state)

    # Push any new decisions (emailed job URLs) back so the next run —
    # local or cloud — sees them. Retries once on a rejected push rather
    # than failing silently or force-pushing over a concurrent run.
    push_after_run()

    return state


def _save_last_run(state) -> None:
    """Persist last run's jobs + goal to last_run.json for the Agent Hub email button."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_run.json")
        payload = {
            "goal": getattr(state, "goal", ""),
            "jobs": getattr(state, "jobs", []),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception:
        pass  # never crash the run over a save failure


def _call_tool(fn, params: dict, state: AgentState):
    """
    Call a tool function, passing state only if it accepts it.
    Search tools take (query=...); processing tools take (state=...).
    Keeps the loop from caring which kind it's calling.
    """
    import inspect
    sig = inspect.signature(fn)
    kwargs = dict(params)
    if "state" in sig.parameters:
        kwargs["state"] = state
    return fn(**kwargs)


def _summarize_result(result) -> str:
    """Short human-readable line for the trace."""
    if isinstance(result, dict):
        return result.get("note") or result.get("result_summary") or str(result)[:120]
    return str(result)[:120]


def _build_report(state: AgentState) -> str:
    if not state.jobs:
        return "No matching jobs found this run."

    lines = [f"Found {len(state.jobs)} job(s) for goal: {state.goal}", ""]
    for i, job in enumerate(state.jobs[:10], 1):
        loc = job.get("location_note", "")
        url = job.get("url", "").strip()
        seen = "+".join(job.get("seen_on", [job.get("source", "?")]))
        tier = job.get("fit_tier", "")
        reason = job.get("fit_reason", "")
        gap = job.get("fit_gap")

        header = (f"{i}. {job.get('title','N/A')} @ {job.get('company','N/A')}"
                  f"  [{seen}]"
                  + (f" — {loc}" if loc else ""))
        lines.append(header)
        if tier:
            gap_str = f"  gap: {gap}" if gap else ""
            lines.append(f"   Fit: {tier}{gap_str} — {reason}")
        if url:
            lines.append(f"   Apply: {url}")

    if len(state.jobs) > 10:
        lines.append(f"...and {len(state.jobs) - 10} more.")

    analyzed = sum(1 for j in state.jobs if j.get("fit_tier"))
    if analyzed:
        lines.append(f"\n(Fit analysis shown for top {analyzed} matches; remaining ranked by score.)")

    return "\n".join(lines)


# ------------------------------------------------------------
# CLI ENTRY
# ------------------------------------------------------------
if __name__ == "__main__":
    default_goal = ("Find me strong remote US matches across all four of my tracks: "
                    "1) LoadRunner / Performance Engineering  2) AI Hybrid (AI Systems, Agent Engineer, LLM Platform)  "
                    "3) QA / Test Engineering (SDET, QA Automation, API testing, manual QA)  "
                    "4) COBOL / Mainframe (if any postings exist). "
                    "Remote only, no hybrid or onsite. Contract or full-time.")
    goal = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else default_goal
    run_agent(goal)
