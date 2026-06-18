"""
tools/analyze_fit.py
====================
The analyze_fit tool: calls Claude to tier the top-scored jobs by fit
and flag the single most notable gap per job.

Only the top N jobs are analyzed (cost control). The function annotates
each job in-place on state.jobs with:
    fit_tier   — Excellent / Strong / Decent / Weak
    fit_reason — one-line why
    fit_gap    — single most notable gap, or None
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_MODEL_CAREER = "claude-sonnet-4-6"   # career track — nuanced gap analysis
CLAUDE_MODEL_BRIDGE = "claude-haiku-4-5-20251001"  # bridge track — straightforward fit
CLAUDE_URL = "https://api.anthropic.com/v1/messages"

CAREER_TRACKS = {"LoadRunner / Performance", "AI Hybrid"}
BRIDGE_TRACKS  = {"QA / Test Engineering", "COBOL / Mainframe"}

TOP_N_CAREER = 10   # career jobs to analyze per run
TOP_N_BRIDGE = 10   # bridge jobs to analyze per run

_SYSTEM = """You evaluate job fit for Hans Richardson, a Senior Performance Engineer pivoting to AI.

Hans's profile:
- 24+ years IT total; 14 years LoadRunner/VuGen/LRE (his strongest differentiator — expert level)
- SAP performance testing — 9 years (CRM, ERP, BRIM, FI modules; load/stress/volume/scalability)
- Protocols: Web HTTP/HTML, TruClient, REST, SOAP, Web Services, Citrix, SAP
- Monitoring/observability: AppDynamics (≈Dynatrace), Splunk, Prometheus, Grafana, SiteScope, AWS X-Ray
- Cloud & DevOps: AWS (strong), Kubernetes (production — led AWS/K8s migration at USDA),
  CI/CD, GitHub, Agile/Scrum
- AI & automation: Claude API (production), multi-agent systems (built Agent Hub + job search platform),
  prompt engineering (IBM cert + bootcamp), Python (real production scripts)
- Trained (coursework, not production years): JMeter, NeoLoad, Selenium, LangChain
- QA / Testing: broad QA background through performance testing — test planning, test case design,
  defect management, API testing (REST/SOAP), Postman, functional and regression testing
- Early career: COBOL/CICS programmer 7 years; DB2; JCL; PeopleSoft admin; mainframe
- Clearance: Public Trust (held during USDA federal contract 2021–2025)
- Seniority: Senior / Lead level — roles expecting < 8 years total experience are a mismatch
- Location: Lee's Summit, MO — US remote or KC-metro hybrid/onsite only

Known gaps (flag the single most relevant one per job):
  Azure         — AWS/K8s strong, but no Azure certifications or Azure-specific project experience
  LangGraph     — familiar with LangChain; limited LangGraph production use
  JMeter-prod   — trained on JMeter (Coursera) but no multi-year production JMeter history
  years-of-AI   — real production AI projects exist, but < 2 years on the AI track vs. 14 on perf
  RAG-prod      — understands RAG concepts but no production RAG system built and deployed
  vector-stores — no production experience with Pinecone, Weaviate, Chroma, or similar
  domain-gap    — role requires deep domain knowledge Hans lacks (e.g. Guidewire, Salesforce, finance)
  selenium-prod — trained on Selenium but no multi-year production automation framework history
  cobol-vintage — COBOL experience is 15+ years old; modern mainframe tooling may differ

DO NOT flag SAP, Kubernetes, AppDynamics, Splunk, REST APIs, or COBOL as gaps — Hans has real
experience in all of these.

Fit tiers:
  Excellent — Hans is clearly qualified; his core strengths ARE the job's core requirements
  Strong    — good match; one addressable gap
  Decent    — partial match; 2+ notable gaps or one significant gap
  Weak      — significant mismatch; role requires depth Hans does not yet have

IMPORTANT — AI Hybrid jobs require stricter grading:
  Hans is EARLY on the AI track (< 2 years). Do not conflate general IT seniority with
  AI/ML depth. Apply these rules for AI Hybrid roles:
  - If the job requires 3+ years NLP/LLM or AI production experience as a PRIMARY requirement → Decent or Weak
  - If the job requires 2+ specific AI frameworks Hans lacks (LangGraph, CrewAI, AutoGen,
    vector stores, RAG production) → Decent, not Strong
  - If the core requirements are almost entirely AI-native depth Hans doesn't have → Weak
  - Strong is only appropriate when Hans's production multi-agent/Claude API work directly
    matches the role's PRIMARY ask, and there is only one notable gap

For each numbered job below, return a JSON array (one object per job, in order):
[
  {
    "index": 1,
    "fit_tier": "Excellent|Strong|Decent|Weak",
    "reason": "one sentence why",
    "gap": "single most notable gap keyword, or null"
  },
  ...
]

