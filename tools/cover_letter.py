"""
tools/cover_letter.py
=====================
Agent tool: generate lightly-personalized cover letters for the top
scored jobs. Two base templates (Performance track / AI Hybrid track)
are pre-written with Hans's real background. One batched Claude call
personalizes each letter by:
  1. Filling in [Company] and [Title]
  2. Replacing the [PERSONALIZED] placeholder with one concrete sentence
     referencing 1-2 specific skills or technologies from the job description

One Claude call handles all letters — not one call per job.
Only generates for jobs already analyzed by analyze_fit (have a fit_tier)
and not tiered Weak (not worth applying to).
"""

import os
import re
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

CLAUDE_API_KEY        = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_MODEL_CAREER   = "claude-sonnet-4-6"
CLAUDE_MODEL_BRIDGE   = "claude-haiku-4-5-20251001"
CLAUDE_URL            = "https://api.anthropic.com/v1/messages"

_CAREER_TRACKS = {"LoadRunner / Performance", "AI Hybrid"}

# Max jobs to generate letters for (cost control)
COVER_LETTER_MAX = 10

# ──────────────────────────────────────────────────────────────
# BASE TEMPLATES
# ──────────────────────────────────────────────────────────────

_PERF_TEMPLATE = """Dear [Company] Hiring Team,

I am writing to express my strong interest in the [Title] position. With 14 years of expert-level LoadRunner/VuGen/LRE experience and a proven track record in enterprise performance engineering, I am confident I can deliver immediate value to your team. [PERSONALIZED]

Throughout my career I have led performance testing across complex enterprise platforms including SAP (9 years of load, stress, volume, and scalability testing across CRM, ERP, and BRIM modules), AWS/Kubernetes migrations, and large-scale web applications. I work closely with monitoring tools such as AppDynamics, Splunk, Prometheus, and Grafana to translate test results into actionable architectural improvements. I also hold an active Public Trust clearance from my federal contract work at USDA.

I am complementing my performance engineering depth with real production AI work — building agentic automation systems and pipelines using the Claude API and Python. I am excited to bring both deep performance expertise and a forward-looking perspective to [Company] and would welcome the opportunity to discuss how my background aligns with your needs.

Sincerely,
Hans Richardson
harichardson68@gmail.com"""

_AI_TEMPLATE = """Dear [Company] Hiring Team,

I am writing to express my strong interest in the [Title] position. With 24+ years of IT experience and a deliberate pivot into AI engineering backed by real production work, I bring a rare combination of deep technical foundation and hands-on AI delivery to this role. [PERSONALIZED]

I have built AI systems in production — including a multi-agent job search platform using the Claude API, agentic orchestration loops, and prompt engineering pipelines. My performance engineering background (14 years LoadRunner/VuGen, production AWS and Kubernetes) means I approach AI systems with an engineer's mindset: reliability, observability, and scale matter as much as the model. I hold the IBM Generative AI Engineering Professional Certificate, have completed prompt engineering and AI development coursework, and carry an active Public Trust clearance.

I am eager to bring this combination of proven engineering depth and current AI production experience to [Company]. I am available for US remote work and would welcome a conversation about how I can contribute to your team.

Sincerely,
Hans Richardson
harichardson68@gmail.com"""

_QA_TEMPLATE = """Dear [Company] Hiring Team,

I am writing to express my strong interest in the [Title] position. With 24+ years of IT experience spanning performance engineering, QA, and software development, I bring a broad and practical testing foundation that translates directly to quality engineering roles. [PERSONALIZED]

Throughout my career I have designed and executed test strategies across enterprise platforms including SAP, AWS-based applications, and large-scale web systems. My performance engineering background (14 years LoadRunner/VuGen) means I approach QA with a deep understanding of how systems behave under load — giving me a perspective most QA engineers don't have. I am experienced with API testing (REST/SOAP), defect management, test case design, and working within Agile/Scrum teams. I also hold an active Public Trust clearance from my federal contract work at USDA.

I am available for US remote work and confident I can contribute immediately to your quality engineering efforts. I would welcome the opportunity to discuss how my background aligns with your team's needs.

Sincerely,
Hans Richardson
harichardson68@gmail.com"""

