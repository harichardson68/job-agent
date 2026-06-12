# Job-Agent Salary Configuration
# Hans Richardson
# ============================================================
# Two-track salary floors. LoadRunner floor is HARD (walk away
# below it). AI floor is SOFT (flex for the right resume-building
# role). Missing salary data is KEPT and FLAGGED, never silently
# dropped — you stay in control of borderline calls.
# ============================================================


# ------------------------------------------------------------
# FLOORS
# ------------------------------------------------------------
SALARY_FLOORS = {
    "loadrunner": {
        "base_annual": 110_000,   # full-time base, USD
        "contract_hr": 55,        # W2 hourly
        "hard": True,             # below this -> drop (genuine waste of time)
        "label": "LoadRunner / Performance",
    },
    "ai_hybrid": {
        "base_annual": 90_000,    # full-time base, USD
        "contract_hr": 45,        # W2 hourly
        "hard": False,            # below this -> keep + flag (worth considering for trajectory)
        "label": "AI Systems / Agent / Hybrid",
    },
}

# Rough annual<->hourly bridge for comparing mixed listings.
# 2,080 = 40 hrs x 52 wks. Used only when a listing gives one unit
# but the goal cares about the other.
HOURS_PER_YEAR = 2_080


# ------------------------------------------------------------
# MISSING-DATA POLICY
# ------------------------------------------------------------
# Many postings list no salary at all. Policy:
#   "keep_and_flag" -> include the role, mark salary as unverified
#   "drop"          -> exclude silently (NOT recommended — loses good roles)
#   "search"        -> agent web-searches for comp (costs more, often inconclusive)
MISSING_SALARY_POLICY = "keep_and_flag"

# Text the agent attaches to a role when salary can't be verified.
MISSING_SALARY_NOTE = "Salary not listed — could not verify against floor."


# ------------------------------------------------------------
# HELPER (the agent / scorer can import this)
# ------------------------------------------------------------
def evaluate_salary(track, base_annual=None, contract_hr=None):
    """
    Returns a dict the agent can reason over:
      {
        "verdict": "above" | "below" | "unverified",
        "hard":    bool,      # is this a hard floor?
        "note":    str,       # human-readable flag
      }

    track: "loadrunner" or "ai_hybrid"
    Pass whichever unit the listing provides (base_annual or contract_hr).
    """
    floor = SALARY_FLOORS.get(track)
    if floor is None:
        return {"verdict": "unverified", "hard": False,
                "note": f"Unknown track '{track}'."}

    # No data at all -> unverified
    if base_annual is None and contract_hr is None:
        return {"verdict": "unverified", "hard": floor["hard"],
                "note": MISSING_SALARY_NOTE}

    # Compare on whichever unit we have
    if base_annual is not None:
        ok = base_annual >= floor["base_annual"]
        unit = f"${base_annual:,}/yr vs floor ${floor['base_annual']:,}/yr"
    else:
        ok = contract_hr >= floor["contract_hr"]
        unit = f"${contract_hr}/hr vs floor ${floor['contract_hr']}/hr"

    if ok:
        return {"verdict": "above", "hard": floor["hard"],
                "note": f"Clears floor ({unit})."}
    else:
        # Below floor. Hard track -> drop. Soft track -> keep + flag.
        action = "drop" if floor["hard"] else "keep + flag (soft floor)"
        return {"verdict": "below", "hard": floor["hard"],
                "note": f"Below floor ({unit}) -> {action}."}


# ------------------------------------------------------------
# QUICK SELF-TEST  (python salary_config.py)
# ------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        ("loadrunner", 105_000, None),   # below hard floor -> drop
        ("loadrunner", 120_000, None),   # above
        ("loadrunner", None, 50),        # below hard hourly -> drop
        ("ai_hybrid",  85_000, None),    # below soft floor -> keep + flag
        ("ai_hybrid",  None, 40),        # below soft hourly -> keep + flag
        ("ai_hybrid",  None, None),      # no data -> unverified
    ]
    for track, base, hr in tests:
        r = evaluate_salary(track, base, hr)
        print(f"{track:11} base={base} hr={hr}  ->  {r['verdict']:10} | {r['note']}")
