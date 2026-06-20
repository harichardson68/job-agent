"""
tools/search.py
===============
Agent-callable job search tools.

KEY DIFFERENCE FROM job_search.py:
  The pipeline's search_adzuna() is hardwired — fixed LoadRunner
  queries, built-in filtering, internal scoring. Great for a pipeline,
  wrong for an agent.

  The AGENT needs to DECIDE the query (LoadRunner? AI? broaden?) and
  DECIDE when to filter/score (separate tools). So these tools are
  thin: take a query, fetch, return clean raw results. Scoring and
  fit analysis live in their own tools the agent calls when it judges
  the time is right.

Each function returns: list[dict] with a consistent shape:
    {source, title, company, url, posted, description, salary}
No scoring here — the agent's score tool handles that later.
"""

import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()

ADZUNA_APP_ID  = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
USAJOBS_API_KEY = os.environ.get("USAJOBS_API_KEY", "")
USAJOBS_EMAIL   = os.environ.get("USAJOBS_EMAIL", "")

ADZUNA_URL  = "https://api.adzuna.com/v1/api/jobs/us/search/1"
SERPER_URL  = "https://google.serper.dev/search"
USAJOBS_URL = "https://data.usajobs.gov/api/search"

# Location words that the planner might append to a query but that break
# Adzuna's AND-match (Adzuna requires every query word in the body text,
# and most remote postings don't use "remote" verbatim).  Strip them
# before sending to the API; location filtering runs separately.
_ADZUNA_LOCATION_RE = re.compile(
    r"\b(remote|wfh|work\s+from\s+home|work-from-home|telecommute|telecommuting"
    r"|virtual|distributed|anywhere)\b",
    re.I,
)

# ------------------------------------------------------------
# LOCATION POLICY
# ------------------------------------------------------------
# Hans accepts:
#   (a) Remote — but US-based only (no foreign postings), OR
#   (b) Hybrid/onsite within ~30 min of Lee's Summit / KC metro.
# Everything else is filtered out.
#
# This is keyword/heuristic matching on title+description+location
# text. It's not perfect — borderline cases get KEPT and flagged so
# Hans decides, rather than silently dropped.

REMOTE_SIGNALS = [
    "remote", "work from home", "wfh", "telecommute",
    "anywhere", "distributed", "virtual", "home-based", "home based",
]

# Explicit on-site signals. If present without a remote or KC signal,
# the posting is onsite somewhere non-KC → reject.
ONSITE_SIGNALS = ["on-site", "onsite", "on site", "in-office", "in office"]

# Signals that a listing has expired / been removed — drop immediately.
_EXPIRED_RE = re.compile(
    r'no longer available|job has expired|listing has expired|'
    r'position has been filled|this job is no longer|'
    r'job is closed|posting has been removed|posting is no longer|'
    r'sorry this job is no longer|similar jobs shown below',
    re.I,
)

# Location field values too vague to be authoritative ("US", "Remote", etc.).
# When the location field matches this, fall back to body-text parsing.
_AMBIGUOUS_LOC = re.compile(
    r"^\s*(united\s+states?|u\.?s\.?a?\.?|us\s+remote|remote|work\s+from\s+home|"
    r"wfh|anywhere|virtual|distributed|n/?a|not\s+specified|flexible|nationwide"
    r"|north\s+america|multiple\s+locations)\s*$",
    re.I,
)

# If a "remote" role also names a foreign country/region, it's not US-remote.
NON_US_SIGNALS = [
    "united kingdom", "uk only", "canada", "canadian", "ontario",
    "india", "bangalore", "hyderabad", "pune", "emea", "apac",
    "europe", "european", "germany", "ireland", "australia",
    "philippines", "manila", "singapore", "mexico", "brazil",
    "latam", "poland", "romania", "ukraine", "eu-based", "eu based",
]

# KC-metro commutable signals (both KS and MO sides, ~30 min of Lee's Summit).
KC_SIGNALS = [
    "kansas city", "lee's summit", "lees summit", "overland park",
    "olathe", "lenexa", "shawnee, ks", "shawnee mission", "leawood", "mission, ks",
    "independence, mo", "blue springs", "raytown", "grandview, mo",
    "north kansas city", "liberty, mo", "gladstone, mo", "merriam",
    "prairie village", "kcmo", "kck", " kc ", "jackson county, mo",
    "johnson county, ks",
]

