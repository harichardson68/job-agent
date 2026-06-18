"""
tools/score.py
==============
The score_results tool: turn the raw, messy pile of gathered jobs into
a clean, deduped, ranked list — BEFORE Hans ever sees it or pays to
analyze it.

ORDER OF OPERATIONS (this order matters):
    1. DEDUP — drop jobs already seen, and collapse the same job that
       showed up from multiple sources. No point scoring a duplicate.
    2. SCORE — apply Hans's tiered keyword scoring (LoadRunner > Perf >
       AI > COBOL), mirroring job_search.py's philosophy.
    3. SALARY — annotate against the two-track floors.
    4. RANK  — sort best-first, hand back the clean list.

Scoring weights mirror job_search.py so the agent and the pipeline
agree on what "good" means. Kept self-contained (no import from
job_search.py) so job-agent stays a clean standalone project.
"""

import os
import re
import json

# Real decisions history (across-run dedup source)
DECISIONS_PATH = r"C:\Users\haric\Jobsearch\job_decisions.json"

# Salary logic from the config we built.
try:
    from config.salary_config import evaluate_salary
except ImportError:
    try:
        from salary_config import evaluate_salary
    except ImportError:
        evaluate_salary = None  # graceful: scoring still works without it


# ------------------------------------------------------------
# SCORING KEYWORDS  (mirrors job_search.py tiers)
# ------------------------------------------------------------
LOADRUNNER_PRIORITY = [
    "loadrunner", "load runner", "vugen", "vu gen", "lre",
    "loadrunner enterprise", "performance center",
]
AI_TITLE_PRIORITY = [
    "ai systems", "agent engineer", "llm platform", "ai engineer",
    "ai workflow", "ai automation", "agentic",
]
PERF_HIGH = [
    "performance engineer", "performance testing", "performance test",
    "load testing", "stress testing", "performance tuning",
]
AI_HIGH = [
    "machine learning", "llm", "rag", "generative ai", "langchain",
    "langgraph", "llamaindex", "prompt engineering", "vector database",
]
PERF_BONUS = [
    "appdynamics", "dynatrace", "splunk", "grafana", "prometheus",
    "new relic", "datadog", "observability", "jmeter", "neoload",
]
COBOL_KW = ["cobol", "cics", "mainframe", "db2", "jcl", "vsam", "natural", "adabas"]

# QA / Test Engineering track — broad net for bridge roles while Hans searches.
QA_TITLE_PRIORITY = [
    "sdet", "qa engineer", "qe engineer", "quality engineer",
    "test engineer", "software tester", "automation engineer",
]
QA_HIGH = [
    "quality assurance", "test automation", "manual testing",
    "functional testing", "regression testing", "selenium",
    "cypress", "playwright", "postman", "api testing",
    "test cases", "test plans", "defect management", "jira",
]
QA_BONUS = [
    "testng", "junit", "pytest", "rest assured", "appium",
    "cucumber", "bdd", "tdd", "zephyr", "testrail",
]

# Hard-drop title signals — these never clear Hans's bar regardless of keywords.
JUNIOR_TITLE = ["junior", "intern", "entry level", "entry-level"]
SENIOR_TITLE = [
    "director", "vice president", "vp ", "vp,", "vp-",
    "head of", "staff engineer", "distinguished engineer",
    "chief ", "cto", "ciso", "principal engineer",
]

# AI-track-only seniority hard-drop signals.
# These apply ONLY when the job is on the "AI Hybrid" track — Hans wants
# senior performance roles but is early/mid on the AI track.
# "architect" needs a companion AI/ML signal to avoid catching plain
# "Solutions Architect" on a perf posting.
_AI_SENIOR_TITLE = ["staff", "principal", "lead", "director", "vp", "head of", "distinguished"]
_AI_ARCH_SIGNAL_RE = re.compile(
    r'\b(ai|ml|llm|genai|agentic|machine\s*learning|generative)\b', re.I
)

# Regex: description requires 4+ years of AI/ML production experience.
_AI_YEARS_RE = re.compile(
    r'([4-9]|\d{2})\s*\+\s*years?\s+(?:of\s+)?(?:hands.on\s+)?(?:experience\s+)?'
    r'(?:with\s+|in\s+|building\s+|leading\s+)?'
    r'(?:AI|ML|machine\s+learning|LLM|large\s+language|artificial\s+intelligence|'
    r'AI/ML|ai\s+agent|llm\s+solution)',
    re.I,
)

