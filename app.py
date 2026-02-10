"""
app.py - Zendesk Assistant (Tetra Tech)
Run: pip install flask requests && python app.py
"""

import os
import csv
import uuid
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, session, render_template_string
from werkzeug.utils import secure_filename
from llm import call_llm

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", uuid.uuid4().hex)

BASE_DIR = Path(__file__).resolve().parent
TRAINING_DOCS_DIR = BASE_DIR / "training_docs"
SESSIONS_DIR = BASE_DIR / "sessions"
TRAINING_DOCS_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ALLOWED_EXT = {"txt", "md", "csv", "json", "html", "xml", "yml", "yaml", "log", "docx", "pdf"}

SYSTEM_PROMPT = """You are the Zendesk Assistant, an AI support tool for Tetra Tech.
Answer questions using the provided knowledge base. Be professional and concise.
If the answer isn't in the documents, say so honestly."""

# In-memory chat sessions: {session_id: [{"role":..., "content":...}]}
chat_sessions = {}

# ── Helpers ──────────────────────────────────────────────────────

def load_knowledge():
    docs = []
    for f in sorted(TRAINING_DOCS_DIR.iterdir()):
        if f.is_file():
            try:
                docs.append(f"--- {f.name} ---\n{f.read_text(encoding='utf-8', errors='replace')}")
            except Exception:
                pass
    return "\n\n".join(docs)

def get_session_id():
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex[:12]
    return session["sid"]

def log_csv(sid, role, content):
    path = SESSIONS_DIR / f"{sid}.csv"
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "session_id", "role", "message"])
        w.writerow([datetime.now().isoformat(), sid, role, content])

def require_admin(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"error": "Unauthorized"}), 403
        return f(*args, **kwargs)
    return wrapper

# ── API Routes ───────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/chat", methods=["POST"])
def api_chat():
    msg = request.json.get("message", "").strip()
    if not msg:
        return jsonify({"error": "Empty message"}), 400
    sid = get_session_id()
    if sid not in chat_sessions:
        chat_sessions[sid] = []
    chat_sessions[sid].append({"role": "user", "content": msg})
    log_csv(sid, "user", msg)
    reply = call_llm(chat_sessions[sid], system_prompt=SYSTEM_PROMPT, knowledge_context=load_knowledge())
    chat_sessions[sid].append({"role": "assistant", "content": reply})
    log_csv(sid, "assistant", reply)
    return jsonify({"reply": reply, "session_id": sid})

@app.route("/api/session/new", methods=["POST"])
def new_session():
    old = session.pop("sid", None)
    if old in chat_sessions:
        del chat_sessions[old]
    session["sid"] = uuid.uuid4().hex[:12]
    chat_sessions[session["sid"]] = []
    return jsonify({"session_id": session["sid"]})

@app.route("/api/session/info")
def session_info():
    sid = get_session_id()
    return jsonify({"session_id": sid, "history": chat_sessions.get(sid, [])})

# ── Admin Auth ───────────────────────────────────────────────────

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    if request.json.get("password") == ADMIN_PASSWORD:
        session["is_admin"] = True
        return jsonify({"success": True})
    return jsonify({"success": False}), 401

@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    return jsonify({"success": True})

@app.route("/api/admin/status")
def admin_status():
    return jsonify({"is_admin": session.get("is_admin", False)})

# ── File Management ──────────────────────────────────────────────

@app.route("/api/files")
def list_files():
    files = []
    for f in sorted(TRAINING_DOCS_DIR.iterdir()):
        if f.is_file():
            s = f.stat()
            files.append({"name": f.name, "size": s.st_size, "modified": datetime.fromtimestamp(s.st_mtime).isoformat()})
    return jsonify({"files": files})

@app.route("/api/files/upload", methods=["POST"])
@require_admin
def upload_file():
    uploaded = []
    for f in request.files.getlist("file"):
        if f and f.filename:
            name = secure_filename(f.filename)
            f.save(TRAINING_DOCS_DIR / name)
            uploaded.append(name)
    return jsonify({"uploaded": uploaded}) if uploaded else (jsonify({"error": "No valid files"}), 400)

@app.route("/api/files/<filename>", methods=["GET"])
@require_admin
def get_file(filename):
    p = TRAINING_DOCS_DIR / secure_filename(filename)
    if not p.exists():
        return jsonify({"error": "Not found"}), 404
    return jsonify({"name": p.name, "content": p.read_text(encoding="utf-8", errors="replace")})

