import json
import os
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template_string

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
      --bg: #070b12;
      --card: #0f1724;
      --line: #1f2c43;
      --green: #3cf0a8;
      --red: #ff5f7a;
      --cyan: #57d8ff;
      --text: #d8e6ff;
      --muted: #89a0c6;
    }
    body { margin:0; font-family: ui-monospace, Menlo, Consolas, monospace; background: radial-gradient(circle at 15% 20%, #0c1424 0%, var(--bg) 60%); color: var(--text); }
    .wrap { max-width: 1200px; margin: 20px auto; padding: 0 14px; }
    .head { display:flex; justify-content:space-between; align-items:center; margin-bottom: 10px; }
    .title { font-size: 22px; color: var(--cyan); letter-spacing: .5px; }
    .grid { display:grid; grid-template-columns: 1.3fr 1fr; gap: 12px; }
    .card { background: linear-gradient(180deg, #0f1724, #0b1220); border:1px solid var(--line); border-radius:12px; padding:12px; box-shadow: 0 0 0 1px #0a1020 inset; }
    .kpi { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
    .pill { padding:8px 10px; border-radius:10px; border:1px solid var(--line); background:#0a1220; }
    .pill b { color: var(--cyan); }
    table { width:100%; border-collapse: collapse; font-size: 12px; }
    th, td { border-bottom: 1px solid #162238; padding: 7px 6px; text-align:left; }
    th { color: var(--muted); font-weight: 600; }
    .g { color: var(--green); }
    .r { color: var(--red); }
    .logs { height: 210px; overflow:auto; background:#080f1a; border:1px solid #162238; border-radius:10px; padding:8px; font-size:12px; }
    .floaters { position: fixed; top: 14px; right: 14px; display: flex; flex-direction: column; gap:8px; z-index: 10; }
    .toast { min-width: 260px; padding:10px 12px; border-radius: 10px; border:1px solid #1f2c43; background:#0e1828; animation: pop .25s ease-out; }
    .toast.buy { border-color:#1e6d54; box-shadow:0 0 0 1px #123e31 inset; }
    .toast.sell { border-color:#7a3145; box-shadow:0 0 0 1px #3d1a25 inset; }
    @keyframes pop { from { transform: translateY(-6px); opacity:0; } to { transform: translateY(0); opacity:1; } }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class=\"floaters\" id=\"floaters\"></div>
  <div class=\"wrap\">
    <div class=\"head\">
      <div class=\"title\">AIG Live Dashboard</div>
      <div id=\"stamp\" class=\"pill\">loading...</div>
    </div>

    <div class=\"kpi\" id=\"kpi\"></div>

    <div class=\"grid\">
      <div class=\"card\">
        <h3>Signals</h3>
        <table>
          <thead><tr><th>Symbol</th><th>Price</th><th>Score</th><th>Conf</th><th>Vol</th></tr></thead>
          <tbody id=\"signals\"></tbody>
        </table>
      </div>
      <div class=\"card\">
        <h3>Open Positions</h3>
        <table>
          <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Mark</th></tr></thead>
          <tbody id=\"positions\"></tbody>
        </table>
      </div>
    </div>

    <div class=\"card\" style=\"margin-top:12px\">
      <h3>Execution Feed</h3>
      <div class=\"logs\" id=\"logs\"></div>
    </div>
  </div>

<script>
let seen = new Set();
function fmt(n){ return (typeof n === 'number') ? n.toLocaleString(undefined,{maximumFractionDigits:6}) : '-'; }
function addToast(type, text){
  const box = document.getElementById('floaters');
  const d = document.createElement('div');
  d.className = 'toast ' + (type || 'buy');
  d.textContent = text;
  box.prepend(d);
  setTimeout(()=>d.remove(), 7000);
}
async function refresh(){
  const s = await fetch('/api/state').then(r=>r.json());
  const ev = await fetch('/api/events?limit=30').then(r=>r.json());

  document.getElementById('stamp').textContent = `tick ${s.tick ?? '-'} | ${s.mode ?? '-'} | ${s.trading_venue ?? '-'}`;

  const kpi = document.getElementById('kpi');
  kpi.innerHTML = `
    <div class=\"pill\"><b>Equity</b> $${fmt(s.equity)}</div>
    <div class=\"pill\"><b>Cash</b> $${fmt(s.cash)}</div>
    <div class=\"pill\"><b>Realized</b> $${fmt(s.realized_pnl)}</div>
    <div class=\"pill\"><b>Fees</b> $${fmt(s.total_fees)}</div>
    <div class=\"pill\"><b>Win Rate</b> ${((s.win_rate||0)*100).toFixed(1)}%</div>
    <div class=\"pill\"><b>Status</b> ${s.kill_switch ? 'HALTED' : 'ACTIVE'}</div>
  `;

  const sg = document.getElementById('signals');
  sg.innerHTML = (s.top_signals || []).map(x => {
    const cls = (x.score || 0) >= 0 ? 'g' : 'r';
    return `<tr><td>${x.symbol}</td><td>${fmt(x.price)}</td><td class='${cls}'>${((x.score||0)*100).toFixed(2)}%</td><td>${fmt(x.confidence)}</td><td>${fmt(x.quote_volume)}</td></tr>`;
  }).join('') || '<tr><td colspan=5>-</td></tr>';

  const ps = document.getElementById('positions');
  ps.innerHTML = (s.open_positions || []).map(x => {
    return `<tr><td>${x.symbol}</td><td>${x.side||'-'}</td><td>${fmt(x.qty)}</td><td>${fmt(x.entry_price)}</td><td>${fmt(x.mark_price)}</td></tr>`;
  }).join('') || '<tr><td colspan=5>-</td></tr>';

  const logs = document.getElementById('logs');
  logs.innerHTML = ev.map(x => `${x.ts || ''}  ${x.event || ''}  ${(x.payload && x.payload.symbol) || ''} ${(x.payload && x.payload.reason) || ''}`).join('<br/>');
  logs.scrollTop = logs.scrollHeight;

  ev.slice(-5).forEach(e => {
    const id = `${e.ts}-${e.event}-${(e.payload&&e.payload.symbol)||''}`;
    if (seen.has(id)) return;
    seen.add(id);
    if (e.event && (e.event.includes('entry') || e.event.includes('exit'))){
      const t = `${e.event.toUpperCase()} ${(e.payload&&e.payload.symbol)||''} ${(e.payload&&e.payload.reason)||''}`;
      const kind = e.event.includes('entry') ? 'buy' : 'sell';
      addToast(kind, t);
    }
  });
}
setInterval(refresh, 2000); refresh();
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


def read_events(path: Path, limit: int = 30):
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

    def runner():
        while True:
            start = time.time()
            try:
                bot.step()
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
        return jsonify(read_json(p, {"mode": cfg.mode, "trading_venue": cfg.trading_venue}))

    @app.get("/api/events")
    def events():
        p = bot.logs_dir / "events.jsonl"
        limit = int(os.getenv("EVENTS_LIMIT", "30"))
        return jsonify(read_events(p, limit=limit))

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    app = create_service()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