# Regex: description requires a proven production GenAI/agentic AI track record.
# "proven track record of building and scaling end-to-end GenAI applications"
# — Hans doesn't have this yet; flag it as a penalty.
_AI_PROVEN_TRACK_RE = re.compile(r'proven\s+track\s+record', re.I)
_AI_GENAI_SIGNAL_RE  = re.compile(
    r'\b(genai|gen\s*ai|agentic\s+ai|agentic\s+application|end[- ]to[- ]end\s+(?:ai|gen)'
    r'|building\s+and\s+scaling\s+.*?(?:ai|llm)|llm\s+application|ai\s+application'
    r'|large\s+language\s+model\s+application)',
    re.I,
)

# Travel hard-drop — Hans won't travel heavily unless salary is exceptional.
# Catches "75% travel", "50% travel required", "travel up to 80%", etc.
_TRAVEL_RE = re.compile(
    r'\b([2-9]\d|100)\s*%\s*travel\b'
    r'|\btravel\s+(?:up\s+to\s+)?([2-9]\d|100)\s*%',
    re.I,
)

# Salary extraction from description text.
_SAL_HR_RE  = re.compile(
    r'\$\s*(\d+(?:\.\d+)?)\s*(?:[-–]\s*\$?\s*(\d+(?:\.\d+)?))?\s*(?:/\s*hr\b|/\s*hour\b|per\s+hour)',
    re.I,
)
_SAL_ANN_RE = re.compile(
    r'\$\s*(\d{2,3})(?:[,.](\d{3}))?\s*(?:k\b|,?000\b|per\s+year|/\s*year\b|annually)',
    re.I,
)


def _parse_salary(text: str):
    """Return (contract_hr, base_annual) — either may be None."""
    m = _SAL_HR_RE.search(text)
    if m:
        low  = float(m.group(1))
        high = float(m.group(2)) if m.group(2) else low
        return (low + high) / 2, None
    m = _SAL_ANN_RE.search(text)
    if m:
        annual = float(f"{m.group(1)}{m.group(2)}") if m.group(2) \
                 else float(m.group(1)) * 1000
        return None, annual
    return None, None


_BRIDGE_SENIOR_TITLE = ["staff", "principal", "director", "vp", "head of", "distinguished",
                        "chief", "fellow"]

def ai_seniority_drop(title: str, track: str) -> bool:
    """
    Return True when the job should be hard-dropped due to overseniority.
    AI Hybrid: drops staff/principal/lead/director (Hans is early on AI track).
    Bridge tracks: drops staff/principal/director and above (bridge = mid-level fill).
    Never fires on LoadRunner / Performance track.
    """
    if "Performance" in track or "LoadRunner" in track:
        return False
    t = title.lower()
    if track == "AI Hybrid":
        if any(kw in t for kw in _AI_SENIOR_TITLE):
            return True
        if "architect" in t and _AI_ARCH_SIGNAL_RE.search(t):
            return True
    elif track in ("QA / Test Engineering", "COBOL / Mainframe"):
        if any(kw in t for kw in _BRIDGE_SENIOR_TITLE):
            return True
    return False


