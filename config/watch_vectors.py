# config/watch_vectors.py
# ============================================================
# WATCH VECTORS — market-intelligence-driven search angles.
#
# These are high-priority queries tied to SPECIFIC opportunities Hans
# has identified from news/contracts. They are DATA, not code — edit
# this list freely as the market moves. The agent can pull these as
# ready-made queries (alongside Hans's standing tracks).
#
# Each vector: a query, why it matters, likely employers to watch, and
# an expiry note so stale ones get pruned.
# ============================================================

WATCH_VECTORS = [
    {
        "id": "opm_oracle_hr2",
        "active": True,
        "queries": [
            "PeopleSoft performance testing remote",
            "Oracle HCM performance engineer federal",
            "federal HR modernization performance test",
            "Oracle Federal HR performance LoadRunner",
        ],
        "why": (
            "OPM awarded Oracle a 10-yr $395.8M 'Federal HR 2.0' contract "
            "(announced ~June 2026) consolidating 100+ HR systems for 2M "
            "federal employees onto one PeopleSoft-lineage platform. Core "
            "implementation targeted for fall 2026. Massive load/perf testing "
            "need at federal scale — directly matches Hans's LoadRunner + "
            "federal background + reinstatable Public Trust clearance."
        ),
        "watch_employers": [
            "Oracle", "Accenture Federal", "Deloitte", "Booz Allen",
            "GDIT", "Leidos", "SAIC", "ICF",
        ],
        "track": "loadrunner",          # maps to the LoadRunner (hard-floor) track
        "caveats": (
            "Work flows to Oracle + subcontractors, not OPM directly. Award "
            "still in protest window (~10 days from June 2026 announcement). "
            "Fixed-price contract may pressure staffing toward offshore."
        ),
        "added": "2026-06",
        "review_by": "2026-12",         # re-check relevance; prune if cold
    },

    # --- add new vectors below as opportunities surface ---
    # {
    #     "id": "...",
    #     "active": True,
    #     "queries": [...],
    #     "why": "...",
    #     "watch_employers": [...],
    #     "track": "loadrunner" | "ai_hybrid",
    #     "caveats": "...",
    #     "added": "YYYY-MM",
    #     "review_by": "YYYY-MM",
    # },
]


def active_queries(track: str = None) -> list[str]:
    """
    Flatten all queries from ACTIVE watch vectors. Optionally filter by
    track ('loadrunner' or 'ai_hybrid'). The agent can fold these into a
    run alongside Hans's standing searches.
    """
    out = []
    for v in WATCH_VECTORS:
        if not v.get("active"):
            continue
        if track and v.get("track") != track:
            continue
        out.extend(v.get("queries", []))
    return out


def watch_employers() -> set:
    """All employers worth flagging across active vectors. The agent can
    boost or highlight a result whose company matches one of these."""
    emps = set()
    for v in WATCH_VECTORS:
        if v.get("active"):
            emps.update(e.lower() for e in v.get("watch_employers", []))
    return emps


# ------------------------------------------------------------
# QUICK SELF-TEST  (python config/watch_vectors.py)
# ------------------------------------------------------------
if __name__ == "__main__":
    print("Active LoadRunner-track queries:")
    for q in active_queries("loadrunner"):
        print(f"  - {q}")
    print("\nWatch employers:")
    print("  " + ", ".join(sorted(watch_employers())))