# Canadian postal code (letter-digit-letter space digit-letter-digit) —
# this format doesn't exist in US ZIP codes, so it's an unambiguous
# non-US signal even when no country name appears in the text.
_CA_POSTAL_RE = re.compile(r"\b[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d\b")

# Canadian province abbreviations, as a distinct token after a comma
# (e.g. "North York, ON"). None of these collide with US state codes,
# so matching is safe without a full word list of province names.
_CA_PROVINCE_RE = re.compile(r",\s*(on|bc|ab|qc|mb|sk|ns|nb|pe|nl|yt|nt|nu)\b", re.I)

# Non-US Indeed locale subdomains (ph.indeed.com, it.indeed.com, ...).
# The US site is indeed.com / www.indeed.com with no two-letter country
# prefix, so any two-letter subdomain means a foreign job board.
_NON_US_INDEED_RE = re.compile(r"^https?://([a-z]{2})\.indeed\.com", re.I)


def classify_location(title: str, desc: str, location: str = "", url: str = "") -> dict:
    """
    Decide whether a posting clears Hans's location rule.

    Returns:
        {
          "verdict": "remote_us" | "kc_local" | "reject" | "flag",
          "keep":    bool,
          "note":    str,
        }

    Key invariant: when the `location` field explicitly names a specific
    city/state that isn't KC (e.g. "Hybrid in Atlanta, GA"), we trust it
    as ground truth and reject — we do NOT let body-text "remote" override
    an explicit non-KC onsite/hybrid location.
    """
    loc  = location.strip().lower()
    body = f"{title} {desc}".lower()

    # KC metro wins immediately — commutable onsite/hybrid is fine.
    if any(s in loc for s in KC_SIGNALS) or any(s in body for s in KC_SIGNALS):
        return {"verdict": "kc_local", "keep": True, "note": "KC-metro commutable."}

    # Hard non-US signals that don't rely on a country name appearing in the
    # text — a foreign job-board subdomain or a Canadian postal code/province
    # abbreviation. These override any "remote" mention in the body, since
    # body text often doesn't name the country at all (e.g. "Ortigas" with
    # no "Philippines", or "ON" instead of "Ontario").
    if _NON_US_INDEED_RE.match(url or ""):
        return {"verdict": "reject", "keep": False,
                "note": "Non-US Indeed locale (foreign job board)."}
    if _CA_POSTAL_RE.search(location) or _CA_PROVINCE_RE.search(location):
        return {"verdict": "reject", "keep": False,
                "note": f"Canadian location signal ({location.strip()[:40]})."}

    # If the location field is specific (non-empty, non-ambiguous), trust it
    # over body-text signals — prevents "Hybrid in Atlanta, GA" + description
    # mention of "remote" from being misclassified as US-remote.
    if loc and not _AMBIGUOUS_LOC.match(loc):
        loc_is_remote = any(s in loc for s in REMOTE_SIGNALS)
        if loc_is_remote:
            # "Remote in Olympia, WA" = geographically restricted.
            # "Remote, TX" = genuinely remote, state is the employer's address.
            # Only reject when "remote in" precedes a city/state (comma present).
            if "remote in" in loc and "," in loc:
                return {"verdict": "reject", "keep": False,
                        "note": f"Location-restricted remote ({location.strip()[:40]}) — not US-wide."}
            is_foreign = any(s in f"{body} {loc}" for s in NON_US_SIGNALS)
            if not is_foreign:
                return {"verdict": "remote_us", "keep": True, "note": "US remote."}
            return {"verdict": "reject", "keep": False,
                    "note": "Remote but appears non-US."}
        # Specific non-remote, non-KC location — but many remote jobs list the
        # company's physical office as location. Check body text before rejecting.
        is_foreign = any(s in f"{body} {loc}" for s in NON_US_SIGNALS)
        if not is_foreign and any(s in body for s in REMOTE_SIGNALS):
            return {"verdict": "remote_us", "keep": True,
                    "note": f"Remote signal in description (location {location.strip()[:30]} may be company HQ)."}
        return {"verdict": "reject", "keep": False,
                "note": f"Onsite outside KC ({location.strip()[:40]})."}

    # Location field is empty or too vague — fall back to body-text heuristics.
    is_remote  = any(s in body for s in REMOTE_SIGNALS)
    is_foreign = any(s in f"{body} {loc}" for s in NON_US_SIGNALS)
    is_onsite  = any(s in f"{body} {loc}" for s in ONSITE_SIGNALS)

    if is_remote and not is_foreign:
        return {"verdict": "remote_us", "keep": True, "note": "US remote."}
    if is_remote and is_foreign:
        return {"verdict": "reject", "keep": False,
                "note": "Remote but appears non-US."}
    # Explicit on-site signal with no remote/KC → onsite somewhere non-KC.
    if is_onsite:
        return {"verdict": "reject", "keep": False,
                "note": "Onsite (no remote or KC signal)."}
    # No positive signal at all — drop conservatively.
    # Remote jobs always advertise "remote"; absence of the word means risk.
    return {"verdict": "flag", "keep": False,
            "note": "Location unclear — no remote or KC signal, dropped."}


