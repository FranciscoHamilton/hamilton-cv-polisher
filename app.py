# app.py
import os, json, re, tempfile, traceback, zipfile, io
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, request, send_file, render_template_string, abort, jsonify, make_response
from flask import session, redirect, url_for  # <-- ADDED earlier

# Try fast PDF extraction first (PyMuPDF)
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

# Text extraction / DOCX tooling
from pdfminer.high_level import extract_text as pdf_extract_text
from docx import Document as Docx
from docx.shared import Pt, Inches, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH

PROJECT_DIR = Path(__file__).parent.resolve()
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # used only if OPENAI_API_KEY is set

# --- simple persistent stats + history ---
STATS_FILE = PROJECT_DIR / "stats.json"
if STATS_FILE.exists():
    try:
        STATS = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        STATS = {"downloads": 0, "last_candidate": "", "last_time": "", "history": []}
else:
    STATS = {"downloads": 0, "last_candidate": "", "last_time": "", "history": []}
STATS.setdefault("history", [])
# NEW: credits bucket for director view (does not change polish behavior)
STATS.setdefault("credits", {"balance": 0, "purchased": 0})

def _save_stats():
    if len(STATS.get("history", [])) > 1000:
        STATS["history"] = STATS["history"][-1000:]
    STATS_FILE.write_text(json.dumps(STATS, indent=2), encoding="utf-8")

# NEW: simple users store (for recruiters you create in Director)
USERS_FILE = PROJECT_DIR / "users.json"
if USERS_FILE.exists():
    try:
        USERS_DB = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        USERS_DB = {"users": []}
else:
    USERS_DB = {"users": []}

def _save_users():
    USERS_FILE.write_text(json.dumps(USERS_DB, indent=2), encoding="utf-8")

def _get_user(u):
    for x in USERS_DB.get("users", []):
        if (x.get("username") or "").lower() == (u or "").lower():
            return x
    return None

# --- tiny trial request logger ---
TRIALS_FILE = PROJECT_DIR / "trials.json"

def _log_trial(data: dict):
    try:
        if TRIALS_FILE.exists():
            buf = json.loads(TRIALS_FILE.read_text(encoding="utf-8"))
        else:
            buf = []
        # keep it light; don’t store anything sensitive
        buf.append({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "company": (data.get("company") or "")[:200],
            "email": (data.get("email") or "")[:200],
            "name": (data.get("name") or "")[:200],
            "team_size": (data.get("team_size") or "")[:50],
            "notes": (data.get("notes") or "")[:1000],
        })
        # keep last 1000 only
        buf = buf[-1000:]
        TRIALS_FILE.write_text(json.dumps(buf, indent=2), encoding="utf-8")
    except Exception:
        # don’t break the flow if logging fails
        pass

# alias so either name works (covers older calls)
def _log_trial_request(data: dict):
    _log_trial(data)

# --- tiny trial sign-up logger (local JSON file) ---
TRIALS_FILE = PROJECT_DIR / "trials.json"
if TRIALS_FILE.exists():
    try:
        TRIALS = json.loads(TRIALS_FILE.read_text(encoding="utf-8"))
    except Exception:
        TRIALS = []
else:
    TRIALS = []

def _log_trial(entry: dict):
    TRIALS.append(entry)
    TRIALS_FILE.write_text(json.dumps(TRIALS, indent=2), encoding="utf-8")

