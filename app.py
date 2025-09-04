# app.py
import os, json, re, tempfile, traceback, zipfile
from pathlib import Path
from datetime import datetime
from flask import Flask, request, send_file, render_template_string, abort, jsonify, make_response
from flask import session, redirect, url_for  # <-- ADDED (new import only)

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

def _save_stats():
    if len(STATS.get("history", [])) > 1000:
        STATS["history"] = STATS["history"][-1000:]
    STATS_FILE.write_text(json.dumps(STATS, indent=2), encoding="utf-8")

# ------------------------ Public Home (NEW) ------------------------
HOMEPAGE_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Recruiter First — CV Polisher</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --blue:#003366; --blue-2:#0a4d8c; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --bg:#f2f6fb; --card:#ffffff;
      --shadow: 0 8px 24px rgba(0,0,0,.06);
    }
    *{box-sizing:border-box}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}
    .wrap{max-width:820px;margin:56px auto;padding:0 18px;text-align:center}
    .brand-logo{width:56px;height:56px;border-radius:12px;background:linear-gradient(135deg,var(--blue),var(--blue-2));display:flex;align-items:center;justify-content:center;margin:0 auto 14px}
    .brand-logo img{width:100%;height:100%;object-fit:contain}
    h1{margin:0 0 8px;font-size:28px;color:var(--blue);letter-spacing:-0.01em}
    p.sub{margin:0 auto 24px;color:var(--muted);font-size:14px;max-width:600px}
    .actions{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
    a.btn{display:inline-block;padding:12px 16px;border-radius:10px;font-weight:800;text-decoration:none;box-shadow:var(--shadow)}
    a.primary{background:linear-gradient(90deg,var(--blue),var(--blue-2));color:#fff}
    a.secondary{background:#fff;color:var(--blue);border:1px solid var(--line)}
    .links{margin-top:16px;font-size:13px}
    .links a{color:var(--blue);text-decoration:none;margin:0 8px}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand-logo"><img src="/logo" alt="Hamilton Logo" onerror="this.style.display='none'"/></div>
    <h1>Recruiter First — CV Polisher</h1>
    <p class="sub">Upload a raw CV (PDF / DOCX / TXT) and download a branded, polished version — fast and consistent.</p>
    <div class="actions">
      <a class="btn primary" href="/login">Start free trial</a>
      <a class="btn secondary" href="/login">Sign in</a>
    </div>
    <div class="links">
      <a href="/login">Pricing</a> ·
      <a href="/login">About</a> ·
      <a href="/login">Contact</a>
    </div>
  </div>
</body>
</html>
"""

# ------------------------ Branded App UI ------------------------
HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Hamilton Recruitment — Executive Search & Selection</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --blue:#003366; --blue-2:#0a4d8c; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --bg:#f2f6fb; --card:#ffffff;
      --ok:#16a34a; --shadow: 0 8px 24px rgba(0,0,0,.06);
    }
    *{box-sizing:border-box}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}
    .wrap{max-width:1060px;margin:28px auto;padding:0 18px}
    .nav{display:flex;align-items:center;gap:14px;margin-bottom:18px}
    .brand-logo{width:42px;height:42px;border-radius:8px;background:linear-gradient(135deg,var(--blue),var(--blue-2));display:flex;align-items:center;justify-content:center;overflow:hidden}
    .brand-logo img{width:100%;height:100%;object-fit:contain}
    .brand-head{line-height:1.1}
    .brand-title{font-size:22px;margin:0;color:var(--blue);font-weight:900;letter-spacing:-0.01em}
    .brand-sub{margin:0;color:var(--muted);font-size:12px}
    .grid{display:grid;grid-template-columns:1.25fr .75fr;gap:18px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:var(--shadow)}
    .card h3{margin:0 0 12px;font-size:15px;letter-spacing:.2px;color:var(--blue)}
    label{font-weight:600;font-size:13px}
    input[type=file]{width:100%}
    button{background:linear-gradient(90deg,var(--blue),var(--blue-2));color:#fff;border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer;box-shadow:var(--shadow)}
    button[disabled]{opacity:.6;cursor:not-allowed}

    .progress{display:none;margin-top:12px;border:1px solid var(--line);border-radius:12px;padding:12px;background:var(--card)}
    .stage{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:12px}
    .stage .dot{width:8px;height:8px;border-radius:999px;background:var(--line)}
    .stage.active .dot{background:var(--blue)}
    .stage.done .dot{background:var(--ok)}
    .bar{height:12px;border-radius:999px;background:var(--line);overflow:hidden;margin-top:6px;position:relative}
    .bar > span{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--blue),var(--blue-2));transition:width .35s ease}
    .pct{position:absolute;right:8px;top:50%;transform:translateY(-50%);font-size:11px;color:#fff;font-weight:700}
    .success{display:none;margin-top:10px;color:var(--ok);font-weight:700}

    .statsgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:10px}
    .stat{border:1px solid var(--line);border-radius:12px;padding:10px 12px;background:var(--card)}
    .stat .k{font-size:11px;color:var(--muted);font-weight:600}
    .stat .v{font-size:14px;font-weight:800;margin-top:2px;color:var(--blue)}
    .history{border:1px solid var(--line);border-radius:12px;max-height:300px;overflow:auto;background:var(--card)}
    .row{display:flex;justify-content:space-between;gap:10px;padding:8px 12px;border-bottom:1px solid var(--line)}
    .row:last-child{border-bottom:none}
    .candidate{font-weight:600;font-size:13px}
    .ts{color:var(--muted);font-size:11px}

    @media(max-width:900px){ .grid{display:grid;grid-template-columns:1fr;gap:12px} }
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
        document.getElementById('downloadsMonth').textContent = s.downloads_this_month ?? s.downloads;
        document.getElementById('lastCandidate').textContent = s.last_candidate || '—';
        document.getElementById('lastTime').textContent = s.last_time || '—';
        const list = document.getElementById('history');
        if(list){
          list.innerHTML = '';
          (s.history || []).slice().reverse().forEach(item=>{
            const row = document.createElement('div'); row.className='row';
            const left = document.createElement('div');
            left.innerHTML = '<div class="candidate">'+ (item.candidate || item.filename || '—') + '</div><div class="ts">'+ (item.filename || '') +'</div>';
            const right = document.createElement('div'); right.className='ts';
            right.textContent = item.ts || '';
            row.appendChild(left); row.appendChild(right); list.appendChild(row);
          });
        }
      }catch(e){}
    }
    document.addEventListener('DOMContentLoaded',()=>{
      refreshStats();
      setInterval(refreshStats, 5000);

      const form = document.getElementById('upload-form');
      const fileInput = document.getElementById('cv');

      // NEW: fetch + Blob download (fixes second-upload issue)
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
    });
  </script>
</head>
<body>
  <div class="wrap">
    <div class="nav">
      <div class="brand-logo"><img src="/logo" alt="Hamilton Logo" onerror="this.style.display='none'"/></div>
      <div class="brand-head">
        <p class="brand-title">Hamilton Recruitment — CV Polisher</p>
        <p class="brand-sub">Executive Search &amp; Selection</p>
      </div>

      <div style="margin-left:auto">
        <a href="/logout" style="display:inline-block;background:#fff;color:var(--blue);border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-weight:700;text-decoration:none">Log out</a>
      </div>
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
        </div>
        <div class="ts" style="margin:8px 0 6px 2px;">Full History</div>
        <div id="history" class="history"></div>
      </div>
    </div>
  </div>
</body>
</html>
"""

# ------------------------ ADDED: Login page HTML ------------------------
LOGIN_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Hamilton Recruitment — Sign in</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --blue:#003366; --blue-2:#0a4d8c; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --bg:#f2f6fb; --card:#ffffff;
      --ok:#16a34a; --shadow: 0 8px 24px rgba(0,0,0,.06);
    }
    *{box-sizing:border-box}
    body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}
    .wrap{max-width:420px;margin:48px auto;padding:0 18px}
    .nav{display:flex;align-items:center;gap:14px;margin-bottom:18px;justify-content:center}
    .brand-logo{width:42px;height:42px;border-radius:8px;background:linear-gradient(135deg,var(--blue),var(--blue-2));display:flex;align-items:center;justify-content:center;overflow:hidden}
    .brand-logo img{width:100%;height:100%;object-fit:contain}
    .brand-head{line-height:1.1;text-align:center}
    .brand-title{font-size:22px;margin:0;color:var(--blue);font-weight:900;letter-spacing:-0.01em}
    .brand-sub{margin:0;color:var(--muted);font-size:12px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:var(--shadow)}
    .card h3{margin:0 0 12px;font-size:15px;letter-spacing:.2px;color:var(--blue)}
    label{font-weight:600;font-size:13px}
    input[type=text],input[type=password]{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;margin-top:6px}
    button{width:100%;margin-top:12px;background:linear-gradient(90deg,var(--blue),var(--blue-2));color:#fff;border:none;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer;box-shadow:var(--shadow)}
    .err{margin-top:8px;color:#b91c1c;font-weight:700;font-size:12px}
    .muted{color:var(--muted);font-size:12px;text-align:center;margin-top:8px}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="nav">
      <div class="brand-logo"><img src="/logo" alt="Hamilton Logo" onerror="this.style.display='none'"/></div>
      <div class="brand-head">
        <p class="brand-title">Hamilton Recruitment — Sign in</p>
        <p class="brand-sub">Executive Search &amp; Selection</p>
      </div>
    </div>
    <div class="card">
      <h3>Sign in</h3>
      <!--ERROR-->
      <form method="post" action="/login" autocomplete="off">
        <label for="username">Username</label>
        <input id="username" type="text" name="username" autofocus required />
        <div style="height:8px"></div>
        <label for="password">Password</label>
        <input id="password" type="password" name="password" required />
        <button type="submit">Continue</button>
      </form>
      <div class="muted">Default demo: admin / hamilton</div>
    </div>
  </div>
</body>
</html>
"""

app = Flask(__name__)

# ------------------------ ADDED: session secret + default creds ------------------------
app.secret_key = os.getenv("APP_SECRET_KEY", "dev-secret-change-me")
APP_ADMIN_USER = os.getenv("APP_ADMIN_USER", "admin")
APP_ADMIN_PASS = os.getenv("APP_ADMIN_PASS", "hamilton")

# ------------------------ UPDATED: gate protected routes (now /app, /polish, /stats) ------------------------
@app.before_request
def gate_protected_routes():
    protected_prefixes = ["/app", "/polish", "/stats"]
    p = request.path or "/"
    if any(p.startswith(x) for x in protected_prefixes):
        if not session.get("authed"):
            return redirect(url_for("login"))

@app.get("/login")
def login():
    if session.get("authed"):
        return redirect(url_for("app_page"))  # <-- now goes to /app
    resp = make_response(render_template_string(LOGIN_HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.post("/login")
def do_login():
    user = (request.form.get("username") or "").strip()
    pw = (request.form.get("password") or "").strip()
    if user == APP_ADMIN_USER and pw == APP_ADMIN_PASS:
        session["authed"] = True
        return redirect(url_for("app_page"))  # <-- now goes to /app
    html = LOGIN_HTML.replace("<!--ERROR-->", "<div class='err'>Invalid credentials</div>")
    resp = make_response(render_template_string(html))
    resp.headers["Cache-Control"] = "no-store"
    return resp, 401

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------- serve the Hamilton logo for the web UI ----------
@app.get("/logo")
def logo():
    for name in ["Imagem1.png", "hamilton_logo.png", "logo.png"]:
        p = PROJECT_DIR / name
        if p.exists():
            resp = make_response(send_file(str(p)))
            resp.headers["Cache-Control"] = "no-store"
            return resp
    return ("", 204)

# ---------- helper: page/numpages fields (Word) ----------
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
            resp = client.chatCompletions.create(  # some environments use chatCompletions
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
                # fallback to the standard client if available
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
def extract_top_skills(text: str):
    """Return ONLY canonical keywords present in the CV text, no sentences."""
    tokens = re.findall(r"[A-Za-z0-9\-\&\./+]+", text)
    txt_up = " ".join(tokens).upper()
    found = []
    for s in SKILL_CANON:
        if s.upper() in txt_up:
            found.append(s)
    # de-dup preserving order
    out, seen = [], set()
    for it in found:
        key = it.lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out[:25]

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
    r.font.name = "Calibri"
    r.font.size = Pt(14)  # SIZE 14 FOR ALL MAJOR HEADINGS
    r.bold = True
    r.font.color.rgb = SOFT_BLACK
    return p

def _remove_all_body_content(doc: Docx):
    """Clear ONLY the document body; keep headers/footers intact."""
    for t in list(doc.tables):
        t._element.getparent().remove(t._element)
    for p in list(doc.paragraphs):
        p._element.getparent().remove(p._element)

# ---------- Post-save zip scrub of header XML ----------
def _zip_scrub_header_labels(docx_path: Path):
    """
    Open the saved DOCX (zip) and remove any header <w:p> that contains
    'PROFESSIONAL' + 'EXPERIENCE' + 'CONTINUED' (case-insensitive).
    Also removes a two-paragraph split and inserts a single blank paragraph
    to keep page-2+ spacing stable.
    """
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
    """
    Adds a trailing blank paragraph to each section's *primary* header,
    so body text doesn't start flush against the header on page 2+.
    """
    try:
        for sec in doc.sections:
            hdr = sec.header  # primary header (used on pages 2+ if first-page header differs)
            needs = True
            try:
                if hdr.paragraphs:
                    if (hdr.paragraphs[-1].text or "").strip() == "":
                        needs = False
            except Exception:
                pass
            if needs:
                p = hdr.add_paragraph()
                r = p.add_run(" ")  # one space to ensure it affects layout height
    except Exception:
        pass

# ---------- Compose CV (preserve template header/footer) ----------
def build_cv_document(cv: dict) -> Path:
    template_path = None
    for pth in [PROJECT_DIR / "hamilton_template.docx",
                PROJECT_DIR / "HAMILTON TEMPLATE.docx",
                PROJECT_DIR / "master_template.docx"]:
        if pth.exists():
            template_path = pth; break

    if template_path:
        doc = Docx(str(template_path))   # preserve header/footer exactly
    else:
        doc = Docx()  # fallback: simple blank doc

    # Clear body and build fresh content
    _remove_all_body_content(doc)

    # Subtle spacer before name (matches reference)
    spacer = doc.add_paragraph(); spacer.paragraph_format.space_after = Pt(6)

    # NAME (centered) — SIZE 18
    pi = (cv or {}).get("personal_info") or {}
    full_name = (pi.get("full_name") or "Candidate").strip()
    name_p = doc.add_paragraph(); name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER; name_p.paragraph_format.space_after = Pt(2)
    name_r = name_p.add_run(full_name)
    name_r.font.name="Calibri"; name_r.font.size=Pt(18); name_r.bold=True; name_r.font.color.rgb=SOFT_BLACK

    # CONTACT: Tel + Email on ONE line with a vertical bar; Location on its own line
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

    # EXECUTIVE SUMMARY (heading size 14)
    if cv.get("summary"):
        _add_section_heading(doc, "EXECUTIVE SUMMARY")
        p = doc.add_paragraph(cv["summary"]); p.paragraph_format.space_after = Pt(8); _tone_runs(p, size=11, bold=False)

    # PROFESSIONAL QUALIFICATIONS (heading size 14)
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

    # PROFESSIONAL SKILLS (heading size 14)
    skills = cv.get("skills") or []
    if skills:
        _add_section_heading(doc, "PROFESSIONAL SKILLS")
        line = " | ".join(skills)
        p = doc.add_paragraph(line); p.paragraph_format.space_after = Pt(8); _tone_runs(p, size=11, bold=False)

    # PROFESSIONAL EXPERIENCE (heading size 14)
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

    # EDUCATION (heading size 14)
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

    # Ensure spacer in primary header (pages 2+), then save & scrub
    _ensure_primary_header_spacer(doc)

    out = PROJECT_DIR / "polished_cv.docx"
    doc.save(str(out))
    _zip_scrub_header_labels(out)  # keep header-cleaning safety net
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

# ---------- Routes ----------
@app.get("/")
def index():
    # Public homepage (no login required)
    resp = make_response(render_template_string(HOMEPAGE_HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.get("/app")
def app_page():
    # The actual tool UI (login required; protected by before_request gate)
    resp = make_response(render_template_string(HTML))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.get("/stats")
def stats():
    data = dict(STATS)
    data["downloads_this_month"] = _downloads_this_month()
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.post("/polish")
def polish():
    # Always reprocess (no caching) so changes in the source CV are reflected
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

        # (1) SKILLS = KEYWORDS ONLY (ignore long sentences from AI)
        data["skills"] = extract_top_skills(text)

        out = build_cv_document(data)

        candidate_name = (data.get("personal_info") or {}).get("full_name") or f.filename
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        STATS["downloads"] += 1
        STATS["last_candidate"] = candidate_name
        STATS["last_time"] = now
        STATS["history"].append({"candidate": candidate_name, "filename": f.filename, "ts": now})
        _save_stats()

        resp = make_response(send_file(str(out), as_attachment=True, download_name="polished_cv.docx"))
        resp.headers["Cache-Control"] = "no-store"
        return resp

@app.get("/health")
def health():
    return "ok"

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT","5000")), debug=True, use_reloader=False)