def search_adzuna(query: str, results_per_page: int = 20,
                  location_filter: bool = True) -> dict:
    """
    Agent tool: search Adzuna for `query`.

    The AGENT supplies the query — that's the whole point. It can search
    "loadrunner performance engineer" on one iteration and pivot to
    "AI systems engineer remote" on the next, based on what it found.

    location_filter: if True, applies Hans's rule (US-remote OR
    KC-commutable). Borderline/unclear postings are kept + flagged.

    Returns a result dict the agent/loop can reason over:
        {
          "ok": bool, "tool": "search_adzuna", "query": str,
          "count": int, "jobs": list[dict], "note": str,
        }
    Each job carries a "location_note" so the agent can see WHY it
    was kept (US remote / KC local / flagged).
    """
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        return {
            "ok": False, "tool": "search_adzuna", "query": query,
            "count": 0, "jobs": [],
            "note": "Adzuna skipped — ADZUNA_APP_ID/KEY not set in environment.",
        }

    # Strip location words before sending — Adzuna AND-matches every query
    # token against body text, so "loadrunner remote" returns 0 results.
    clean_query = re.sub(r"\s+", " ", _ADZUNA_LOCATION_RE.sub("", query)).strip()
    if not clean_query:
        clean_query = query  # fallback: send as-is if stripping ate everything

    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "results_per_page": results_per_page,
        "what": clean_query,
        "sort_by": "date",
        "max_days_old": 7,
        "content-type": "application/json",
    }

    try:
        resp = requests.get(ADZUNA_URL, params=params, timeout=15)
        if resp.status_code != 200:
            return {
                "ok": False, "tool": "search_adzuna", "query": query,
                "count": 0, "jobs": [],
                "note": f"Adzuna returned status {resp.status_code}.",
            }

        items = resp.json().get("results", [])
        jobs = []
        filtered_loc = 0

        for item in items:
            title   = item.get("title", "")
            desc    = item.get("description", "")
            url_job = item.get("redirect_url", "")
            posted  = item.get("created", "")
            company = item.get("company", {}).get("display_name", "N/A")

            if _EXPIRED_RE.search(title) or _EXPIRED_RE.search(desc):
                continue

            # Adzuna exposes a location label we can use for the gate
            loc_label = item.get("location", {}).get("display_name", "")

            if location_filter:
                loc = classify_location(title, desc, loc_label, url_job)
                if not loc["keep"]:
                    filtered_loc += 1
                    continue
                location_note = loc["note"]
            else:
                location_note = "location filter off"

            jobs.append({
                "source": "Adzuna",
                "title": title,
                "company": company,
                "url": url_job,
                "posted": posted,
                "description": desc[:500],
                "salary": "",   # Adzuna sometimes provides salary_min/max; wire later if useful
                "location": loc_label,
                "location_note": location_note,
            })

        stripped = f" [sent as: '{clean_query}']" if clean_query != query else ""
        note = (f"{len(jobs)} jobs (filtered {filtered_loc} on location){stripped}"
                if location_filter else f"{len(jobs)} jobs{stripped}")
        return {
            "ok": True, "tool": "search_adzuna", "query": query,
            "count": len(jobs), "jobs": jobs, "note": note,
        }

    except Exception as e:
        return {
            "ok": False, "tool": "search_adzuna", "query": query,
            "count": 0, "jobs": [],
            "note": f"Adzuna error: {e}",
        }


