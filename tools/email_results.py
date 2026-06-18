"""
tools/email_results.py
======================
Agent tool: format the ranked job results as an HTML digest and send
via Gmail SMTP to EMAIL_TO.

Can be called two ways:
  1. By the agent planner (registered in registry.py) — passes state.
  2. Directly from Agent Hub's "Email Results" button — passes a jobs list.

Uses the same Gmail credentials as job_search.py:
  GMAIL_ADDRESS  — sender address
  GMAIL_APP_PASS — Gmail App Password (not the account password)
  EMAIL_TO       — recipient (defaults to GMAIL_ADDRESS)
"""

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
EMAIL_TO       = os.environ.get("EMAIL_TO", GMAIL_ADDRESS)

_CAREER_TRACKS = {"LoadRunner / Performance", "AI Hybrid"}

# Fit tier → badge colour
_TIER_COLORS = {
    "Excellent": ("#1b5e20", "#e8f5e9"),
    "Strong":    ("#0d47a1", "#e3f2fd"),
    "Decent":    ("#e65100", "#fff3e0"),
    "Weak":      ("#b71c1c", "#ffebee"),
}


def _tier_badge(tier: str) -> str:
    """Render a coloured fit-tier badge."""
    fg, bg = _TIER_COLORS.get(tier, ("#555", "#f5f5f5"))
    return (f'<span style="background:{bg};color:{fg};padding:2px 10px;'
            f'border-radius:10px;font-size:12px;font-weight:bold;">{tier}</span>')


def _job_card(index: int, job: dict, accent_color: str = "#1F3864") -> str:
    """Render a single job card div. Called once per job in each section."""
    title    = job.get("title", "N/A")
    company  = job.get("company", "") or "(company not listed)"
    track    = job.get("track", "")
    score    = job.get("score", 0)
    url      = job.get("url", "").strip()
    posted   = (job.get("posted") or "")[:10] or "Date unknown"
    sources  = "+".join(job.get("seen_on", [job.get("source", "?")]))
    keywords = ", ".join((job.get("matched_keywords") or [])[:6])
    tier     = job.get("fit_tier", "")
    reason   = job.get("fit_reason", "")
    gap      = job.get("fit_gap")
    sal_note = job.get("salary_note", "")

    tier_html = ""
    if tier:
        badge    = _tier_badge(tier)
        gap_html = (f'<span style="font-size:12px;color:#888;"> · gap: {gap}</span>'
                    if gap else "")
        tier_html = f"""
<div style="background:#f0f7ff;border-left:3px solid {accent_color};padding:10px 14px;
margin:8px 0 12px;border-radius:0 4px 4px 0;">
  <p style="margin:0 0 4px;font-size:12px;font-weight:bold;color:{accent_color};">
    FIT: {badge}{gap_html}
  </p>
  <p style="margin:0;font-size:13px;color:#444;line-height:1.4;">{reason}</p>
</div>"""

    apply_btn = ""
    if url:
        apply_btn = (f'<a href="{url}" style="background:{accent_color};color:#fff;'
                     f'padding:8px 16px;border-radius:4px;text-decoration:none;'
                     f'font-size:13px;">View &amp; Apply</a>')

    sal_html = (f'<p style="margin:4px 0;font-size:12px;color:#555;">'
                f'<strong>Salary note:</strong> {sal_note}</p>'
                if sal_note else "")

    cover      = job.get("cover_letter", "")
    cover_html = ""
    if cover:
        cover_formatted = cover.replace("\n", "<br>")
        cover_html = f"""
<div style="margin-top:12px;">
  <p style="margin:0 0 6px;font-size:12px;font-weight:bold;color:#555;">COVER LETTER</p>
  <div style="background:#f9f9f9;padding:12px 14px;border-radius:4px;
  font-size:13px;line-height:1.6;color:#333;">{cover_formatted}</div>
</div>"""

    return f"""
<div style="border:1px solid #e0e0e0;border-radius:8px;padding:16px;margin-bottom:16px;">
  <h3 style="color:{accent_color};margin:0 0 4px;">
    #{index} — {title}
    <span style="font-size:11px;background:#e8f0fe;color:#1F3864;padding:2px 8px;
    border-radius:10px;margin-left:6px;">{track}</span>
  </h3>
  <p style="margin:4px 0;"><strong>Company:</strong> {company}
    &nbsp;|&nbsp; <strong>Score:</strong> {score} pts
    &nbsp;|&nbsp; <strong>Source:</strong> {sources}
  </p>
  <p style="margin:4px 0;font-size:12px;color:#e65100;">
    <strong>Posted:</strong> {posted}
  </p>
  {sal_html}
  <p style="margin:4px 0;font-size:12px;color:#555;">
    <strong>Keywords:</strong> {keywords}
  </p>
  {tier_html}
  {apply_btn}
  {cover_html}
</div>"""


