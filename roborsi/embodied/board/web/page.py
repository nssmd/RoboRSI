"""Evo dashboard page — inline HTML/CSS/JS served by board.web.evo_app.

Moved verbatim from scripts/evo_dashboard.py; the JS fetches /data.json,
/frame.jpg, /sessions and POSTs /message, /command (routes kept identical in
evo_app so this markup is unchanged)."""

HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoboRSI · 自进化看板</title>
<style>
:root{--bg:#0e1524;--card:#151f33;--line:#243350;--ink:#e8eefc;--muted:#8fa3c4;
--dim:#5f6f8f;--soft:#1b273e;--blue:#2f6df0;--green:#27a567;--violet:#7c5cff;
--amber:#e0930f;--red:#e0544e;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:'Outfit','Noto Sans SC',system-ui,sans-serif;font-size:14px}
.wrap{max-width:1500px;margin:0 auto;padding:18px}
header{display:flex;align-items:center;gap:12px;margin-bottom:14px}
header h1{font-size:19px;margin:0;font-weight:600;letter-spacing:.3px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--green);
box-shadow:0 0 0 3px rgba(39,165,103,.2)}
.dot.off{background:var(--red);box-shadow:0 0 0 3px rgba(224,84,78,.2)}
.ts{color:var(--dim);font-size:12px;margin-left:14px}
.lanesel{display:flex;gap:6px;margin-left:auto}
.lanebtn{background:#141821;color:#9fb0c8;border:1px solid var(--line);border-radius:8px;
  padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer;transition:all .15s;
  white-space:nowrap;max-width:240px;overflow:hidden;text-overflow:ellipsis}