@app.route("/api/files/<filename>", methods=["PUT"])
@require_admin
def update_file(filename):
    p = TRAINING_DOCS_DIR / secure_filename(filename)
    if not p.exists():
        return jsonify({"error": "Not found"}), 404
    p.write_text(request.json.get("content", ""), encoding="utf-8")
    return jsonify({"success": True})

@app.route("/api/files/<filename>", methods=["DELETE"])
@require_admin
def delete_file(filename):
    p = TRAINING_DOCS_DIR / secure_filename(filename)
    if p.exists():
        p.unlink()
    return jsonify({"success": True})

# ── Session Logs ─────────────────────────────────────────────────

@app.route("/api/sessions")
@require_admin
def list_sessions():
    logs = []
    for f in sorted(SESSIONS_DIR.glob("*.csv"), reverse=True):
        try:
            with open(f, "r") as fh:
                count = max(sum(1 for _ in fh) - 1, 0)
        except Exception:
            count = 0
        logs.append({"session_id": f.stem, "message_count": count, "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    return jsonify({"sessions": logs})

@app.route("/api/sessions/<sid>", methods=["GET"])
@require_admin
def get_session_log(sid):
    p = SESSIONS_DIR / f"{secure_filename(sid)}.csv"
    if not p.exists():
        return jsonify({"error": "Not found"}), 404
    msgs = []
    with open(p, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            msgs.append(row)
    return jsonify({"session_id": sid, "messages": msgs})

@app.route("/api/sessions/<sid>", methods=["DELETE"])
@require_admin
def delete_session(sid):
    p = SESSIONS_DIR / f"{secure_filename(sid)}.csv"
    if p.exists():
        p.unlink()
    chat_sessions.pop(sid, None)
    return jsonify({"success": True})

# ── System Prompt ────────────────────────────────────────────────

@app.route("/api/system-prompt", methods=["GET"])
@require_admin
def get_prompt():
    return jsonify({"prompt": SYSTEM_PROMPT})

@app.route("/api/system-prompt", methods=["PUT"])
@require_admin
def set_prompt():
    global SYSTEM_PROMPT
    SYSTEM_PROMPT = request.json.get("prompt", SYSTEM_PROMPT)
    return jsonify({"success": True})


# ═════════════════════════════════════════════════════════════════
# INLINE HTML (entire UI in one string)
# ═════════════════════════════════════════════════════════════════

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Zendesk Assistant - Tetra Tech</title>
<style>
:root{--p:#0057B8;--pd:#003D82;--pl:#E8F0FE;--bg:#F4F6F9;--card:#FFF;--txt:#1A1A2E;--muted:#6B7280;--bdr:#E2E8F0;--danger:#DC3545;--ok:#28A745;--r:8px}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--txt);height:100vh;overflow:hidden}
.app{display:flex;height:100vh}
.main{flex:1;display:flex;flex-direction:column;min-width:0}

/* Top bar */
.top{display:flex;align-items:center;justify-content:space-between;padding:.65rem 1.1rem;background:var(--card);border-bottom:1px solid var(--bdr);flex-shrink:0}
.top-l,.top-r{display:flex;align-items:center;gap:.6rem}
.logo{width:36px;height:36px;background:var(--p);color:#fff;border-radius:var(--r);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:1rem}
.brand h1{font-size:1rem;line-height:1.2}.brand-sub{font-size:.72rem;color:var(--muted)}
.badge{font-size:.68rem;background:var(--pl);color:var(--p);padding:.15rem .5rem;border-radius:99px;font-family:monospace}

/* Buttons */
.btn{padding:.4rem .85rem;border:none;border-radius:var(--r);background:var(--p);color:#fff;font-size:.82rem;font-weight:500;cursor:pointer}
.btn:hover{background:var(--pd)}.btn-s{padding:.25rem .6rem;font-size:.78rem}
.btn-g{background:var(--bg);color:var(--txt);border:1px solid var(--bdr)}.btn-g:hover{background:var(--bdr)}
.btn-d{background:var(--danger)}.btn-d:hover{background:#c82333}
.btn-x{background:none;border:none;font-size:1.3rem;cursor:pointer;color:var(--muted);padding:.1rem .3rem;border-radius:var(--r)}.btn-x:hover{background:var(--bg);color:var(--txt)}

/* Chat */
.chat-wrap{flex:1;overflow-y:auto;padding:1.2rem}
.chat{max-width:780px;margin:0 auto;display:flex;flex-direction:column;gap:.85rem}
.welcome{text-align:center;padding:3rem 1rem;color:var(--muted)}.welcome h2{color:var(--txt);margin:.8rem 0 .4rem;font-size:1.3rem}.welcome-ico{font-size:2.8rem}
.msg-row{display:flex;gap:.6rem;animation:fi .2s ease}.msg-row.user{justify-content:flex-end}
.avatar{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;flex-shrink:0;margin-top:2px}
.msg-row.assistant .avatar{background:var(--pl);color:var(--p)}.msg-row.user .avatar{background:var(--p);color:#fff}
.bubble{max-width:72%;padding:.65rem .9rem;border-radius:10px;line-height:1.5;font-size:.9rem;white-space:pre-wrap;word-wrap:break-word}
.msg-row.user .bubble{background:var(--p);color:#fff;border-bottom-right-radius:3px}
.msg-row.assistant .bubble{background:var(--card);border:1px solid var(--bdr);border-bottom-left-radius:3px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.msg-time{font-size:.63rem;color:var(--muted);margin-top:.2rem}
.typing .bubble::after{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--muted);animation:blink 1.2s infinite;margin-left:2px}
@keyframes blink{0%,80%,100%{opacity:.3}40%{opacity:1}}@keyframes fi{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}

/* Input */
.inp-area{padding:.6rem 1.1rem .8rem;background:var(--card);border-top:1px solid var(--bdr);flex-shrink:0}
.inp-wrap{max-width:780px;margin:0 auto;display:flex;align-items:flex-end;gap:.4rem;background:var(--bg);border:1px solid var(--bdr);border-radius:10px;padding:.4rem .4rem .4rem .85rem}
.inp-wrap:focus-within{border-color:var(--p)}
.inp-wrap textarea{flex:1;border:none;outline:none;background:transparent;font:inherit;font-size:.9rem;resize:none;max-height:110px;line-height:1.5}
.btn-send{background:var(--p);color:#fff;border:none;border-radius:var(--r);width:36px;height:36px;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0}
.btn-send:hover{background:var(--pd)}.btn-send:disabled{background:var(--bdr);cursor:default}
.inp-hint{text-align:center;font-size:.68rem;color:var(--muted);margin-top:.3rem}

/* Sidebar */
.sidebar{width:370px;background:var(--card);border-right:1px solid var(--bdr);display:flex;flex-direction:column;overflow:hidden;flex-shrink:0;position:relative}
.sidebar.hidden{display:none}
.sb-head{display:flex;align-items:center;justify-content:space-between;padding:.65rem .9rem;border-bottom:1px solid var(--bdr)}.sb-head h2{font-size:.95rem}
.tabs{display:flex;border-bottom:1px solid var(--bdr)}
.tab{flex:1;padding:.55rem .4rem;border:none;background:none;font-size:.76rem;font-weight:500;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent}
.tab:hover{color:var(--txt)}.tab.on{color:var(--p);border-bottom-color:var(--p)}
.tc{display:none;overflow-y:auto;flex:1}.tc.on{display:flex;flex-direction:column}
.sec{padding:.85rem;border-bottom:1px solid var(--bdr)}.sec h3{font-size:.88rem;margin-bottom:.4rem}

/* Upload */
.upload{border:2px dashed var(--bdr);border-radius:var(--r);padding:1.1rem;text-align:center}.upload p{font-size:.8rem;color:var(--muted)}.upload .sm{font-size:.68rem;margin-top:.4rem}
.upload.over{border-color:var(--p);background:var(--pl)}
.status{font-size:.78rem;margin-top:.4rem;min-height:1rem}.status.ok{color:var(--ok)}.status.err{color:var(--danger)}

/* File list */
.flist{display:flex;flex-direction:column;gap:.3rem}
.fitem{display:flex;align-items:center;justify-content:space-between;padding:.45rem .55rem;background:var(--bg);border-radius:var(--r);font-size:.8rem;gap:.4rem}
.fname{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}
.fmeta{font-size:.68rem;color:var(--muted);white-space:nowrap}
.factions{display:flex;gap:.2rem}.factions button{background:none;border:none;cursor:pointer;font-size:.85rem;padding:.1rem .3rem;border-radius:4px}.factions button:hover{background:var(--bdr)}

/* Session viewer */
.det-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:.6rem}
.smsg-list{display:flex;flex-direction:column;gap:.4rem;max-height:380px;overflow-y:auto}
.smsg{padding:.45rem .6rem;border-radius:var(--r);font-size:.78rem;line-height:1.4}
.smsg.user{background:var(--pl);margin-left:1.2rem}.smsg.assistant{background:var(--bg);margin-right:1.2rem}
.smsg-h{font-size:.68rem;color:var(--muted);margin-bottom:.15rem}

/* System prompt / file editor textareas */
.ed{width:100%;border:1px solid var(--bdr);border-radius:var(--r);padding:.65rem;font-family:Menlo,Consolas,monospace;font-size:.8rem;line-height:1.5;resize:vertical;outline:none}
.ed:focus{border-color:var(--p)}

/* File editor overlay */
.fe-overlay{position:absolute;inset:0;z-index:10;background:var(--card);padding:.85rem;overflow-y:auto;display:none}
.fe-overlay.on{display:block}

/* Modal */
.modal{position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:100}
.modal.hidden{display:none}
.modal-c{background:var(--card);border-radius:10px;padding:1.5rem;width:320px;box-shadow:0 4px 12px rgba(0,0,0,.12)}
.modal-c h3{margin-bottom:.4rem}.modal-c input{width:100%;padding:.5rem .65rem;border:1px solid var(--bdr);border-radius:var(--r);font-size:.88rem;margin-top:.6rem;outline:none}
.modal-c input:focus{border-color:var(--p)}
.err{color:var(--danger);font-size:.78rem;margin-top:.4rem}
.muted{color:var(--muted);font-size:.8rem}
.hidden{display:none!important}

::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--bdr);border-radius:99px}
@media(max-width:768px){.sidebar{width:100%}.bubble{max-width:88%}}
</style>
</head>
<body>
<div class="app">

<!-- SIDEBAR -->
<aside id="sidebar" class="sidebar hidden">
  <div class="sb-head"><h2>⚙ Admin Panel</h2><button class="btn-x" onclick="toggleSB()">&times;</button></div>
  <div class="tabs">
    <button class="tab on" data-t="t-files">Documents</button>
    <button class="tab" data-t="t-sess">Sessions</button>
    <button class="tab" data-t="t-prompt">System Prompt</button>
  </div>

  <!-- Documents -->
  <div id="t-files" class="tc on">
    <div class="sec">
      <div class="upload" id="upArea">
        <p>Drag & drop files here or</p>
        <label class="btn btn-s">Browse<input type="file" id="fileIn" multiple hidden></label>
        <p class="sm">txt, md, csv, json, html, xml, yml, etc.</p>
      </div>
      <div id="upStatus" class="status"></div>
    </div>
    <div class="sec"><h3>Training Documents</h3><div id="fileList" class="flist"><p class="muted">Loading...</p></div></div>
  </div>

  <!-- Sessions -->
  <div id="t-sess" class="tc">
    <div class="sec">
      <h3>Chat Session Logs</h3>
      <button class="btn btn-s" onclick="loadSess()">↻ Refresh</button>
      <div id="sessList" class="flist" style="margin-top:.4rem"><p class="muted">Loading...</p></div>
    </div>
    <div id="sessDet" class="sec hidden">
      <div class="det-head"><h3 id="sessDetTitle">Session</h3><button class="btn-x" onclick="closeSessDet()">&times;</button></div>
      <div id="sessMsgs" class="smsg-list"></div>
    </div>
  </div>

  <!-- System Prompt -->
  <div id="t-prompt" class="tc">
    <div class="sec">
      <h3>System Prompt</h3>
      <p class="muted">Sent with every LLM request.</p>
      <textarea id="promptEd" class="ed" rows="14"></textarea>
      <div style="margin-top:.4rem;display:flex;gap:.4rem">
        <button class="btn" onclick="savePrompt()">Save</button>
        <button class="btn btn-g" onclick="loadPrompt()">Reset</button>
      </div>
      <div id="promptStatus" class="status"></div>
    </div>
  </div>

  <!-- File editor overlay -->
  <div id="feOverlay" class="fe-overlay">
    <div class="det-head"><h3 id="feTitle">Edit</h3><button class="btn-x" onclick="closeFE()">&times;</button></div>
    <textarea id="feContent" class="ed" rows="18"></textarea>
    <div style="margin-top:.4rem;display:flex;gap:.4rem">
      <button class="btn" onclick="saveFE()">Save</button>
      <button class="btn btn-g" onclick="closeFE()">Cancel</button>
    </div>
  </div>
</aside>

<!-- MAIN -->
<main class="main">
  <header class="top">
    <div class="top-l">
      <button id="sbBtn" class="btn-x hidden" onclick="toggleSB()" title="Admin Panel">☰</button>
      <div class="logo">Z</div>
      <div class="brand"><h1>Zendesk Assistant</h1><span class="brand-sub">Tetra Tech</span></div>
    </div>
    <div class="top-r">
      <span id="badge" class="badge"></span>
      <button class="btn btn-s" onclick="newChat()">+ New Chat</button>
      <button id="adminBtn" class="btn btn-s btn-g" onclick="showLogin()">Admin</button>
      <button id="logoutBtn" class="btn btn-s btn-d hidden" onclick="doLogout()">Logout</button>
    </div>
  </header>

  <div id="chatWrap" class="chat-wrap">
    <div id="chatMsgs" class="chat">
      <div class="welcome"><div class="welcome-ico">💬</div><h2>Welcome to Zendesk Assistant</h2><p>Ask me anything about the knowledge base.</p></div>
    </div>
  </div>

  <div class="inp-area">
    <div class="inp-wrap">
      <textarea id="userIn" rows="1" placeholder="Type your message..." onkeydown="inKey(event)"></textarea>
      <button id="sendBtn" class="btn-send" onclick="send()">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
      </button>
    </div>
    <p class="inp-hint">Enter to send · Shift+Enter for new line</p>
  </div>
</main>

<!-- Login Modal -->
<div id="modal" class="modal hidden">
  <div class="modal-c">
    <h3>Admin Login</h3>
    <p class="muted">Enter admin password.</p>
    <input type="password" id="pw" placeholder="Password" onkeydown="if(event.key==='Enter')doLogin()">
    <div id="pwErr" class="err hidden"></div>
    <div style="display:flex;gap:.4rem;margin-top:.8rem">
      <button class="btn" onclick="doLogin()">Login</button>
      <button class="btn btn-g" onclick="hideLogin()">Cancel</button>
    </div>
  </div>
</div>
</div>

<script>
let isAdmin=false,editFile=null;

document.addEventListener("DOMContentLoaded",()=>{
  checkAdmin();loadInfo();autoTA();setupDrop();setupTabs();
});

// ── Chat ────────────────────────────────────────────────────
function send(){
  const el=document.getElementById("userIn"),t=el.value.trim();if(!t)return;
  addMsg("user",t);el.value="";el.style.height="auto";
  const tid=addTyping();document.getElementById("sendBtn").disabled=true;
  fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:t})})
    .then(r=>r.json()).then(d=>{rmTyping(tid);d.error?addMsg("assistant","⚠ "+d.error):addMsg("assistant",d.reply);if(d.session_id)setBadge(d.session_id);})
    .catch(()=>{rmTyping(tid);addMsg("assistant","⚠ Network error.");})
    .finally(()=>{document.getElementById("sendBtn").disabled=false;el.focus();});
}
function addMsg(role,text){
  const c=document.getElementById("chatMsgs"),w=c.querySelector(".welcome");if(w)w.remove();
  const row=document.createElement("div");row.className="msg-row "+role;
  const av=role==="user"?"U":"Z",now=new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});
  row.innerHTML=role==="assistant"
    ?`<div class="avatar">${av}</div><div><div class="bubble">${esc(text)}</div><div class="msg-time">${now}</div></div>`
    :`<div><div class="bubble">${esc(text)}</div><div class="msg-time">${now}</div></div><div class="avatar">${av}</div>`;
  c.appendChild(row);scroll();
}
function addTyping(){const c=document.getElementById("chatMsgs"),id="t"+Date.now(),r=document.createElement("div");r.className="msg-row assistant typing";r.id=id;r.innerHTML='<div class="avatar">Z</div><div><div class="bubble">Thinking</div></div>';c.appendChild(r);scroll();return id;}
function rmTyping(id){const e=document.getElementById(id);if(e)e.remove();}
function scroll(){const c=document.getElementById("chatWrap");c.scrollTop=c.scrollHeight;}
function inKey(e){if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send();}}
function autoTA(){const t=document.getElementById("userIn");t.addEventListener("input",()=>{t.style.height="auto";t.style.height=Math.min(t.scrollHeight,110)+"px";});}
function newChat(){if(!confirm("Start new chat?"))return;fetch("/api/session/new",{method:"POST"}).then(r=>r.json()).then(d=>{setBadge(d.session_id);document.getElementById("chatMsgs").innerHTML='<div class="welcome"><div class="welcome-ico">💬</div><h2>Welcome to Zendesk Assistant</h2><p>Ask me anything about the knowledge base.</p></div>';});}
function loadInfo(){fetch("/api/session/info").then(r=>r.json()).then(d=>{setBadge(d.session_id);if(d.history&&d.history.length){const c=document.getElementById("chatMsgs"),w=c.querySelector(".welcome");if(w)w.remove();d.history.forEach(m=>addMsg(m.role,m.content));}});}
function setBadge(s){document.getElementById("badge").textContent="Session: "+s;}

