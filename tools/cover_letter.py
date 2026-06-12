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

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_MODEL   = "claude-sonnet-4-6"
CLAUDE_URL     = "https://api.anthropic.com/v1/messages"

# Max jobs to generate letters for (cost control)
COVER_LETTER_MAX = 7

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

_TEMPLATE_BY_TRACK = {
    "LoadRunner / Performance": _PERF_TEMPLATE,
    "AI Hybrid":                _AI_TEMPLATE,
}

_SYSTEM = """You are editing cover letters for Hans Richardson, a Senior Performance Engineer.

The user message contains:
1. A PERFORMANCE TEMPLATE and an AI_HYBRID TEMPLATE
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


def generate_cover_letters(state=None) -> dict:
    """
    Agent tool. Generates lightly-personalized cover letters for the
    top scored jobs in state.jobs. Annotates each job with a
    'cover_letter' field. One batched Claude call for all letters.
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

    # Templates sent ONCE at the top — not repeated per job
    job_lines = []
    for i, job in enumerate(eligible, 1):
        track   = job.get("track", "")
        tmpl    = "PERFORMANCE" if "Performance" in track else "AI_HYBRID"
        desc    = (job.get("description") or "")[:250]
        company = _clean_company(job.get("company", "") or "")
        job_lines.append(
            f"{i}. [USE {tmpl} TEMPLATE]\n"
            f"   Title: {job.get('title', 'N/A')}\n"
            f"   Company: {company or '(unknown)'}\n"
            f"   Description: {desc}"
        )

    user_msg = (
        f"PERFORMANCE TEMPLATE:\n{_PERF_TEMPLATE}\n\n"
        f"AI_HYBRID TEMPLATE:\n{_AI_TEMPLATE}\n\n"
        f"JOBS:\n" + "\n\n".join(job_lines)
    )

    # Brief pause — this call follows analyze_fit; avoid back-to-back rate limits
    time.sleep(2)

    try:
        resp = requests.post(
            CLAUDE_URL,
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 6000,
                "system": _SYSTEM,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=90,
        )

        if resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", resp.text[:120])
            return {"ok": False, "tool": "generate_cover_letters",
                    "note": f"API error {resp.status_code}: {err} — cover letters skipped."}

        raw     = resp.json()["content"][0]["text"]
        letters = json.loads(_strip_fences(raw))

        if not isinstance(letters, list):
            return {"ok": False, "tool": "generate_cover_letters",
                    "note": "Unexpected response shape — cover letters skipped."}

        # Annotate jobs in-place
        for item in letters:
            idx = int(item.get("index", 0)) - 1
            if 0 <= idx < len(eligible):
                eligible[idx]["cover_letter"] = item.get("cover_letter", "")

        note = f"Cover letters generated for {len(letters)} job(s)."
        return {"ok": True, "tool": "generate_cover_letters", "note": note}

    except json.JSONDecodeError as e:
        return {"ok": False, "tool": "generate_cover_letters",
                "note": f"JSON parse error: {e} — cover letters skipped."}
    except Exception as exc:
        return {"ok": False, "tool": "generate_cover_letters",
                "note": f"Cover letter error: {exc} — skipped."}


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