def _build_html(jobs: list, goal: str = "", run_note: str = "") -> str:
    today = datetime.now().strftime("%B %d, %Y  %I:%M %p")
    count = len(jobs)
    subject_line = (f"Agent found {count} match{'es' if count != 1 else ''}"
                    if count else "Agent run — no matches")

    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:800px;margin:auto;color:#333;">
<h2 style="color:#1F3864;">Job Agent Results</h2>
<p style="margin:0 0 4px;">Hi Hans — here are your latest matches as of <strong>{today}</strong>.</p>"""

    if goal:
        html += f'<p style="margin:4px 0 12px;font-size:13px;color:#555;"><strong>Goal:</strong> {goal}</p>'
    if run_note:
        html += f'<p style="margin:4px 0 12px;font-size:12px;color:#888;">{run_note}</p>'

    if count == 0:
        html += """<div style="background:#fff8e1;border:1px solid #ffe082;border-radius:8px;
padding:16px;margin:16px 0;">
  <p style="margin:0;color:#f57f17;font-weight:bold;">No matches found this run.</p>
  <p style="margin:8px 0 0;color:#555;">Try a different goal or broaden the search query.</p>
</div>"""
    else:
        career_jobs = [j for j in jobs if j.get("track", "") in _CAREER_TRACKS]
        bridge_jobs = [j for j in jobs if j.get("track", "") not in _CAREER_TRACKS]

        html += "<hr/>"
        if career_jobs:
            html += """
<div style="background:#1F3864;color:#fff;padding:10px 16px;border-radius:6px 6px 0 0;margin-bottom:0;">
  <h3 style="margin:0;font-size:15px;letter-spacing:0.5px;">&#9733; Career Track Opportunities</h3>
  <p style="margin:2px 0 0;font-size:12px;opacity:0.8;">LoadRunner / Performance &nbsp;&middot;&nbsp; AI Hybrid</p>
</div>
<div style="border:1px solid #1F3864;border-top:none;border-radius:0 0 6px 6px;
padding:16px;margin-bottom:28px;">"""
            for i, job in enumerate(career_jobs, 1):
                html += _job_card(i, job, accent_color="#1F3864")
            html += "</div>"

        if bridge_jobs:
            html += """
<div style="background:#4a7c59;color:#fff;padding:10px 16px;border-radius:6px 6px 0 0;margin-bottom:0;">
  <h3 style="margin:0;font-size:15px;letter-spacing:0.5px;">&#9670; Bridge Opportunities</h3>
  <p style="margin:2px 0 0;font-size:12px;opacity:0.8;">QA / Test Engineering &nbsp;&middot;&nbsp; COBOL / Mainframe</p>
</div>
<div style="border:1px solid #4a7c59;border-top:none;border-radius:0 0 6px 6px;
padding:16px;margin-bottom:28px;">"""
            for i, job in enumerate(bridge_jobs, 1):
                html += _job_card(i, job, accent_color="#4a7c59")
            html += "</div>"

    html += "\n</body></html>"

    return html