// ── Admin ───────────────────────────────────────────────────
function showLogin(){document.getElementById("modal").classList.remove("hidden");document.getElementById("pw").value="";document.getElementById("pwErr").classList.add("hidden");setTimeout(()=>document.getElementById("pw").focus(),100);}
function hideLogin(){document.getElementById("modal").classList.add("hidden");}
function doLogin(){fetch("/api/admin/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password:document.getElementById("pw").value})}).then(r=>r.json()).then(d=>{if(d.success){isAdmin=true;hideLogin();applyAdmin();loadFiles();loadSess();loadPrompt();}else{const e=document.getElementById("pwErr");e.textContent="Invalid password";e.classList.remove("hidden");}});}
function doLogout(){fetch("/api/admin/logout",{method:"POST"}).then(()=>{isAdmin=false;applyAdmin();document.getElementById("sidebar").classList.add("hidden");});}
function checkAdmin(){fetch("/api/admin/status").then(r=>r.json()).then(d=>{isAdmin=d.is_admin;applyAdmin();if(isAdmin){loadFiles();loadSess();loadPrompt();}});}
function applyAdmin(){document.getElementById("adminBtn").classList.toggle("hidden",isAdmin);document.getElementById("logoutBtn").classList.toggle("hidden",!isAdmin);document.getElementById("sbBtn").classList.toggle("hidden",!isAdmin);}

// ── Sidebar ─────────────────────────────────────────────────
function toggleSB(){document.getElementById("sidebar").classList.toggle("hidden");}
function setupTabs(){document.querySelectorAll(".tab").forEach(b=>{b.addEventListener("click",()=>{document.querySelectorAll(".tab").forEach(t=>t.classList.remove("on"));document.querySelectorAll(".tc").forEach(t=>t.classList.remove("on"));b.classList.add("on");document.getElementById(b.dataset.t).classList.add("on");closeFE();});});}

// ── Files ───────────────────────────────────────────────────
function loadFiles(){fetch("/api/files").then(r=>r.json()).then(d=>{const l=document.getElementById("fileList");if(!d.files||!d.files.length){l.innerHTML='<p class="muted">No documents yet.</p>';return;}l.innerHTML=d.files.map(f=>`<div class="fitem"><span class="fname" title="${esc(f.name)}">📄 ${esc(f.name)}</span><span class="fmeta">${fmtSz(f.size)}</span><div class="factions"><button title="Edit" onclick="openFE('${esc(f.name)}')">✏️</button><button title="Delete" onclick="delFile('${esc(f.name)}')">🗑️</button></div></div>`).join("");});}
function openFE(name){editFile=name;fetch("/api/files/"+encodeURIComponent(name)).then(r=>r.json()).then(d=>{if(d.error)return alert(d.error);document.getElementById("feTitle").textContent="Edit: "+d.name;document.getElementById("feContent").value=d.content;document.getElementById("feOverlay").classList.add("on");});}
function saveFE(){if(!editFile)return;fetch("/api/files/"+encodeURIComponent(editFile),{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({content:document.getElementById("feContent").value})}).then(r=>r.json()).then(d=>{if(d.success){closeFE();loadFiles();}});}
function closeFE(){document.getElementById("feOverlay").classList.remove("on");editFile=null;}
function delFile(name){if(!confirm('Delete "'+name+'"?'))return;fetch("/api/files/"+encodeURIComponent(name),{method:"DELETE"}).then(()=>loadFiles());}

function setupDrop(){
  const a=document.getElementById("upArea"),fi=document.getElementById("fileIn");
  a.addEventListener("dragover",e=>{e.preventDefault();a.classList.add("over");});
  a.addEventListener("dragleave",()=>a.classList.remove("over"));
  a.addEventListener("drop",e=>{e.preventDefault();a.classList.remove("over");if(e.dataTransfer.files.length)upFiles(e.dataTransfer.files);});
  fi.addEventListener("change",()=>{if(fi.files.length)upFiles(fi.files);fi.value="";});
}
function upFiles(files){
  const st=document.getElementById("upStatus"),fd=new FormData();for(let f of files)fd.append("file",f);
  st.textContent="Uploading...";st.className="status";
  fetch("/api/files/upload",{method:"POST",body:fd}).then(r=>r.json()).then(d=>{if(d.uploaded){st.textContent="✓ "+d.uploaded.join(", ");st.className="status ok";loadFiles();}else{st.textContent="✗ "+(d.error||"Failed");st.className="status err";}setTimeout(()=>st.textContent="",4000);}).catch(()=>{st.textContent="✗ Network error";st.className="status err";});
}

// ── Sessions ────────────────────────────────────────────────
function loadSess(){fetch("/api/sessions").then(r=>r.json()).then(d=>{const l=document.getElementById("sessList");if(!d.sessions||!d.sessions.length){l.innerHTML='<p class="muted">No sessions yet.</p>';return;}l.innerHTML=d.sessions.map(s=>`<div class="fitem"><span class="fname">💬 ${s.session_id}</span><span class="fmeta">${s.message_count} msgs</span><div class="factions"><button title="View" onclick="viewSess('${s.session_id}')">👁️</button><button title="Delete" onclick="delSess('${s.session_id}')">🗑️</button></div></div>`).join("");});}
function viewSess(sid){fetch("/api/sessions/"+encodeURIComponent(sid)).then(r=>r.json()).then(d=>{if(d.error)return alert(d.error);document.getElementById("sessDetTitle").textContent="Session: "+sid;document.getElementById("sessMsgs").innerHTML=d.messages.map(m=>`<div class="smsg ${m.role}"><div class="smsg-h">${m.role.toUpperCase()} · ${m.timestamp||""}</div>${esc(m.message)}</div>`).join("");document.getElementById("sessDet").classList.remove("hidden");});}
function closeSessDet(){document.getElementById("sessDet").classList.add("hidden");}
function delSess(sid){if(!confirm("Delete session "+sid+"?"))return;fetch("/api/sessions/"+encodeURIComponent(sid),{method:"DELETE"}).then(()=>{loadSess();closeSessDet();});}

// ── System Prompt ───────────────────────────────────────────
function loadPrompt(){fetch("/api/system-prompt").then(r=>r.json()).then(d=>{document.getElementById("promptEd").value=d.prompt||"";}).catch(()=>{});}
function savePrompt(){const st=document.getElementById("promptStatus");fetch("/api/system-prompt",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:document.getElementById("promptEd").value})}).then(r=>r.json()).then(d=>{if(d.success){st.textContent="✓ Saved";st.className="status ok";}else{st.textContent="✗ Failed";st.className="status err";}setTimeout(()=>st.textContent="",3000);});}

// ── Utils ───────────────────────────────────────────────────
function esc(s){const d=document.createElement("div");d.textContent=s;return d.innerHTML;}
function fmtSz(b){return b<1024?b+" B":b<1048576?(b/1024).toFixed(1)+" KB":(b/1048576).toFixed(1)+" MB";}
</script>
</body>
</html>
"""

# ═════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 45)
    print("  Zendesk Assistant - Tetra Tech")
    print("  http://localhost:5000")
    print("=" * 45)
    app.run(debug=True, host="0.0.0.0", port=5000)
