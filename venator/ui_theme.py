# ui_theme.py - VENATOR design system (brushed-steel industrial).
# Single source of truth for every colour, type size and surface in the app.
# Look is deliberately corporate and dense: steel gradients, one amber accent,
# a calm teal for labels and charts. White panels, hairline dividers, no tiles.

THEME = r"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@600;700&display=swap">
<style>
/* ---------- tokens ---------- */
:root{
  --page:linear-gradient(135deg,#E9EDF0,#CBD3DA);
  --steel:linear-gradient(180deg,#C9D1D8,#AEB8C2);
  --steel-line:#8E99A5;
  --steel-hi:rgba(255,255,255,0.5);
  --panel:#FFFFFF;
  --panel-line:#A9B3BD;
  --hair:#DDE3E8;
  --metal:linear-gradient(180deg,#F4F6F8,#D2D9E0);
  --ink:#1F2933;
  --ink-2:#3B4652;
  --ink-3:#4B5763;
  --mark:#2F3A45;
  --amber:#F2A900;
  --amber-line:#C98900;
  --teal:#0B6A72;
  --teal-soft:linear-gradient(180deg,#DCEBE9,#C7DEDA);
  --hi-bg:#F6D9D2;--hi-tx:#7A2A1E;
  --md-bg:#FBE7B0;--md-tx:#5C4100;
  --lo-bg:#CDE8E6;--lo-tx:#0B4F55;
  --nu-bg:#DDE3E8;--nu-tx:#3B4652;
  --head:66px;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{background:var(--page);color:var(--ink);font:15px/1.55 'Barlow',ui-sans-serif,system-ui,sans-serif;
  display:flex;flex-direction:column;overflow:hidden;font-variant-numeric:tabular-nums;
  -webkit-font-smoothing:antialiased}
:focus-visible{outline:2px solid var(--teal);outline-offset:2px}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}

/* ---------- type ---------- */
.cond{font-family:'Barlow Condensed',sans-serif;font-weight:700;text-transform:uppercase;letter-spacing:.02em}
.label{font-size:12px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-3)}
.mono{font-family:ui-monospace,Menlo,Consolas,'Cascadia Mono',monospace}

/* ---------- chrome ---------- */
.topbar{display:flex;align-items:center;gap:20px;min-height:var(--head);padding:0 20px;
  background:var(--steel);border-bottom:1px solid var(--steel-line);
  box-shadow:inset 0 1px 0 var(--steel-hi);flex:none}
.brandlink{display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--ink);
  background:transparent;border:0;cursor:pointer;padding:4px 6px;border-radius:8px;min-height:44px}
.brandlink:hover{background:rgba(255,255,255,.55)}
.wordmark{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:23px;
  letter-spacing:.28em;text-transform:uppercase;color:var(--mark);padding-left:.28em}
.brandsub{border-left:1px solid var(--steel-line);padding-left:18px;display:flex;flex-direction:column;gap:1px}
.brandsub b{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:17px;
  letter-spacing:.08em;text-transform:uppercase}
.brandsub span{font-size:12.5px;color:var(--ink-2)}
.topright{margin-left:auto;display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.kpi{background:rgba(255,255,255,.62);border:1px solid var(--steel-line);border-radius:9px;
  padding:5px 11px;min-width:82px;box-shadow:inset 0 1px 0 #fff}
.kpi i{display:block;font-style:normal;font-size:10.5px;font-weight:600;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-3)}
.kpi b{display:block;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:23px;line-height:1.05}
.who{font-size:12.5px;color:var(--ink-2);text-align:right;line-height:1.3}
.who b{display:block;font-size:13.5px;color:var(--ink)}

.shell{flex:1;display:flex;min-height:0;flex-wrap:wrap}

/* ---------- sidebar ---------- */
.side{flex:0 0 248px;min-width:0;padding:18px 14px 16px;background:var(--steel);
  border-right:1px solid var(--steel-line);box-shadow:inset -1px 0 0 var(--steel-hi);
  display:flex;flex-direction:column;gap:4px;overflow-y:auto}
.gbtn{width:100%;display:flex;align-items:center;gap:9px;min-height:44px;padding:0 12px;
  border:0;border-radius:8px;background:transparent;cursor:pointer;text-align:left;
  font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:15px;letter-spacing:.07em;
  text-transform:uppercase;color:var(--ink);transition:background .12s}
.gbtn:hover{background:rgba(255,255,255,.55)}
.nav{width:100%;display:flex;align-items:center;gap:9px;min-height:44px;padding:0 12px;
  border:0;border-radius:8px;background:transparent;cursor:pointer;text-align:left;
  font:500 14.5px 'Barlow',sans-serif;color:var(--ink-2);transition:background .12s}
.nav:hover{background:rgba(255,255,255,.55)}
.nav[aria-current="page"]{background:#F4F6F8;color:var(--ink);font-weight:600;
  box-shadow:inset 4px 0 0 var(--amber)}
.navwrap{display:flex;flex-direction:column;gap:2px;margin:0 0 6px 8px;
  padding-left:9px;border-left:1px solid rgba(47,58,69,.22)}
.gsep{height:1px;background:rgba(47,58,69,.16);margin:0 8px 8px}
.sidefoot{margin-top:auto;display:flex;flex-direction:column;gap:10px;padding-top:14px}
.cta{background:var(--amber);border:1px solid var(--amber-line);color:var(--ink);
  min-height:46px;border-radius:8px;display:flex;align-items:center;justify-content:center;
  gap:8px;font-weight:600;font-size:14.5px;cursor:pointer;font-family:inherit;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.55)}
.cta:hover{filter:brightness(1.06)}
.sysstat{font-size:12px;color:var(--ink-2);line-height:1.45;padding:0 4px}
.sysstat em{font-style:normal;display:flex;align-items:center;gap:6px}
.pip{width:7px;height:7px;border-radius:50%;background:#0B6A72;flex:none;
  box-shadow:0 0 0 2px rgba(11,106,114,.18)}
.pip.bad{background:#B4402C;box-shadow:0 0 0 2px rgba(180,64,44,.18)}

/* ---------- main ---------- */
.main{flex:999 1 640px;min-width:0;overflow-y:auto;padding:22px 26px 30px}
.head{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap;padding-bottom:14px;
  border-bottom:1px solid var(--panel-line)}
.crumb{font-size:12.5px;color:var(--ink-3);letter-spacing:.05em;text-transform:uppercase;
  font-weight:600;margin-bottom:5px}
.head h1{margin:0;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:38px;
  line-height:1;text-transform:uppercase;letter-spacing:.01em}
.head p{margin:5px 0 0;font-size:14.5px;color:var(--ink-2);max-width:70ch}
.headact{margin-left:auto;display:flex;gap:9px;align-items:center;flex-wrap:wrap}

/* KPI strip — hairlines, never boxes */
.strip{display:flex;flex-wrap:wrap;border-bottom:1px solid var(--panel-line);
  border-top:1px solid var(--panel-line);margin:0 0 20px}
.cell{flex:1 1 150px;min-width:0;padding:12px 18px 13px 0;
  border-right:1px solid var(--hair)}
.cell:last-child{border-right:0;padding-right:0}
.cell i{display:block;font-style:normal;font-size:11px;font-weight:600;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-3);margin-bottom:2px}
.cell b{display:block;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:33px;
  line-height:1.05}
.cell s{display:block;text-decoration:none;font-size:12.5px;color:var(--ink-3);margin-top:2px}
.cell.ok b{color:#0B4F55}.cell.warn b{color:#5C4100}.cell.bad b{color:#7A2A1E}

/* ---------- surfaces ---------- */
.grid{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-start}
.card{background:var(--panel);border:1px solid var(--panel-line);border-radius:12px;
  min-width:0;overflow:hidden}
.card>h2,.cardhead{display:flex;align-items:center;gap:10px;padding:11px 16px;
  background:#E3E8ED;border-bottom:1px solid var(--panel-line);
  font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:16.5px;
  letter-spacing:.09em;text-transform:uppercase;margin:0}
.cardhead .r{margin-left:auto;font-family:'Barlow',sans-serif;font-size:12.5px;
  font-weight:600;letter-spacing:.03em;text-transform:none;color:var(--ink-3)}
.pad{padding:16px}
.dense{padding:13px 16px}

/* ---------- table ---------- */
.tbl{width:100%;border-collapse:collapse;font-size:14px}
.tbl th{text-align:left;padding:9px 14px;background:#E3E8ED;border-bottom:1px solid var(--panel-line);
  font-size:11px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-2);
  white-space:nowrap}
.tbl td{padding:10px 14px;border-bottom:1px solid var(--hair);vertical-align:middle}
.tbl tbody tr{cursor:pointer;transition:background .1s}
.tbl tbody tr:hover{background:#EEF1F4}
.tbl tbody tr[aria-current="true"]{background:#EEF1F4;box-shadow:inset 4px 0 0 var(--amber)}
.tbl .num{text-align:right;font-weight:600;white-space:nowrap}
.scrollx{overflow-x:auto}
.scrollx>table{min-width:640px}

/* ---------- chips ---------- */
.chip{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;font-weight:600;
  padding:3px 10px;border-radius:999px;white-space:nowrap;letter-spacing:.01em}
.c-hi{background:var(--hi-bg);color:var(--hi-tx)}
.c-md{background:var(--md-bg);color:var(--md-tx)}
.c-lo{background:var(--lo-bg);color:var(--lo-tx)}
.c-nu{background:var(--nu-bg);color:var(--nu-tx)}
.c-bl{background:#E3E8ED;color:var(--mark)}
.st-clear,.st-cleared{background:var(--lo-bg);color:var(--lo-tx)}
.st-escalated{background:var(--hi-bg);color:var(--hi-tx)}
.st-pending_confirmation{background:var(--md-bg);color:var(--md-tx)}
.st-received,.st-triaged{background:#DCE6F2;color:#1E3A5F}
.st-cancelled{background:var(--nu-bg);color:var(--nu-tx)}

/* ---------- buttons ---------- */
.btn{font:600 14.5px 'Barlow',sans-serif;min-height:44px;padding:0 17px;border-radius:8px;
  cursor:pointer;border:1px solid var(--steel-line);background:var(--metal);color:var(--ink);
  box-shadow:inset 0 1px 0 #fff;transition:filter .12s}
.btn:hover{filter:brightness(1.05)}.btn:active{filter:brightness(.94)}
.btn:disabled{opacity:.5;cursor:wait}
.btn.go{background:var(--amber);border-color:var(--amber-line);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.55)}
.btn.teal{background:var(--teal-soft);border-color:#8FB5B2;color:#0B4F55}
.btn.danger{background:#F6D9D2;border-color:#C08A7E;color:#7A2A1E}
.btn.ghost{background:transparent;border-color:transparent;color:var(--ink-3);box-shadow:none}
.btn.ghost:hover{background:rgba(255,255,255,.6)}
.btn.sm{min-height:34px;padding:0 12px;font-size:13.5px}

/* ---------- pills ---------- */
.pills{display:flex;gap:7px;flex-wrap:wrap;align-items:center}
.pill{font:600 13.5px 'Barlow',sans-serif;min-height:36px;padding:0 14px;border-radius:999px;
  cursor:pointer;border:1px solid var(--panel-line);background:#F4F6F8;color:var(--ink)}
.pill:hover{background:#fff}
.pill[aria-pressed="true"]{background:var(--ink);color:#F4F6F8;border-color:var(--ink)}

/* ---------- inputs ---------- */
input,textarea,select{font:400 14.5px 'Barlow',sans-serif;color:var(--ink);
  background:#fff;border:1px solid var(--panel-line);border-radius:8px;padding:9px 12px;
  min-height:44px;width:100%;outline:none}
input:focus,textarea:focus{border-color:var(--teal);box-shadow:0 0 0 3px rgba(11,106,114,.13)}
textarea{min-height:84px;resize:vertical;line-height:1.5}
.rowin{display:flex;gap:10px;align-items:flex-start}
.rowin .btn{flex:none}

/* ---------- agent output ---------- */
.find{display:flex;gap:12px;align-items:baseline;padding:10px 0;border-bottom:1px solid var(--hair)}
.find:last-child{border-bottom:0}
.find .tag{flex:none;min-width:74px;justify-content:center}
.find .tx{flex:1;font-size:14.5px;line-height:1.45}
.find .cf{flex:none;font-size:13.5px;font-weight:600;color:var(--ink-2)}
.msg{background:#F7F5EF;border:1px dashed var(--steel-line);border-radius:8px;
  padding:13px 15px;font-size:14.5px;line-height:1.6;color:var(--ink-2)}
.rec{margin-top:12px;font-size:14.5px}

/* ---------- timeline (decision log) ---------- */
.tl{margin-top:4px}
.tlrow{display:grid;grid-template-columns:20px 92px minmax(0,1fr);gap:11px;
  padding:9px 0;border-bottom:1px solid var(--hair)}
.tlrow:last-child{border-bottom:0}
.dot{width:9px;height:9px;border-radius:50%;margin-top:6px;flex:none;justify-self:center}
.a-tool{background:#4A7FB5}.a-llm{background:#0B6A72}.a-guard{background:#B4402C}
.a-human{background:#2E7D5B}.a-agent{background:var(--amber-line)}.a-system{background:#68757F}
.tx-tool{color:#3A6690}.tx-llm{color:var(--teal)}.tx-guard{color:#8F3120}
.tx-human{color:#24604A}.tx-agent{color:#8A6100}.tx-system{color:#4B5763}
.tlrow .actor{font-size:11.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  padding-top:4px}
.tlrow .stp{font-size:13.5px;font-weight:600}
.tlrow .det{font-size:13.5px;color:var(--ink-2);line-height:1.45}
.pulse{animation:pul 1.15s ease-in-out infinite}
@keyframes pul{50%{opacity:.25}}

/* ---------- verdict banner ---------- */
.verdict{display:flex;align-items:center;gap:14px;border-radius:10px;padding:13px 16px;
  border:1px solid var(--panel-line);background:#F7F9FA;margin-top:14px;flex-wrap:wrap}
.verdict b{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:24px;
  letter-spacing:.04em;text-transform:uppercase}
.verdict .why{flex:1;min-width:180px;font-size:14px;color:var(--ink-2)}

/* ---------- rails ---------- */
.rail{display:flex;flex-direction:column;gap:14px;flex:1 1 300px;min-width:0}
.rail.wide{flex:1.35 1 460px}
.rail.narrow{flex:1 1 290px}
.meter{height:9px;border-radius:5px;background:#DDE3E8;overflow:hidden;margin-top:7px}
.meter>i{display:block;height:100%;background:var(--teal);border-radius:5px}
.meter.amber>i{background:var(--amber-line)}
.hi{color:var(--hi-tx)}.lo{color:var(--lo-tx)}

/* ---------- home ---------- */
.home{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:30px;min-height:0}
.home .w{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:46px;
  letter-spacing:.42em;padding-left:.42em;text-transform:uppercase;color:var(--mark)}
.home .t{font-size:14.5px;color:var(--ink-2);text-align:center;max-width:52ch}

/* ---------- toast / modal ---------- */
.toast{position:fixed;right:18px;bottom:18px;max-width:430px;background:#fff;
  border:1px solid #C08A7E;border-left:4px solid #B4402C;border-radius:10px;
  padding:12px 15px;font-size:13.5px;display:none;z-index:20;
  box-shadow:0 8px 26px rgba(31,41,51,.18)}
.modalbg{position:fixed;inset:0;background:rgba(31,41,51,.42);display:none;
  place-items:center;z-index:30}
.modal{background:#fff;border:1px solid var(--panel-line);border-radius:12px;padding:22px;
  max-width:540px;width:92%;box-shadow:0 18px 46px rgba(31,41,51,.26)}
.modal h2{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:24px;
  text-transform:uppercase;margin-bottom:4px}
.modal p{color:var(--ink-2);font-size:14.5px;margin-bottom:16px}
.empty{color:var(--ink-3);text-align:center;padding:44px 20px;font-size:14.5px}
.footbar{flex:none;display:flex;gap:18px;align-items:center;padding:8px 20px;
  background:var(--steel);border-top:1px solid var(--steel-line);
  font-size:12.5px;color:var(--ink-2);box-shadow:inset 0 1px 0 var(--steel-hi);
  white-space:nowrap;overflow:hidden}
.footbar .feed{text-overflow:ellipsis;overflow:hidden;color:var(--hi-tx);font-weight:600}
.legend{display:flex;gap:14px;align-items:center}
.legend i{font-style:normal;display:inline-flex;align-items:center;gap:6px}

/* ---------- responsive ---------- */
@media(max-width:1000px){
  .side{flex:1 1 100%;border-right:0;border-bottom:1px solid var(--steel-line)}
  .main{flex:1 1 100%;padding:18px 16px 24px}
  .brandsub{display:none}
}
@media(max-width:600px){
  .head h1{font-size:30px}
  .cell{flex:1 1 46%}
  .who{display:none}
}
</style>
"""
