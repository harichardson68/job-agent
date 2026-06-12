"""
core/logger.py
==============
The observability layer.

Every agent run produces TWO things:
  1. A live console stream (so you watch it think in real time —
     this is what feeds the Agent Hub window).
  2. A saved reasoning trace in logs/ (the portfolio artifact —
     proof the agent made real decisions, with timestamps).

This is deliberately the second thing built, before any tool or
the planner, so the rest of the build is observable as you write it.

The logger READS from AgentState; it never decides anything.
"""

import os
import json
from datetime import datetime
from typing import Callable, Optional


# Where traces are saved. Resolves to job-agent/logs/ regardless of CWD.
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


class RunLogger:
    """
    One logger per agent run. Streams to console (and optionally to a
    UI callback), and saves a full trace file at the end.
    """

    def __init__(self, goal: str, stream_callback: Optional[Callable[[str], None]] = None):
        """
        goal:            the run's goal (for the trace header)
        stream_callback: optional fn(text) -> None. If provided, every
                         logged line is ALSO pushed here. This is how
                         Agent Hub receives the live stream. If None,
                         output goes to console only.
        """
        self.goal = goal
        self.stream_callback = stream_callback
        self.lines: list[str] = []
        self.started_at = datetime.now()

        os.makedirs(LOG_DIR, exist_ok=True)
        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        self.trace_path = os.path.join(LOG_DIR, f"run_{ts}.log")

        self._emit(f"{'='*60}")
        self._emit(f"AGENT RUN — {self.started_at.strftime('%Y-%m-%d %I:%M %p')}")
        self._emit(f"GOAL: {goal}")
        self._emit(f"{'='*60}")

    # ------------------------------------------------------------
    # internal: write one line everywhere it needs to go
    # ------------------------------------------------------------
    def _emit(self, text: str) -> None:
        self.lines.append(text)
        print(text)                       # console
        if self.stream_callback:
            self.stream_callback(text + "\n")   # UI (Agent Hub)

    # ------------------------------------------------------------
    # public logging methods — called from the loop
    # ------------------------------------------------------------
    def iteration(self, n: int) -> None:
        self._emit(f"\n--- Iteration {n} ---")

    def thinking(self, reasoning: str) -> None:
        """The planner's reasoning for its next move (the gold)."""
        self._emit(f"  THINK: {reasoning}")

    def action(self, tool: str, params: dict) -> None:
        """The tool the agent chose and what it passed in."""
        p = json.dumps(params, default=str)
        self._emit(f"  ACT:   {tool}({p})")

    def observation(self, summary: str) -> None:
        """What came back."""
        self._emit(f"  SEE:   {summary}")

    def note(self, text: str) -> None:
        """Freeform note (warnings, pivots, self-critique)."""
        self._emit(f"  NOTE:  {text}")

    def error(self, text: str) -> None:
        self._emit(f"  ERROR: {text}")

    # ------------------------------------------------------------
    # finish: write the report + save trace to disk
    # ------------------------------------------------------------
    def finish(self, report: str, jobs_count: int) -> str:
        elapsed = (datetime.now() - self.started_at).total_seconds()
        self._emit(f"\n{'='*60}")
        self._emit(f"REPORT")
        self._emit(f"{'='*60}")
        self._emit(report)
        self._emit(f"\n{'-'*60}")
        self._emit(f"Jobs found: {jobs_count}  |  Elapsed: {elapsed:.1f}s")
        self._emit(f"Trace saved: {self.trace_path}")

        try:
            with open(self.trace_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.lines))
        except Exception as e:
            print(f"[logger] failed to write trace: {e}")

        return self.trace_path


# ------------------------------------------------------------
# QUICK SELF-TEST  (python core/logger.py)
# ------------------------------------------------------------
if __name__ == "__main__":
    # Simulate a tiny run. No real tools — just exercising the logger.
    captured = []
    log = RunLogger(
        goal="Find LoadRunner and AI hybrid roles, remote, above floor",
        stream_callback=lambda t: captured.append(t),   # pretend this is Agent Hub
    )

    log.iteration(1)
    log.thinking("Start with the highest-priority track on the broadest source.")
    log.action("search_adzuna", {"query": "LoadRunner remote"})
    log.observation("6 results")

    log.iteration(2)
    log.thinking("Adzuna was thin. Expand reach before scoring.")
    log.action("search_serper", {"query": "LoadRunner contract remote"})
    log.observation("3 results, 1 duplicate dropped")
    log.note("Only 8 unique so far — below the 5 strong matches I want. Will score then decide.")

    log.iteration(3)
    log.thinking("Enough raw results. Score and analyze top matches.")
    log.action("score_results", {"count": 8})
    log.observation("3 clear the bar, 2 unverified salary, 3 dropped")

    path = log.finish(
        report="Top match: Sr Performance Engineer (remote, $120k) — clears LoadRunner floor.",
        jobs_count=3,
    )

    print("\n[selftest] stream_callback captured", len(captured), "chunks")
    print("[selftest] trace written to:", path)