_COBOL_TEMPLATE = """Dear [Company] Hiring Team,

I am writing to express my strong interest in the [Title] position. With 7 years of COBOL/CICS programming experience and 24+ years in IT overall, I offer a rare combination of mainframe development depth and modern enterprise engineering skills. [PERSONALIZED]

My mainframe background includes COBOL, CICS, DB2, and JCL development across large-scale business systems. I transitioned into performance and systems engineering, giving me a broad technical perspective that extends well beyond the mainframe — including AWS, Kubernetes, Python, and enterprise performance testing. I hold an active Public Trust clearance from my federal contract work at USDA, which is often valuable in the organizations that still run critical COBOL systems.

I am available for US remote work and would welcome the opportunity to discuss how my background fits your mainframe needs.

Sincerely,
Hans Richardson
harichardson68@gmail.com"""

_TEMPLATE_BY_TRACK = {
    "LoadRunner / Performance": _PERF_TEMPLATE,
    "AI Hybrid":                _AI_TEMPLATE,
    "QA / Test Engineering":    _QA_TEMPLATE,
    "COBOL / Mainframe":        _COBOL_TEMPLATE,
}

_SYSTEM = """You are editing cover letters for Hans Richardson, a Senior Performance Engineer.

The user message contains:
1. Four templates: PERFORMANCE, AI_HYBRID, QA_TESTING, and COBOL
2. A numbered list of jobs — each specifies which template to use, plus title, company, and description

Your ONLY job is to make three targeted edits to the indicated template for each job:
1. Replace [Title] with the actual job title
2. Replace [Company] with the actual company name — if company is "(unknown)" or blank,
   use "Hiring Team" for the salutation and "your organization" everywhere else in the body
3. Replace [PERSONALIZED] with ONE specific sentence (20-30 words) that references
   1-2 relevant skills or technologies mentioned in the job description.
   Good examples:
   - "Your focus on AppDynamics-driven performance analysis and Kubernetes-based load environments aligns directly with work I have led at scale."
   - "The emphasis on LangChain-based RAG pipelines maps closely to the agentic workflows I have built in production."

Do NOT rewrite any other part of the letter. Do NOT add paragraphs. Do NOT change the sign-off.

Return ONLY a JSON array, one object per job, in order:
[
  {"index": 1, "cover_letter": "full letter text with edits applied"},
  ...
]
No prose, no markdown fences."""


_GARBAGE_COMPANY_RE = re.compile(
    r'\b(remote|travel|hybrid|on.?site|onsite|\d+\s*%)\b', re.I
)

def _clean_company(raw: str) -> str:
    """Return empty string if the company field looks like location/travel data."""
    if not raw:
        return ""
    return "" if _GARBAGE_COMPANY_RE.search(raw) else raw


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        t = t.replace("```json", "").replace("```", "").strip()
    return t


def _eligible_jobs(jobs: list) -> list:
    """Return jobs worth generating a letter for."""
    eligible = []
    for job in jobs:
        tier  = job.get("fit_tier", "")
        track = job.get("track", "")
        # Only Performance and AI Hybrid tracks have templates
        if track not in _TEMPLATE_BY_TRACK:
            continue
        # Skip Weak — not worth applying
        if tier == "Weak":
            continue
        eligible.append(job)
    return eligible[:COVER_LETTER_MAX]


def _build_cover_batch(jobs: list, offset: int = 0) -> str:
    """Build the user message for a batch of jobs (templates + job list)."""
    job_lines = []
    for i, job in enumerate(jobs, offset + 1):
        track   = job.get("track", "")
        if "Performance" in track or "LoadRunner" in track:
            tmpl = "PERFORMANCE"
        elif "AI" in track:
            tmpl = "AI_HYBRID"
        elif "COBOL" in track:
            tmpl = "COBOL"
        else:
            tmpl = "QA_TESTING"
        desc    = (job.get("description") or "")[:250]
        company = _clean_company(job.get("company", "") or "")
        job_lines.append(
            f"{i}. [USE {tmpl} TEMPLATE]\n"
            f"   Title: {job.get('title', 'N/A')}\n"
            f"   Company: {company or '(unknown)'}\n"
            f"   Description: {desc}"
        )
    return (
        f"PERFORMANCE TEMPLATE:\n{_PERF_TEMPLATE}\n\n"
        f"AI_HYBRID TEMPLATE:\n{_AI_TEMPLATE}\n\n"
        f"QA_TESTING TEMPLATE:\n{_QA_TEMPLATE}\n\n"
        f"COBOL TEMPLATE:\n{_COBOL_TEMPLATE}\n\n"
        f"JOBS:\n" + "\n\n".join(job_lines)
    )


