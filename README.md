# AI Trader OS (Paper/Live Parity)

Autonomous crypto trading runtime with one shared accounting engine for both paper and live fills, so win/loss metrics are computed consistently.

## What changed

- Paper and live now pass through the same `PortfolioEngine.apply_fill(...)` logic.
- PnL, win rate, fees, and trade counts use a single formula regardless of mode.
- Paper fill model includes configurable slippage and fees.
- Live mode uses Binance spot market orders and normalizes exchange fills into the same `TradeFill` format.

## Core parity model

Both modes write fills as:

- `symbol`
- `side` (`BUY`/`SELL`)
- `qty`
- `avg_price`
- `fee_usd`
- `order_id`
- `source`

Then accounting computes:

- Entry basis: `qty * entry_price + entry_fee`
- Exit proceeds: `qty * exit_price - exit_fee`
- Realized PnL: `exit_proceeds - entry_basis`

## Quick start (paper)

```bash
cd /workspaces/AI
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python src/bot.py
```

## Live mode guardrails

Set all of these first:

```env
BOT_MODE=live
LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK
EXCHANGE_API_KEY=...
EXCHANGE_API_SECRET=...
```

Without the exact ACK phrase, live mode is blocked.

## OpenClaw hook

```bash
python src/openclaw_task.py
```

Reads `logs/latest_cycle.json` and outputs compact JSON for orchestration.

## Deploy on Render (worker)

This repo includes [render.yaml](/workspaces/AI/render.yaml) for Blueprint deployment.

1. Push this repo to GitHub.
2. In Render, choose `New +` -> `Blueprint`.
3. Select the repo and deploy.
4. Service type is `worker` and starts with `./scripts/start_worker.sh`.

Check status/log output:

```bash
python src/openclaw_task.py
python src/render_health.py
```

Live mode is intentionally blocked unless you set:

```env
BOT_MODE=live
LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK
EXCHANGE_API_KEY=...
EXCHANGE_API_SECRET=...
```

## Runtime outputs

- `logs/latest_cycle.json`
- `logs/events.jsonl`

## Notes

- Keep paper mode until you have enough forward-test data.
- This is experimental software and not financial advice.