def send_digest(jobs: list, goal: str = "", run_note: str = "") -> dict:
    """
    Build and send the HTML digest email.
    Returns a result dict (ok, note) suitable for agent observation.
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASS:
        return {"ok": False, "tool": "email_results",
                "note": "Email skipped — GMAIL_ADDRESS/GMAIL_APP_PASS not set."}

    to_addr = EMAIL_TO or GMAIL_ADDRESS
    today   = datetime.now().strftime("%b %d, %Y")
    count   = len(jobs)
    subject = (f"Agent: {count} job match{'es' if count != 1 else ''} — {today}"
               if count else f"Agent run — no matches — {today}")

    html = _build_html(jobs, goal=goal, run_note=run_note)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = to_addr
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
            server.sendmail(GMAIL_ADDRESS, to_addr, msg.as_string())

        note = f"Digest sent to {to_addr} — {count} job{'s' if count != 1 else ''}."
        return {"ok": True, "tool": "email_results", "note": note}

    except Exception as e:
        return {"ok": False, "tool": "email_results",
                "note": f"Email failed: {e}"}


def email_results(state=None) -> dict:
    """
    Agent tool entry point. Runs score → analyze → cover letters → email.
    Self-healing: if jobs arrive unscored (planner skipped steps), scores and
    analyzes them here before sending so the digest is always complete.
    """
    if state is None or not getattr(state, "jobs", None):
        return {"ok": False, "tool": "email_results",
                "note": "No jobs to email — run search and score first."}

    try:
        from tools.score import score_results
        from tools.analyze_fit import analyze_fit
        from tools.cover_letter import generate_cover_letters
    except ImportError:
        from score import score_results
        from analyze_fit import analyze_fit
        from cover_letter import generate_cover_letters

    # Auto-score if the planner skipped it (all scores are 0 or missing)
    if all(j.get("score", 0) == 0 for j in state.jobs):
        score_results(state)

    # Auto-analyze if fit tiers are absent
    if not any(j.get("fit_tier") for j in state.jobs):
        analyze_fit(state)

    generate_cover_letters(state)

    jobs     = state.jobs
    goal     = getattr(state, "goal", "")
    analyzed = sum(1 for j in jobs if j.get("fit_tier"))
    run_note = (f"Fit analysis shown for top {analyzed} matches; "
                f"remaining ranked by score.") if analyzed else ""

    return send_digest(jobs, goal=goal, run_note=run_note)


# ------------------------------------------------------------
# QUICK SELF-TEST  (python tools/email_results.py)
# Sends a real email with fake jobs — needs live SMTP creds.
# ------------------------------------------------------------
if __name__ == "__main__":
    fake_jobs = [
        {
            "title": "Senior Performance Engineer",
            "company": "INSPYR Solutions",
            "track": "LoadRunner / Performance",
            "score": 120,
            "url": "https://www.dice.com/job-detail/test-1",
            "posted": "2026-06-11",
            "seen_on": ["Adzuna"],
            "matched_keywords": ["loadrunner", "performance testing", "appdynamics"],
            "fit_tier": "Excellent",
            "fit_reason": "LoadRunner is the core requirement and Hans has 14 years of expert-level experience.",
            "fit_gap": None,
            "salary_note": "",
            "cover_letter": "Dear INSPYR Solutions Hiring Team,\n\nTest letter...\n\nSincerely,\nHans Richardson",
        },
        {
            "title": "AI Systems Engineer",
            "company": "Cerner",
            "track": "AI Hybrid",
            "score": 65,
            "url": "https://www.dice.com/job-detail/test-2",
            "posted": "2026-06-10",
            "seen_on": ["Serper"],
            "matched_keywords": ["llm", "rag", "python", "langchain"],
            "fit_tier": "Decent",
            "fit_reason": "Good AI alignment but role requires 3+ years LangGraph production use.",
            "fit_gap": "LangGraph",
            "salary_note": "",
        },
        {
            "title": "QA Automation Engineer",
            "company": "H&R Block",
            "track": "QA / Test Engineering",
            "score": 55,
            "url": "https://www.dice.com/job-detail/test-3",
            "posted": "2026-06-09",
            "seen_on": ["Serper"],
            "matched_keywords": ["selenium", "api testing", "postman", "agile"],
            "fit_tier": "Strong",
            "fit_reason": "Broad QA background and API testing experience align well; Selenium is coursework not production.",
            "fit_gap": "selenium-prod",
            "salary_note": "~$35/hr — above bridge floor",
        },
        {
            "title": "COBOL Programmer",
            "company": "Citi",
            "track": "COBOL / Mainframe",
            "score": 40,
            "url": "https://www.dice.com/job-detail/test-4",
            "posted": "2026-06-08",
            "seen_on": ["Adzuna"],
            "matched_keywords": ["cobol", "cics", "db2", "jcl"],
            "fit_tier": "Decent",
            "fit_reason": "Early-career COBOL/CICS experience is real; tooling may have evolved since.",
            "fit_gap": "cobol-vintage",
            "salary_note": "~$45/hr — at floor",
        },
    ]
    result = send_digest(fake_jobs, goal="Find matches across all four tracks")
    print(result["note"])