# Serper uses Google's search index — no dedicated /jobs endpoint on free plans.
# Strategy: target individual job-posting URL patterns via site: operator so
# organic results are actual listings (not aggregator landing pages), then
# parse title/company/location from Google's structured title string.
_SERPER_JOB_SITES = (
    "site:dice.com/job-detail OR "
    "site:indeed.com/viewjob OR "
    "site:jobs.lever.co OR "
    "site:boards.greenhouse.io OR "
    "site:jobs.ashbyhq.com OR "
    "site:myworkdayjobs.com OR "
    "site:jobs.smartrecruiters.com"
)

# URL fragments that mark an individual job posting (not a list page)
_JOB_POST_PATTERNS = [
    "dice.com/job-detail/",
    "indeed.com/viewjob",
    "jobs.lever.co/",
    "boards.greenhouse.io/",
    "jobs.ashbyhq.com/",
    "/job/",                  # Workday's job-vs-listing-page marker. Verified
                              # against 6 real postings (Accenture, Intel,
                              # Salesforce, Cloudera, Samsung, Rolls-Royce) —
                              # job pages always contain "/job/" in the path;
                              # listing pages never do.
    "jobs.smartrecruiters.com/",
]

# Employment-type / location tags that get appended to job titles on
# aggregator sites.  They are NOT company names — filter them out of the
# company field (and from the end of parsed title strings).
_JUNK_PART_RE = re.compile(
    r"^(only\s+w2|w2\s+only|c2c|remote|contract|full[- ]?time"
    r"|onsite|on-site|on\s+site|hybrid|usa|us)$",
    re.I,
)

# Same tokens as a trailing suffix on a title string (e.g. "Sr Eng - ONLY W2")
_JUNK_SUFFIX_RE = re.compile(
    r"[\s\-–—|,]+(only\s+w2|w2\s+only|c2c|remote|contract|full[- ]?time"
    r"|onsite|on-site|on\s+site|hybrid|usa|us)\s*$",
    re.I,
)


def _scrub(s: str) -> str:
    """Strip trailing junk suffixes from a title or company string."""
    prev = None
    while prev != s:
        prev = s
        s = _JUNK_SUFFIX_RE.sub("", s).strip()
    return s


_VERIFY_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def _job_still_active(url: str) -> bool:
    """
    Return False if the job URL is definitively gone (HTTP 410).
    Only checks Dice URLs — other boards don't reliably return 410.
    On any error or timeout, returns True (benefit of the doubt).
    """
    if "dice.com" not in url:
        return True
    try:
        resp = requests.head(url, headers=_VERIFY_HEADERS, timeout=5, allow_redirects=True)
        if resp.status_code == 410:
            return False
        # Some servers don't support HEAD — fall back to GET with streaming
        if resp.status_code == 405:
            resp = requests.get(url, headers=_VERIFY_HEADERS, timeout=5, stream=True)
            resp.close()
            return resp.status_code != 410
    except Exception:
        pass
    return True