Return ONLY the JSON array. No prose, no markdown fences."""


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if Claude returns them despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n", 1)
        t = lines[1] if len(lines) > 1 else ""
        t = t.rsplit("```", 1)[0].strip()
    return t


def _call_fit(jobs: list, model: str) -> list:
    """
    Call Claude to tier a list of jobs. Returns list of assessment dicts.
    Jobs are passed with a local 1-based index; caller maps back to state.jobs.
    """
    job_lines = []
    for i, job in enumerate(jobs, 1):
        desc = (job.get("description") or "")[:400]
        job_lines.append(
            f"{i}. Title: {job.get('title', 'N/A')}\n"
            f"   Company: {job.get('company', 'N/A')}\n"
            f"   Description: {desc}"
        )
    prompt = "\n\n".join(job_lines)

    resp = requests.post(
        CLAUDE_URL,
        headers={
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 800,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=45,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"API error {resp.status_code}: {resp.text[:120]}")

    raw = resp.json()["content"][0]["text"]
    return json.loads(_strip_fences(raw))


def analyze_fit(state=None) -> dict:
    """
    Call Claude to tier jobs and flag the notable gap per job.
    Career tracks (LoadRunner/Performance, AI Hybrid) use Sonnet.
    Bridge tracks (QA/Testing, COBOL) use Haiku — simpler fit, lower cost.
    Annotates each job in-place with fit_tier, fit_reason, fit_gap.
    """
    if state is None or not getattr(state, "jobs", None):
        return {"ok": True, "tool": "analyze_fit", "note": "No jobs to analyze."}

    if not CLAUDE_API_KEY:
        return {"ok": False, "tool": "analyze_fit",
                "note": "No CLAUDE_API_KEY — fit analysis skipped."}

    # Split into career and bridge pools
    career_jobs = [j for j in state.jobs if j.get("track", "") in CAREER_TRACKS][:TOP_N_CAREER]
    bridge_jobs  = [j for j in state.jobs if j.get("track", "") in BRIDGE_TRACKS][:TOP_N_BRIDGE]

    tiers = []
    errors = []

    for pool, model, label in [
        (career_jobs, CLAUDE_MODEL_CAREER, "career"),
        (bridge_jobs,  CLAUDE_MODEL_BRIDGE, "bridge"),
    ]:
        if not pool:
            continue
        try:
            assessments = _call_fit(pool, model)
            for item in assessments:
                idx = int(item.get("index", 0)) - 1
                if 0 <= idx < len(pool):
                    pool[idx]["fit_tier"]    = item.get("fit_tier", "?")
                    pool[idx]["fit_reason"]  = item.get("reason", "")
                    pool[idx]["fit_gap"]     = item.get("gap")
                    pool[idx]["fit_track_type"] = label
                    tiers.append(item.get("fit_tier", "?"))
        except json.JSONDecodeError as e:
            errors.append(f"{label} JSON error: {e}")
        except Exception as exc:
            errors.append(f"{label} error: {exc}")

    analyzed = len(career_jobs) + len(bridge_jobs)
    note = f"Fit analysis done for {analyzed} jobs ({len(career_jobs)} career / {len(bridge_jobs)} bridge)"
    if tiers:
        note += f": {', '.join(tiers)}"
    if errors:
        note += f" | ERRORS: {'; '.join(errors)}"
    return {"ok": True, "tool": "analyze_fit", "note": note}


# ------------------------------------------------------------
# QUICK SELF-TEST  (python tools/analyze_fit.py)
# ------------------------------------------------------------
if __name__ == "__main__":
    class FakeState:
        def __init__(self, jobs): self.jobs = jobs

    test_jobs = [
        {
            "title": "Senior Performance Engineer",
            "company": "Fidelity Investments",
            "description": "LoadRunner, AppDynamics, performance testing, Java, remote KC area",
            "score": 85,
        },
        {
            "title": "AI Systems Engineer",
            "company": "Sprint / T-Mobile",
            "description": "LangChain, LangGraph, Azure OpenAI, RAG pipelines, Python, agentic workflows",
            "score": 72,
        },
        {
            "title": "Performance Test Engineer",
            "company": "Cerner",
            "description": "JMeter performance testing, Splunk, Kubernetes, Docker, healthcare domain",
            "score": 55,
        },
    ]
    st = FakeState(test_jobs)
    result = analyze_fit(st)
    print(result["note"])
    print()
    for j in st.jobs[:3]:
        gap = j.get("fit_gap") or "none"
        print(f"  [{j.get('fit_tier','?')}]  {j['title'][:40]:40}  gap: {gap}")
        print(f"         {j.get('fit_reason', '')}")