def _norm_title(title: str) -> str:
    """Normalize a title for dedup matching: lowercase, strip punctuation,
    collapse sr/senior, squeeze whitespace."""
    t = title.lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = t.replace("sr ", "senior ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _titles_match(a: str, b: str) -> bool:
    """
    Fuzzy-ish title match for dedup. Real postings tack junk onto the
    same role: 'Performance Test Engineer - ONLY W2' vs
    'Performance Test Engineer'. Exact match misses those. So we treat
    them as the same job if one normalized title contains the other,
    OR they share a long common prefix (first 4+ significant words).
    """
    if a == b:
        return True
    if not a or not b:
        return False
    # containment: shorter title fully inside the longer one
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter and shorter in longer:
        return True
    # strong shared prefix (first 4 words)
    aw, bw = a.split(), b.split()
    n = 4
    if len(aw) >= n and len(bw) >= n and aw[:n] == bw[:n]:
        return True
    # word-set containment: all words of shorter appear in longer AND shorter
    # is at least 75% of longer's word count — prevents "AI Engineer" (2 words)
    # from merging into "Senior AI Platform Engineer" (4 words, 50%) while still
    # catching "Agentic AI Engineer" vs "Agentic AI Platform Engineer" (3/4 = 75%)
    sw, lw = (set(aw), set(bw)) if len(aw) <= len(bw) else (set(bw), set(aw))
    sl, ll = (len(aw), len(bw)) if len(aw) <= len(bw) else (len(bw), len(aw))
    if len(sw) >= 2 and sw.issubset(lw) and sl / ll >= 0.75:
        return True
    return False


_AGGREGATORS = {"adzuna.com", "ziprecruiter.com", "indeed.com", "glassdoor.com",
                "linkedin.com", "monster.com", "dice.com", "careerbuilder.com"}


def _is_aggregator(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip("www.")
        return any(host == a or host.endswith("." + a) for a in _AGGREGATORS)
    except Exception:
        return False


def _dedup(jobs: list[dict], seen_urls: set, seen_keys: set) -> tuple[list, dict]:
    """
    Two-layer dedup:
      - across-run: drop if url already in seen_urls (job_decisions.json)
      - cross-source/in-run: collapse same company + matching title
        (uses _titles_match to handle trailing junk like '- ONLY W2')
    Returns (kept_jobs, stats). Kept jobs gain a 'seen_on' list of sources.
    """
    kept = []
    stats = {"already_seen": 0, "merged_dupes": 0}

    for job in jobs:
        url = (job.get("url") or "").strip()
        company = (job.get("company") or "").strip().lower()
        norm = _norm_title(job.get("title", ""))

        # across-run: seen in a prior run
        if url and url in seen_urls:
            stats["already_seen"] += 1
            continue

        # cross-source / in-run duplicate: same company + matching title
        merged = False
        for existing in kept:
            if existing["_company"] == company and _titles_match(existing["_norm"], norm):
                src = job.get("source", "?")
                if src not in existing["seen_on"]:
                    existing["seen_on"].append(src)
                # prefer a direct company URL over an aggregator redirect
                incoming_url = (job.get("url") or "").strip()
                if incoming_url and _is_aggregator(existing.get("url", "")) and not _is_aggregator(incoming_url):
                    existing["url"] = incoming_url
                stats["merged_dupes"] += 1
                merged = True
                break
        if merged:
            continue

        job = dict(job)                       # copy so we don't mutate caller's
        job["seen_on"] = [job.get("source", "?")]
        job["_company"] = company             # internal helper fields
        job["_norm"] = norm
        kept.append(job)
        if url:
            seen_keys.add(url)

    # strip internal helper fields before returning
    for j in kept:
        j.pop("_company", None)
        j.pop("_norm", None)

    return kept, stats


def _score_one(title: str, desc: str) -> tuple[int, list, str]:
    """Score a single job. Returns (score, matched_keywords, track)."""
    text = f"{title} {desc}".lower()
    t = title.lower()
    score = 0
    matched = []

    # LoadRunner in TITLE — the jackpot
    if any(kw in t for kw in LOADRUNNER_PRIORITY):
        score += 50
        matched.append("LoadRunner-in-title")
    # AI in title — strong boost (count once)
    for kw in AI_TITLE_PRIORITY:
        if kw in t:
            score += 20
            matched.append(f"AI-title:{kw}")
            break
    # COBOL in title — last-resort floor
    if any(kw in t for kw in COBOL_KW):
        score += 2
        matched.append("COBOL-in-title")

    # LoadRunner anywhere in body
    for kw in LOADRUNNER_PRIORITY:
        if kw in text and "LoadRunner-in-title" not in matched:
            score += 50
            matched.append(kw)
            break

    # JMeter-only penalty (JMeter but no LoadRunner = not Hans's core strength)
    if "jmeter" in text and not any(kw in text for kw in LOADRUNNER_PRIORITY):
        score -= 30
        matched.append("jmeter-only-penalty")

    # Performance high-value
    for kw in PERF_HIGH:
        if kw in text and kw not in matched:
            score += 35
            matched.append(kw)
    # AI high-value
    for kw in AI_HIGH:
        if kw in text and kw not in matched:
            score += 20
            matched.append(kw)
    # Bonus stack
    for kw in PERF_BONUS:
        if kw in text and kw not in matched:
            score += 3
            matched.append(kw)
    # QA title priority
    for kw in QA_TITLE_PRIORITY:
        if kw in t and kw not in matched:
            score += 20
            matched.append(f"QA-title:{kw}")
            break
    # QA high-value body keywords
    for kw in QA_HIGH:
        if kw in text and kw not in matched:
            score += 15
            matched.append(kw)
    # QA bonus stack
    for kw in QA_BONUS:
        if kw in text and kw not in matched:
            score += 5
            matched.append(kw)
    # COBOL — rare skill, score meaningfully
    for kw in COBOL_KW:
        if kw in text and kw not in matched:
            score += 20
            matched.append(kw)

    # Hard drops — return -200 immediately, no further scoring
    if any(kw in t for kw in JUNIOR_TITLE):
        return -200, ["hard-drop:junior/entry"], "Skip"
    if any(kw in t for kw in SENIOR_TITLE):
        return -200, ["hard-drop:overseniority"], "Skip"
    if _TRAVEL_RE.search(desc):
        return -200, ["hard-drop:heavy-travel"], "Skip"

    # AI experience gates — description signals Hans isn't there yet
    if _AI_YEARS_RE.search(desc):
        score -= 30
        matched.append("ai-years-gate-penalty")
    if _AI_PROVEN_TRACK_RE.search(desc) and _AI_GENAI_SIGNAL_RE.search(desc):
        score -= 30
        matched.append("ai-proven-track-penalty")

    # Track label for display — order matters (most specific first)
    if any(kw in text for kw in LOADRUNNER_PRIORITY) or any(kw in text for kw in PERF_HIGH):
        track = "LoadRunner / Performance"
    elif any(kw in text for kw in AI_HIGH) or any(kw in t for kw in AI_TITLE_PRIORITY):
        track = "AI Hybrid"
    elif any(kw in text for kw in COBOL_KW):
        track = "COBOL / Mainframe"
    elif any(kw in t for kw in QA_TITLE_PRIORITY) or any(kw in text for kw in QA_HIGH):
        track = "QA / Test Engineering"
    else:
        track = "Other"

    return score, sorted(set(matched)), track


def _load_seen_urls() -> set:
    """Load URLs already decided on, from job_decisions.json (across-run dedup)."""
    path = DECISIONS_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # job_decisions.json may be: a list of records, a dict with a
        # "decisions" key, or a dict keyed by date strings (each value a list)
        if isinstance(data, list):
            records = data
        elif "decisions" in data:
            records = data["decisions"]
        else:
            # date-keyed format: {"2026-04-23": [{...}, ...], ...}
            records = [r for v in data.values() if isinstance(v, list) for r in v]
        return {r.get("url") for r in records if isinstance(r, dict) and r.get("url")}
    except Exception:
        return set()   # no file yet (or unreadable) -> nothing seen before


def score_results(state=None, min_score: int = 1) -> dict:
    """
    Agent tool. Dedup -> score -> salary-annotate -> rank the jobs
    currently in state.jobs. Writes the cleaned, ranked list back into
    state.jobs so analyze_fit and the report use the good version.

    min_score: drop anything below this after scoring (default 1 = drop
    zero/negative). The agent can pass a higher bar if a goal demands it.
    """
    if state is None or not getattr(state, "jobs", None):
        return {"ok": True, "tool": "score_results", "count": 0,
                "note": "No jobs to score."}

    raw = state.jobs
    seen_urls = _load_seen_urls()

    # 1. DEDUP
    deduped, dstats = _dedup(raw, seen_urls, set())

    # 2. SCORE + 3. SALARY
    scored = []
    ai_senior_dropped = 0
    for job in deduped:
        s, matched, track = _score_one(job.get("title", ""), job.get("description", ""))
        job["score"] = s
        job["matched_keywords"] = matched
        job["track"] = track

        # AI-track seniority gate (fires after track is known, title-only)
        if s > -200 and ai_seniority_drop(job.get("title", ""), track):
            job["score"] = -200
            job["matched_keywords"] = ["hard-drop:ai-track-overseniority"]
            ai_senior_dropped += 1

        # salary: parse from description, enforce hard floors
        desc_text = job.get("description", "")
        contract_hr, base_annual = _parse_salary(desc_text)
        if evaluate_salary:
            if "LoadRunner" in track or "Performance" in track:
                track_key = "loadrunner"
            elif "COBOL" in track:
                track_key = "cobol"
            elif "QA" in track:
                track_key = "qa_testing"
            else:
                track_key = "ai_hybrid"
            sal = evaluate_salary(track_key, base_annual=base_annual, contract_hr=contract_hr)
            job["salary_note"] = sal["note"]
            if sal.get("hard") and sal.get("verdict") == "below_floor":
                job["score"] = -200
                job["matched_keywords"] = [f"hard-drop:salary<floor({contract_hr or base_annual})"]
        scored.append(job)

    # filter by min_score
    before = len(scored)
    scored = [j for j in scored if j["score"] >= min_score]
    dropped_low = before - len(scored)

    # 4. RANK
    scored.sort(key=lambda j: j["score"], reverse=True)

    # write the clean version back into state
    state.jobs = scored

    note = (f"{len(scored)} ranked "
            f"(seen-before {dstats['already_seen']}, "
            f"merged {dstats['merged_dupes']}, "
            f"ai-senior dropped {ai_senior_dropped}, "
            f"low-score dropped {dropped_low})")
    return {"ok": True, "tool": "score_results", "count": len(scored),
            "note": note, "jobs": scored}


# ------------------------------------------------------------
# QUICK SELF-TEST  (python tools/score.py)
# ------------------------------------------------------------
if __name__ == "__main__":
    class FakeState:
        def __init__(self, jobs): self.jobs = jobs

    AI_DESC  = "Build LLM-based agentic workflows, RAG pipelines, generative AI systems"
    PERF_DESC = "LoadRunner performance testing, AppDynamics, performance engineering"

    jobs = [
        # AI-track seniority filter cases
        {"title": "Senior AI Engineer",              "company": "Co A", "url": "http://a/1",
         "source": "Adzuna", "description": AI_DESC},   # KEEP — "senior" alone not dropped
        {"title": "Principal AI Engineer",           "company": "Co B", "url": "http://a/2",
         "source": "Adzuna", "description": AI_DESC},   # DROP  — principal on AI track
        {"title": "Staff Machine Learning Engineer", "company": "Co C", "url": "http://a/3",
         "source": "Adzuna", "description": AI_DESC},   # DROP  — staff on AI track
        {"title": "AI Engineer II",                  "company": "Co D", "url": "http://a/4",
         "source": "Adzuna", "description": AI_DESC},   # KEEP
        {"title": "Agentic AI Engineer",             "company": "Co E", "url": "http://a/5",
         "source": "Adzuna", "description": AI_DESC},   # KEEP
        {"title": "Lead AI Systems Engineer",        "company": "Co F", "url": "http://a/6",
         "source": "Adzuna", "description": AI_DESC},   # DROP  — lead on AI track
        {"title": "AI Architect",                    "company": "Co G", "url": "http://a/7",
         "source": "Adzuna", "description": AI_DESC},   # DROP  — architect + AI signal
        {"title": "Solutions Architect",             "company": "Co H", "url": "http://a/8",
         "source": "Adzuna", "description": PERF_DESC}, # KEEP  — architect, no AI signal
        # Performance-track cases — AI seniority filter must NOT fire
        {"title": "Senior Performance Engineer",     "company": "Co I", "url": "http://a/9",
         "source": "Adzuna", "description": PERF_DESC}, # KEEP  — perf track, untouched
        {"title": "Lead Performance Engineer",       "company": "Co J", "url": "http://a/10",
         "source": "Adzuna", "description": PERF_DESC}, # KEEP  — perf track, untouched
    ]

    st = FakeState(jobs)
    result = score_results(st)
    print(result["note"])
    print()

    # Rebuild lookup from all jobs (scored includes only kept; re-score individually for report)
    all_titles = {j["title"] for j in jobs}
    kept_titles = {j["title"] for j in st.jobs}
    print(f"  {'RESULT':<6}  {'TITLE':<40}  TRACK")
    print(f"  {'------':<6}  {'-----':<40}  -----")
    for j in jobs:
        verdict = "KEEP " if j["title"] in kept_titles else "DROP "
        s, _, track = _score_one(j["title"], j["description"])
        ai_drop = ai_seniority_drop(j["title"], track)
        display_track = track if not ai_drop else f"{track} [ai-senior]"
        print(f"  {verdict:<6}  {j['title'][:40]:<40}  {display_track}")