def _parse_serper_job(item: dict) -> dict | None:
    """
    Convert one Serper organic result into a job dict.
    Returns None if the URL doesn't look like an individual posting.
    """
    link = item.get("link", "")
    if not any(p in link for p in _JOB_POST_PATTERNS):
        return None

    title_raw = item.get("title", "")
    snippet   = item.get("snippet", "")
    title     = title_raw
    company   = ""
    location  = ""

    if "dice.com" in link:
        # "Job Title - Company Name - Location | Dice.com"  (pipe or just " - Dice")
        clean = re.sub(r"\s*(\||-)\s*Dice(\.com)?.*$", "", title_raw, flags=re.I).strip()
        parts = [p.strip() for p in clean.split(" - ")]
        # Drop pure-junk fragments (ONLY W2, C2C, etc.) before positional assignment.
        # Dice format: Title - Company - Location (3 parts), or Title - Company (2 parts).
        # After stripping the source marker, the second meaningful part is always the
        # company (not a location), because Dice shows location as a 3rd part.
        meaningful = [p for p in parts if not _JUNK_PART_RE.match(p)]
        title    = _scrub(meaningful[0]) if meaningful else _scrub(parts[0])
        company  = meaningful[1] if len(meaningful) >= 2 else ""
        location = meaningful[2] if len(meaningful) >= 3 else ""
    elif "indeed.com" in link:
        # "Job Title - Location - Indeed.com" (company not in Google's title)
        clean = re.sub(r"\s*-\s*Indeed(\.com)?.*$", "", title_raw, flags=re.I).strip()
        parts = [p.strip() for p in clean.split(" - ")]
        title    = _scrub(parts[0])
        location = parts[-1] if len(parts) > 1 else ""
    elif "lever.co" in link:
        # "Job Title - Company - Lever"  OR  "Job Title at Company"
        clean = re.sub(r"\s*-?\s*Lever\s*$", "", title_raw, flags=re.I).strip()
        if " at " in clean:
            t, c = clean.split(" at ", 1)
            title, company = _scrub(t.strip()), _scrub(c.strip())
        else:
            parts = [p.strip() for p in clean.split(" - ")]
            meaningful = [p for p in parts if not _JUNK_PART_RE.match(p)]
            title   = _scrub(meaningful[0]) if meaningful else _scrub(parts[0])
            company = meaningful[1] if len(meaningful) >= 2 else ""
    elif "greenhouse.io" in link or "ashbyhq.com" in link:
        # "Job Title | Company" or "Job Title at Company"
        if " | " in title_raw:
            t, c = [p.strip() for p in title_raw.split(" | ", 1)]
            title, company = _scrub(t), _scrub(c)
        elif " at " in title_raw:
            t, c = title_raw.split(" at ", 1)
            title, company = _scrub(t.strip()), _scrub(c.strip())
    elif "myworkdayjobs.com" in link:
        # Google's title metadata is just "Job Title - Myworkdayjobs.com" —
        # no company name in it at all. Company comes from the URL subdomain
        # instead (e.g. accenture.wd103.myworkdayjobs.com -> "Accenture").
        title = re.sub(r"\s*-\s*Myworkdayjobs(\.com)?\s*$", "", title_raw, flags=re.I).strip()
        title = _scrub(title)
        m = re.search(r"https?://([a-z0-9_-]+)\.wd\d+\.myworkdayjobs\.com", link, flags=re.I)
        if m:
            company = m.group(1).replace("-", " ").replace("_", " ").title()
    elif "jobs.smartrecruiters.com" in link:
        # "{Company} is looking for a {Job Title} in {Location}" — confirmed
        # against one live example only, so this may not hold for every
        # posting's phrasing. Falls back to the raw title if it doesn't match.
        m = re.match(r"^(.*?)\s+is looking for an?\s+(.*?)\s+in\s+(.*)$", title_raw, flags=re.I)
        if m:
            company, title, location = m.group(1).strip(), _scrub(m.group(2).strip()), m.group(3).strip()

    # Drop if Google's cached snippet already shows the listing is gone.
    if _EXPIRED_RE.search(snippet) or _EXPIRED_RE.search(title_raw):
        return None

    return {
        "source": "Serper",
        "title": title,
        "company": company,
        "url": link,
        "posted": "",
        "description": snippet[:500],
        "salary": "",
        "location": location,
        "location_note": "",
    }


def search_serper(query: str = "", num: int = 20,
                  location_filter: bool = True) -> dict:
    """
    Agent tool: search Google (via Serper) for individual job postings.

    Targets site-specific URL patterns (Dice, Indeed viewjob, Lever,
    Greenhouse) so results are actual listings, not aggregator pages.
    Different source from Adzuna — running both on the same query
    generates cross-source dupes that score.py's dedup collapses.

    Returns the same shape as search_adzuna.
    """
    if not query or not query.strip():
        return {
            "ok": False, "tool": "search_serper", "query": query,
            "count": 0, "jobs": [],
            "note": "search_serper requires a query — planner must supply one.",
        }

    if not SERPER_API_KEY:
        return {
            "ok": False, "tool": "search_serper", "query": query,
            "count": 0, "jobs": [],
            "note": "Serper skipped — SERPER_API_KEY not set in environment.",
        }

    search_q = f"{query} ({_SERPER_JOB_SITES})"
    try:
        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": search_q, "num": num, "tbs": "qdr:w"},
            timeout=15,
        )
        if resp.status_code != 200:
            return {
                "ok": False, "tool": "search_serper", "query": query,
                "count": 0, "jobs": [],
                "note": f"Serper returned status {resp.status_code}.",
            }

        items = resp.json().get("organic", [])
        jobs = []
        filtered_loc = 0

        for item in items:
            job = _parse_serper_job(item)
            if job is None:
                continue

            if not _job_still_active(job["url"]):
                continue

            if location_filter:
                loc = classify_location(job["title"], job["description"], job["location"], job["url"])
                if not loc["keep"]:
                    filtered_loc += 1
                    continue
                job["location_note"] = loc["note"]
            else:
                job["location_note"] = "location filter off"

            jobs.append(job)

        note = f"{len(jobs)} jobs (filtered {filtered_loc} on location)" \
               if location_filter else f"{len(jobs)} jobs"
        return {
            "ok": True, "tool": "search_serper", "query": query,
            "count": len(jobs), "jobs": jobs, "note": note,
        }

    except Exception as e:
        return {
            "ok": False, "tool": "search_serper", "query": query,
            "count": 0, "jobs": [],
            "note": f"Serper error: {e}",
        }