# ------------------------ Public Home ------------------------
HOMEPAGE_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>CVStudio</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
  --blue:#2563eb;      /* vivid indigo */
  --blue-2:#22d3ee;    /* bright cyan  */
  --ink:#0f172a; --muted:#5b677a; --line:#e5e7eb;
  --bg:#f5f8fd; --card:#ffffff; --shadow: 0 10px 28px rgba(13,59,102,.08);
}
    *{box-sizing:border-box}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}

    .wrap{max-width:1100px;margin:24px auto 64px;padding:0 20px}

    /* top nav */
    .nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
    .brand{font-weight:900;color:var(--blue);text-decoration:none;font-size:22px;letter-spacing:.2px}
    .nav a{color:var(--ink);text-decoration:none;font-weight:800;margin-left:22px}

    /* hero */
    .hero{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:28px;box-shadow:var(--shadow)}
    .kicker{font-size:12px;letter-spacing:.14em;font-weight:900;color:var(--blue);margin-bottom:10px}
    h1{font-size:48px;line-height:1.05;letter-spacing:-.01em;margin:0 0 10px}
    .lead{font-size:17px;color:var(--muted);max-width:720px;margin:6px 0 16px}
    .actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}
    .btn{display:inline-block;padding:12px 16px;border-radius:12px;font-weight:800;text-decoration:none}
    .btn.primary{background:linear-gradient(90deg,var(--blue),var(--blue-2));color:#fff}
    .btn.secondary{background:#fff;border:1px solid var(--line);color:var(--blue)}
    .meta{color:var(--muted);font-size:13px;margin-top:8px}

    /* features */
    .grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px}
    .card h3{margin:0 0 6px;font-size:18px;color:var(--blue)}
    .card p{margin:0;color:var(--muted);font-size:14px}

    /* stepper */
    .stepper{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:18px}
    .step{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;display:flex;align-items:center;gap:10px}
    .b{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:999px;border:1px solid var(--line);font-weight:900;color:var(--blue)}

    @media (max-width:900px){
      h1{font-size:36px}
      .lead{font-size:16px}
      .grid3,.stepper{grid-template-columns:1fr}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="nav">
      <a class="brand" href="/">CVStudio</a>
      <div>
        <a href="/pricing">Pricing</a>
        <a href="/about" style="margin-left:18px">About</a>
        <a href="/login" style="margin-left:18px">Sign in</a>
      </div>
    </div>

    <div class="hero">
      <div class="kicker">BUILT BY RECRUITERS, FOR RECRUITERS</div>
      <h1>Client-ready CVs.<br/>On your brand.<br/>In seconds.</h1>
      <p class="lead">Upload a raw CV (PDF / DOCX / TXT). We extract what’s there, structure it, and format into your company template—no fuss.</p>
      <div class="actions">
        <a class="btn primary" href="/start">Start free trial</a>
        <a class="btn secondary" href="/login">Sign in</a>
      </div>
      <div class="meta">No card needed · Keep your headers/footers · Works with PDFs</div>
    </div>

    <div class="grid3">
      <div class="card">
        <h3>On-brand output</h3>
        <p>Your logo, fonts and spacing. Clean and consistent across the team.</p>
      </div>
      <div class="card">
        <h3>No learning curve</h3>
        <p>Upload → Download. 15 minutes saved per CV, every time.</p>
      </div>
      <div class="card">
        <h3>Built for recruiters</h3>
        <p>No invented facts. We only structure what’s in the candidate’s CV.</p>
      </div>
    </div>

    <div class="stepper">
      <div class="step"><span class="b">1</span>Upload a CV</div>
      <div class="step"><span class="b">2</span>We extract &amp; structure</div>
      <div class="step"><span class="b">3</span>Download polished DOCX</div>
    </div>
  </div>
</body>
</html>
"""

# ------------------------ About (already added earlier) ------------------------
ABOUT_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>About — CVStudio</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
  --blue:#2563eb;      /* vivid indigo */
  --blue-2:#22d3ee;    /* bright cyan  */
  --ink:#0f172a; --muted:#5b677a; --line:#e5e7eb;
  --bg:#f5f8fd; --card:#ffffff; --shadow: 0 10px 28px rgba(13,59,102,.08);
}
    *{box-sizing:border-box}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}
    .wrap{max-width:1100px;margin:24px auto 64px;padding:0 20px}

    /* nav like homepage */
    .nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
    .brand{font-weight:900;color:var(--blue);text-decoration:none;font-size:22px;letter-spacing:.2px}
    .nav a{color:var(--ink);text-decoration:none;font-weight:800;margin-left:22px}

    .card{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:0;box-shadow:var(--shadow)}
    .inner{max-width:780px;padding:24px}
    h1{margin:6px 0 12px;font-size:28px;color:var(--blue)}
h2{margin:18px 0 10px;font-size:18px;color:var(--blue)}
p{margin:8px 0;color:var(--ink);font-size:15px;line-height:1.7}
ul{margin:8px 0 16px 20px;color:var(--ink);font-size:15px;line-height:1.7}
    .btn{display:inline-block;margin-top:14px;padding:12px 16px;border-radius:12px;background:linear-gradient(90deg,var(--blue),var(--blue-2));border:none;text-decoration:none;font-weight:800;color:#fff}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="nav">
      <a class="brand" href="/">CVStudio</a>
      <div>
        <a href="/pricing">Pricing</a>
        <a href="/about" style="margin-left:18px">About</a>
        <a href="/login" style="margin-left:18px">Sign in</a>
      </div>
    </div>

    <div class="card">
      <div class="inner">
        <h1>Built by recruiters, for recruiters</h1>
        <p>Formatting CVs is necessary—but it’s not why you got into recruitment. After 10+ years running desks and a recruitment business, I’ve felt the pain first-hand: breaking flow to rework a CV, juggling fonts and spacing, fixing headers, and trying to keep branding consistent across the team.</p>
        <p><strong>This tool turns that 10–20 minute task into seconds.</strong> Upload a raw CV (PDF, DOCX, or TXT). We extract the content, structure it, and lay it out in your company’s template. You download a polished, on-brand DOCX—ready to send.</p>

        <h2>Why it matters</h2>
        <ul>
          <li><strong>Time back on the desk:</strong> 15 minutes per CV ≈ 0.25 hours. At £20–£40/hour, that’s £5–£10 per CV. 50 CVs/month ≈ 12.5 hours saved → £250–£500/month in recruiter time.</li>
          <li><strong>Consistency at scale:</strong> every consultant outputs the same, branded format.</li>
          <li><strong>Better candidate & client experience:</strong> clean, readable CVs that reflect your brand.</li>
        </ul>

        <h2>How it works</h2>
        <ul>
          <li><strong>Upload</strong> a raw CV (PDF / DOCX / TXT).</li>
          <li><strong>Extract & structure:</strong> we pull out the real content (experience, education, skills) without inventing facts.</li>
          <li><strong>Lay out in your template:</strong> headers/footers, fonts, sizes and spacing are applied automatically.</li>
          <li><strong>Download</strong> a polished DOCX.</li>
        </ul>

        <h2>Privacy & control</h2>
        <ul>
          <li>No CV content is stored by default—only basic usage metrics (filename + timestamp) for tracking volume and billing.</li>
          <li>Your company template and logo are stored securely to ensure output is always on-brand.</li>
        </ul>

        <h2>What’s on the site</h2>
        <ul>
          <li>Multi-company login (soon): per-company routes and templates.</li>
          <li>Director dashboards (soon): usage counts, CSV export, trends.</li>
          <li>Credit plans: pay-as-you-go or monthly bundles.</li>
          <li>Self-serve template builder (soon): upload a DOCX to switch branding instantly.</li>
        </ul>

        <a class="btn" href="/start">Start free trial</a>
      </div>
    </div>
  </div>
</body>
</html>
"""
  # (unchanged content from your script above)
# For brevity here, keep the exact ABOUT_HTML, PRICING_HTML, HTML, LOGIN_HTML strings from your script.

# ------------------------ Pricing (NEW) ------------------------
PRICING_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Pricing — CVStudio</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --brand:#2563eb;          /* electric blue */
      --brand-2:#22d3ee;        /* cyan accent  */
      --ink:#0f172a; --muted:#64748b; --line:#e5e7eb;
      --bg:#f5f8fd; --card:#fff; --shadow:0 12px 28px rgba(2,6,23,.07);
    }
    *{box-sizing:border-box}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink)}
    .wrap{max-width:1100px;margin:12px auto 64px;padding:0 20px}

    /* nav right */
    .nav{display:flex;justify-content:flex-end;gap:22px;margin:6px 0 8px}
    .nav a{font-weight:900;text-decoration:none;color:var(--ink)}

    /* headings */
    h1{margin:6px 0 10px;font-size:40px;letter-spacing:-.01em;color:var(--ink)} /* toned heading */
    .sub{margin:0 0 18px;color:var(--muted)}
    .section{margin:22px 0 8px;font-weight:900;color:var(--brand);font-size:22px}
    .note{margin:6px 0 18px;color:var(--muted);font-size:13.5px}

    /* grids (same sizing for PAYG and Monthly) */
    .grid3,.grid5{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
    @media(max-width:640px){ .grid3,.grid5{grid-template-columns:1fr} }

    /* cards */
    .card{
      background:var(--card); border:1px solid var(--line); border-radius:18px; overflow:hidden;
      box-shadow:var(--shadow); display:flex; flex-direction:column; min-height:230px; position:relative;
    }
    .card.tight{min-height:unset}
    .topbar{height:6px;background:linear-gradient(90deg,var(--brand),var(--brand-2))}
    .inner{
      padding:16px;display:flex;flex-direction:column;height:100%;
      align-items:center; text-align:center; /* center everything */
    }
    .card.tight .inner{padding:12px 16px}

    .name{font-weight:900;color:var(--ink);font-size:15px;margin:6px 0 8px}
    .price-row{display:flex;align-items:baseline;gap:8px;margin:0 0 6px}
    .price-row .amount{font-size:30px;font-weight:900;letter-spacing:-.01em}
    .price-row .cost{font-size:30px;font-weight:900;letter-spacing:-.01em}
    .price-row .per{font-size:14px;color:var(--muted);font-weight:700}

    .chip{display:inline-block;margin-top:2px;padding:6px 10px;border-radius:999px;background:#f1f5ff;border:1px solid #dbeafe;color:#1e3a8a;font-weight:800;font-size:12px}
    .muted{color:var(--muted);font-size:12.5px;margin-top:6px}

    .btn{margin-top:auto;display:inline-block;padding:12px 14px;border-radius:12px;text-align:center;
         font-weight:900;text-decoration:none;border:1px solid var(--line);color:var(--brand);background:#fff;transition:transform .15s ease}
    .btn.primary{background:linear-gradient(90deg,var(--brand),var(--brand-2));color:#fff;border:none}
    .btn:hover{transform:translateY(-1px)}
    .cta-spacer{height:44px}

    .badge{position:absolute;top:10px;right:10px;background:#0ea5e9;color:#fff;font-weight:900;
           padding:6px 10px;border-radius:999px;font-size:11px;letter-spacing:.08em}
    .feat{margin:8px 0 0 0;padding:0;list-style:none;color:var(--muted);font-size:12.5px;display:flex;flex-direction:column;align-items:center}
    .feat li{display:flex;align-items:center;gap:6px;margin-top:4px;justify-content:center}
    .tick{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;
          background:rgba(34,211,238,.15);color:#0891b2;font-weight:900;font-size:11px}

    /* show more toggle row */
    .moreRow{display:flex;justify-content:center;margin:6px 0 10px}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="nav">
      <a href="/">Home</a><a href="/about">About</a><a href="/login">Sign in</a>
    </div>

    <h1>Pricing</h1>
    <p class="sub">Start with a free trial (5 CVs). Upgrade any time.</p>

    <!-- PAYG -->
    <div class="section">Pay-as-you-go packs</div>
    <p class="note">For occasional use. No commitment. Overage: £1.50/CV.</p>

    <div class="grid3">
      <div class="card">
        <div class="topbar"></div>
        <div class="inner">
          <div class="name">Mini</div>
          <div class="price-row"><div class="amount">50 CVs</div><div class="per">•</div><div class="cost">£75</div></div>
          <span class="chip">£1.50 per CV</span>
          <a class="btn primary" href="/trial">Buy pack</a>
        </div>
      </div>

      <div class="card">
        <div class="topbar"></div>
        <div class="inner">
          <div class="name">Standard</div>
          <div class="price-row"><div class="amount">100 CVs</div><div class="per">•</div><div class="cost">£140</div></div>
          <span class="chip">£1.40 per CV</span>
          <a class="btn primary" href="/trial">Buy pack</a>
        </div>
      </div>

      <div class="card">
        <div class="topbar"></div>
        <div class="inner">
          <div class="name">Bulk</div>
          <div class="price-row"><div class="amount">200 CVs</div><div class="per">•</div><div class="cost">£260</div></div>
          <span class="chip">£1.30 per CV</span>
          <a class="btn primary" href="/trial">Buy pack</a>
        </div>
      </div>
    </div>

    <!-- Monthly -->
    <div class="section">Monthly plans</div>
    <p class="note">CVs reset monthly. Overage is cheaper than PAYG and varies by plan.</p>

    <!-- Visible (3) -->
    <div class="grid5">
      <div class="card">
        <div class="topbar"></div>
        <div class="inner">
          <div class="name">Team</div>
          <div class="price-row"><div class="cost">£300</div><div class="per">/mo</div></div>
          <span class="chip">250 CVs · £1.20/CV</span>
          <ul class="feat"><li><span class="tick">✓</span><span>Shared workspace</span></li><li><span class="tick">✓</span><span>Email support</span></li></ul>
          <a class="btn primary" href="/trial">Join plan</a>
        </div>
      </div>

      <div class="card">
        <span class="badge">MOST POPULAR</span>
        <div class="topbar"></div>
        <div class="inner">
          <div class="name">Pro</div>
          <div class="price-row"><div class="cost">£550</div><div class="per">/mo</div></div>
          <span class="chip">500 CVs · £1.10/CV</span>
          <ul class="feat"><li><span class="tick">✓</span><span>Priority processing</span></li><li><span class="tick">✓</span><span>Team analytics</span></li></ul>
          <a class="btn primary" href="/trial">Join plan</a>
        </div>
      </div>

      <div class="card">
        <div class="topbar"></div>
        <div class="inner">
          <div class="name">Scale</div>
          <div class="price-row"><div class="cost">£750</div><div class="per">/mo</div></div>
          <span class="chip">750 CVs · £1.00/CV</span>
          <ul class="feat"><li><span class="tick">✓</span><span>Advanced reporting</span></li><li><span class="tick">✓</span><span>Priority support</span></li></ul>
          <a class="btn primary" href="/trial">Join plan</a>
        </div>
      </div>
    </div>

    <!-- Toggle -->
    <div class="moreRow">
      <button id="togglePlans" class="btn">Show more plans</button>
    </div>

    <!-- Hidden (3): High Volume, Enterprise, Enterprise+ -->
    <div id="plansMore" class="grid5" style="display:none">
      <div class="card">
        <div class="topbar"></div>
        <div class="inner">
          <div class="name">High Volume</div>
          <div class="price-row"><div class="cost">£900</div><div class="per">/mo</div></div>
          <span class="chip">1,000 CVs · £0.90/CV</span>
          <ul class="feat"><li><span class="tick">✓</span><span>Dedicated success</span></li></ul>
          <a class="btn primary" href="/trial">Join plan</a>
        </div>
      </div>

      <div class="card">
        <div class="topbar"></div>
        <div class="inner">
          <div class="name">Enterprise</div>
          <div class="price-row"><div class="cost">£1,500</div><div class="per">/mo</div></div>
          <span class="chip">2,000+ CVs · £0.75/CV · custom terms</span>
          <ul class="feat"><li><span class="tick">✓</span><span>Custom SLAs</span></li></ul>
          <a class="btn primary" href="/trial">Join plan</a>
        </div>
      </div>

      <div class="card">
        <div class="topbar"></div>
        <div class="inner">
          <div class="name">Enterprise+ (5,000+)</div>
          <div class="price-row"><div class="amount">5,000+ CVs</div><div class="per">/mo</div></div>
          <span class="chip">£0.60 per CV · custom terms</span>
          <ul class="feat"><li><span class="tick">✓</span><span>Volume pricing</span></li></ul>
          <div class="cta-spacer"></div>
        </div>
      </div>
    </div>

    /* calc (OLD STYLE) */
.calc .inner{
  /* undo card centering just for calculator */
  align-items: stretch !important;
  text-align: left !important;
}
.calc .name{font-size:18px;color:var(--brand); text-align:left}
.calc .sub{ text-align:left }

.calc-grid{
  display:grid;
  grid-template-columns:repeat(3,1fr);
  gap:12px;
}
@media(max-width:900px){
  .calc-grid{grid-template-columns:1fr}
}

/* labels ABOVE inputs */
.calc label{
  display:block;
  font-weight:900;
  margin-bottom:6px;
}

/* inputs: boxed, rounded */
.calc input[type=number]{
  width:100%;
  padding:12px;
  border:1px solid var(--line);
  border-radius:12px;
  background:#fff;
  box-shadow: inset 0 1px 2px rgba(2,6,23,.03);
}

/* outputs left-aligned, brand color numbers */
.calc-out{
  display:flex;
  flex-wrap:wrap;
  gap:24px;
  align-items:center;
  margin-top:12px;
  justify-content:flex-start;   /* left */
}
.calc-out .n{
  font-weight:900;
  color:var(--brand);
  font-size:22px;
}

    <!-- Template setup -->
    <div class="card tight" style="margin-top:14px">
      <div class="inner">
        <div class="name">Template setup</div>
        <div class="sub">£50 one-off per company — fully credited back as usage (your first £50 of CVs are free once you start paying).</div>
      </div>
    </div>
  </div>

  <script>
    function fmt(n){ return new Intl.NumberFormat('en-GB',{maximumFractionDigits:0}).format(n); }
    function fmtGBP(n){ return '£' + new Intl.NumberFormat('en-GB',{maximumFractionDigits:0}).format(Math.round(n)); }

    function bestPayg(volume){
      const packs=[{name:'Bulk (200 CVs)',size:200,cost:260},{name:'Standard (100 CVs)',size:100,cost:140},{name:'Mini (50 CVs)',size:50,cost:75}];
      let best={name:'PAYG packs',cost:Infinity,percv:Infinity,credits:0,breakdown:''};
      for(let b=0;b<=Math.ceil(volume/200)+1;b++){
        for(let s=0;s<=Math.ceil(Math.max(0,volume-200*b)/100)+1;s++){
          const used=200*b+100*s; const rem=Math.max(0,volume-used); const m=Math.ceil(rem/50);
          const credits=used+50*m; const cost=260*b+140*s+75*m;
          if(cost<best.cost){ const detail=[b?`${b}×Bulk`:null,s?`${s}×Standard`:null,m?`${m}×Mini`:null].filter(Boolean).join(' + ');
            best={name:'PAYG packs',cost,percv:(volume?cost/volume:0),credits,breakdown:detail};}
        }
      } return best;
    }
    function planOptions(volume){
      const plans=[{name:'Team (250 CVs/mo)',credits:250,cost:300,overRate:1.15},{name:'Pro (500 CVs/mo)',credits:500,cost:550,overRate:1.05},{name:'Scale (750 CVs/mo)',credits:750,cost:750,overRate:0.95},{name:'High Volume (1000 CVs/mo)',credits:1000,cost:900,overRate:0.85},{name:'Enterprise (2000+ CVs/mo)',credits:2000,cost:1500,overRate:0.60}];
      return plans.map(p=>{const over=Math.max(0,volume-p.credits); const overCost=over*p.overRate; const total=p.cost+overCost; return {name:p.name,cost:total,percv:(volume?total/volume:0),credits:p.credits,over,overRate:p.overRate};});
    }
    function calc(){
      const cvs=parseFloat(document.getElementById('cvs').value)||0;
      const mManual=parseFloat(document.getElementById('minManual').value)||0;
      const rate=parseFloat(document.getElementById('hourRate').value)||0;
      const timeSavedHours=(Math.max(0,mManual)*cvs)/60;
      const moneySaved=timeSavedHours*rate;
      document.getElementById('outHours').textContent=(Math.round(timeSavedHours*10)/10).toFixed(1);
      document.getElementById('outMoney').textContent=fmt(Math.round(moneySaved));
      const pickEl=document.getElementById('planPick'); if(!cvs){ pickEl.textContent=''; return; }
      const payg=bestPayg(cvs); const monthly=planOptions(cvs);
      const all=[{kind:'PAYG',name:payg.name,cost:payg.cost,percv:payg.percv,meta:payg},...monthly.map(x=>({kind:'Monthly',name:x.name,cost:x.cost,percv:x.percv,meta:x}))].sort((a,b)=>a.cost-b.cost);
      const best=all[0]; const percv=best.percv?` (~£${(Math.round(best.percv*100)/100).toFixed(2)}/CV)`:''; let extra='';
      if(best.kind==='PAYG'&&best.meta.breakdown){ extra=` · ${best.meta.breakdown}`; }
      if(best.kind==='Monthly'&&best.meta.over>0){ extra=` · includes ${best.meta.over} overage @ £${best.meta.overRate.toFixed(2)}`; }
      const suffix=best.kind==='Monthly'?'/mo':' total';
      const hvHint=(cvs>=900)?`<br><span class="sub">Around 1,000+ CVs/mo? High Volume or Enterprise may be cheaper.</span>`:'';
      pickEl.innerHTML=`Best option: <strong>${best.name}</strong> — <strong>${fmtGBP(best.cost)}</strong>${suffix}${percv}${extra}${hvHint}`;
    }
    document.addEventListener('input',calc); document.addEventListener('DOMContentLoaded',calc);

    /* Show/Hide extra monthly plans */
    document.addEventListener('DOMContentLoaded', function(){
      const more = document.getElementById('plansMore');
      const btn  = document.getElementById('togglePlans');
      if(btn && more){
        btn.addEventListener('click', function(){
          const show = (more.style.display==='none' || more.style.display==='');
          more.style.display = show ? 'grid' : 'none';
          btn.textContent = show ? 'Hide extra plans' : 'Show more plans';
        });
      }
    });
  </script>
</body>
</html>
"""

# ------------------------ Start Free Trial (page) ------------------------
START_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Start free trial — CV Polisher</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{--blue:#003366;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--bg:#f2f6fb;--card:#fff}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink)}
    .wrap{max-width:560px;margin:36px auto;padding:0 18px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
    h1{margin:0 0 12px;font-size:22px;color:var(--blue)}
    label{display:block;margin-top:10px;font-weight:600;font-size:13px}
    input,select,textarea{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;margin-top:6px}
    button{width:100%;margin-top:14px;background:linear-gradient(90deg,var(--blue),#0a4d8c);color:#fff;border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer}
    .muted{color:var(--muted);font-size:12px;margin-top:8px}
    a{color:var(--blue);text-decoration:none}
  </style>
</head>
<body>
  <div class="wrap">
    <a href="/">← Home</a>
    <div class="card">
      <h1>Start your free trial</h1>
      <div class="muted">Get <strong>5 free CVs</strong>. No credit card required. You’ll sign in after this.</div>

      <form method="post" action="/start" autocomplete="off">
        <label>Company</label>
        <input type="text" name="company" required />

        <label>Work email</label>
        <input type="email" name="email" required />

        <label>Your name</label>
        <input type="text" name="name" required />

        <label>Team size</label>
        <select name="team_size">
          <option value="">Select…</option>
          <option>1</option>
          <option>2–5</option>
          <option>6–10</option>
          <option>11–20</option>
          <option>21+</option>
        </select>

        <label>Notes (optional)</label>
        <textarea name="notes" rows="3" placeholder="Anything we should know?"></textarea>

        <!-- Honeypot (anti-spam). Leave empty. -->
        <input name="company_website" style="position:absolute;left:-9999px;top:-9999px" tabindex="-1" autocomplete="off"/>

        <button type="submit">Get 5 free CVs</button>
      </form>

      <div class="muted" style="margin-top:10px">
        By starting, you agree to fair use of the trial. See <a href="/about">About</a>.
      </div>
    </div>
  </div>
</body>
</html>
"""

# ------------------------ Start Free Trial (new) ------------------------
START_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Start free trial — CV Polisher</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{--blue:#003366;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--bg:#f2f6fb;--card:#fff}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink)}
    .wrap{max-width:620px;margin:36px auto;padding:0 18px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
    h1{margin:0 0 12px;font-size:26px;color:var(--blue)}
    label{font-weight:600;font-size:13px}
    input,select,textarea{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;margin-top:6px}
    button{width:100%;margin-top:12px;background:linear-gradient(90deg,#003366,#0a4d8c);color:#fff;border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer}
    .muted{color:var(--muted);font-size:12px;margin-top:8px}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    @media(max-width:640px){ .row{grid-template-columns:1fr} }
  </style>
</head>
<body>
  <div class="wrap">
    <a href="/">← Home</a>
    <div class="card">
      <h1>Start your free trial</h1>
      <p class="muted">5 free CVs, no card required. On submit you’ll be taken to Sign in. Your banner in the app will show trial credits left.</p>
      <form method="post" action="/start" autocomplete="off">
        <label>Company</label>
        <input name="company" required />
        <div class="row">
          <div>
            <label>Work email</label>
            <input name="email" type="email" required />
          </div>
          <div>
            <label>Your name</label>
            <input name="name" required />
          </div>
        </div>
        <div class="row">
          <div>
            <label>Team size</label>
            <select name="team_size">
              <option value="">Choose…</option>
              <option>Just me</option>
              <option>2–5</option>
              <option>6–15</option>
              <option>16–50</option>
              <option>50+</option>
            </select>
          </div>
          <div style="display:none">
            <!-- Honeypot (bots will fill this) -->
            <label>Website</label>
            <input name="website" />
          </div>
        </div>
        <label>Notes (optional)</label>
        <textarea name="notes" rows="4" placeholder="Anything we should know?"></textarea>
        <label style="display:flex;gap:8px;align-items:center;margin-top:10px">
          <input type="checkbox" name="agree" required style="width:auto"/> I agree to fair use of the free trial.
        </label>
        <button type="submit">Create free trial</button>
        <p class="muted" style="text-align:center">Already have an account? <a href="/login">Sign in</a></p>
      </form>
    </div>
  </div>
</body>
</html>
"""

# ------------------------ Branded App UI (unchanged except banner hook + Director button) ------------------------
HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Hamilton Recruitment — Executive Search & Selection</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
  --bg:#f6f8fb;
  --ink:#0f172a;
  --muted:#64748b;
  --line:#e5e7eb;
  --card:#ffffff;
  --blue:#0f4c81;
  --blue-2:#1a5fb4;
  --ok:#16a34a;
  --shadow:0 12px 28px rgba(2,6,23,.06);
}
*{box-sizing:border-box}
body{
  margin:0;
  color:var(--ink);
  background:var(--bg);
  font-family:Inter, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  letter-spacing:.2px;
}

/* shell */
.wrap{max-width:1160px;margin:28px auto;padding:0 20px}
.nav{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.brand-logo{width:46px;height:46px;border-radius:10px;background:linear-gradient(135deg,var(--blue),var(--blue-2));display:flex;align-items:center;justify-content:center;overflow:hidden}
.brand-logo img{width:100%;height:100%;object-fit:contain}
.brand-head{line-height:1.05}
.brand-title{margin:0;font-size:24px;font-weight:900;color:var(--blue);letter-spacing:-.01em}
.brand-sub{margin:0;color:var(--muted);font-size:12.5px}

.nav-right{margin-left:auto;display:flex;gap:10px}
.nav-right a{
  display:inline-block;background:#fff;color:var(--blue);border:1px solid var(--line);
  border-radius:12px;padding:9px 14px;font-weight:800;text-decoration:none;box-shadow:var(--shadow)
}
.nav-right a:hover{transform:translateY(-1px)}

/* layout */
.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:16px}
@media(max-width:980px){ .grid{grid-template-columns:1fr;gap:12px} }

/* cards */
.card{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:18px;
  padding:18px 20px;
  box-shadow:var(--shadow)
}
.card h3{margin:0 0 12px;color:var(--blue);font-size:18px;letter-spacing:.2px}

/* form + buttons */
label{font-weight:700;font-size:13.5px}
input[type=file]{width:100%;padding:10px;border:1px solid var(--line);border-radius:12px;margin-top:6px;background:#fff}
.ts{color:var(--muted);font-size:12.5px}
button{
  background:linear-gradient(90deg,var(--blue),var(--blue-2));color:#fff;border:none;border-radius:12px;
  padding:12px 18px;font-weight:900;cursor:pointer;box-shadow:var(--shadow)
}
button:hover{transform:translateY(-1px)}
button[disabled]{opacity:.6;cursor:not-allowed}

/* progress */
.progress{display:none;margin-top:12px;border:1px solid var(--line);border-radius:14px;padding:12px;background:var(--card)}
.stage{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:12.5px;color:var(--muted)}
.stage .dot{width:9px;height:9px;border-radius:999px;background:#cbd5e1}
.stage.active{color:var(--ink)}
.stage.active .dot{background:var(--blue)}
.stage.done{color:var(--ok)}
.stage.done .dot{background:var(--ok)}
.bar{height:12px;border-radius:999px;background:var(--line);overflow:hidden;margin-top:6px;position:relative}
.bar>span{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--blue),var(--blue-2));transition:width .35s ease}
.pct{position:absolute;right:8px;top:50%;transform:translateY(-50%);font-size:11px;color:#fff;font-weight:800}
.success{display:none;margin-top:10px;color:var(--ok);font-weight:800}

/* stats */
.statsgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px}
.stat{border:1px solid var(--line);border-radius:14px;padding:12px;background:var(--card)}
.stat .k{font-size:12px;color:var(--muted);font-weight:700}
.stat .v{font-size:18px;font-weight:900;margin-top:4px;color:var(--blue)}
.kicker{color:var(--muted);font-size:12.5px;margin:8px 0 6px}
.history{border:1px solid var(--line);border-radius:14px;max-height:300px;overflow:auto;background:var(--card)}
.row{display:flex;justify-content:space-between;gap:10px;padding:8px 12px;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:none}
.candidate{font-weight:700;font-size:13.5px}
.tsm{color:var(--muted);font-size:12px}

/* credits chip in stats */
.chip{
  display:inline-block;border:1px dashed var(--line);border-radius:12px;padding:6px 10px;font-weight:800;
  color:var(--blue);background:#fff
}
.pill{
  display:inline-flex;align-items:center;gap:6px;
  padding:6px 10px;border:1px solid var(--line);border-radius:999px;
  margin:4px 6px 0 0;font-weight:800;font-size:12px;background:#fff
}
.pill.base{opacity:.85}
.pill.off{opacity:.55;text-decoration:line-through}
.pill .x{cursor:pointer;border:none;background:transparent;font-weight:900}
  </style>
  <script>
    let timer=null, pct=0;
    function setProgress(p){
      pct = Math.max(0, Math.min(100, p));
      const bar = document.getElementById('barfill');
      const pctEl = document.getElementById('pct');
      if(bar){ bar.style.width = pct + '%'; }
      if(pctEl){ pctEl.textContent = Math.round(pct) + '%'; }
    }
    function setStage(i, total){
      const items = document.querySelectorAll('.stage');
      items.forEach((el,idx)=>{
        el.classList.remove('active','done');
        if(idx < i) el.classList.add('done');
        if(idx === i) el.classList.add('active');
      });
      setProgress((i/(total-1))*95);
    }
    function startProgress(){
      const prog = document.getElementById('progress');
      const ok = document.getElementById('success');
      const btn = document.getElementById('btn');
      if(prog) prog.style.display='block';
      if(ok) ok.style.display='none';
      if(btn) btn.disabled=true;
      const total = 5; let i = 0;
      setStage(0,total);
      if(timer){ clearInterval(timer); }
      timer = setInterval(()=>{
        if(i < total-1){ i++; setStage(i,total); } else { clearInterval(timer); }
      }, 700);
    }
    function stopProgressSuccess(){
      const prog = document.getElementById('progress');
      const ok = document.getElementById('success');
      const btn = document.getElementById('btn');
      if(timer){ clearInterval(timer); timer=null; }
      document.querySelectorAll('.stage').forEach(el=>el.classList.add('done'));
      setProgress(100);
      if(ok) ok.style.display='block';
      if(btn) btn.disabled=false;
      setTimeout(()=>{ if(prog) { prog.style.display='none'; setProgress(0);} }, 800);
      const form = document.getElementById('upload-form');
      if(form){ form.reset(); }
      const nameEl = document.getElementById('filenamePreview');
      if(nameEl){ nameEl.textContent = "—"; }
    }
    async function refreshStats(){
  try{
    const r = await fetch('/stats', {cache:'no-store'});
    if(!r.ok) return;
    const s = await r.json();

    // Trial banner
    const tb = document.getElementById('trialBanner');
    if (tb) {
      const left = s.trial_credits_left || 0;
      if (left > 0) {
        tb.style.display = 'block';
        tb.querySelector('.left').textContent = left;
      } else {
        tb.style.display = 'none';
      }
    }

    // Top stats
    const dm = document.getElementById('downloadsMonth');
    if (dm) dm.textContent = (s.downloads_this_month ?? s.downloads);
    const lc = document.getElementById('lastCandidate');
    if (lc) lc.textContent = s.last_candidate || '—';
    const lt = document.getElementById('lastTime');
    if (lt) lt.textContent = s.last_time || '—';

    // Credits (paid + trial)
    const clEl = document.getElementById('creditsLeft');
    if (clEl) {
      const creditsLeft = (((s.credits || {}).balance) || 0) + (s.trial_credits_left || 0);
      clEl.textContent = creditsLeft;
    }

    // History list
    const list = document.getElementById('history');
    if (list) {
      list.innerHTML = '';
      (s.history || []).slice().reverse().forEach(item => {
        const row = document.createElement('div'); row.className = 'row';
        const left = document.createElement('div');
        left.innerHTML = '<div class="candidate">' + (item.candidate || item.filename || '—') + '</div><div class="ts">' + (item.filename || '') + '</div>';
        const right = document.createElement('div'); right.className = 'ts';
        right.textContent = item.ts || '';
        row.appendChild(left); row.appendChild(right); list.appendChild(row);
      });
    }
  }catch(e){}
}
async function loadSkills(){
  const r = await fetch('/skills', {cache:'no-store'}); if(!r.ok) return;
  const s = await r.json(); renderSkills(s);
}
function makePill(label, actionLabel, onClick, extraClass){
  const span = document.createElement('span'); span.className='pill' + (extraClass?(' '+extraClass):'');
  span.append(document.createTextNode(label+' '));
  const b = document.createElement('button'); b.type='button'; b.className='x'; b.textContent = actionLabel;
  b.addEventListener('click', onClick); span.appendChild(b); return span;
}
function renderSkills(s){
  const custom = document.getElementById('customSkills');
  const base = document.getElementById('baseSkills');
  // sort defensively A–Z client-side too
  const sortAZ = arr => (arr||[]).slice().sort((a,b)=>a.localeCompare(b, undefined, {sensitivity:'base'}));
  if(custom){
    custom.innerHTML='';
    sortAZ(s.custom).forEach(k=>{
      custom.appendChild(makePill(k,'×',()=> removeCustom(k)));
    });
  }
  if(base){
    base.innerHTML='';
    const disabled = new Set(sortAZ(s.base_disabled).map(x=>x.toLowerCase()));
    sortAZ(s.base).forEach(k=>{
      const off = disabled.has(k.toLowerCase());
      base.appendChild(
        makePill(k, off?'Enable':'Disable', ()=> toggleBase(k, off?'enable':'disable'), 'base'+(off?' off':'')));
    });
  }
}
async function addCustom(skill){
  const fd = new FormData(); fd.append('skill', skill);
  const r = await fetch('/skills/custom/add', {method:'POST', body: fd});
  if(r.ok){ renderSkills(await r.json()); }
}
async function removeCustom(skill){
  const fd = new FormData(); fd.append('skill', skill);
  const r = await fetch('/skills/custom/remove', {method:'POST', body: fd});
  if(r.ok){ renderSkills(await r.json()); }
}
async function toggleBase(skill, action){
  const fd = new FormData(); fd.append('skill', skill); fd.append('action', action);
  const r = await fetch('/skills/base/toggle', {method:'POST', body: fd});
  if(r.ok){ renderSkills(await r.json()); }
}
    document.addEventListener('DOMContentLoaded',()=>{
      refreshStats();
      setInterval(refreshStats, 5000);

      const form = document.getElementById('upload-form');
      const fileInput = document.getElementById('cv');

      // fetch + Blob download
      form.addEventListener('submit', async (e)=>{
        e.preventDefault();
        startProgress();
        try{
          const fd = new FormData(form);
          const r = await fetch('/polish', { method:'POST', body: fd, cache:'no-store' });
          if(!r.ok) throw new Error('Server error ('+r.status+')');
          const blob = await r.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = 'polished_cv.docx';
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
          stopProgressSuccess();
          refreshStats();
        }catch(err){
          alert('Polishing failed: ' + (err?.message||'Unknown error'));
          const btn = document.getElementById('btn'); if(btn) btn.disabled=false;
          const prog = document.getElementById('progress'); if(prog) prog.style.display='none';
        }
      });

      fileInput.addEventListener('change',()=>{
        const v=fileInput.files?.[0]?.name||'';
        const name=v.replace(/[_-]/g,' ').replace(/\.(pdf|docx|txt)$/i,'');
        if(name){document.getElementById('filenamePreview').textContent=name;}
      });
      fileInput.addEventListener('change',()=>{
  const v=fileInput.files?.[0]?.name||'';
  const name=v.replace(/[_-]/g,' ').replace(/\.(pdf|docx|txt)$/i,'');
  if(name){document.getElementById('filenamePreview').textContent=name;}
});

// Skills manager toggle + events
const skillsToggle = document.getElementById('skillsToggle');
const skillsCard = document.getElementById('skillsCard');
if (skillsToggle && skillsCard){
  skillsToggle.addEventListener('click', ()=>{
    const show = skillsCard.style.display === 'none' || skillsCard.style.display === '';
    skillsCard.style.display = show ? 'block' : 'none';
    skillsToggle.textContent = show ? 'Hide' : 'Show';
    if (show) loadSkills();
  });
}
const skillForm = document.getElementById('skillAddForm');
if (skillForm){
  skillForm.addEventListener('submit', (e)=>{
    e.preventDefault();
    const inp = document.getElementById('skillInput');
    const val = (inp.value||'').trim();
    if (val){ addCustom(val); inp.value=''; }
  });
}

});
</script>
    });
  </script>
</head>
<body>
  <div class="wrap">
    <div class="nav">
      <div class="brand-logo"><img src="/logo" alt="Hamilton Logo" onerror="this.style.display='none'"/></div>
      <div class="brand-head">
        <p class="brand-title">Hamilton Recruitment</p>
        <p class="brand-sub">Executive Search &amp; Selection</p>
      </div>
      <div style="margin-left:auto; display:flex; gap:8px;">
        <!-- NEW: Director button -->
        <a href="/director" style="display:inline-block;background:#fff;color:var(--blue);border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-weight:700;text-decoration:none">Director</a>
        <a href="/logout" style="display:inline-block;background:#fff;color:var(--blue);border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-weight:700;text-decoration:none">Log out</a>
      </div>
    </div>

    <!-- NEW: free-trial banner -->
    <div id="trialBanner" class="card" style="display:none; margin-bottom:12px">
      <strong>Free trial:</strong> <span class="left">5</span> CVs left.
      <span class="ts">Need more? <a href="/pricing">See plans</a></span>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Upload CV</h3>
        <form id="upload-form" method="post" action="/polish" enctype="multipart/form-data">
          <label for="cv">Raw CV (PDF / DOCX / TXT)</label><br/>
          <input id="cv" type="file" name="cv" accept=".pdf,.docx,.txt" required />
          <div class="ts" style="margin-top:4px">Header (logo &amp; bar) preserved from Hamilton template.</div>
          <div class="ts" style="margin-top:2px">Candidate: <span id="filenamePreview">—</span></div>

          <div id="progress" class="progress">
            <div class="stage"><div class="dot"></div><div>Uploading</div></div>
            <div class="stage"><div class="dot"></div><div>Extracting text</div></div>
            <div class="stage"><div class="dot"></div><div>Structuring</div></div>
            <div class="stage"><div class="dot"></div><div>Composing document</div></div>
            <div class="stage"><div class="dot"></div><div>Downloading</div></div>
            <div class="bar"><span id="barfill"></span><span id="pct" class="pct">0%</span></div>
          </div>

          <div id="success" class="success">Done. Your download should start automatically.</div>

          <div style="margin-top:12px"><button id="btn" type="submit">Polish & Download</button></div>
        </form>
      </div>

      <div class="card">
        <h3>Session Stats</h3>
        <div class="statsgrid">
  <div class="stat"><div class="k">Downloads this month</div><div class="v" id="downloadsMonth">0</div></div>
  <div class="stat"><div class="k">Last Candidate</div><div class="v" id="lastCandidate">—</div></div>
  <div class="stat"><div class="k">Last Polished</div><div class="v" id="lastTime">—</div></div>
  <div class="stat"><div class="k">Credits left</div><div class="v" id="creditsLeft">0</div></div>
</div>
<div class="ts" style="margin:6px 0 10px 2px;">Low on credits? <a href="/pricing">Buy more</a></div>

        <div class="ts" style="margin:8px 0 6px 2px;">Full History</div>
        <div id="history" class="history"></div>
        <!-- Skills manager (hide/show) -->
<div class="kicker" style="margin:10px 0 6px 2px; display:flex; align-items:center; justify-content:space-between">
  <span>Skills dictionary (matching keywords)</span>
  <button id="skillsToggle" type="button" class="chip">Show</button>
</div>
<div id="skillsCard" class="ts" style="display:none; border:1px dashed var(--line); border-radius:12px; padding:10px; background:#fff">
  <div class="ts" style="margin-bottom:8px">
    These keywords are used to surface <strong>Skills</strong> when polishing. Add your own, remove yours, or disable built-ins.
  </div>
  <form id="skillAddForm" style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px">
    <input id="skillInput" placeholder="Add a skill (e.g., ACCA)" style="flex:1; min-width:220px; padding:10px; border:1px solid var(--line); border-radius:10px"/>
    <button type="submit">Add</button>
  </form>
  <div class="ts" style="margin:6px 0 2px">Custom skills (A–Z)</div>
  <div id="customSkills"></div>
  <div class="ts" style="margin:10px 0 2px">Built-in skills (A–Z)</div>
  <div id="baseSkills"></div>
</div>
      </div>
    </div>
  </div>
</body>
</html>
"""

# ------------------------ Login page HTML (unchanged, plus a small "Forgot password?" link) ------------------------
LOGIN_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>CVStudio — Sign in</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
  --blue:#2563eb;      /* vivid indigo */
  --blue-2:#22d3ee;    /* bright cyan  */
  --ink:#0f172a; --muted:#5b677a; --line:#e5e7eb;
  --bg:#f5f8fd; --card:#ffffff; --shadow: 0 10px 28px rgba(13,59,102,.08);
}
    *{box-sizing:border-box}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}
    .wrap{max-width:1100px;margin:12px auto 56px;padding:0 20px}
    .nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
    .brand{font-weight:900;color:var(--blue);text-decoration:none;font-size:22px;letter-spacing:.2px}
    .nav a{color:var(--ink);text-decoration:none;font-weight:800;margin-left:22px}

    .auth{max-width:520px;margin:28px auto 0;background:var(--card);border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:var(--shadow)}
    h1{margin:0 0 12px;font-size:28px;color:var(--blue)}
    label{font-weight:600;font-size:13px}
    input[type=text],input[type=password]{width:100%;padding:12px;border:1px solid var(--line);border-radius:12px;margin-top:6px}
    button{width:100%;margin-top:14px;background:linear-gradient(90deg,var(--blue),var(--blue-2));color:#fff;border:none;border-radius:12px;padding:12px 16px;font-weight:800;cursor:pointer;box-shadow:var(--shadow)}
    .muted{color:var(--muted);font-size:12px;text-align:center;margin-top:10px}
    .err{margin-top:8px;color:#b91c1c;font-weight:800;font-size:12px}
    a{color:var(--blue);text-decoration:none}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="nav">
      <a class="brand" href="/">CVStudio</a>
      <div>
  <a href="/">Home</a>
  <a href="/about" style="margin-left:18px">About</a>
  <a href="/login" style="margin-left:18px">Sign in</a>
</div>
    </div>

    <div class="auth">
      <h1>Sign in</h1>
      <!--ERROR-->
      <form method="post" action="/login" autocomplete="off">
        <label for="username">Username</label>
        <input id="username" type="text" name="username" autofocus required />
        <div style="height:10px"></div>
        <label for="password">Password</label>
        <input id="password" type="password" name="password" required />
        <button type="submit">Continue</button>
      </form>
      <div class="muted">
        Default demo: admin / hamilton • <a href="/forgot">Forgot password?</a> • <a href="/">Home</a>
      </div>
    </div>
  </div>
</body>
</html>
"""

# ------------------------ Director: gate + pages ------------------------
DIRECTOR_PASS = os.getenv("DIRECTOR_PASS", "director")
RESET_CODE = os.getenv("RESET_CODE", "reset123")  # used for password resets

DIRECTOR_LOGIN_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Director access</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{--blue:#003366;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--bg:#f2f6fb;--card:#ffffff}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}
    .wrap{max-width:520px;margin:48px auto;padding:0 18px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
    label{font-weight:600;font-size:13px}
    input[type=password]{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;margin-top:6px}
    button{width:100%;margin-top:12px;background:linear-gradient(90deg,var(--blue),#0a4d8c);color:#fff;border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer}
    .muted{color:var(--muted);font-size:12px;text-align:center;margin-top:8px}
    .err{margin-top:8px;color:#b91c1c;font-weight:700;font-size:12px}
    a{color:var(--blue);text-decoration:none}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h3>Director verification</h3>
      <!--DERR-->
      <form method="post" action="/director/login">
        <label for="dp">Director password</label>
        <input id="dp" type="password" name="password" autofocus required />
        <button type="submit">Enter</button>
      </form>
      <div class="muted"><a href="/director/forgot">Forgot director password?</a> · <a href="/app">Back</a></div>
    </div>
  </div>
</body>
</html>
"""

DIRECTOR_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Director — Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{--blue:#003366;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--bg:#f2f6fb;--card:#ffffff;--ok:#16a34a}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}
    .wrap{max-width:1100px;margin:28px auto;padding:0 18px}
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
    h1{margin:0 0 12px;color:var(--blue);font-size:22px}
    h3{margin:0 0 10px;color:var(--blue);font-size:16px}
    .k{color:var(--muted);font-size:12px}
    table{width:100%;border-collapse:collapse}
    th,td{border-bottom:1px solid var(--line);padding:8px;text-align:left;font-size:13px}
    .actions a,.actions button{display:inline-block;margin-right:6px;margin-top:6px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:6px 10px;font-weight:700;text-decoration:none;color:var(--blue);cursor:pointer}
    .ok{color:var(--ok);font-weight:700}
    input,select{padding:8px;border:1px solid var(--line);border-radius:10px}
    form.inline{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Director dashboard</h1>
    <div class="actions">
      <a href="/director/export.csv">Export CSV</a>
      <a href="/pricing">Buy more credits</a>
      <a href="/app">← Back to app</a>
    </div>

    <div class="grid" style="margin-top:12px">
      <div class="card">
        <h3>Usage stats</h3>
        <div class="k">This month: <strong>{{m1}}</strong> · 3 mo: <strong>{{m3}}</strong> · 6 mo: <strong>{{m6}}</strong> · 12 mo: <strong>{{m12}}</strong> · Total: <strong>{{tot}}</strong></div>
        <div class="k" style="margin-top:6px">Last candidate: <strong>{{last_candidate}}</strong> · Last time: <strong>{{last_time}}</strong></div>
      </div>

      <div class="card">
        <h3>Credits (manual)</h3>
        <div class="k">Balance: <strong>{{credits_balance}}</strong> · Purchased: <strong>{{credits_purchased}}</strong></div>
        <form class="inline" method="post" action="/director/credits/add">
          <label>Add credits:</label>
          <input name="amount" type="number" min="1" step="1" required />
          <button type="submit">Add</button>
        </form>
        <div class="k" style="margin-top:6px">Trial credits (this session): <strong>{{trial_left}}</strong></div>
      </div>

      <div class="card" style="grid-column:1 / -1">
        <h3>Users</h3>
        <table>
          <thead><tr><th>Username</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>
          {% for u in users %}
            <tr>
              <td>{{u.username}}</td>
              <td>{{'active' if u.active else 'disabled'}}</td>
              <td>
                <form method="post" action="/director/users/toggle" style="display:inline">
                  <input type="hidden" name="username" value="{{u.username}}"/>
                  <input type="hidden" name="action" value="{{'disable' if u.active else 'enable'}}"/>
                  <button type="submit">{{'Disable' if u.active else 'Enable'}}</button>
                </form>
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
        <h3 style="margin-top:14px">Create user</h3>
        <form class="inline" method="post" action="/director/users/create">
          <input name="username" placeholder="username" required />
          <input name="password" placeholder="password" required />
          <button type="submit">Create</button>
        </form>
      </div>

      <div class="card" style="grid-column:1 / -1">
        <h3>Recent activity</h3>
        <table>
          <thead><tr><th>Time</th><th>Candidate</th><th>Filename</th></tr></thead>
          <tbody>
          {% for item in history %}
            <tr><td>{{item.ts}}</td><td>{{item.candidate or '—'}}</td><td>{{item.filename}}</td></tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</body>
</html>
"""

DIRECTOR_FORGOT_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Reset director password</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{--blue:#003366;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--bg:#f2f6fb;--card:#ffffff}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}
    .wrap{max-width:520px;margin:48px auto;padding:0 18px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
    label{font-weight:600;font-size:13px}
    input{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;margin-top:6px}
    button{width:100%;margin-top:12px;background:linear-gradient(90deg,var(--blue),#0a4d8c);color:#fff;border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer}
    .muted{color:var(--muted);font-size:12px;text-align:center;margin-top:8px}
    .err{margin-top:8px;color:#b91c1c;font-weight:700;font-size:12px}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h3>Reset director password</h3>
      <!--RERR-->
      <form method="post" action="/director/forgot">
        <label>Reset code</label>
        <input type="text" name="code" required />
        <label>New director password</label>
        <input type="password" name="newpass" required />
        <button type="submit">Set new password</button>
      </form>
      <div class="muted">Back to <a href="/director">Director login</a></div>
    </div>
  </div>
</body>
</html>
"""

FORGOT_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Reset password</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{--blue:#003366;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--bg:#f2f6fb;--card:#ffffff}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}
    .wrap{max-width:520px;margin:48px auto;padding:0 18px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
    label{font-weight:600;font-size:13px}
    input{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;margin-top:6px}
    button{width:100%;margin-top:12px;background:linear-gradient(90deg,var(--blue),#0a4d8c);color:#fff;border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer}
    .muted{color:var(--muted);font-size:12px;text-align:center;margin-top:8px}
    .err{margin-top:8px;color:#b91c1c;font-weight:700;font-size:12px}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h3>Reset password</h3>
      <!--FERR-->
      <form method="post" action="/forgot">
        <label>Username</label>
        <input type="text" name="username" required />
        <label>Reset code</label>
        <input type="text" name="code" required />
        <label>New password</label>
        <input type="password" name="newpass" required />
        <button type="submit">Reset</button>
      </form>
      <div class="muted">Back to <a href="/login">Sign in</a></div>
    </div>
  </div>
</body>
</html>
"""

app = Flask(__name__)

# ------------------------ session secret + default creds (unchanged) ------------------------
app.secret_key = os.getenv("APP_SECRET_KEY", "dev-secret-change-me")
APP_ADMIN_USER = os.getenv("APP_ADMIN_USER", "admin")
APP_ADMIN_PASS = os.getenv("APP_ADMIN_PASS", "hamilton")

# ------------------------ Gate protected routes (/app, /polish, /stats, /director*) ------------------------
@app.before_request
def gate_protected_routes():
    protected_prefixes = ["/app", "/polish", "/stats", "/director", "/skills"]
    p = request.path or "/"
    if any(p.startswith(x) for x in protected_prefixes):
        if not session.get("authed"):
            return redirect(url_for("login"))

# ------------------------ Public routes ------------------------
@app.get("/")
def home():
    resp = make_response(render_template_string(HOMEPAGE_HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.get("/about")
def about():
    resp = make_response(render_template_string(ABOUT_HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.get("/pricing")
def pricing():
    resp = make_response(render_template_string(PRICING_HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.get("/trial")
def start_trial():
    # Keep buttons working: send them to the new Start Free Trial form
    return redirect("/start")

# --- Start Free Trial: new routes ---
@app.get("/start")
def start_free_trial_form():
    resp = make_response(render_template_string(START_HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.post("/start")
def start_free_trial_submit():
    # Honeypot: if filled, treat as bot, but still continue to login
    if (request.form.get("company_website") or "").strip():
        return redirect(url_for("login"))

    data = {
        "company": (request.form.get("company") or "").strip(),
        "email": (request.form.get("email") or "").strip(),
        "name": (request.form.get("name") or "").strip(),
        "team_size": (request.form.get("team_size") or "").strip(),
        "notes": (request.form.get("notes") or "").strip(),
    }

    # Basic validation
    if not data["company"] or not data["email"] or not data["name"]:
        return redirect("/start")

    # Give 5 trial credits in this session
    session["trial_credits"] = 5

    # Log the request (safe if logger not present)
    try:
        _log_trial_request(data)
    except Exception:
        pass

    # Send them to Sign in — your banner will show the 5 credits
    return redirect(url_for("login"))

# ------------------------ Auth routes (unchanged, plus user DB support) ------------------------
@app.get("/login")
def login():
    if session.get("authed"):
        return redirect(url_for("app_page"))  # goes to /app
    resp = make_response(render_template_string(LOGIN_HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.post("/login")
def do_login():
    user = (request.form.get("username") or "").strip()
    pw = (request.form.get("password") or "").strip()
    # Keep env-admin working
    if user == APP_ADMIN_USER and pw == APP_ADMIN_PASS:
        session["authed"] = True
        session["user"] = user
        return redirect(url_for("app_page"))
    # Recruiter users from users.json
    u = _get_user(user)
    if u and u.get("active", True) and pw == u.get("password", ""):
        session["authed"] = True
        session["user"] = user
        return redirect(url_for("app_page"))
    html = LOGIN_HTML.replace("<!--ERROR-->", "<div class='err'>Invalid credentials</div>")
    resp = make_response(render_template_string(html))
    resp.headers["Cache-Control"] = "no-store"
    return resp, 401

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------- forgot password (for recruiter users in users.json) ----------
@app.get("/forgot")
def forgot_get():
    resp = make_response(render_template_string(FORGOT_HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.post("/forgot")
def forgot_post():
    username = (request.form.get("username") or "").strip()
    code = (request.form.get("code") or "").strip()
    newpass = (request.form.get("newpass") or "")
    if code != RESET_CODE:
        html = FORGOT_HTML.replace("<!--FERR-->", "<div class='err'>Invalid reset code</div>")
        return render_template_string(html), 400
    u = _get_user(username)
    if not u:
        html = FORGOT_HTML.replace("<!--FERR-->", "<div class='err'>User not found</div>")
        return render_template_string(html), 404
    u["password"] = newpass
    _save_users()
    return redirect(url_for("login"))

# ---------- serve the logo ----------
@app.get("/logo")
def logo():
    for name in ["Imagem1.png", "hamilton_logo.png", "logo.png"]:
        p = PROJECT_DIR / name
        if p.exists():
            resp = make_response(send_file(str(p)))
            resp.headers["Cache-Control"] = "no-store"
            return resp
    return ("", 204)

# ---------- helper: Word field ----------
def _add_field(paragraph, instr_text: str):
    fld = OxmlElement('w:fldSimple')
    fld.set(qn('w:instr'), instr_text)
    r = OxmlElement('w:r')
    t = OxmlElement('w:t'); t.text = ""
    r.append(t); fld.append(r)
    paragraph._p.append(fld)

# ---------- Extraction ----------
def extract_text_any(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        if fitz is not None:
            try:
                parts = []
                with fitz.open(str(path)) as doc:
                    for page in doc:
                        parts.append(page.get_text("text"))
                return "\n".join(parts) or ""
            except Exception:
                pass
        return pdf_extract_text(str(path)) or ""
    elif ext == ".docx":
        d = Docx(str(path))
        parts = []
        for p in d.paragraphs:
            if p.text: parts.append(p.text)
        for table in d.tables:
            for row in table.rows:
                cells = [c.text for c in row.cells if c.text]
                if cells: parts.append(" | ".join(cells))
        return "\n".join(parts)
    else:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except:
            return ""

# ---------- AI structuring ----------
SCHEMA_PROMPT = """
You are a CV structuring assistant for recruiters. Extract ONLY what exists in the CV and return STRICT JSON:

{
  "personal_info": {"full_name":"","email":"","phone":"","location":"","links":[]},
  "summary":"",
  "experience":[{"job_title":"","company":"","location":"","start_date":"","end_date":"","currently_employed":false,"bullets":[],"raw_text":""}],
  "education":[{"degree":"","institution":"","location":"","start_date":"","end_date":"","bullets":[]}],
  "skills":[],
  "certifications":[],
  "languages":[],
  "awards":[],
  "other":[{"section_title":"","items":[]}]
}

Rules:
- Do NOT invent or embellish content.
- Preserve wording verbatim where possible.
- Use "currently_employed": true when the role is ongoing; leave "end_date" empty in that case.
"""

def ai_or_heuristic_structuring(cv_text: str) -> dict:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.chatCompletions.create(
                model=MODEL,
                messages=[{"role":"system","content":SCHEMA_PROMPT},
                          {"role":"user","content":cv_text}],
                temperature=0
            )
            out = resp.choices[0].message.content.strip()
            if out.startswith("```"):
                out = out.strip("`")
                if out.lower().startswith("json"):
                    out = out[4:].strip()
            import json as _json
            return _json.loads(out)
        except Exception:
            try:
                from openai import OpenAI
                client = OpenAI()
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role":"system","content":SCHEMA_PROMPT},
                              {"role":"user","content":cv_text}],
                    temperature=0
                )
                out = resp.choices[0].message.content.strip()
                if out.startswith("```"):
                    out = out.strip("`")
                    if out.lower().startswith("json"):
                        out = out[4:].strip()
                import json as _json
                return _json.loads(out)
            except Exception as e:
                print("OpenAI failed, falling back to heuristics:", e)
                print(traceback.format_exc())

    # Fallback heuristic
    blocks = {"summary":[],"experience":[],"education":[],"skills":[],"certifications":[],"languages":[],"awards":[]}
    current = "summary"
    for ln in cv_text.splitlines():
        U = ln.upper()
        if re.search(r'\b(EXPERIENCE|EMPLOYMENT|CAREER|PROFESSIONAL EXPERIENCE)\b', U):
            current = "experience"; continue
        if "EDUCATION" in U or "QUALIFICATIONS" in U:
            current = "education"; continue
        if "SKILLS" in U:
            current = "skills"; continue
        if "CERTIFICATION" in U or "QUALIFICATION" in U:
            current = "certifications"; continue
        if "LANGUAGE" in U:
            current = "languages"; continue
        if "AWARD" in U or "HONOR" in U:
            current = "awards"; continue
        blocks[current].append(ln.strip())

    def pack(bl): return [x for x in bl if x][:30]
    return {
        "personal_info":{"full_name":"","email":"","phone":"","location":"","links":[]},
        "summary":" ".join(blocks["summary"])[:2000],
        "experience":[{"job_title":"","company":"","location":"","start_date":"","end_date":"","currently_employed":False,"bullets":pack(blocks["experience"]), "raw_text":""}],
        "education":[{"degree":"","institution":"","location":"","start_date":"","end_date":"","bullets":pack(blocks["education"])}],
        "skills":pack(blocks["skills"]),
        "certifications":pack(blocks["certifications"]),
        "languages":pack(blocks["languages"]),
        "awards":pack(blocks["awards"]),
        "other":[]
    }

# ---------- Skills booster (keywords only) ----------
SKILL_CANON = [
    "IFRS","US GAAP","BMA","Statutory Reporting","Reinsurance","Captives",
    "Life Insurance","P&C","Audit","SOX","IFRS 17","Pricing","Reserving",
    "Risk Management","Credit Risk","Financial Modeling","Power BI","Alteryx",
    "SQL","VBA","Prophet","Anaplan","Internal Controls","Regulatory Compliance",
    "Underwriting","NatCat","Catastrophe Modelling"
]
# --- Per-client skills config (custom skills + disabled built-ins) ---
SKILLS_FILE = PROJECT_DIR / "skills.json"

def _load_skills_config():
    try:
        if SKILLS_FILE.exists():
            return json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    # default: no custom, nothing disabled
    return {"custom": [], "base_disabled": []}

SKILLS_CFG = _load_skills_config()

def _save_skills_config():
    SKILLS_FILE.write_text(json.dumps(SKILLS_CFG, indent=2), encoding="utf-8")

def _effective_skills():
    """Built-ins minus disabled + custom (dedup, case-insensitive)."""
    disabled = {s.lower() for s in SKILLS_CFG.get("base_disabled", [])}
    base = [s for s in SKILL_CANON if s.lower() not in disabled]
    custom = [s.strip() for s in SKILLS_CFG.get("custom", []) if isinstance(s, str) and s.strip()]
    eff, seen = [], set()
    for s in base + custom:
        k = s.lower()
        if k not in seen:
            seen.add(k); eff.append(s)
    return eff
def extract_top_skills(text: str):
    tokens = re.findall(r"[A-Za-z0-9\-\&\./+]+", text)
    txt_up = " ".join(tokens).upper()
    canon = _effective_skills()
    found, seen = [], set()
    for s in canon:
        if s.upper() in txt_up:
            k = s.lower()
            if k not in seen:
                seen.add(k)
                found.append(s)
    return found[:25]

# ---------- Word helpers ----------
SOFT_BLACK = RGBColor(64, 64, 64)

def _tone_runs(paragraph, size=11, bold=False, color=SOFT_BLACK, name="Calibri"):
    for run in paragraph.runs:
        run.font.name = name
        run.font.size = Pt(size)
        run.bold = bool(bold)
        run.font.color.rgb = color

def _add_center_line(doc: Docx, text: str, size=11, bold=False, space_after=0):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(space_after)
    r = p.add_run(text)
    r.font.name = "Calibri"; r.font.size = Pt(size); r.bold = bool(bold); r.font.color.rgb = SOFT_BLACK
    return p

def _add_section_heading(doc: Docx, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(text.upper().strip())
    r.font.name = "Calibri"; r.font.size = Pt(14)
    r.bold = True
    r.font.color.rgb = SOFT_BLACK
    return p

def _remove_all_body_content(doc: Docx):
    for t in list(doc.tables):
        t._element.getparent().remove(t._element)
    for p in list(doc.paragraphs):
        p._element.getparent().remove(p._element)

# ---------- Post-save zip scrub of header XML ----------
def _zip_scrub_header_labels(docx_path: Path):
    pat_one = re.compile(
        r'<w:p\b[^>]*>.*?(?:professional).*?(?:experience).*?(?:continued).*?</w:p>',
        re.I | re.S
    )
    pat_two = re.compile(
        r'(<w:p\b[^>]*>.*?(?:professional).*?(?:experience).*?</w:p>)\s*(<w:p\b[^>]*>.*?(?:continued).*?</w:p>)',
        re.I | re.S
    )
    blank_p = '<w:p><w:r><w:t> </w:t></w:r></w:p>'

    src = str(docx_path)
    tmp = str(docx_path.with_suffix('.tmp.docx'))

    with zipfile.ZipFile(src, 'r') as zin, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith('word/header') and item.filename.endswith('.xml'):
                xml = data.decode('utf-8', errors='ignore')
                xml = pat_two.sub(blank_p, xml)
                xml = pat_one.sub(blank_p, xml)
                data = xml.encode('utf-8')
            zout.writestr(item, data)

    os.replace(tmp, src)

# ---------- Ensure a spacer paragraph in primary headers (pages 2+) ----------
def _ensure_primary_header_spacer(doc: Docx):
    try:
        for sec in doc.sections:
            hdr = sec.header
            needs = True
            try:
                if hdr.paragraphs:
                    if (hdr.paragraphs[-1].text or "").strip() == "":
                        needs = False
            except Exception:
                pass
            if needs:
                p = hdr.add_paragraph()
                r = p.add_run(" ")
    except Exception:
        pass

# ---------- Compose CV ----------
def build_cv_document(cv: dict) -> Path:
    template_path = None
    for pth in [PROJECT_DIR / "hamilton_template.docx",
                PROJECT_DIR / "HAMILTON TEMPLATE.docx",
                PROJECT_DIR / "master_template.docx"]:
        if pth.exists():
            template_path = pth; break

    if template_path:
        doc = Docx(str(template_path))
    else:
        doc = Docx()

    _remove_all_body_content(doc)

    spacer = doc.add_paragraph(); spacer.paragraph_format.space_after = Pt(6)

    pi = (cv or {}).get("personal_info") or {}
    full_name = (pi.get("full_name") or "Candidate").strip()
    name_p = doc.add_paragraph(); name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER; name_p.paragraph_format.space_after = Pt(2)
    name_r = name_p.add_run(full_name)
    name_r.font.name="Calibri"; name_r.font.size=Pt(18); name_r.bold=True; name_r.font.color.rgb=SOFT_BLACK

    tel = (pi.get("phone") or "").strip()
    email = (pi.get("email") or "").strip()
    location = (pi.get("location") or "").strip()
    bits = []
    if tel: bits.append(f"Tel: {tel}")
    if email: bits.append(f"Email: {email}")
    if bits:
        _add_center_line(doc, " | ".join(bits), size=11, bold=False, space_after=0)
    if location:
        _add_center_line(doc, f"Location: {location}", size=11, bold=False, space_after=6)
    links = [s for s in (pi.get("links") or []) if s]
    if links: _add_center_line(doc, " | ".join(links), size=11, bold=False, space_after=6)

    if cv.get("summary"):
        _add_section_heading(doc, "EXECUTIVE SUMMARY")
        p = doc.add_paragraph(cv["summary"]); p.paragraph_format.space_after = Pt(8); _tone_runs(p, size=11, bold=False)

    quals = []
    if cv.get("certifications"): quals += [q for q in cv["certifications"] if q]
    edu = cv.get("education") or []
    for ed in edu:
        deg = (ed.get("degree") or "").strip()
        inst = (ed.get("institution") or "").strip()
        date_span = " – ".join([x for x in [(ed.get("start_date") or "").strip(), (ed.get("end_date") or "").strip()] if x]).strip(" –")
        line = " | ".join([s for s in [deg, inst, date_span] if s])
        if line: quals.append(line)
    if quals:
        _add_section_heading(doc, "PROFESSIONAL QUALIFICATIONS")
        for q in quals:
            p = doc.add_paragraph(q, style="List Bullet")
            p.paragraph_format.space_before = Pt(0); p.paragraph_format.space_after = Pt(0)
            _tone_runs(p, size=11, bold=False)

    skills = cv.get("skills") or []
    if skills:
        _add_section_heading(doc, "PROFESSIONAL SKILLS")
        line = " | ".join(skills)
        p = doc.add_paragraph(line); p.paragraph_format.space_after = Pt(8); _tone_runs(p, size=11, bold=False)

    exp = cv.get("experience") or []
    if exp:
        _add_section_heading(doc, "PROFESSIONAL EXPERIENCE")
        first = True
        for role in exp:
            if not first:
                g = doc.add_paragraph(); g.paragraph_format.space_after = Pt(8); _tone_runs(g, size=11, bold=False)
            first = False

            title_company = " — ".join([x for x in [role.get("job_title",""), role.get("company","")] if x]).strip()
            p = doc.add_paragraph(); r = p.add_run(title_company or "Role")
            r.font.name="Calibri"; r.font.size=Pt(11); r.bold=True; r.font.color.rgb=SOFT_BLACK
            p.paragraph_format.space_after = Pt(0)

            sd = (role.get("start_date") or "").strip()
            edd = (role.get("end_date") or "").strip()
            if role.get("currently_employed") and not edd: edd = "Present"
            dates = f"{sd} – {edd}".strip(" –")
            loc = (role.get("location") or "").strip()
            meta = " | ".join([x for x in [dates, loc] if x])
            if meta:
                meta_p = doc.add_paragraph(meta); meta_p.paragraph_format.space_after = Pt(6); _tone_runs(meta_p, size=11, bold=False)

            if role.get("bullets"):
                for b in role["bullets"]:
                    bp = doc.add_paragraph(b, style="List Bullet")
                    bp.paragraph_format.space_before = Pt(0); bp.paragraph_format.space_after = Pt(0)
                    _tone_runs(bp, size=11, bold=False)
            elif role.get("raw_text"):
                rp = doc.add_paragraph(role["raw_text"]); rp.paragraph_format.space_after = Pt(0); _tone_runs(rp, size=11, bold=False)

    if edu:
        _add_section_heading(doc, "EDUCATION")
        for ed in edu:
            line = " — ".join([x for x in [ed.get("degree",""), ed.get("institution","")] if x]).strip()
            p = doc.add_paragraph(); rr = p.add_run(line or "Education")
            rr.font.name="Calibri"; rr.font.size=Pt(11); rr.bold=True; rr.font.color.rgb=SOFT_BLACK
            p.paragraph_format.space_after = Pt(0)

            sd = (ed.get("start_date") or "").strip()
            ee = (ed.get("end_date") or "").strip()
            dates = f"{sd} – {ee}".strip(" –")
            loc = (ed.get("location") or "").strip()
            meta = " | ".join([x for x in [dates, loc] if x])
            if meta:
                meta_p = doc.add_paragraph(meta); meta_p.paragraph_format.space_after = Pt(2); _tone_runs(meta_p, size=11, bold=False)

            if ed.get("bullets"):
                for b in ed["bullets"]:
                    bp = doc.add_paragraph(b, style="List Bullet")
                    bp.paragraph_format.space_before = Pt(0); bp.paragraph_format.space_after = Pt(0)
                    _tone_runs(bp, size=11, bold=False)

    _ensure_primary_header_spacer(doc)

    out = PROJECT_DIR / "polished_cv.docx"
    doc.save(str(out))
    _zip_scrub_header_labels(out)
    return out

# ---------- helpers ----------
def _downloads_this_month():
    try:
        now = datetime.now()
        ym = (now.year, now.month)
        count = 0
        for item in STATS.get("history", []):
            ts = item.get("ts")
            if not ts: continue
            d = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            if (d.year, d.month) == ym:
                count += 1
        return count
    except Exception:
        return STATS.get("downloads", 0)

def _count_since(months: int) -> int:
    try:
        cutoff = datetime.now() - timedelta(days=30*months)
        c = 0
        for item in STATS.get("history", []):
            ts = item.get("ts")
            if not ts: continue
            d = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            if d >= cutoff:
                c += 1
        return c
    except Exception:
        return 0

# ---------- App + API ----------
@app.get("/app")
def app_page():
    resp = make_response(render_template_string(HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.get("/stats")
def stats():
    data = dict(STATS)
    data["downloads_this_month"] = _downloads_this_month()
    # NEW: include trial credits left for the banner
    data["trial_credits_left"] = int(session.get("trial_credits", 0))
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp
    # ---------- Skills API (view/add/remove/toggle) ----------
@app.get("/skills")
def skills_get():
    data = {
        "base": sorted(SKILL_CANON, key=lambda s: s.lower()),
        "custom": sorted(SKILLS_CFG.get("custom", []), key=lambda s: s.lower()),
        "base_disabled": sorted(SKILLS_CFG.get("base_disabled", []), key=lambda s: s.lower()),
        "effective": sorted(_effective_skills(), key=lambda s: s.lower()),
    }
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.post("/skills/custom/add")
def skills_custom_add():
    # accept form or JSON
    skill = (request.form.get("skill") or (request.json.get("skill") if request.is_json else "")).strip()
    if not skill or len(skill) > 60 or re.search(r"[<>]", skill):
        abort(400, "Invalid skill")
    custom = SKILLS_CFG.setdefault("custom", [])
    if skill.lower() not in {x.lower() for x in custom} and skill.lower() not in {x.lower() for x in SKILL_CANON}:
        custom.append(skill)
        custom.sort(key=lambda s: s.lower())
        _save_skills_config()
    return jsonify({
        "ok": True,
        "custom": sorted(SKILLS_CFG["custom"], key=lambda s: s.lower()),
        "effective": sorted(_effective_skills(), key=lambda s: s.lower())
    })

@app.post("/skills/custom/remove")
def skills_custom_remove():
    skill = (request.form.get("skill") or (request.json.get("skill") if request.is_json else "")).strip()
    custom = SKILLS_CFG.setdefault("custom", [])
    SKILLS_CFG["custom"] = [x for x in custom if x.lower() != skill.lower()]
    _save_skills_config()
    return jsonify({
        "ok": True,
        "custom": sorted(SKILLS_CFG["custom"], key=lambda s: s.lower()),
        "effective": sorted(_effective_skills(), key=lambda s: s.lower())
    })

@app.post("/skills/base/toggle")
def skills_base_toggle():
    skill = (request.form.get("skill") or (request.json.get("skill") if request.is_json else "")).strip()
    action = (request.form.get("action") or (request.json.get("action") if request.is_json else "")).strip().lower()
    if skill not in SKILL_CANON:
        abort(400, "Unknown built-in skill")
    disabled = {*(SKILLS_CFG.setdefault("base_disabled", []))}
    if action == "disable":
        disabled.add(skill)
    elif action == "enable":
        disabled.discard(skill)
    else:
        abort(400, "Bad action")
    SKILLS_CFG["base_disabled"] = sorted(disabled, key=lambda s: s.lower())
    _save_skills_config()
    return jsonify({
        "ok": True,
        "base_disabled": sorted(SKILLS_CFG["base_disabled"], key=lambda s: s.lower()),
        "effective": sorted(_effective_skills(), key=lambda s: s.lower())
    })

# ---------- Director routes ----------
@app.get("/director")
def director_home():
    if not session.get("director"):
        html = DIRECTOR_LOGIN_HTML
        return render_template_string(html)
    # Render dashboard
    ctx = {
        "m1": _count_since(1),
        "m3": _count_since(3),
        "m6": _count_since(6),
        "m12": _count_since(12),
        "tot": len(STATS.get("history", [])),
        "last_candidate": STATS.get("last_candidate","—"),
        "last_time": STATS.get("last_time","—"),
        "credits_balance": STATS.get("credits",{}).get("balance",0),
        "credits_purchased": STATS.get("credits",{}).get("purchased",0),
        "trial_left": int(session.get("trial_credits",0)),
        "users": USERS_DB.get("users", []),
        "history": (STATS.get("history", []) or [])[-50:][::-1],
    }
    return render_template_string(DIRECTOR_HTML, **ctx)

@app.post("/director/login")
def director_login():
    pw = (request.form.get("password") or "").strip()
    if pw == STATS.get("director_pass_override", DIRECTOR_PASS):
        session["director"] = True
        return redirect(url_for("director_home"))
    html = DIRECTOR_LOGIN_HTML.replace("<!--DERR-->", "<div class='err'>Incorrect director password</div>")
    return render_template_string(html), 401

@app.get("/director/logout")
def director_logout():
    session.pop("director", None)
    return redirect(url_for("app_page"))

@app.post("/director/credits/add")
def director_add_credits():
    if not session.get("director"):
        abort(403)
    try:
        amt = int(request.form.get("amount") or "0")
        if amt <= 0: raise ValueError()
    except Exception:
        abort(400, "Invalid amount")
    STATS.setdefault("credits", {"balance": 0, "purchased": 0})
    STATS["credits"]["balance"] = int(STATS["credits"]["balance"]) + amt
    STATS["credits"]["purchased"] = int(STATS["credits"]["purchased"]) + amt
    _save_stats()
    return redirect(url_for("director_home"))

@app.get("/director/export.csv")
def director_export():
    if not session.get("director"):
        abort(403)
    rows = ["ts, candidate, filename"]
    for it in STATS.get("history", []):
        ts = it.get("ts","")
        cand = (it.get("candidate","") or "").replace(","," ")
        fn = (it.get("filename","") or "").replace(","," ")
        rows.append(f"{ts},{cand},{fn}")
    csv_data = "\n".join(rows)
    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename=export.csv"
    return resp

@app.post("/director/users/create")
def director_user_create():
    if not session.get("director"):
        abort(403)
    u = (request.form.get("username") or "").strip()
    p = (request.form.get("password") or "")
    if not u or not p:
        abort(400, "Missing fields")
    if _get_user(u):
        abort(400, "User already exists")
    USERS_DB.setdefault("users", []).append({"username": u, "password": p, "active": True})
    _save_users()
    return redirect(url_for("director_home"))

@app.post("/director/users/toggle")
def director_user_toggle():
    if not session.get("director"):
        abort(403)
    u = (request.form.get("username") or "").strip()
    action = (request.form.get("action") or "").strip()
    rec = _get_user(u)
    if not rec:
        abort(404, "User not found")
    if action == "disable":
        rec["active"] = False
    elif action == "enable":
        rec["active"] = True
    else:
        abort(400, "Bad action")
    _save_users()
    return redirect(url_for("director_home"))

@app.get("/director/forgot")
def director_forgot_get():
    if not session.get("authed"):
        return redirect(url_for("login"))
    return render_template_string(DIRECTOR_FORGOT_HTML)

@app.post("/director/forgot")
def director_forgot_post():
    if not session.get("authed"):
        return redirect(url_for("login"))
    code = (request.form.get("code") or "").strip()
    newpass = (request.form.get("newpass") or "").strip()
    if code != RESET_CODE:
        html = DIRECTOR_FORGOT_HTML.replace("<!--RERR-->", "<div class='err'>Invalid reset code</div>")
        return render_template_string(html), 400
    if not newpass:
        html = DIRECTOR_FORGOT_HTML.replace("<!--RERR-->", "<div class='err'>New password required</div>")
        return render_template_string(html), 400
    STATS["director_pass_override"] = newpass
    _save_stats()
    return redirect(url_for("director_home"))

# ---------- App polishing + API (unchanged) ----------
@app.post("/polish")
def polish():
    # Always reprocess (no caching)
    f = request.files.get("cv")
    if not f:
        abort(400, "No file uploaded")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f.filename
        f.save(str(p))

        text = extract_text_any(p)
        if not text or len(text.strip()) < 30:
            abort(400, "Couldn't read enough text. If it's a scanned PDF, please use a DOCX or an OCRed PDF.")
        data = ai_or_heuristic_structuring(text)

        # Skills = keywords only
        data["skills"] = extract_top_skills(text)

        out = build_cv_document(data)

        # update stats
        candidate_name = (data.get("personal_info") or {}).get("full_name") or f.filename
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        STATS["downloads"] += 1
        STATS["last_candidate"] = candidate_name
        STATS["last_time"] = now
        STATS["history"].append({"candidate": candidate_name, "filename": f.filename, "ts": now})
        _save_stats()

        # NEW: decrement trial credits if present (kept from your script)
        try:
            left = int(session.get("trial_credits", 0))
            if left > 0:
                session["trial_credits"] = max(0, left - 1)
        except Exception:
            pass

        resp = make_response(send_file(str(out), as_attachment=True, download_name="polished_cv.docx"))
        resp.headers["Cache-Control"] = "no-store"
        return resp

@app.get("/app")
def app_page_dup():  # keep route name unique in this file
    resp = make_response(render_template_string(HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.get("/health")
def health():
    return "ok"

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT","5000")), debug=True, use_reloader=False)




















































