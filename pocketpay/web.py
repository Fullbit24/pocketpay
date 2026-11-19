# web.py — the presentation layer: a real product UI on top of the agent.
# Zero dependencies: pure stdlib http.server + one embedded HTML page.
# The dashboard never decides anything — it only reads the database and calls
# PocketPayAgent, exactly like the terminal UI does. Architecture unchanged,
# just a face.
#
#   run:  ~/.venv/bin/python main.py web      →  http://127.0.0.1:8401

import json
import sqlite3
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from . import db
from .agent import PocketPayAgent

PORT = 8401


class Handler(BaseHTTPRequestHandler):
    # ---------- tiny json plumbing ----------
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def log_message(self, *a):  # silence request spam during demo
        pass

    # ---------- routes ----------
    def do_GET(self):
        u = urlparse(self.path)
        con = db.connect()
        try:
            if u.path == "/":
                self._html()
            elif u.path == "/api/state":
                self._send(self._state(con))
            elif u.path == "/api/txn":
                tid = parse_qs(u.query).get("id", [""])[0]
                txn = con.execute("SELECT * FROM transactions WHERE txn_id=?", (tid,)).fetchone()
                if txn is None:
                    return self._send({"error": "no such txn"}, 404)
                log = [dict(r) for r in db.get_log(con, tid)]
                self._send({"txn": dict(txn), "log": log})
            else:
                self._send({"error": "404"}, 404)
        finally:
            con.close()

    def do_POST(self):
        u = urlparse(self.path)
        payload = self._body()
        con = db.connect()
        try:
            agent = PocketPayAgent(con)
            if u.path == "/api/triage":
                action, rule, expl = agent.triage(payload.get("txn_id", ""))
                self._send({"action": action, "rule": rule, "explanation": expl})
            elif u.path == "/api/resolve":
                final = agent.resolve(payload.get("txn_id", ""), payload.get("choice", ""))
                self._send({"final": final})
            elif u.path == "/api/report":
                # judge-moment: type any shady message, the real chain triages it live
                msg = (payload.get("message") or "").strip()
                if not msg:
                    return self._send({"error": "empty message"}, 400)
                cnt = con.execute(
                    "SELECT COUNT(*) c FROM transactions WHERE txn_id LIKE 'ADHOC%'").fetchone()["c"]
                tid = f"ADHOC-{cnt + 1:03d}"
                con.execute(
                    "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed, purpose, message) "
                    "VALUES (?,?,?,?,?,?)",
                    (tid, "You", "R-GHOST", 100.0, "live report (from the audience)", msg))
                con.commit()
                action, rule, expl = agent.triage(tid)
                self._send({"txn_id": tid, "action": action, "rule": rule, "explanation": expl})
            else:
                self._send({"error": "404"}, 404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            con.close()

    def _state(self, con):
        txns = [dict(r) for r in con.execute(
            "SELECT txn_id, student, recipient_id, amount_aed, purpose, state, decision "
            "FROM transactions ORDER BY txn_id")]
        stats = dict(con.execute(
            "SELECT state, COUNT(*) c FROM transactions GROUP BY state").fetchall())
        bc = [dict(r) for r in con.execute(
            "SELECT message, created_at FROM broadcast_log ORDER BY id DESC LIMIT 6")]
        return {"transactions": txns, "stats": stats, "broadcasts": bc}

    def _html(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PocketPay · Campus Shield</title>
<style>
:root{--bg:#0b0e14;--panel:#121726;--panel2:#0e131f;--line:#1f2637;--tx:#e6ebf4;--mut:#8b96ad;
--green:#34d399;--amber:#fbbf24;--red:#f87171;--blue:#60a5fa;--violet:#a78bfa}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:15px/1.5 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{display:flex;align-items:center;gap:14px;padding:14px 20px;border-bottom:1px solid var(--line);background:linear-gradient(120deg,#101627,#0b0e14 60%)}
.logo{width:34px;height:34px;border-radius:9px;background:radial-gradient(circle at 30% 30%,#f87171,#7f1d1d);display:grid;place-items:center;font-weight:800;color:#fff}
h1{font-size:17px;letter-spacing:.3px}h1 b{color:var(--red)}
.sub{font-size:12px;color:var(--mut)}
.stats{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
.chip{padding:6px 11px;border-radius:999px;background:var(--panel);border:1px solid var(--line);font-size:12.5px;color:var(--mut)}
.chip b{color:var(--tx);font-size:14px;margin-left:4px}
main{flex:1;display:grid;grid-template-columns:390px 1fr;min-height:0}
.queue{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;background:var(--panel2)}
.searchbox{display:flex;gap:8px;padding:12px;border-bottom:1px solid var(--line)}
input,textarea{background:#0a0f1a;border:1px solid var(--line);color:var(--tx);border-radius:8px;padding:9px 11px;font:inherit;outline:none;flex:1}
input:focus,textarea:focus{border-color:var(--blue)}
.pills{display:flex;gap:6px;padding:10px 12px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.pill{font-size:11.5px;padding:4px 10px;border-radius:999px;background:var(--panel);border:1px solid var(--line);color:var(--mut);cursor:pointer;user-select:none}
.pill.on{color:#fff;border-color:var(--blue)}
.list{flex:1;overflow-y:auto;padding:8px}
.card{padding:11px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:7px;cursor:pointer;background:var(--panel);transition:border-color .15s}
.card:hover{border-color:#38507a}.card.sel{border-color:var(--blue)}
.row1{display:flex;align-items:center;gap:8px;font-size:13px}
.id{font-weight:700}.who{color:var(--mut);font-size:12px}
.amt{margin-left:auto;font-weight:700}
.st{font-size:10.5px;font-weight:700;letter-spacing:.4px;padding:2px 8px;border-radius:999px}
.CLEAR,.CLEARED{color:var(--green);background:rgba(52,211,153,.1)}.ESCALATED{color:var(--red);background:rgba(248,113,113,.1)}
.PENDING_CONFIRMATION{color:var(--amber);background:rgba(251,191,36,.1)}.RECEIVED,.TRIAGED{color:var(--blue);background:rgba(96,165,250,.1)}
.CANCELLED{color:var(--mut);background:rgba(139,150,173,.1)}
.detail{overflow-y:auto;padding:20px 24px;min-height:0}
.empty{color:var(--mut);text-align:center;margin-top:14vh}
.msg{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--blue);border-radius:8px;padding:12px 14px;margin:14px 0;font-size:14.5px}
.meta{color:var(--mut);font-size:13px}
.btn{border:0;border-radius:9px;padding:10px 16px;font:inherit;font-weight:650;cursor:pointer}
.btn.go{background:linear-gradient(120deg,#2563eb,#4f46e5);color:#fff}
.btn.go:disabled{opacity:.5;cursor:wait}
.btn.green{background:#059669;color:#fff}.btn.danger{background:#b91c1c;color:#fff}.btn.ghost{background:var(--panel);color:var(--mut);border:1px solid var(--line)}
.actions{display:flex;gap:9px;margin-top:16px;align-items:center}
.spinner{width:14px;height:14px;border:2px solid #fff;border-right-color:transparent;border-radius:50%;animation:sp 1s linear infinite;display:none}
@keyframes sp{to{transform:rotate(360deg)}}
.verdict{margin-top:16px;border-radius:10px;padding:13px 15px;border:1px solid var(--line);background:var(--panel);display:none}
.verdict .v{font-weight:800;font-size:16px}.verdict .e{margin-top:4px;font-size:14px;color:var(--tx)}
.chain{margin-top:20px}
.chain h3{font-size:12px;letter-spacing:1.5px;color:var(--mut);margin-bottom:10px}
.step{display:flex;gap:12px;padding:9px 0;border-bottom:1px dashed var(--line)}
.dot{width:9px;height:9px;border-radius:50%;margin-top:7px;flex:none}
.pulse{animation:pul 1.1s ease-in-out infinite}@keyframes pul{50%{opacity:.2}}
.actor{font-weight:700;font-size:12.5px;text-transform:uppercase;letter-spacing:.5px;min-width:58px;margin-top:3px}
.sdetail{font-size:13.5px;color:#c6cfdd}
.t_ac_llm{background:var(--violet)}.t_ac_llm2{color:var(--violet)}
.t_ac_tool{background:var(--blue)}.t_ac_tool2{color:var(--blue)}
.t_ac_guard{background:var(--red)}.t_ac_guard2{color:var(--red)}
.t_ac_system{background:var(--mut)}.t_ac_system2{color:var(--mut)}
.t_ac_human{background:var(--green)}.t_ac_human2{color:var(--green)}
.t_ac_agent{background:var(--amber)}.t_ac_agent2{color:var(--amber)}
footer{border-top:1px solid var(--line);padding:9px 20px;font-size:12.5px;color:var(--mut);display:flex;gap:18px;align-items:center;white-space:nowrap;overflow:hidden}
.bc{animation:fadein .5s}@keyframes fadein{from{opacity:0;transform:translateY(6px)}}
.toast{position:fixed;right:18px;bottom:64px;max-width:420px;background:#7f1d1d;border:1px solid var(--red);color:#fff;padding:11px 14px;border-radius:10px;font-size:13px;display:none;box-shadow:0 12px 30px rgba(0,0,0,.5)}
.modalbg{position:fixed;inset:0;background:rgba(4,6,10,.72);display:none;place-items:center;z-index:9}
.modal{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px;max-width:520px;width:92%}
.modal h2{font-size:16px;margin-bottom:6px}.modal p{color:var(--mut);font-size:13.5px;margin-bottom:16px}
.kbd{font-size:11px;color:var(--mut)}.kbd code{background:var(--panel);padding:1px 6px;border-radius:5px;border:1px solid var(--line)}
</style></head>
<body>
<header>
  <div class="logo">P</div>
  <div><h1>PocketPay <b>Campus Shield</b></h1><div class="sub">the LLM guesses · the code decides · your money moves only through both</div></div>
  <div class="stats" id="stats"></div>
</header>
<main>
  <section class="queue">
    <div class="searchbox"><input id="q" placeholder="🔍 search student / txn / purpose…"></div>
    <div class="pills" id="pills"></div>
    <div class="list" id="list"></div>
    <div class="searchbox" style="border-bottom:0;border-top:1px solid var(--line)">
      <input id="adhoc" placeholder="🚩 paste a shady message a judge says…">
      <button class="btn go" id="report" style="flex:none">Triage live</button>
    </div>
  </section>
  <section class="detail" id="detail"><div class="empty">← pick a flagged transaction.<br><br>the agent chain, the LLM's opinion and the guard's verdict<br>all appear here as an audit trail.</div></section>
</main>
<footer><span><span class="kbd" style="color:var(--mut)">chain legend:</span> <span style="color:var(--blue)">■ tools/facts</span> <span style="color:var(--violet)">■ LLM opinion</span> <span style="color:var(--red)">■ guard decision</span> <span style="color:var(--green)">■ human</span></span><span id="feed" class="bc" style="text-overflow:ellipsis;overflow:hidden"></span></footer>
<div class="toast" id="toast"></div>
<div class="modalbg" id="modalbg"><div class="modal">
  <h2>Do you know this seller?</h2><p id="mtext"></p>
  <div class="actions"><button class="btn green" id="mconfirm">Confirm I know them</button><button class="btn danger" id="mcancel">Cancel transfer</button><button class="btn ghost" onclick="closeModal()">Later</button></div>
</div></div>
<script>
let ALL=[], FILTER="ALL", Q="", SEL="", LAST_V=null, LAST_N=-1;
const states=["ALL","RECEIVED","CLEAR","PENDING_CONFIRMATION","ESCALATED","CLEARED","CANCELLED"];
async function api(path,body){const r=await fetch(path,body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:undefined);return r.json()}
function fmt(n){return "AED "+Number(n).toLocaleString(undefined,{maximumFractionDigits:0})}
function renderStats(){const s=ALL.reduce((m,t)=>(m[t.state]=(m[t.state]||0)+1,m),{});const auto=(s.CLEAR||0);
 document.getElementById("stats").innerHTML=
 `<span class="chip">auto-cleared<b style="color:var(--green)">${s.CLEAR||0}</b></span>
  <span class="chip">awaiting student<b style="color:var(--amber)">${s.PENDING_CONFIRMATION||0}</b></span>
  <span class="chip">escalated<b style="color:var(--red)">${s.ESCALATED||0}</b></span>
  <span class="chip">staff min saved this batch<b>${auto*12}</b></span>`}
function renderPills(){document.getElementById("pills").innerHTML=states.map(s=>`<span class="pill ${s==FILTER?"on":""}" onclick="FILTER='${s}';render()">${s}${s!="ALL"?" ("+(ALL.filter(t=>t.state==s).length)+")":""}</span>`).join("")}
function render(){renderStats();renderPills();const q=Q.toLowerCase();
 document.getElementById("list").innerHTML=ALL.filter(t=>(FILTER=="ALL"||t.state==FILTER)&&(!q||(t.txn_id+t.student+t.purpose+t.recipient_id).toLowerCase().includes(q)))
 .map(t=>`<div class="card ${t.txn_id==SEL?"sel":""}" onclick="openTxn('${t.txn_id}')">
   <div class="row1"><span class="id">${t.txn_id}</span><span class="st ${t.state}">${t.state.replace("_"," ")}</span><span class="amt">${fmt(t.amount_aed)}</span></div>
   <div class="who">${t.student} → ${t.recipient_id} · ${t.purpose}</div></div>`).join("")||`<div class="empty" style="margin-top:30px">nothing here</div>`}
async function refresh(){ALL=(await api("/api/state")).transactions;render()}
function chainHtml(log){return log.map(s=>`<div class="step"><span class="dot t_ac_${s.actor}"></span><span class="actor t_ac_${s.actor}2">${s.actor}</span><div><b style="font-size:13px">${s.step}</b><div class="sdetail">${esc(s.detail)}</div></div></div>`).join("")}
const GHOST=`<div class="step"><span class="dot t_ac_llm pulse"></span><span class="actor t_ac_llm2">llm</span><div><b style="font-size:13px">reading the message…</b><div class="sdetail" style="color:var(--mut)">the brain is thinking. this is the slow part — everything after it takes milliseconds.</div></div></div>`;
async function openTxn(id){SEL=id;render();const d=await api("/api/txn?id="+id);const t=d.txn;
 LAST_N=d.log.length;
 const pending=t.state=="PENDING_CONFIRMATION";
 const v=(LAST_V&&LAST_V.id==id)?`<div class="verdict" style="display:block"><span class="v" style="color:${vcol(LAST_V.r.action)}">${LAST_V.r.action}</span> <span class="meta">via ${LAST_V.r.rule}</span><div class="e">${esc(LAST_V.r.explanation||"")}</div></div>`:`<div class="verdict" id="verdict"></div>`;
 document.getElementById("detail").innerHTML=`
  <div class="row1"><span class="id" style="font-size:18px">${t.txn_id}</span><span class="st ${t.state}">${t.state.replace("_"," ")}</span></div>
  <div class="meta" style="margin-top:4px">${t.student} → ${t.recipient_id} · <b>${fmt(t.amount_aed)}</b> · ${t.purpose} · filed ${t.created_at}</div>
  <div class="msg">${esc(t.message)}</div>
  <div class="actions">
    <button class="btn go" id="tri" onclick="triage('${t.txn_id}')"><span class="spinner" id="sp"></span>&nbsp;Run agent chain</button>
    ${pending?`<button class="btn green" onclick="openModal('${t.txn_id}')">🙋 Answer as the student</button>`:""}
  </div>
  ${v}
  <div class="chain"><h3>DECISION LOG — every step, in order, no black boxes</h3><div id="chainlist">${chainHtml(d.log)||"<div class='meta'>no chain yet — hit ‘Run agent chain’.</div>"}</div></div>`}
function vcol(a){return (a=="CLEAR"||a=="CLEARED")?"var(--green)":a=="ESCALATED"?"var(--red)":a=="CANCELLED"?"var(--mut)":"var(--amber)"}
function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
async function triage(id){const b=document.getElementById("tri");b.disabled=true;document.getElementById("sp").style.display="inline-block";
 // live view: while the agent runs server-side, poll its audit trail so the
 // board paints each step the instant the agent commits it. Watch it think.
 const iv=setInterval(async()=>{try{const d=await api("/api/txn?id="+id);const el=document.getElementById("chainlist");
  if(el){LAST_N=d.log.length;el.innerHTML=chainHtml(d.log)+GHOST;}}catch(e){}},800);
 let r;try{r=await api("/api/triage",{txn_id:id});}finally{clearInterval(iv)}
 LAST_V={id,r};
 await refresh();openTxn(id);
 if(r.action=="ESCALATED")ping();}
function openModal(id){document.getElementById("modalbg").style.display="grid";document.getElementById("mtext").textContent=`Transaction ${id}: confirm and the money moves now, cancel and it's reported. The guard logs your answer and nothing else can decide for you.`;document.getElementById("mconfirm").onclick=()=>answer(id,"confirm");document.getElementById("mcancel").onclick=()=>answer(id,"cancel")}
function closeModal(){document.getElementById("modalbg").style.display="none"}
async function answer(id,c){const r=await api("/api/resolve",{txn_id:id,choice:c});closeModal();await refresh();openTxn(id)}
async function ping(){const s=await api("/api/state");if(s.broadcasts.length){document.getElementById("feed").textContent="🔊 "+s.broadcasts[0].message;const t=document.getElementById("toast");t.textContent=s.broadcasts[0].message;t.style.display="block";setTimeout(()=>t.style.display="none",6000)}}
document.getElementById("q").oninput=e=>{Q=e.target.value;render()};
document.getElementById("report").onclick=async()=>{const m=document.getElementById("adhoc").value.trim();if(!m)return;
 const b=document.getElementById("report");b.disabled=true;b.textContent="agent working…";
 const r=await api("/api/report",{message:m});b.disabled=false;b.textContent="Triage live";document.getElementById("adhoc").value="";
 LAST_V={id:r.txn_id,r};
 await refresh();openTxn(r.txn_id);if(r.action=="ESCALATED")ping()};
refresh().then(()=>api("/api/state").then(s=>{if(s.broadcasts.length)document.getElementById("feed").textContent="🔊 "+s.broadcasts[0].message}));
</script></body></html>
"""


def serve():
    print(f"PocketPay Campus Shield → http://127.0.0.1:{PORT}   (ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    serve()