def search_usajobs(query: str, results_per_page: int = 20,
                   location_filter: bool = True) -> dict:
    """
    Agent tool: search USAJOBS.gov — the official US federal government
    job board API. No scraping, no ToS gray area: this is a documented
    public API (data.usajobs.gov) that just needs a free API key + email
    in the request headers.

    Most relevant to the COBOL/Mainframe track (federal agencies still run
    a lot of COBOL), but covers any federal IT/QA/AI role too — the agent
    decides the query like with the other search tools.

    Location nuance: USAJOBS distinguishes RemoteIndicator (genuinely
    location-independent — rare) from TeleworkEligible (hybrid, but still
    tied to a fixed duty station — common). Only RemoteIndicator=True
    short-circuits straight to "remote_us"; TeleworkEligible alone still
    goes through the normal classify_location() check against the listed
    duty station, same as Hans's KC-or-remote rule for every other source.

    Returns the same shape as search_adzuna/search_serper.
    """
    if not USAJOBS_API_KEY or not USAJOBS_EMAIL:
        return {
            "ok": False, "tool": "search_usajobs", "query": query,
            "count": 0, "jobs": [],
            "note": "USAJOBS skipped — USAJOBS_API_KEY/USAJOBS_EMAIL not set in environment.",
        }

    try:
        resp = requests.get(
            USAJOBS_URL,
            headers={
                "Authorization-Key": USAJOBS_API_KEY,
                "User-Agent": USAJOBS_EMAIL,
                "Host": "data.usajobs.gov",
            },
            params={"Keyword": query, "ResultsPerPage": results_per_page},
            timeout=15,
        )
        if resp.status_code != 200:
            return {
                "ok": False, "tool": "search_usajobs", "query": query,
                "count": 0, "jobs": [],
                "note": f"USAJOBS returned status {resp.status_code}.",
            }

        items = resp.json().get("SearchResult", {}).get("SearchResultItems", [])
        jobs = []
        filtered_loc = 0

        for item in items:
            d = item.get("MatchedObjectDescriptor", {})
            details = d.get("UserArea", {}).get("Details", {})

            title    = d.get("PositionTitle", "")
            company  = d.get("OrganizationName", "") or d.get("DepartmentName", "")
            apply_uris = d.get("ApplyURI") or []
            url_job  = d.get("PositionURI", "") or (apply_uris[0] if apply_uris else "")
            posted   = d.get("PublicationStartDate", "")
            loc_label = d.get("PositionLocationDisplay", "")
            desc     = details.get("JobSummary", "") or ""

            remuneration = (d.get("PositionRemuneration") or [{}])[0]
            sal_low  = remuneration.get("MinimumRange", "")
            sal_high = remuneration.get("MaximumRange", "")
            interval = remuneration.get("Description", "")
            salary = f"${sal_low}-${sal_high} {interval}".strip() if sal_low else ""

            if location_filter:
                if details.get("RemoteIndicator") is True:
                    location_note = "US remote (USAJOBS RemoteIndicator)."
                else:
                    loc = classify_location(title, desc, loc_label, url_job)
                    if not loc["keep"]:
                        filtered_loc += 1
                        continue
                    location_note = loc["note"]
            else:
                location_note = "location filter off"

            jobs.append({
                "source": "USAJOBS",
                "title": title,
                "company": company,
                "url": url_job,
                "posted": posted,
                "description": desc[:500],
                "salary": salary,
                "location": loc_label,
                "location_note": location_note,
            })

        note = (f"{len(jobs)} jobs (filtered {filtered_loc} on location)"
                if location_filter else f"{len(jobs)} jobs")
        return {
            "ok": True, "tool": "search_usajobs", "query": query,
            "count": len(jobs), "jobs": jobs, "note": note,
        }

    except Exception as e:
        return {
            "ok": False, "tool": "search_usajobs", "query": query,
            "count": 0, "jobs": [],
            "note": f"USAJOBS error: {e}",
        }


