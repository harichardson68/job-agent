"""
core/state.py
=============
The agent's memory for a single run.

Everything the agent knows lives here: the goal, every tool it has
called, every result it has gathered, and its running reasoning. The
planner READS state to decide the next action; tools WRITE their
results back into state; the logger READS state to produce the trace.

This is the spine. Build it first — planner, tools, and logger all
depend on it.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


# ------------------------------------------------------------
# One record per tool the agent calls (one iteration of the loop)
# ------------------------------------------------------------
@dataclass
class ToolCall:
    iteration: int                  # which loop pass this was
    tool: str                       # e.g. "search_adzuna"
    params: dict                    # what the agent passed in
    reasoning: str                  # WHY the agent chose this (the gold)
    result_summary: str = ""        # short human-readable outcome
    raw_result: Any = None          # full payload (jobs list, etc.)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ------------------------------------------------------------
# The full state of one agent run
# ------------------------------------------------------------
@dataclass
class AgentState:
    goal: str                                   # plain-English goal from the user
    iteration: int = 0                          # current loop count
    max_iterations: int = 15                    # safety cap — never loop forever
    finished: bool = False                      # set True when stop() is called

    history: list[ToolCall] = field(default_factory=list)   # every action taken
    jobs: list[dict] = field(default_factory=list)          # accumulated job results
    final_report: str = ""                                  # what we hand back to Hans

    # --- lifecycle ---
    def next_iteration(self) -> int:
        """Advance the loop counter and return the new value."""
        self.iteration += 1
        return self.iteration

    def should_continue(self) -> bool:
        """Loop guard: stop if finished or if we hit the safety cap."""
        if self.finished:
            return False
        if self.iteration >= self.max_iterations:
            return False
        return True

    def stop(self, reason: str = "") -> None:
        """Agent decided it's done."""
        self.finished = True
        if reason:
            self.record(self.iteration, "stop", {}, reason, "Loop terminated.")

    # --- writing ---
    def record(self, iteration: int, tool: str, params: dict,
               reasoning: str, result_summary: str = "",
               raw_result: Any = None) -> ToolCall:
        """Append a tool call to history. Returns the record."""
        call = ToolCall(
            iteration=iteration,
            tool=tool,
            params=params,
            reasoning=reasoning,
            result_summary=result_summary,
            raw_result=raw_result,
        )
        self.history.append(call)
        return call

    def add_jobs(self, new_jobs: list[dict]) -> int:
        """
        Merge new jobs into the accumulated list, de-duplicating by URL.
        Returns the count actually added.
        """
        seen = {j.get("url") for j in self.jobs if j.get("url")}
        added = 0
        for job in new_jobs:
            url = job.get("url")
            if url and url in seen:
                continue
            self.jobs.append(job)
            if url:
                seen.add(url)
            added += 1
        return added

    # --- reading (for the planner / logger) ---
    def summary_for_planner(self) -> str:
        """
        Compact snapshot the planner sends to Claude so it can decide
        the next move. Keep this SHORT — it goes into every planner call
        and you pay tokens for it each iteration.
        """
        lines = [
            f"GOAL: {self.goal}",
            f"Iteration: {self.iteration}/{self.max_iterations}",
            f"Jobs gathered so far: {len(self.jobs)}",
            "Actions taken:",
        ]
        if not self.history:
            lines.append("  (none yet)")
        else:
            for c in self.history:
                lines.append(f"  [{c.iteration}] {c.tool} -> {c.result_summary}")
        return "\n".join(lines)

    def tools_used(self) -> list[str]:
        """Distinct tools called this run."""
        return list({c.tool for c in self.history})


# ------------------------------------------------------------
# QUICK SELF-TEST  (python core/state.py)
# ------------------------------------------------------------
if __name__ == "__main__":
    s = AgentState(goal="Find LoadRunner and AI hybrid roles, remote, above floor")

    # simulate a couple of iterations
    s.next_iteration()
    s.record(s.iteration, "search_adzuna", {"query": "LoadRunner remote"},
             reasoning="Start with the highest-priority track on the broadest source.",
             result_summary="6 results")
    s.add_jobs([
        {"title": "Sr Performance Engineer", "url": "http://x/1"},
        {"title": "Perf Test Lead", "url": "http://x/2"},
    ])

    s.next_iteration()
    s.record(s.iteration, "search_serper", {"query": "LoadRunner contract remote"},
             reasoning="Adzuna was thin. Expand reach before scoring.",
             result_summary="3 results")
    s.add_jobs([
        {"title": "Performance Engineer", "url": "http://x/2"},   # dup url -> skipped
        {"title": "LoadRunner Consultant", "url": "http://x/3"},
    ])

    s.stop(reason="Enough results gathered to report.")

    print(s.summary_for_planner())
    print("\nDistinct tools used:", s.tools_used())
    print("Total unique jobs:", len(s.jobs))
    print("Finished:", s.finished)
