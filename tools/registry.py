"""
tools/registry.py
=================
The single source of truth for what tools the agent has.

THE PROBLEM THIS SOLVES:
  Two separate things need to know about every tool —
    1. The PLANNER's menu (text Claude reads to know its options)
    2. The LOOP (code that actually CALLS the chosen function)
  If those two drift apart, the agent breaks: Claude picks a tool the
  loop can't run, or the loop has a function Claude never sees.

THE FIX:
  Describe each tool ONCE in the TOOLS dict below. Everything else —
  the menu text, the function lookup — is GENERATED from this dict.
  Add a tool here, and both the menu and the loop get it for free.

A "dict" is just Python's lookup table: { key: value }. Here the key
is the tool's NAME (what Claude says), and the value is everything we
know about that tool (the real function, its params, its description).
"""

# Import the real tool functions.
# (When run as part of the package: from tools.search import search_adzuna)
try:
    from tools.search import search_adzuna, search_serper, search_usajobs
    from tools.score import score_results
    from tools.analyze_fit import analyze_fit
    from tools.email_results import email_results
except ImportError:
    from search import search_adzuna, search_serper, search_usajobs
    from score import score_results
    from analyze_fit import analyze_fit
    from email_results import email_results

def stop(state=None, reason: str = ""):
    """Signal the loop to finish. Handled specially in agent.py."""
    return {"ok": True, "tool": "stop", "note": reason or "Agent chose to stop."}


# ============================================================
# THE REGISTRY DICT  <-- this is the thing you were missing
# ============================================================
# key   = tool name (what Claude returns in its decision)
# value = dict describing that tool:
#         "fn"     -> the real Python function the loop calls
#         "params" -> human-readable params for the menu text
#         "desc"   -> what the tool does (Claude reads this)
TOOLS = {
    "search_adzuna": {
        "fn": search_adzuna,
        "params": "query: str",
        "desc": "Search Adzuna job board. Pass only role/skill keywords "
                "(e.g. 'loadrunner performance engineer', 'AI systems engineer') "
                "— prefer 1-2 key terms; Adzuna AND-matches every word so longer "
                "queries return fewer results. Do NOT include location words like "
                "'remote' or 'wfh'; location is filtered separately.",
    },
    "search_serper": {
        "fn": search_serper,
        "params": "query: str",
        "desc": "Search Google Jobs (via Serper) for a query. Complementary "
                "source to Adzuna — run both to maximize coverage and trigger "
                "cross-source dedup.",
    },
    "search_usajobs": {
        "fn": search_usajobs,
        "params": "query: str",
        "desc": "Search USAJOBS.gov (official US federal job board API). Best "
                "for the COBOL/Mainframe track — federal agencies still run a "
                "lot of legacy COBOL — but covers any federal IT/QA/AI role. "
                "Yields fewer results than Adzuna/Serper since most federal "
                "postings are tied to a specific duty station, not fully remote. "
                "REQUIRED: query (str) — always pass keywords, e.g. {\"query\": "
                "\"COBOL mainframe developer\"}; calling with no params raises "
                "a missing-argument error.",
    },
    "score_results": {
        "fn": score_results,
        "params": "",
        "desc": "Score and filter the jobs gathered so far against Hans's "
                "tracks and salary floors. Call once you have raw results.",
    },
    "analyze_fit": {
        "fn": analyze_fit,
        "params": "",
        "desc": "Write a short fit analysis (Excellent/Strong/Decent/Weak) for "
                "the top scored matches. Call after scoring.",
    },
    "email_results": {
        "fn": email_results,
        "params": "",
        "desc": "Send an HTML email digest of the ranked results to Hans. "
                "Call after score_results (and optionally analyze_fit) when "
                "the search goal is satisfied.",
    },
    "stop": {
        "fn": stop,
        "params": "reason: str",
        "desc": "Finish the run and report. Choose this when the goal is "
                "satisfied or further searching won't help.",
    },
}


# ============================================================
# GENERATED FROM THE DICT — you never type the menu by hand
# ============================================================
def build_menu(tools: dict = TOOLS) -> str:
    """
    Walk the TOOLS dict and build the menu text the planner shows Claude.
    This REPLACES the hand-typed DEMO_MENU in planner.py.
    """
    lines = []
    for name, info in tools.items():
        params = info.get("params", "")
        lines.append(f"- {name}({params}): {info['desc']}")
    return "\n".join(lines)


def get_tool(name: str):
    """
    Look up the real function for a tool name (used by the loop).
    Returns None if the name isn't registered.
    """
    entry = TOOLS.get(name)
    return entry["fn"] if entry else None


def is_valid_tool(name: str) -> bool:
    return name in TOOLS


# ------------------------------------------------------------
# QUICK SELF-TEST  (python tools/registry.py)
# ------------------------------------------------------------
if __name__ == "__main__":
    print("=== The TOOLS dict has these keys (tool names) ===")
    for name in TOOLS:
        print(f"  {name}")

    print("\n=== Menu GENERATED from the dict (no hand-typing) ===")
    print(build_menu())

    print("\n=== Looking up a real function by name ===")
    fn = get_tool("search_adzuna")
    print(f"  get_tool('search_adzuna') -> {fn}")
    print(f"  get_tool('make_coffee')   -> {get_tool('make_coffee')}  (not registered)")

    print("\n=== Calling a stub through the registry ===")
    result = get_tool("score_results")()
    print(f"  {result}")