.lanebtn:hover{border-color:#3a4a63;color:#cdd8e8}
.lanebtn.on{background:#132218;color:#5fd39b;border-color:#2f6b45}
.kpis{display:flex;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:10px 16px;min-width:120px}
.kpi b{font-size:22px;font-weight:700;display:block;line-height:1.1}
.kpi span{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:1.1fr 1fr;gap:14px}
@media(max-width:1000px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:14px;margin-bottom:14px}
.card h2{font-size:13px;margin:0 0 10px;color:var(--muted);font-weight:600;
text-transform:uppercase;letter-spacing:.6px}
.warroom{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
@media(max-width:700px){.warroom{grid-template-columns:repeat(2,1fr)}}
.wr{border:1px solid var(--line);border-radius:11px;padding:11px;background:var(--soft);
border-left:3px solid var(--dim);opacity:.6;transition:.2s}
.wr.active{opacity:1;box-shadow:0 0 0 2px rgba(255,255,255,.05)}
.wr-badge{width:26px;height:26px;border-radius:7px;display:flex;align-items:center;
justify-content:center;font-weight:700;color:#fff;font-size:13px;margin-bottom:7px}
.wr-role{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.wr-act{font-size:12.5px;margin-top:4px;line-height:1.4;word-break:break-word}
.cam{width:100%;border-radius:10px;border:1px solid var(--line);background:#000;
display:block;aspect-ratio:4/3;object-fit:contain}
.cam-none{aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;
color:var(--dim);border:1px dashed var(--line);border-radius:10px}
.steps{max-height:340px;overflow:auto}
.step{display:flex;align-items:center;gap:8px;padding:5px 0;
border-bottom:1px solid var(--soft);font-size:13px}
.step .n{color:var(--dim);font-family:'IBM Plex Mono',monospace;font-size:11px;width:34px}
.step .tool{font-weight:600}
.bdg{margin-left:auto;font-size:11px;padding:1px 7px;border-radius:6px}
.bdg.ok{background:rgba(39,165,103,.18);color:#5fd39b}
.bdg.no{background:rgba(224,84,78,.18);color:#f0938e}
.reflect{background:var(--soft);border-left:3px solid var(--violet);border-radius:8px;
padding:9px 11px;margin-top:10px;font-size:12.5px;color:#cbd6ee;line-height:1.5}
.runs{display:flex;flex-wrap:wrap;gap:6px}
.run{font-size:11px;padding:2px 8px;border-radius:6px;border:1px solid var(--line)}
.run.ok{background:rgba(39,165,103,.14);border-color:rgba(39,165,103,.4)}
.run.no{background:rgba(224,84,78,.10)}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px;font-size:11.5px;color:var(--muted)}
.chainwrap{overflow-x:auto;overflow-y:hidden;border-radius:8px}
.chainlegend{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px;font-size:11px;color:var(--muted)}
.chainlegend span{display:inline-flex;align-items:center;gap:5px}
.chainlegend i{width:8px;height:8px;border-radius:50%;display:inline-block}
.hint{font-size:11px;color:var(--dim);font-weight:400;margin-left:8px}
.teleads{display:flex;flex-direction:column;gap:7px;max-height:300px;overflow:auto}
.telead{font-size:12.5px;line-height:1.5;color:#c7d2e0;background:#111722;border:1px solid var(--line);
  border-left:3px solid #5fd39b;border-radius:7px;padding:8px 11px 8px 8px;display:flex;gap:8px}
.teidx{flex:0 0 auto;width:18px;height:18px;border-radius:50%;background:#1a2a1e;color:#5fd39b;
  font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}
.teempty{color:var(--dim);font-size:12px;padding:6px 2px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:middle}
.tree{font-size:13px}
.tnode{padding:6px 0;border-bottom:1px solid var(--soft)}
.thead{display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none}
.tcaret{color:var(--dim);font-size:10px;width:10px;transition:transform .12s}
.tnode.collapsed .tcaret{transform:rotate(-90deg)}
.tsum{margin-left:auto;display:flex;gap:8px;font-size:11px;color:var(--muted)}
.tnode.collapsed .tdetail{display:none}
.tdetail{margin-top:6px}
.tdetail .tex{font-size:11px;color:var(--dim);margin-top:6px;line-height:1.55}
.tname{font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:5px}
.chip{font-size:11px;padding:2px 9px;border-radius:20px;border:1px solid var(--line);
display:inline-flex;align-items:center;gap:5px}
.chip i{width:8px;height:8px;border-radius:50%}
.skillrow{font-size:12px;padding:3px 0;color:var(--muted)}
.skillrow b{color:var(--ink)}
/* 当前动作(now/next)面板 */
.nowcard{background:linear-gradient(135deg,#16233c,#141d31);border-color:#2a3a5c}
.nowgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:700px){.nowgrid{grid-template-columns:1fr}}
.nowbox{background:var(--soft);border:1px solid var(--line);border-radius:11px;padding:12px 14px}
.nowlbl{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;
display:flex;align-items:center;gap:7px;margin-bottom:8px}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--green);
box-shadow:0 0 0 0 rgba(39,165,103,.6);animation:pz 1.4s infinite}
.pulse.wait{background:var(--amber);animation:none;box-shadow:0 0 0 3px rgba(224,147,15,.18)}
@keyframes pz{0%{box-shadow:0 0 0 0 rgba(39,165,103,.55)}70%{box-shadow:0 0 0 9px rgba(39,165,103,0)}
100%{box-shadow:0 0 0 0 rgba(39,165,103,0)}}
.nowtool{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:#eaf1ff;
word-break:break-word;line-height:1.3}
.nowargs{font-family:'IBM Plex Mono',monospace;font-size:12px;color:#7f92b5;margin-top:5px;
word-break:break-word;line-height:1.45}
.nowstat{font-size:11px;padding:2px 9px;border-radius:20px;margin-top:8px;display:inline-block}
.nowstat.run{background:rgba(39,165,103,.16);color:#5fd39b}
.nowstat.ok{background:rgba(39,165,103,.16);color:#5fd39b}
.nowstat.no{background:rgba(224,84,78,.16);color:#f0938e}
.nexttxt{font-size:13.5px;color:#cdd8f0;line-height:1.55}
.nextby{font-size:11px;color:var(--muted);margin-top:8px}
/* LIBERO 纯视觉评测面板 */
.libero{background:linear-gradient(135deg,#141d31,#16233c);border-color:#2a3a5c}
.libtop{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.libgauge{flex:1;min-width:240px}
.libbar{position:relative;height:30px;border-radius:8px;background:#0e1526;border:1px solid var(--line);overflow:visible}
.libfill{position:absolute;top:0;left:0;bottom:0;border-radius:8px 0 0 8px;background:linear-gradient(90deg,#27a567,#3fbf82);transition:width .4s}
.libmark{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--muted)}
.libmlab{position:absolute;top:32px;font-size:10px;color:var(--muted);transform:translateX(-50%)}
.libkpis{display:flex;gap:10px;flex-wrap:wrap}
.libkpis .kpi{min-width:92px;padding:8px 12px}
.libgrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
@media(max-width:800px){.libgrid{grid-template-columns:1fr}}
.liblbl{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.librow{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--soft);font-size:12.5px}
.librow .mono{font-family:'IBM Plex Mono',monospace}
.libshards{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:6px}
@media(max-width:800px){.libshards{grid-template-columns:1fr}}
.libsh{background:var(--soft);border:1px solid var(--line);border-radius:9px;padding:8px 10px;font-size:12px}
.libsh .hd{display:flex;align-items:center;gap:6px}
.libsh .rf{color:var(--dim);font-size:11px;margin-top:3px;line-height:1.4}
</style></head><body><div class="wrap">
<header>
  <div class="dot" id="live"></div>
  <h1>RoboRSI · 纯视觉 campaign 自进化看板</h1>
  <div class="lanesel" id="lanesel"></div>
  <div class="ts" id="ts">—</div>
</header>
<div id="liberoview" style="display:none">
<div class="card libero" id="liberocard"><h2>🤖 LIBERO 纯视觉评测 · ASPIRE 协议 <span class="hint" id="libsrc"></span></h2>
  <div class="libtop">
    <div class="libgauge"><div class="libbar"><div class="libfill" id="libfill"></div>
      <div class="libmark" style="left:18%"></div><div class="libmlab" style="left:18%">CaP 18</div>
      <div class="libmark" style="left:72%"></div><div class="libmlab" style="left:72%">ASPIRE 72</div></div></div>
    <div class="libkpis" id="libkpis"></div>
  </div>
  <div class="libgrid">
    <div><div class="liblbl">按 suite</div><div id="libsuite"></div></div>
    <div><div class="liblbl">按扰动维度</div><div id="libdim"></div></div>
    <div><div class="liblbl">卡点 · 失败模式</div><div id="libfail"></div></div>
  </div>
  <div class="liblbl" style="margin-top:12px">6 shard 实时</div>
  <div class="libshards" id="libshards"></div></div>
<div class="card"><h2>相机画面 · head_camera(实时)</h2>
  <img class="cam" id="libcam" alt="live" style="display:none">
  <div class="cam-none" id="libcamnone">暂无实时帧(eval 未在渲染)</div></div>
</div>
<div id="campaignview">
<div class="kpis" id="kpis"></div>
<div class="card"><h2>三角协作 · 当前在岗</h2><div class="warroom" id="war"></div></div>
<div class="card"><h2>Engineer 调用链 · 坐标树 <span class="hint" id="chainhint"></span></h2>
  <div class="chainlegend" id="chainlegend"></div>
  <div class="chainwrap"><svg id="chainsvg" width="100%" height="230"></svg></div></div>
<div class="card"><h2>🧬 本任务自进化 · <span id="teTask">—</span> <span class="hint" id="teHint"></span></h2>
  <div class="teleads" id="teLeads"></div></div>
<div class="grid">
  <div>
    <div class="card nowcard"><h2>当前动作 · 现在 / 下一步</h2>
      <div class="nowgrid">
        <div class="nowbox"><div class="nowlbl"><span class="pulse" id="nowpulse"></span>正在调用的工具</div>
          <div class="nowtool" id="nowtool">—</div><div class="nowargs" id="nowargs"></div>
          <span class="nowstat" id="nowstat" style="display:none"></span></div>
        <div class="nowbox"><div class="nowlbl">下一步意图</div>
          <div class="nexttxt" id="nexttxt">—</div><div class="nextby" id="nextby"></div></div>
      </div></div>
    <div class="card"><h2>相机画面 · head_camera(实时)</h2>
      <img class="cam" id="cam" alt="live" style="display:none">
      <div class="cam-none" id="camnone">暂无实时帧(run 未在渲染)</div></div>
    <div class="card"><h2>🤖 LIBERO head_camera (live)</h2>
      <img class="cam" id="evocam" alt="LIBERO head_camera live" style="display:none">
      <div class="cam-none" id="evocamnone">暂无实时帧(eval 未在渲染)</div></div>
    <div class="card"><h2>当前 run · 技能执行流</h2><div class="steps" id="steps"></div></div>
    <div class="card"><h2>近期 run 结果</h2><div class="runs" id="runs"></div></div>
  </div>
  <div>
    <div class="card"><h2>🧬 自进化树 <span class="hint">点任务名展开 · 每个任务积累了什么</span></h2>
      <div class="legend">
        <span><i style="background:#2f6df0"></i>Planner·种子plan</span>
        <span><i style="background:#27a567"></i>Engineer·成功轨迹</span>
        <span><i style="background:#7c5cff"></i>Reviewer·失败诊断</span>
        <span><i style="background:#e0930f"></i>Manager·审核晋升</span>
      </div>
      <div class="tree" id="tree"></div>
      <div style="margin-top:12px" id="skills"></div>
    </div>
  </div>
</div>
</div>
</div>
<script>
const RC={planner:'#2f6df0',engineer:'#27a567',reviewer:'#7c5cff',manager:'#e0930f'};
const RN={planner:'Planner',engineer:'Engineer',reviewer:'Reviewer',manager:'Manager'};
const RB={planner:'P',engineer:'E',reviewer:'R',manager:'M'};
let camHas=false;
function applyView(){
  const lib=LANE==='LIBERO';
  const cv=document.getElementById('campaignview'),lv=document.getElementById('liberoview');
  if(cv)cv.style.display=lib?'none':'';
  if(lv)lv.style.display=lib?'':'none';
}
function el(t,c,h){const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;}
function chip(color,label){return `<span class="chip"><i style="background:${color}"></i>${label}</span>`;}
function chipt(color,label,title){return `<span class="chip" title="${esc(title)}"><i style="background:${color}"></i>${label}</span>`;}
// 自进化树:点表头折叠/展开该任务
document.addEventListener('click',e=>{
  const h=e.target.closest('#tree .thead'); if(!h)return;
  h.parentElement.classList.toggle('collapsed');
});
const CATL={perceive:'感知',reach:'可达',grasp:'抓取',move:'运动',place:'放置',verify:'核验',other:'其它'};
const CATC={perceive:'#2f6df0',reach:'#12b5c9',grasp:'#27a567',move:'#e0930f',place:'#7c5cff',verify:'#5fd39b',other:'#8a97ad'};
const CATR={perceive:0,reach:1,grasp:2,move:3,place:4,verify:5,other:6};
function esc(t){return String(t).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));}
function renderChain(chain){
  const svg=document.getElementById('chainsvg'); if(!svg)return;
  const nR=chain.filter(n=>n.kind==='replan').length, nT=chain.filter(n=>n.status==='RETRY').length;
  document.getElementById('chainhint').textContent=chain.length?(`${chain.length} 动作 · replan ${nR} · retry ${nT} · 横轴=步序 竖轴=状态/工具类别 · 红=失败`):'（暂无调用）';
  document.getElementById('chainlegend').innerHTML=
    `<span><i style="background:#e0930f;border-radius:1px"></i>replan(改计划)</span>`+
    `<span><i style="background:#e0930f;border-radius:50%"></i>retry(重试)</span>`+
    `<span><i style="background:#5fd39b;border-radius:1px"></i>done(终态)</span>`+
    Object.keys(CATR).map(c=>`<span><i style="background:${CATC[c]}"></i>${CATL[c]}</span>`).join('');
  if(!chain.length){svg.innerHTML='';return;}
  const rowH=26,padTop=16,padL=54,padR=20,dx=Math.max(22,Math.min(56,980/chain.length));
  const W=padL+chain.length*dx+padR,H=padTop+8*rowH+10;
  const yOf=n=>(n.kind==='replan'||n.kind==='done')?padTop:padTop+(CATR[n.cat]+1)*rowH;
  let s='';
  s+=`<line x1="${padL}" y1="${padTop}" x2="${W-padR}" y2="${padTop}" stroke="#3a3520"/>`;
  s+=`<text x="6" y="${padTop+4}" fill="#e0930f" font-size="10.5">控制</text>`;
  for(const c in CATR){const y=padTop+(CATR[c]+1)*rowH;
    s+=`<line x1="${padL}" y1="${y}" x2="${W-padR}" y2="${y}" stroke="#1b2330"/>`;
    s+=`<text x="6" y="${y+4}" fill="${CATC[c]}" font-size="10.5">${CATL[c]}</text>`;}
  chain.forEach((n,i)=>{if(n.kind==='replan'){const x=padL+i*dx;
    s+=`<line x1="${x}" y1="${padTop-6}" x2="${x}" y2="${H-6}" stroke="#e0930f" stroke-dasharray="3 4" opacity="0.45"/>`;}});
  for(let i=0;i<chain.length-1;i++){const a=chain[i],b=chain[i+1];
    const x1=padL+i*dx,y1=yOf(a),x2=padL+(i+1)*dx,y2=yOf(b);
    s+=`<path d="M${x1} ${y1} C ${(x1+x2)/2} ${y1}, ${(x1+x2)/2} ${y2}, ${x2} ${y2}" stroke="#2b3648" fill="none" stroke-width="1.3"/>`;}
  chain.forEach((n,i)=>{const x=padL+i*dx,y=yOf(n);
    const fail=n.ok==='False',run=n.ok==null,retry=n.status==='RETRY';
    let tip=`step ${n.step} · ${n.tool}(${n.args})`;
    if(n.kind==='replan')tip=`REPLAN · ${n.note||n.args}`;
    else if(n.kind==='done')tip=`DONE · ${n.args}`;
    if(retry)tip+=` ⟲RETRY: ${n.note||''}`;
    tip+=fail?' ✗失败':(run?' …运行中':' ✓ok'); tip=esc(tip);
    if(n.kind==='replan'){
      s+=`<rect x="${x-6}" y="${y-6}" width="12" height="12" transform="rotate(45 ${x} ${y})" fill="#e0930f" stroke="#0d1017"><title>${tip}</title></rect>`;
    }else if(n.kind==='done'){
      s+=`<rect x="${x-7}" y="${y-7}" width="14" height="14" rx="2" fill="${n.args.includes('True')?'#5fd39b':'#ff5a5a'}" stroke="#0d1017"><title>${tip}</title></rect>`;
    }else{
      if(retry)s+=`<circle cx="${x}" cy="${y}" r="10" fill="none" stroke="#e0930f" stroke-width="1.6" opacity="0.85"/>`;
      s+=`<circle cx="${x}" cy="${y}" r="6" fill="${CATC[n.cat]}" stroke="${fail?'#ff5a5a':(run?'#fff':'#0d1017')}" stroke-width="${fail?2.2:1.4}"><title>${tip}</title></circle>`;
      if(fail)s+=`<text x="${x}" y="${y-10}" fill="#ff5a5a" font-size="10" text-anchor="middle">✗</text>`;
    }});
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`); svg.setAttribute('height',H);
  svg.innerHTML=s;
}
async function tick(){
  let d; try{d=await (await fetch('/data.json?lane='+LANE+'&_='+Date.now())).json();}catch(e){return;}
  const c=d.campaign;
  if(LANE!=='LIBERO' && c.lanes && c.lanes.length && !c.lanes.some(l=>l.id===LANE)) LANE=c.lanes[0].id;
  let laneHtml=(c.lanes||[]).map(l=>
    `<button class="lanebtn${l.id===LANE?' on':''}" data-lane="${l.id}">`+
    `<b>${l.id}</b> · ${l.task||'—'}${l.seed!=null?(' · s'+l.seed):''}</button>`).join('');
  laneHtml+=`<button class="lanebtn${LANE==='LIBERO'?' on':''}" data-lane="LIBERO">🤖 LIBERO</button>`;
  document.getElementById('lanesel').innerHTML=laneHtml;
  applyView();
  if(LANE==='LIBERO'){document.getElementById('live').className='dot';document.getElementById('ts').textContent='LIBERO 纯视觉评测';return;}
  document.getElementById('live').className='dot'+(c.live?'':' off');
  document.getElementById('ts').textContent='更新 '+d.generated_str;
  // KPIs
  const cur=c.current||{};
  document.getElementById('kpis').innerHTML=
    `<div class="kpi"><b style="color:#5fd39b">${c.real_success}</b><span>Sim 真成功</span></div>`+
    `<div class="kpi"><b>${c.total_runs}</b><span>累计 run</span></div>`+
    `<div class="kpi"><b>${cur.task||'—'}</b><span>当前任务 · seed ${cur.seed??'—'} · 轮 ${cur.round??'—'}</span></div>`+
    `<div class="kpi"><b style="color:#f0b352">${Object.values(d.evolution.pending).reduce((a,b)=>a+b,0)}</b><span>待审队列</span></div>`;
  // war-room
  document.getElementById('war').innerHTML=d.roles.map(r=>
    `<div class="wr ${r.active?'active':''}" style="border-left-color:${r.color}">
      <div class="wr-badge" style="background:${r.color}">${RB[r.role]}</div>
      <div class="wr-role">${RN[r.role]}${r.active?' · 在岗':''}</div>
      <div class="wr-act">${r.action||''}</div></div>`).join('');
  renderChain(d.chain||[]);
  const te=d.task_evo||{};
  document.getElementById('teTask').textContent=te.task||'—';
  const f=te.funnel||{};
  document.getElementById('teHint').textContent=`approved ${f.approved||0} · rejected ${f.rejected||0} · pending ${f.pending||0} · 成功轨迹 ${te.success||0} · 失败 ${te.fail||0}`;
  const leads=te.leads||[];
  document.getElementById('teLeads').innerHTML=leads.length
    ? leads.map((t,i)=>`<div class="telead"><span class="teidx">${i+1}</span>${esc(t)}</div>`).join('')
    : '<div class="teempty">（该任务尚无 Manager-approved leads — 跑出失败假设、我审批后会累积在这里）</div>';
  // now / next 动作面板
  camHas=!!d.has_frame;
  const st=d.steps||[]; const last=st.length?st[st.length-1]:null;
  const nt=document.getElementById('nowtool'),na=document.getElementById('nowargs'),
        ns=document.getElementById('nowstat'),np=document.getElementById('nowpulse');
  if(last){
    nt.textContent=last.tool+'( )';
    na.textContent=last.args||'';
    const running=last.ok==null;
    np.className='pulse'+(running?'':' wait');
    ns.style.display='inline-block';
    ns.className='nowstat '+(running?'run':(last.ok=='True'?'ok':'no'));
    ns.textContent=running?'执行中…':(last.ok=='True'?'✓ 成功':'✗ 失败');
  }else{nt.textContent='等待工具调用…';na.textContent='';ns.style.display='none';np.className='pulse wait';}
  const active=(d.roles||[]).find(r=>r.active);
  document.getElementById('nexttxt').textContent=d.reflection||(active&&active.action)||'—';
  document.getElementById('nextby').textContent=active?('— '+RN[active.role]+' 在岗规划'):'';
  // steps
  document.getElementById('steps').innerHTML=st.length? st.map(s=>
    `<div class="step"><span class="n">#${s.step}</span>
      <span class="tool">${s.tool}</span>
      <span style="color:#5f6f8f;font-size:11px">${s.args||''}</span>
      ${s.ok==null?'':`<span class="bdg ${s.ok=='True'?'ok':'no'}">${s.ok=='True'?'ok':'fail'}</span>`}
    </div>`).join('') : '<div style="color:#5f6f8f">等待工具调用…</div>';
  document.getElementById('steps').scrollTop=9e9;
  // runs
  document.getElementById('runs').innerHTML=d.runs.map(r=>
    `<span class="run ${r.ok?'ok':'no'}">${r.ok?'✓':'✗'} ${r.task} s${r.seed}${r.tool_calls?'·'+r.tool_calls:''}</span>`).join('');
  // evolution tree — collapsed by default; click a task to expand its detail.
  document.getElementById('tree').innerHTML=d.evolution.tasks.map(t=>{
    let ch='';
    if(t.seed_plan) ch+=chipt(RC.planner,'种子 plan','Planner 首次为该任务写的初始 plan.md');
    if(t.success)   ch+=chipt(RC.engineer,'成功 '+t.success+' 次','Engineer 跑通、且被 Sim 判据判定成功的次数(纯视觉,非自报 done)');
    if(t.fail)      ch+=chipt('#5f6f8f','失败 '+t.fail+' 次','Engineer 未通过 Sim 判据的次数');
    if(t.leads)     ch+=chipt(RC.reviewer,'已审经验 '+t.leads,'Manager 审核通过、写进 wiki 指导下次 plan 的经验(lead)');
    if(t.hyp_pending)ch+=chipt('#f0b352','待审失败诊断 '+t.hyp_pending,'Reviewer 对某次失败给出的根因猜想(假设),排队等 Manager 审核;审核前不影响 plan');
    if(t.promo_applied)ch+=chipt(RC.manager,'plan 晋升 '+t.promo_applied,'被采纳为该任务基线 plan 的次数');
    if(t.promo_pending)ch+=chipt('#f0b352','待晋升 plan '+t.promo_pending,'等待 Manager 审核晋升为基线 plan');
    // compact summary shown while collapsed
    const pend=(t.hyp_pending||0)+(t.promo_pending||0);
    let sum=`<b style="color:#27a567">✓${t.success||0}</b><b style="color:#8896ad">✗${t.fail||0}</b>`;
    if(t.leads) sum+=`<b style="color:#7c5cff">经验${t.leads}</b>`;
    if(pend)    sum+=`<b style="color:#f0b352">●${pend}待审</b>`;
    const ex='读法:Engineer 每跑一次留下成功/失败轨迹 → Reviewer 给失败写「诊断(假设)」进待审队列 → 我(Manager)审核通过后它才变成「经验(线索)」写进 wiki,指导 Planner 下次 plan。';
    return `<div class="tnode collapsed"><div class="thead"><span class="tcaret">▾</span>`+
      `<span class="tname">${t.task}</span><span class="tsum">${sum}</span></div>`+
      `<div class="tdetail"><div class="chips">${ch||'<span style=color:#5f6f8f>—</span>'}</div>`+
      `<div class="tex">${ex}</div></div></div>`;
  }).join('');
  // skills library
  const sk=d.evolution.skills;
  document.getElementById('skills').innerHTML=
    `<div class="tname" style="margin-bottom:6px">🛠 技能库进化</div>`+
    `<div class="chips">${chip(RC.engineer,'新建技能 '+sk.new)}${chip(RC.planner,'技能更新 '+sk.updated)}</div>`+
    sk.recent.map(s=>`<div class="skillrow"><b>${s.skill}</b> · ${s.kind=='new'?'新建':'更新'}</div>`).join('');
}
// 相机:独立高频刷新 + 双缓冲(新帧解码完成才换 src → 绝不闪屏/切屏)
function refreshCam(){
  const cam=document.getElementById('cam'),none=document.getElementById('camnone');
  if(!camHas){cam.style.display='none';none.style.display='flex';return;}
  none.style.display='none';cam.style.display='block';
  const img=new Image();
  img.onload=()=>{cam.src=img.src;};   // 只有加载好才换，旧帧一直显示到新帧就绪
  img.src='/frame.jpg?lane='+LANE+'&_='+Date.now();
}
let libLive=false;
function refreshLibCam(){
  if(LANE!=='LIBERO')return;
  const cam=document.getElementById('libcam'),none=document.getElementById('libcamnone');
  if(!libLive){cam.style.display='none';none.style.display='flex';none.textContent='暂无实时帧 · eval 未在运行';return;}
  const img=new Image();
  img.onload=()=>{cam.src=img.src;cam.style.display='block';none.style.display='none';};
  img.onerror=()=>{cam.style.display='none';none.style.display='flex';none.textContent='暂无实时帧(eval 未在渲染)';};
  img.src='/aspire_frame.jpg?_='+Date.now();
}
// LIBERO head_camera live 面板(evo campaign 视图):独立高频轮询 /cam,双缓冲。
// /cam 无帧时返回 204 → img 触发 onerror → 保持上一帧,仅在从未有帧时显示占位。
function refreshEvoCam(){
  if(LANE==='LIBERO')return;
  const cam=document.getElementById('evocam'),none=document.getElementById('evocamnone');
  const img=new Image();
  img.onload=()=>{cam.src=img.src;cam.style.display='block';none.style.display='none';};
  img.onerror=()=>{if(cam.style.display==='none'){none.style.display='flex';none.textContent='暂无实时帧(eval 未在渲染)';}};
  img.src='/cam?_='+Date.now();
}
let LANE='A';
document.getElementById('lanesel').addEventListener('click',e=>{
  const b=e.target.closest('.lanebtn'); if(!b)return;
  LANE=b.getAttribute('data-lane'); applyView(); tick(); if(LANE==='LIBERO'){tickAspire();refreshLibCam();} refreshCam(); refreshEvoCam();
});
const FAILL={grasp_retry:'grasp 重试',budget_exhausted:'tool budget 耗尽',detect_sentinel:'检测哨兵(128)',graspgen_zmq_timeout:'GraspGen ZMQ 超时'};
function libtbl(o){const e=Object.entries(o||{});if(!e.length)return '<div style="color:#5f6f8f">—</div>';
  return e.map(([k,x])=>`<div class="librow"><span>${k}</span><span class="mono">${x.s}/${x.n} <b style="color:${x.rate>18?'#5fd39b':'#f0938e'}">${x.rate}%</b></span></div>`).join('');}
async function tickAspire(){
  let d; try{d=await (await fetch('/aspire.json?_='+Date.now())).json();}catch(e){return;}
  libLive=(d.live_shards||0)>0;
  document.getElementById('libsrc').textContent=(d.source||'')+' · 对照 CaP 18 / ASPIRE 72';
  const bc=d.beats_cap?'#5fd39b':'#f0938e';
  document.getElementById('libfill').style.width=Math.min(100,d.success_rate)+'%';
  document.getElementById('libkpis').innerHTML=
    `<div class="kpi"><b style="color:${bc}">${d.success_rate}%</b><span>成功率</span></div>`+
    `<div class="kpi"><b>${d.successes}/${d.episodes}</b><span>ep 成功</span></div>`+
    `<div class="kpi"><b>${d.done_tasks}</b><span>已判定任务</span></div>`+
    `<div class="kpi"><b style="color:#f0b352">${d.error_tasks}</b><span>error 任务</span></div>`+
    `<div class="kpi"><b>${d.live_shards}/6</b><span>${d.done_marker?'✓ 完成':'存活 shard'}</span></div>`;
  document.getElementById('libsuite').innerHTML=libtbl(d.by_suite);
  document.getElementById('libdim').innerHTML=libtbl(d.by_dim);
  document.getElementById('libfail').innerHTML=Object.entries(d.fail_modes||{}).map(([k,n])=>
    `<div class="librow"><span>${FAILL[k]||k}</span><span class="mono" style="color:#e0930f">${n}</span></div>`).join('');
  document.getElementById('libshards').innerHTML=(d.shards||[]).map(s=>
    `<div class="libsh"><div class="hd"><span class="dot${s.alive?'':' off'}" style="width:8px;height:8px;box-shadow:none"></span>`+
    `<b>shard ${s.shard}</b> <span style="color:#5f6f8f">${s.suite||''}</span>${s.step?(' · step '+s.step):''}</div>`+
    `<div class="mono" style="color:#9fb0c8;margin-top:3px">${esc(s.action||'—')}</div>`+
    (s.reflect?`<div class="rf">💭 ${esc(s.reflect)}</div>`:'')+`</div>`).join('');
}
tick(); setInterval(tick,3000);
tickAspire(); setInterval(tickAspire,3000);
refreshCam(); setInterval(refreshCam,700);
refreshLibCam(); setInterval(refreshLibCam,700);
refreshEvoCam(); setInterval(refreshEvoCam,1000);
</script></body></html>"""

