import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from flask import Flask, jsonify, render_template_string, request

from bot import BotApp, load_config


HTML = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>AIG Live Dashboard</title>
  <style>
    :root {
      --bg: #f3ead0;
      --card: #fff9ea;
      --line: #e2c889;
      --soft: #fff5df;
      --text: #4b2d1f;
      --muted: #8c6f4e;
      --cyan: #cf5f3f;
      --green: #2f9e62;
      --red: #c24f4f;
      --amber: #d08f2d;
      --coffee: #6b4a2d;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--text);
      font-family: "Trebuchet MS", "Segoe UI", Tahoma, sans-serif;
      background:
        radial-gradient(circle at 12% 14%, rgba(255,255,255,.7) 0%, rgba(255,255,255,0) 40%),
        radial-gradient(circle at 86% 10%, rgba(255,214,140,.32) 0%, rgba(255,214,140,0) 45%),
        linear-gradient(180deg, #f7edd2 0%, var(--bg) 100%);
    }
    .wrap { width: 100vw; max-width: none; margin: 8px auto; padding: 0 16px 16px; }
    .head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; gap: 10px; }
    .head-right { display:flex; align-items:center; gap:8px; }
    .title { font-size: 30px; color: var(--coffee); letter-spacing: .5px; font-weight: 700; }
    .badge { border:1px solid var(--line); background: var(--soft); border-radius:10px; padding:8px 10px; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .ctrl-btn {
      border:1px solid var(--line);
      background: #fff3d5;
      color: var(--coffee);
      border-radius:10px;
      padding:8px 10px;
      cursor:pointer;
      font-family: inherit;
      font-size: 12px;
      font-weight: 700;
    }
    .ctrl-btn:hover { filter: brightness(1.12); }
    .ctrl-btn.stop { border-color:#be6a5f; color:#8f2f2f; }
    .ctrl-btn.start { border-color:#4f965f; color:#246a3a; }
    .ctrl-btn.emergency { border-color:#a43f3f; color:#fff4f4; background:#be4d4d; }
    .mode-select {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fffdf5;
      color: var(--coffee);
      padding: 8px;
      font-size: 12px;
      font-weight: 700;
    }

    .kpi { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
    .pill { border:1px solid var(--line); background:#fff7e7; border-radius:10px; padding:8px 10px; min-width: 140px; }
    .pill .k { color: var(--muted); font-size:11px; }
    .pill .v { color: var(--coffee); font-size:16px; margin-top:2px; font-weight: 700; }
    .hero {
      border:1px solid var(--line);
      background: linear-gradient(120deg, #fff6df, #fff1cf);
      border-radius:12px;
      padding:14px;
      margin-bottom:10px;
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:10px;
      box-shadow: 0 8px 20px rgba(179, 128, 31, 0.12);
      animation: glow 3s ease-in-out infinite alternate;
    }
    .hero .label { color: var(--muted); font-size:12px; }
    .hero .val { color: #6b3a24; font-size:42px; font-weight:800; letter-spacing:.2px; }
    .hero .sub { color: var(--muted); font-size:12px; margin-top:4px; }
    .hero .mode {
      border:1px solid var(--line);
      border-radius:10px;
      padding:8px 10px;
      font-size:12px;
      color: var(--coffee);
      background:#fff8ea;
    }
    .alert {
      border:1px solid #d59e4f;
      background:#fff3d8;
      color:#7b4a16;
      border-radius:10px;
      padding:8px 10px;
      margin-bottom:10px;
      display:none;
    }

    .layout { display:grid; grid-template-columns: minmax(0, 1.6fr) minmax(340px, .8fr); gap:10px; }
    .card { border:1px solid var(--line); border-radius:12px; background: linear-gradient(180deg, #fffbf1, #fff6e6); padding:10px; box-shadow: 0 5px 12px rgba(179, 128, 31, 0.08);}
    .card h3 { margin:2px 0 8px; color: var(--amber); font-size:14px; letter-spacing:.6px; }

    .chart-wrap { position:relative; height:260px; border:1px solid #e4c990; border-radius:10px; background:#fffef8; overflow:hidden; }
    .signal-chart-wrap { height: clamp(560px, 72vh, 860px); }
    canvas { display:block; width:100%; height:100%; }

    .right-grid { display:grid; grid-template-rows: 1fr 1fr; gap:10px; }

    table { width:100%; border-collapse:collapse; font-size:12px; }
    th, td { padding:7px 6px; border-bottom:1px solid #efdeb8; text-align:left; }
    th { color: var(--muted); font-weight:600; }

    .depth { display:grid; grid-template-columns: 1fr 1fr; gap:8px; }
    .book { border:1px solid #e8cf9d; border-radius:10px; padding:6px; background:#fff9ea; }
    .row { position:relative; display:flex; justify-content:space-between; font-size:12px; padding:4px 6px; margin:2px 0; overflow:hidden; }
    .row span { position:relative; z-index:1; }
    .row::before { content:''; position:absolute; inset:0; opacity:.28; }
    .ask::before { background: linear-gradient(90deg, rgba(194,79,79,.48), transparent); }
    .bid::before { background: linear-gradient(90deg, rgba(47,158,98,.48), transparent); }

    .feed { height:220px; overflow:auto; border:1px solid #e8cf9d; border-radius:10px; background:#fff8e8; padding:8px; font-size:12px; }

    .floaters { position: fixed; top: 14px; right: 14px; z-index: 20; display:flex; flex-direction:column; gap:8px; }
    .toast { min-width:260px; border-radius:10px; border:1px solid var(--line); background:#fff7e4; padding:10px 12px; animation: pop .24s ease-out; color:#5d3e24; }
    .toast.entry { border-color:#4f965f; box-shadow:0 0 0 1px #abd8b6 inset; }
    .toast.exit { border-color:#be6a5f; box-shadow:0 0 0 1px #f0c6bf inset; }
    @keyframes pop { from { opacity:0; transform:translateY(-6px);} to { opacity:1; transform:translateY(0);} }
    @keyframes glow { from { box-shadow: 0 8px 20px rgba(179, 128, 31, 0.09);} to { box-shadow: 0 12px 28px rgba(179, 128, 31, 0.18);} }

    .g { color: var(--green); }
    .r { color: var(--red); }

    @media (max-width: 960px) {
      .layout { grid-template-columns: 1fr; }
      .right-grid { grid-template-rows: auto auto; }
      .signal-chart-wrap { height: 420px; }
      .head { flex-direction: column; align-items: flex-start; }
      .head-right { flex-wrap: wrap; }
    }
  </style>
</head>
<body>
  <div class=\"floaters\" id=\"floaters\"></div>
  <div class=\"wrap\">
    <div id=\"alert\" class=\"alert\"></div>
    <div class=\"head\">
      <div class=\"title\">AIG // LIVE TRADER DASHBOARD</div>
      <div class=\"head-right\">
        <select id=\"modeSelect\" class=\"mode-select\">
          <option value=\"paper\">PAPER</option>
          <option value=\"live\">LIVE</option>
        </select>
        <button class=\"ctrl-btn\" onclick=\"switchMode()\">APPLY MODE</button>
        <button class=\"ctrl-btn start\" onclick=\"control('start')\">START</button>
        <button class=\"ctrl-btn stop\" onclick=\"control('stop')\">STOP</button>
        <button class=\"ctrl-btn emergency\" onclick=\"control('emergency-close')\">EMERGENCY CLOSE</button>
        <div id=\"stamp\" class=\"badge\">loading...</div>
      </div>
    </div>

    <div class=\"hero\">
      <div>
        <div class=\"label\">TOTAL BALANCE</div>
        <div id=\"heroBalance\" class=\"val\">$0.00</div>
        <div id=\"heroSub\" class=\"sub\">budget $0.00 | pnl $0.00</div>
        <div id=\"heroChain\" class=\"sub\">chain wallet: loading...</div>
      </div>
      <div id=\"heroMode\" class=\"mode\">MODE: -</div>
    </div>

    <div class=\"kpi\" id=\"kpi\"></div>

    <div class=\"layout\">
      <div class=\"card\">
        <h3>SIGNAL FLOW ENGINE</h3>
        <div class=\"chart-wrap signal-chart-wrap\"><canvas id=\"flowCanvas\"></canvas></div>
        <div style=\"margin-top:8px\">
          <table>
            <thead><tr><th>Symbol</th><th>Price</th><th>Score</th><th>Conf</th><th>Vol</th><th>Action Bias</th></tr></thead>
            <tbody id=\"signals\"></tbody>
          </table>
        </div>
      </div>

      <div class=\"right-grid\">
        <div class=\"card\">
          <h3>PNL CURVE</h3>
          <div class=\"chart-wrap\" style=\"height:220px\"><canvas id=\"pnlCanvas\"></canvas></div>
          <div style=\"margin-top:8px\">
            <table>
              <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Mark</th><th>U-PnL</th></tr></thead>
              <tbody id=\"positions\"></tbody>
            </table>
          </div>
        </div>

        <div class=\"card\">
          <h3>ORDER BOOK + EXECUTION FEED</h3>
          <div class=\"depth\">
            <div class=\"book\"><div style=\"color:var(--red);margin-bottom:4px\">ASKS</div><div id=\"asks\"></div></div>
            <div class=\"book\"><div style=\"color:var(--green);margin-bottom:4px\">BIDS</div><div id=\"bids\"></div></div>
          </div>
          <div id=\"feed\" class=\"feed\" style=\"margin-top:8px\"></div>
        </div>
      </div>
    </div>
  </div>

<script>
const seen = new Set();
let history = [];
let latest = {};
let liveBalance = {};
const flow = { particles: [], tradeBursts: [] };
let staleCount = 0;

function fmt(n, d=4){ return (typeof n === 'number') ? n.toLocaleString(undefined,{maximumFractionDigits:d}) : '-'; }
function clsScore(x){ return (x||0) >= 0 ? 'g' : 'r'; }

function addToast(kind, msg){
  const box = document.getElementById('floaters');
  const d = document.createElement('div');
  d.className = 'toast ' + kind;
  d.textContent = msg;
  box.prepend(d);
  setTimeout(()=>d.remove(), 6500);
}

function spawnTradeBurst(label, side){
  const c = document.getElementById('flowCanvas');
  if(!c) return;
  const h = c.clientHeight || 500;
  const yBase = side === 'BUY' || side === 'COVER' ? h*0.42 : h*0.58;
  for(let i=0;i<22;i++){
    flow.tradeBursts.push({
      x: 44 + Math.random()*28,
      y: yBase + (Math.random()-0.5)*44,
      vx: 4.2 + Math.random()*4.8,
      vy: (Math.random()-0.5)*1.2,
      life: 1,
      label,
      side,
    });
  }
}

function setAlert(msg){
  const el = document.getElementById('alert');
  if(!msg){ el.style.display = 'none'; el.textContent = ''; return; }
  el.style.display = 'block';
  el.textContent = msg;
}

function renderKPIs(s){
  const kpi = document.getElementById('kpi');
  const wr = ((s.win_rate||0)*100).toFixed(1) + '%';
  const status = s.kill_switch ? 'HALTED' : 'ACTIVE';
  const exec = (s.mode || 'paper').toUpperCase();
  const totalPnl = s.total_pnl ?? ((s.realized_pnl||0) + (s.unrealized_pnl||0));
  const roi = ((s.roi_pct||0)*100).toFixed(2) + '%';
  const eq = Number(s.equity||0);
  const useChain = String((s.mode||'paper')).toLowerCase() === 'live' && liveBalance && liveBalance.ok;
  const shownBalance = useChain ? Number(liveBalance.total_usd_estimate||0) : eq;
  document.getElementById('heroBalance').textContent = `$${fmt(shownBalance,2)}`;
  document.getElementById('heroSub').textContent = `budget $${fmt(s.budget_usd ?? s.starting_cash,2)} | total pnl ${(totalPnl>=0?'+':'')}$${fmt(totalPnl,2)} | roi ${roi}`;
  const chainTxt = liveBalance && liveBalance.ok
    ? `chain wallet ≈ $${fmt(liveBalance.total_usd_estimate,2)} | SOL ${fmt(liveBalance.sol_balance,4)} | USDC ${fmt(liveBalance.usdc_balance,2)}`
    : `chain wallet: unavailable`;
  document.getElementById('heroChain').textContent = chainTxt;
  document.getElementById('heroMode').textContent = `MODE: ${(s.mode||'paper').toUpperCase()} | ${(s.trading_venue||'-')}`;
  kpi.innerHTML = `
    <div class='pill'><div class='k'>STARTING BUDGET</div><div class='v'>$${fmt(s.starting_cash ?? s.budget_usd,2)}</div></div>
    <div class='pill'><div class='k'>EQUITY</div><div class='v'>$${fmt(s.equity,2)}</div></div>
    <div class='pill'><div class='k'>CASH</div><div class='v'>$${fmt(s.cash,2)}</div></div>
    <div class='pill'><div class='k'>REALIZED PNL</div><div class='v'>$${fmt(s.realized_pnl,2)}</div></div>
    <div class='pill'><div class='k'>UNREALIZED PNL</div><div class='v'>$${fmt(s.unrealized_pnl,2)}</div></div>
    <div class='pill'><div class='k'>TOTAL PNL</div><div class='v'>${totalPnl>=0?'+':''}$${fmt(totalPnl,2)}</div></div>
    <div class='pill'><div class='k'>ROI</div><div class='v'>${roi}</div></div>
    <div class='pill'><div class='k'>FEES</div><div class='v'>$${fmt(s.total_fees,2)}</div></div>
    <div class='pill'><div class='k'>WIN RATE</div><div class='v'>${wr}</div></div>
    <div class='pill'><div class='k'>EXECUTION</div><div class='v'>${exec}</div></div>
    <div class='pill'><div class='k'>STATUS</div><div class='v'>${status}</div></div>
  `;
}

function renderSignals(s){
  const rows = (s.top_signals||[]).map(x => {
    const bias = x.score > 0.01 ? 'LONG' : x.score < -0.01 ? 'SHORT' : 'HOLD';
    return `<tr>
      <td>${x.symbol}</td>
      <td>${fmt(x.price,6)}</td>
      <td class='${clsScore(x.score)}'>${((x.score||0)*100).toFixed(2)}%</td>
      <td>${fmt(x.confidence,2)}</td>
      <td>${fmt(x.quote_volume,0)}</td>
      <td>${bias}</td>
    </tr>`;
  }).join('');
  document.getElementById('signals').innerHTML = rows || '<tr><td colspan="6">-</td></tr>';
}

function renderPositions(s){
  const rows = (s.open_positions||[]).map(x => `<tr>
      <td>${x.symbol}</td><td>${x.side||'-'}</td><td>${fmt(x.qty,6)}</td>
      <td>${fmt(x.entry_price,6)}</td><td>${fmt(x.mark_price,6)}</td><td class='${(x.unrealized_pnl||0)>=0?"g":"r"}'>${fmt(x.unrealized_pnl,4)}</td>
    </tr>`).join('');
  document.getElementById('positions').innerHTML = rows || '<tr><td colspan="6">-</td></tr>';
}

function levelPrice(v){
  const n = Number(v || 0);
  if(!Number.isFinite(n)) return 0;
  if(n > 1000000) return n / 1000000; // Drift L2 often uses 1e6 precision
  return n;
}
function levelSize(v){
  const n = Number(v || 0);
  if(!Number.isFinite(n)) return 0;
  if(n > 10000000000) return n / 1000000000;
  if(n > 1000000) return n / 1000000;
  return n;
}
function renderOrderBook(ob, s){
  if(ob && Array.isArray(ob.asks) && Array.isArray(ob.bids) && ob.asks.length && ob.bids.length){
    const asks = ob.asks.slice(0,10).map(x => ({px: levelPrice(x.price), sz: levelSize(x.size)}));
    const bids = ob.bids.slice(0,10).map(x => ({px: levelPrice(x.price), sz: levelSize(x.size)}));
    document.getElementById('asks').innerHTML = asks.map(x=>`<div class='row ask'><span>${fmt(x.px,4)}</span><span>${fmt(x.sz,3)}</span></div>`).join('');
    document.getElementById('bids').innerHTML = bids.map(x=>`<div class='row bid'><span>${fmt(x.px,4)}</span><span>${fmt(x.sz,3)}</span></div>`).join('');
    return;
  }
  // fallback if API unavailable
  const sig = (s.top_signals||[])[0];
  if(!sig){ document.getElementById('asks').innerHTML=''; document.getElementById('bids').innerHTML=''; return; }
  const p = sig.price || 0;
  const drift = (sig.score || 0) * 0.2;
  const asks = []; const bids = [];
  for(let i=1;i<=8;i++){
    const spread = i * (0.0008 + Math.abs(drift)*0.0006) * p;
    asks.push({px: p + spread, sz: Math.round((220000/(i+1)) + Math.random()*9000)});
    bids.push({px: p - spread, sz: Math.round((220000/(i+1)) + Math.random()*9000)});
  }
  document.getElementById('asks').innerHTML = asks.map(x=>`<div class='row ask'><span>${fmt(x.px,4)}</span><span>${fmt(x.sz,0)}</span></div>`).join('');
  document.getElementById('bids').innerHTML = bids.map(x=>`<div class='row bid'><span>${fmt(x.px,4)}</span><span>${fmt(x.sz,0)}</span></div>`).join('');
}

function renderFeed(events){
  const feed = document.getElementById('feed');
  feed.innerHTML = events.map(e => `${e.ts||''}  ${e.event||''}  ${(e.payload&&e.payload.symbol)||''} ${(e.payload&&e.payload.reason)||''}`).join('<br/>');
  feed.scrollTop = feed.scrollHeight;
  events.slice(-6).forEach(e => {
    const id = `${e.ts}-${e.event}-${(e.payload&&e.payload.symbol)||''}`;
    if(seen.has(id)) return;
    seen.add(id);
    if(e.event && (e.event.includes('entry') || e.event.includes('exit'))){
      const kind = e.event.includes('entry') ? 'entry' : 'exit';
      addToast(kind, `${e.event.toUpperCase()} ${(e.payload&&e.payload.symbol)||''} ${(e.payload&&e.payload.reason)||''}`);
    }
    const fillSide = (((e.payload||{}).fill||{}).side || '').toUpperCase();
    if(fillSide){
      spawnTradeBurst(fillSide, fillSide);
    }
  });
}

function drawCurve(){
  const c = document.getElementById('pnlCanvas');
  const ctx = c.getContext('2d');
  const w = c.width = c.clientWidth; const h = c.height = c.clientHeight;
  ctx.clearRect(0,0,w,h);
  ctx.fillStyle = '#fffdf6'; ctx.fillRect(0,0,w,h);
  if(history.length < 2) return;
  const eq = history.map(x => x.equity || 0);
  const min = Math.min(...eq), max = Math.max(...eq);
  const span = Math.max(0.0001, max-min);

  ctx.strokeStyle = '#ebd7aa'; ctx.lineWidth = 1;
  for(let i=1;i<4;i++){ const y = (h/4)*i; ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(w,y); ctx.stroke(); }

  ctx.beginPath();
  eq.forEach((v,i)=>{
    const x = (i/(eq.length-1))*w;
    const y = h - ((v-min)/span)*(h-10) - 5;
    if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
  });
  const up = eq[eq.length-1] >= eq[0];
  ctx.strokeStyle = up ? '#2f9e62' : '#c24f4f';
  ctx.lineWidth = 2.2;
  ctx.stroke();
}

function initFlow(){
  const c = document.getElementById('flowCanvas');
  const ctx = c.getContext('2d');
  const stars = [];
  const nodes = [];
  function initScene(){
    const w = c.clientWidth || 1200;
    const h = c.clientHeight || 500;
    stars.length = 0;
    nodes.length = 0;
    for(let i=0;i<180;i++){
      stars.push({x:Math.random()*w,y:Math.random()*h,r:Math.random()*1.8+0.3,a:Math.random()*0.45+0.1,v:(Math.random()*0.3)+0.05});
    }
    for(let i=0;i<44;i++){
      nodes.push({x:w*(0.18+Math.random()*0.44),y:h*(0.12+Math.random()*0.76),vx:(Math.random()-0.5)*0.22,vy:(Math.random()-0.5)*0.22});
    }
  }
  initScene();
  window.addEventListener('resize', ()=>initScene());
  function spawn(){
    const score = ((latest.top_signals||[])[0]||{}).score || 0;
    const conf = ((latest.top_signals||[])[0]||{}).confidence || 0.5;
    const sign = score >= 0 ? 1 : -1;
    const speed = 2.6 + Math.abs(score)*320 + conf*3.2;
    flow.particles.push({x: 38, y: c.clientHeight/2 + (Math.random()-0.5)*36, vx: speed, vy: (Math.random()-0.5)*0.9 + sign*0.12, life: 1});
    if(flow.particles.length > 460) flow.particles.shift();
  }
  function tick(){
    const w = c.width = c.clientWidth; const h = c.height = c.clientHeight;
    ctx.fillStyle = 'rgba(255,250,240,0.20)';
    ctx.fillRect(0,0,w,h);

    // Star field
    for(const s of stars){
      s.y += s.v * 0.35;
      if(s.y > h+2) s.y = -2;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI*2);
      ctx.fillStyle = `rgba(130,100,70,${s.a})`;
      ctx.fill();
    }

    // Node mesh
    for(const n of nodes){
      n.x += n.vx; n.y += n.vy;
      if(n.x < w*0.1 || n.x > w*0.72) n.vx *= -1;
      if(n.y < h*0.08 || n.y > h*0.92) n.vy *= -1;
    }
    for(let i=0;i<nodes.length;i++){
      const a = nodes[i];
      for(let j=i+1;j<nodes.length;j++){
        const b = nodes[j];
        const dx = a.x-b.x, dy = a.y-b.y;
        const d2 = dx*dx+dy*dy;
        if(d2 < 4200){
          const alpha = Math.max(0, 0.18 - d2/4200*0.18);
          ctx.strokeStyle = `rgba(145,114,78,${alpha})`;
          ctx.lineWidth = 0.8;
          ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
        }
      }
    }
    for(const n of nodes){
      ctx.beginPath(); ctx.arc(n.x,n.y,1.7,0,Math.PI*2);
      ctx.fillStyle = 'rgba(110,82,54,0.75)';
      ctx.fill();
    }

    const score = ((latest.top_signals||[])[0]||{}).score || 0;
    const good = score >= 0;
    for(let i=0;i<8;i++) spawn();

    flow.particles.forEach(p=>{
      p.x += p.vx;
      p.y += p.vy;
      p.vy += (Math.random()-0.5)*0.03;
      p.life *= 0.998;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x - p.vx*3.4, p.y - p.vy*3.4);
      ctx.strokeStyle = good ? `rgba(47,158,98,${p.life*0.72})` : `rgba(194,79,79,${p.life*0.72})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    });
    flow.particles = flow.particles.filter(p => p.x < w+30 && p.y > -30 && p.y < h+30 && p.life > 0.04);

    // Trade bursts: explicit BUY/SELL floaters when real fills happen.
    flow.tradeBursts.forEach(t=>{
      t.x += t.vx;
      t.y += t.vy;
      t.vy *= 0.99;
      t.life *= 0.986;
      const isBuy = t.side === 'BUY' || t.side === 'COVER';
      ctx.fillStyle = isBuy ? `rgba(47,158,98,${t.life*0.95})` : `rgba(194,79,79,${t.life*0.95})`;
      ctx.beginPath();
      ctx.arc(t.x, t.y, 2.4, 0, Math.PI*2);
      ctx.fill();
      if(t.life > 0.65){
        ctx.font = 'bold 11px ui-monospace, monospace';
        ctx.fillStyle = isBuy ? `rgba(47,158,98,${t.life})` : `rgba(194,79,79,${t.life})`;
        ctx.fillText(t.label, t.x + 5, t.y - 4);
      }
    });
    flow.tradeBursts = flow.tradeBursts.filter(t => t.life > 0.08 && t.x < w+60 && t.y > -40 && t.y < h+40);

    // Directional beam toward orderbook side
    const beamX0 = 90;
    const beamY0 = h/2;
    const beamX1 = w*0.985;
    const beamSpread = 130 + Math.min(240, Math.abs(score)*1700);
    ctx.strokeStyle = good ? 'rgba(47,158,98,0.08)' : 'rgba(194,79,79,0.08)';
    for(let k=0;k<16;k++){
      ctx.beginPath();
      ctx.moveTo(beamX0, beamY0);
      const yy = beamY0 + (k/15 - 0.5)*beamSpread;
      ctx.lineTo(beamX1, yy);
      ctx.stroke();
    }

    const grad = ctx.createRadialGradient(38, h/2, 4, 38, h/2, 24);
    grad.addColorStop(0, 'rgba(245,183,66,0.95)');
    grad.addColorStop(1, 'rgba(245,183,66,0.08)');
    ctx.fillStyle = grad;
    ctx.beginPath(); ctx.arc(38, h/2, 24, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle = '#fff5dc';
    ctx.beginPath(); ctx.arc(38, h/2, 7, 0, Math.PI*2); ctx.fill();
    requestAnimationFrame(tick);
  }
  tick();
}

async function refresh(){
  let s, ev, hs, ob;
  try {
    const rs = await Promise.all([
      fetch('/api/state').then(r=>r.json()),
      fetch('/api/events?limit=60').then(r=>r.json()),
      fetch('/api/history?limit=220').then(r=>r.json()),
      fetch('/api/orderbook').then(r=>r.json()).catch(()=>({})),
      fetch('/api/wallet-balance').then(r=>r.json()).catch(()=>({ok:false})),
    ]);
    s = rs[0]; ev = rs[1]; hs = rs[2]; ob = rs[3]; liveBalance = rs[4];
    staleCount = 0;
    setAlert('');
  } catch (e){
    staleCount += 1;
    setAlert(`Data feed error (${staleCount}) - retrying...`);
    return;
  }
  latest = s; history = hs;
  const ms = document.getElementById('modeSelect');
  if(ms && s.mode && ms.value !== s.mode) ms.value = s.mode;
  document.getElementById('stamp').textContent = `tick ${s.tick ?? '-'} | ${s.mode ?? '-'} | ${s.trading_venue ?? '-'} | ${(s.engine_running ? 'RUNNING' : 'PAUSED')} | budget $${fmt(s.budget_usd ?? s.starting_cash ?? 0,2)} | entry $${fmt(s.fixed_trade_usd ?? 0,2)} | llm-exit ${s.llm_exit_control ? 'on' : 'off'}`;
  renderKPIs(s);
  renderSignals(s);
  renderPositions(s);
  renderOrderBook(ob, s);
  renderFeed(ev);
  drawCurve();
}

async function control(action){
  const r = await fetch(`/api/control/${action}`, {method: 'POST'}).then(x=>x.json()).catch(()=>({ok:false,error:'request_failed'}));
  if(r && r.ok){
    addToast('entry', `CONTROL: ${action.toUpperCase()} OK`);
    setAlert('');
  } else {
    addToast('exit', `CONTROL FAIL: ${action.toUpperCase()}`);
    setAlert(`Control action failed: ${action}`);
  }
  refresh();
}

async function switchMode(){
  const el = document.getElementById('modeSelect');
  const mode = (el && el.value) ? el.value : 'paper';
  if(mode === 'live'){
    const ok = confirm('Switch to LIVE mode and allow real execution?');
    if(!ok) return;
  }
  const r = await fetch('/api/control/mode', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode}),
  }).then(x=>x.json()).catch(()=>({ok:false}));
  if(r && r.ok){
    addToast('entry', `MODE SWITCHED: ${String(mode).toUpperCase()}`);
    setAlert('');
  } else {
    addToast('exit', `MODE SWITCH FAILED`);
    setAlert(`Mode switch failed: ${(r && r.error) ? r.error : 'unknown error'}`);
  }
  refresh();
}

initFlow();
setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>
"""


def read_json(path: Path, default: dict):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_events(path: Path, limit: int = 60):
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return rows[-limit:]


def create_service():
    cfg = load_config()
    bot = BotApp(cfg)
    app = Flask(__name__)
    history = deque(maxlen=int(os.getenv("HISTORY_MAX", "500")))
    engine_running = {"value": True}
    op_lock = threading.Lock()

    def _rpc_urls() -> list[str]:
        urls = []
        if cfg.solana_rpc_url:
            urls.append(cfg.solana_rpc_url)
        if cfg.solana_rpc_fallback_urls:
            urls.extend([u for u in cfg.solana_rpc_fallback_urls if u and u not in urls])
        return urls

    def _rpc_call(method: str, params: list[Any]) -> Optional[Dict[str, Any]]:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for u in _rpc_urls():
            try:
                r = requests.post(u, json=body, timeout=8)
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    continue
                return data.get("result")
            except Exception:
                continue
        return None

    def _get_sol_balance(address: str) -> float:
        result = _rpc_call("getBalance", [address, {"commitment": "confirmed"}]) or {}
        lamports = (result.get("value") or 0) if isinstance(result, dict) else 0
        try:
            return float(lamports) / 1_000_000_000
        except Exception:
            return 0.0

    def _get_token_balance(owner: str, mint: str) -> float:
        result = _rpc_call(
            "getTokenAccountsByOwner",
            [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        ) or {}
        total = 0.0
        for row in result.get("value", []) if isinstance(result, dict) else []:
            try:
                amount = row["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"]
                total += float(amount or 0.0)
            except Exception:
                continue
        return total

    def _sol_usd_price() -> float:
        try:
            r = requests.get(
                "https://price.jup.ag/v6/price",
                params={"ids": "So11111111111111111111111111111111111111112"},
                timeout=6,
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            row = data.get("SOL") or data.get("So11111111111111111111111111111111111111112") or {}
            px = float(row.get("price") or 0.0)
            if px > 0:
                return px
        except Exception:
            pass
        # Fallback to latest scanned SOL signal price if Jupiter price API is unavailable.
        try:
            for s in bot.last_signals:
                if str(getattr(s, "symbol", "")).upper() == "SOL":
                    p = float(getattr(s, "price", 0.0) or 0.0)
                    if p > 0:
                        return p
        except Exception:
            pass
        return 0.0

    def close_all_positions(reason: str = "manual_emergency_close"):
        prices = {s.symbol: s.price for s in bot.last_signals} if bot.last_signals else {}
        if not prices:
            try:
                signals = bot.scan_markets()
                prices = {s.symbol: s.price for s in signals}
            except Exception:
                prices = {}
        for symbol, pos in list(bot.portfolio.positions.items()):
            mark = prices.get(symbol, pos.entry_price)
            if pos.side == "SHORT":
                bot.execute_close_short(symbol, pos.qty, mark, reason)
            else:
                bot.execute_exit(symbol, pos.qty, mark, reason)
        bot.write_cycle_report(prices)

    def runner():
        while True:
            start = time.time()
            try:
                if engine_running["value"]:
                    with op_lock:
                        bot.step()
                        state = read_json(bot.logs_dir / "latest_cycle.json", {})
                        if state:
                            history.append(state)
            except Exception as e:
                bot.log(f"runtime error: {e}")
            elapsed = time.time() - start
            sleep_for = max(1, cfg.scan_interval - int(elapsed))
            time.sleep(sleep_for)

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    @app.get("/")
    def index():
        return render_template_string(HTML)

    @app.get("/api/state")
    def state():
        p = bot.logs_dir / "latest_cycle.json"
        data = read_json(p, {"mode": cfg.mode, "trading_venue": cfg.trading_venue})
        data["mode"] = bot.cfg.mode
        data["engine_running"] = engine_running["value"]
        data["kill_switch"] = bot.kill_switch
        return jsonify(data)

    @app.get("/api/events")
    def events():
        p = bot.logs_dir / "events.jsonl"
        limit = int(os.getenv("EVENTS_LIMIT", "60"))
        return jsonify(read_events(p, limit=limit))

    @app.get("/api/history")
    def api_history():
        limit = int(os.getenv("HISTORY_LIMIT", "220"))
        return jsonify(list(history)[-limit:])

    @app.get("/api/orderbook")
    def api_orderbook():
        market = os.getenv("DRIFT_MARKET_NAME", "SOL-PERP")
        depth = int(os.getenv("DRIFT_ORDERBOOK_DEPTH", "10"))
        base = os.getenv("DRIFT_DLOB_URL", "https://dlob.drift.trade").rstrip("/")
        url = f"{base}/l2"
        params = {
            "marketName": market,
            "depth": depth,
            "includeVamm": "true",
            "includeIndicative": "true",
        }
        timeout_sec = float(os.getenv("DRIFT_ORDERBOOK_TIMEOUT_SEC", "3.0"))
        try:
            resp = requests.get(url, params=params, timeout=timeout_sec)
            resp.raise_for_status()
            payload = resp.json()
            asks = payload.get("asks") or []
            bids = payload.get("bids") or []
            if not isinstance(asks, list) or not isinstance(bids, list):
                raise ValueError("invalid L2 response structure")
            return jsonify(
                {
                    "source": "drift_dlob",
                    "market": market,
                    "asks": asks,
                    "bids": bids,
                    "ts": int(time.time()),
                }
            )
        except Exception as e:
            return jsonify(
                {
                    "source": "fallback",
                    "market": market,
                    "asks": [],
                    "bids": [],
                    "error": str(e),
                    "ts": int(time.time()),
                }
            )

    @app.get("/api/wallet-balance")
    def api_wallet_balance():
        owner = cfg.solana_wallet_address
        if not owner:
            return jsonify({"ok": False, "error": "SOLANA_WALLET_ADDRESS not configured"})
        try:
            sol = _get_sol_balance(owner)
            usdc = _get_token_balance(owner, cfg.solana_quote_mint)
            sol_px = _sol_usd_price()
            total_usd = usdc + (sol * sol_px)
            return jsonify(
                {
                    "ok": True,
                    "wallet_address": owner,
                    "sol_balance": sol,
                    "usdc_balance": usdc,
                    "sol_usd_price": sol_px,
                    "total_usd_estimate": total_usd,
                    "ts": int(time.time()),
                }
            )
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "ts": int(time.time())})

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True})

    @app.post("/api/control/start")
    def api_control_start():
        with op_lock:
            engine_running["value"] = True
            bot.kill_switch = False
        return jsonify({"ok": True, "engine_running": True, "kill_switch": bot.kill_switch})

    @app.post("/api/control/stop")
    def api_control_stop():
        with op_lock:
            engine_running["value"] = False
        return jsonify({"ok": True, "engine_running": False, "kill_switch": bot.kill_switch})

    @app.post("/api/control/emergency-close")
    def api_control_emergency_close():
        with op_lock:
            engine_running["value"] = False
            bot.kill_switch = True
            close_all_positions("manual_emergency_close")
        return jsonify({"ok": True, "engine_running": False, "kill_switch": True})

    @app.post("/api/control/mode")
    def api_control_mode():
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get("mode", "")).strip().lower()
        if mode not in {"paper", "live"}:
            return jsonify({"ok": False, "error": "mode must be paper or live"}), 400
        with op_lock:
            try:
                bot.switch_mode(mode)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            return jsonify({"ok": True, "mode": bot.cfg.mode})

    return app


if __name__ == "__main__":
    app = create_service()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