def _call_cover(user_msg: str, model: str) -> list:
    resp = requests.post(
        CLAUDE_URL,
        headers={
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 6000,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=90,
    )
    if resp.status_code != 200:
        err = resp.json().get("error", {}).get("message", resp.text[:120])
        raise RuntimeError(f"API error {resp.status_code}: {err}")
    raw = resp.json()["content"][0]["text"]
    return json.loads(_strip_fences(raw))


def generate_cover_letters(state=None) -> dict:
    """
    Generates cover letters for eligible jobs. Career tracks use Sonnet,
    bridge tracks use Haiku. Two batched calls, one per tier.
    Annotates each job with a 'cover_letter' field.
    """
    if state is None or not getattr(state, "jobs", None):
        return {"ok": True, "tool": "generate_cover_letters",
                "note": "No jobs — cover letter generation skipped."}

    if not CLAUDE_API_KEY:
        return {"ok": False, "tool": "generate_cover_letters",
                "note": "No CLAUDE_API_KEY — cover letters skipped."}

    eligible = _eligible_jobs(state.jobs)
    if not eligible:
        return {"ok": True, "tool": "generate_cover_letters",
                "note": "No eligible jobs for cover letters (all Weak or untracked)."}

    career = [j for j in eligible if j.get("track", "") in _CAREER_TRACKS]
    bridge = [j for j in eligible if j.get("track", "") not in _CAREER_TRACKS]

    total = 0
    errors = []

    time.sleep(2)  # rate limit buffer after analyze_fit

    for pool, model, label in [
        (career, CLAUDE_MODEL_CAREER, "career"),
        (bridge, CLAUDE_MODEL_BRIDGE, "bridge"),
    ]:
        if not pool:
            continue
        try:
            letters = _call_cover(_build_cover_batch(pool), model)
            for item in letters:
                idx = int(item.get("index", 0)) - 1
                if 0 <= idx < len(pool):
                    pool[idx]["cover_letter"] = item.get("cover_letter", "")
                    total += 1
        except json.JSONDecodeError as e:
            errors.append(f"{label} JSON error: {e}")
        except Exception as exc:
            errors.append(f"{label} error: {exc}")

    note = f"Cover letters generated for {total} job(s) ({len(career)} career / {len(bridge)} bridge)."
    if errors:
        note += f" ERRORS: {'; '.join(errors)}"
    return {"ok": True, "tool": "generate_cover_letters", "note": note}


# ------------------------------------------------------------
# QUICK SELF-TEST  (python tools/cover_letter.py)
# ------------------------------------------------------------
if __name__ == "__main__":
    class FakeState:
        def __init__(self, jobs): self.jobs = jobs

    test_jobs = [
        {
            "title": "Senior Performance Engineer",
            "company": "INSPYR Solutions",
            "track": "LoadRunner / Performance",
            "score": 120,
            "fit_tier": "Excellent",
            "description": "LoadRunner, VuGen, AppDynamics, performance testing, Kubernetes, AWS",
        },
        {
            "title": "AI Systems Engineer",
            "company": "Cerner",
            "track": "AI Hybrid",
            "score": 65,
            "fit_tier": "Strong",
            "description": "LangChain, RAG pipelines, Python, LLM orchestration, agentic workflows",
        },
    ]

    st = FakeState(test_jobs)
    result = generate_cover_letters(st)
    print(result["note"])
    print()
    for job in st.jobs:
        cl = job.get("cover_letter", "")
        if cl:
            print(f"=== {job['title']} @ {job['company']} ===")
            print(cl[:400], "...")
            print()
