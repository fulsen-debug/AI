import json
import os
import threading
import time
from collections import deque
from pathlib import Path

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
      --bg: #060a11;
      --card: #0d1422;
      --line: #1a2a42;
      --soft: #0a1220;
      --text: #d9e8ff;
      --muted: #86a3c8;
      --cyan: #59d7ff;
      --green: #3af0ad;
      --red: #ff5e7a;
      --amber: #ffbc54;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--text);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      background: radial-gradient(circle at 15% 12%, #11203a 0%, var(--bg) 55%);
    }
    .wrap { max-width: 1320px; margin: 14px auto; padding: 0 12px; }
    .head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; gap: 10px; }
    .head-right { display:flex; align-items:center; gap:8px; }
    .title { font-size: 24px; color: var(--cyan); letter-spacing: .8px; }
    .badge { border:1px solid var(--line); background: var(--soft); border-radius:10px; padding:8px 10px; color: var(--muted); }
    .ctrl-btn {
      border:1px solid var(--line);
      background:#081120;
      color:var(--text);
      border-radius:10px;
      padding:8px 10px;
      cursor:pointer;
      font-family: inherit;
      font-size: 12px;
    }
    .ctrl-btn:hover { filter: brightness(1.12); }
    .ctrl-btn.stop { border-color:#6b2a3d; color:#ff9cb0; }
    .ctrl-btn.start { border-color:#175f49; color:#87f5cf; }
    .ctrl-btn.emergency { border-color:#7c1e2a; color:#ff7f95; background:#1a0a0f; }

    .kpi { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
    .pill { border:1px solid var(--line); background:#081120; border-radius:10px; padding:8px 10px; min-width: 140px; }
    .pill .k { color: var(--muted); font-size:11px; }
    .pill .v { color: var(--cyan); font-size:15px; margin-top:2px; }
    .hero {
      border:1px solid #214068;
      background: linear-gradient(120deg, #0b1a2f, #0a1323);
      border-radius:12px;
      padding:14px;
      margin-bottom:10px;
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:10px;
    }
    .hero .label { color: var(--muted); font-size:12px; }
    .hero .val { color: #b7f5ff; font-size:34px; font-weight:700; letter-spacing:.5px; }
    .hero .sub { color: var(--muted); font-size:12px; margin-top:4px; }
    .hero .mode {
      border:1px solid #325277;
      border-radius:10px;
      padding:8px 10px;
      font-size:12px;
      color:#d7eeff;
      background:#0a1628;
    }
    .alert {
      border:1px solid #6b2a3d;
      background:#1c0d14;
      color:#ffb7c6;
      border-radius:10px;
      padding:8px 10px;
      margin-bottom:10px;
      display:none;
    }

    .layout { display:grid; grid-template-columns: 1.25fr .75fr; gap:10px; }
    .card { border:1px solid var(--line); border-radius:12px; background: linear-gradient(180deg, #0d1422, #0b111d); padding:10px; }
    .card h3 { margin:2px 0 8px; color: var(--amber); font-size:14px; letter-spacing:.6px; }

    .chart-wrap { position:relative; height:260px; border:1px solid #16243b; border-radius:10px; background:#050c18; overflow:hidden; }
    .signal-chart-wrap { height:420px; }
    canvas { display:block; width:100%; height:100%; }

    .right-grid { display:grid; grid-template-rows: 1fr 1fr; gap:10px; }

    table { width:100%; border-collapse:collapse; font-size:12px; }
    th, td { padding:7px 6px; border-bottom:1px solid #17253a; text-align:left; }
    th { color: var(--muted); font-weight:600; }

    .depth { display:grid; grid-template-columns: 1fr 1fr; gap:8px; }
    .book { border:1px solid #16243b; border-radius:10px; padding:6px; background:#08111d; }
    .row { position:relative; display:flex; justify-content:space-between; font-size:12px; padding:4px 6px; margin:2px 0; overflow:hidden; }
    .row span { position:relative; z-index:1; }
    .row::before { content:''; position:absolute; inset:0; opacity:.28; }
    .ask::before { background: linear-gradient(90deg, rgba(255,94,122,.65), transparent); }
    .bid::before { background: linear-gradient(90deg, rgba(58,240,173,.65), transparent); }

    .feed { height:220px; overflow:auto; border:1px solid #16243b; border-radius:10px; background:#07101b; padding:8px; font-size:12px; }

    .floaters { position: fixed; top: 14px; right: 14px; z-index: 20; display:flex; flex-direction:column; gap:8px; }
    .toast { min-width:260px; border-radius:10px; border:1px solid var(--line); background:#0c1728; padding:10px 12px; animation: pop .24s ease-out; }
    .toast.entry { border-color:#175f49; box-shadow:0 0 0 1px #103b2e inset; }
    .toast.exit { border-color:#6b2a3d; box-shadow:0 0 0 1px #3a1621 inset; }
    @keyframes pop { from { opacity:0; transform:translateY(-6px);} to { opacity:1; transform:translateY(0);} }

    .g { color: var(--green); }
    .r { color: var(--red); }

    @media (max-width: 960px) {
      .layout { grid-template-columns: 1fr; }
      .right-grid { grid-template-rows: auto auto; }
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
const flow = { particles: [] };
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
  document.getElementById('heroBalance').textContent = `$${fmt(eq,2)}`;
  document.getElementById('heroSub').textContent = `budget $${fmt(s.budget_usd ?? s.starting_cash,2)} | total pnl ${(totalPnl>=0?'+':'')}$${fmt(totalPnl,2)} | roi ${roi}`;
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
  });
}

function drawCurve(){
  const c = document.getElementById('pnlCanvas');
  const ctx = c.getContext('2d');
  const w = c.width = c.clientWidth; const h = c.height = c.clientHeight;
  ctx.clearRect(0,0,w,h);
  ctx.fillStyle = '#050c18'; ctx.fillRect(0,0,w,h);
  if(history.length < 2) return;
  const eq = history.map(x => x.equity || 0);
  const min = Math.min(...eq), max = Math.max(...eq);
  const span = Math.max(0.0001, max-min);

  ctx.strokeStyle = '#1f2c43'; ctx.lineWidth = 1;
  for(let i=1;i<4;i++){ const y = (h/4)*i; ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(w,y); ctx.stroke(); }

  ctx.beginPath();
  eq.forEach((v,i)=>{
    const x = (i/(eq.length-1))*w;
    const y = h - ((v-min)/span)*(h-10) - 5;
    if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
  });
  const up = eq[eq.length-1] >= eq[0];
  ctx.strokeStyle = up ? '#3af0ad' : '#ff5e7a';
  ctx.lineWidth = 2.2;
  ctx.stroke();
}

function initFlow(){
  const c = document.getElementById('flowCanvas');
  const ctx = c.getContext('2d');
  function spawn(){
    const score = ((latest.top_signals||[])[0]||{}).score || 0;
    const conf = ((latest.top_signals||[])[0]||{}).confidence || 0.5;
    const sign = score >= 0 ? 1 : -1;
    const speed = 1.1 + Math.abs(score)*150 + conf*1.2;
    flow.particles.push({x: 30, y: c.clientHeight/2 + (Math.random()-0.5)*20, vx: speed, vy: (Math.random()-0.5)*0.6 + sign*0.08, life: 1});
    if(flow.particles.length > 220) flow.particles.shift();
  }
  function tick(){
    const w = c.width = c.clientWidth; const h = c.height = c.clientHeight;
    ctx.fillStyle = 'rgba(5,12,24,0.22)';
    ctx.fillRect(0,0,w,h);

    const score = ((latest.top_signals||[])[0]||{}).score || 0;
    const good = score >= 0;
    for(let i=0;i<3;i++) spawn();

    flow.particles.forEach(p=>{
      p.x += p.vx;
      p.y += p.vy;
      p.vy += (Math.random()-0.5)*0.02;
      p.life *= 0.995;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x - p.vx*2.6, p.y - p.vy*2.6);
      ctx.strokeStyle = good ? `rgba(58,240,173,${p.life*0.7})` : `rgba(255,94,122,${p.life*0.7})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    });
    flow.particles = flow.particles.filter(p => p.x < w+20 && p.y > -20 && p.y < h+20 && p.life > 0.07);

    ctx.fillStyle = '#d0dbff';
    ctx.beginPath(); ctx.arc(28, h/2, 7, 0, Math.PI*2); ctx.fill();
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
    ]);
    s = rs[0]; ev = rs[1]; hs = rs[2]; ob = rs[3];
    staleCount = 0;
    setAlert('');
  } catch (e){
    staleCount += 1;
    setAlert(`Data feed error (${staleCount}) - retrying...`);
    return;
  }
  latest = s; history = hs;
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

    return app


if __name__ == "__main__":
    app = create_service()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