# ------------------------------------------------------------
# QUICK SELF-TEST  (python tools/search.py)
# Tests the location classifier directly (no API keys needed),
# then exercises the live search path (graceful skip without keys).
# ------------------------------------------------------------
if __name__ == "__main__":
    print("=== Location classifier ===")
    cases = [
        ("Sr Performance Engineer", "Fully remote role, US-based team", "", ""),
        ("LoadRunner Consultant", "Remote position", "Bangalore, India", ""),
        ("Performance Test Lead", "Hybrid, 2 days onsite", "Overland Park, KS", ""),
        ("QA Engineer", "Onsite required", "Austin, TX", ""),
        ("AI Systems Engineer", "Remote within EMEA", "", ""),
        ("Perf Engineer", "Onsite", "Lee's Summit, MO", ""),
        ("DevOps Engineer", "No location given", "", ""),
        # Regression cases: foreign postings that slipped through before the
        # Indeed-locale / Canadian-postal-code checks were added.
        ("Senior SQA Engineer", "Hybrid-remote role", "Ortigas",
         "https://ph.indeed.com/viewjob?jk=abc"),
        ("Software Engineer (QA)", "Hybrid onsite", "North York, ON M3C 1Z3",
         "https://it.indeed.com/viewjob?jk=abc"),
    ]
    for title, desc, loc, url in cases:
        r = classify_location(title, desc, loc, url)
        flag = "KEEP" if r["keep"] else "DROP"
        print(f"  [{flag}] {r['verdict']:10} | {title[:28]:28} | {r['note']}")

    print("\n=== Serper title parser (no keys needed) ===")
    parser_cases = [
        # (url_fragment, raw_google_title, expected_title, expected_company)
        ("dice.com/job-detail/x",
         "Performance Test Engineer - ONLY W2 - Akshaya Inc | Dice.com",
         "Performance Test Engineer", "Akshaya Inc"),  # ONLY W2 filtered; real company recovered
        ("dice.com/job-detail/x",
         "Performance Tester - Black Rock Group - Phoenix, AZ, US | Dice.com",
         "Performance Tester", "Black Rock Group"),
        ("dice.com/job-detail/x",
         "Sr. Performance Engineer - ElevaIT Solutions - Hybrid in Atlanta, GA ... - Dice",
         "Sr. Performance Engineer", "ElevaIT Solutions"),
        ("dice.com/job-detail/x",
         "Senior Performance Test Engineer - INSPYR Solutions - Dice",
         "Senior Performance Test Engineer", "INSPYR Solutions"),
        ("indeed.com/viewjob?jk=abc",
         "Performance Test Engineer (LoadRunner) - Irving, TX - Indeed.com",
         "Performance Test Engineer (LoadRunner)", ""),
        ("jobs.lever.co/saviynt/abc",
         "Associate Principal SDET ( Performance Engineer) - Saviynt - Lever",
         "Associate Principal SDET ( Performance Engineer)", "Saviynt"),
    ]
    all_ok = True
    for url_frag, raw, exp_title, exp_company in parser_cases:
        job = _parse_serper_job({"link": f"https://{url_frag}", "title": raw, "snippet": ""})
        got_title   = job["title"]   if job else "NONE"
        got_company = job["company"] if job else "NONE"
        title_ok   = got_title   == exp_title
        company_ok = got_company == exp_company
        status = "OK" if (title_ok and company_ok) else "FAIL"
        if status == "FAIL":
            all_ok = False
        print(f"  [{status}] title={got_title!r:45} company={got_company!r}")
        if not title_ok:
            print(f"        expected title:   {exp_title!r}")
        if not company_ok:
            print(f"        expected company: {exp_company!r}")
    if all_ok:
        print("  all parser cases passed")

    print("\n=== Live Adzuna (needs keys) ===")
    r1 = search_adzuna("loadrunner performance engineer")
    print(f"  ok={r1['ok']}  {r1['note']}")
    for j in r1["jobs"][:3]:
        print(f"    - {j['title']} @ {j['company']} [{j.get('location_note','')}]")

    print("\n=== Live Serper (needs key) ===")
    r2 = search_serper("loadrunner performance engineer")
    print(f"  ok={r2['ok']}  {r2['note']}")
    for j in r2["jobs"][:3]:
        print(f"    - {j['title']} @ {j['company']} [{j.get('location_note','')}]")
