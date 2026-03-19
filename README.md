# AIG Trader OS (Drift + Solana / Paper-Live Parity)

Autonomous trader with one shared accounting engine for both paper and live fills.
Now includes an agentic LLM brain module for per-cycle LONG/SHORT/HOLD decisions.

## Current live path

- Recommended venue for true long/short: `drift_gateway`
- Spot fallback venue: `solana_jupiter`
- Market discovery: DexScreener (Solana pairs)
- Execution:
  - `drift_gateway`: perp orders (long + short)
  - `solana_jupiter`: swaps (long + flat)
- Shared PnL engine for paper/live parity
- Directional behavior:
  - Live on Drift gateway: LONG / SHORT
  - Live on Jupiter spot: LONG / FLAT
  - Paper mode: LONG / SHORT

## Quick start

```bash
cd /workspaces/AI
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python src/bot.py
```

## Live mode (Solana)

```env
BOT_MODE=live
TRADING_VENUE=solana_jupiter
LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK
SOLANA_RPC_URL=...
JUPITER_API_KEY=...
SOLANA_WALLET_ADDRESS=...
SOLANA_WALLET_PRIVATE_KEY=...
```

## Live mode (Drift long/short)

```env
BOT_MODE=live
TRADING_VENUE=drift_gateway
LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK
DRIFT_GATEWAY_URL=...
DRIFT_API_KEY=...
DRIFT_MARKET_SYMBOL=SOL
DRIFT_MARKET_INDEX=0
```

## Agent brain config

```env
BRAIN_ENABLED=1
BRAIN_PROVIDER=local
BRAIN_MODEL=qwen2.5:7b-instruct
LOCAL_BRAIN_URL=http://127.0.0.1:11434
```

Fallback behavior:
- If the LLM is unavailable or returns invalid JSON, the bot falls back to heuristic decisions and logs that fallback in `logs/brain_memory.jsonl`.

## OpenClaw hook

```bash
python src/openclaw_task.py
```

## Render deploy

- Blueprint file: `render.yaml`
- Worker start script: `scripts/start_worker.sh`

## Runtime outputs

- `logs/latest_cycle.json`
- `logs/events.jsonl`

## Notes

- Keep paper mode until forward tests are stable.
- This is experimental software and not financial advice.


## Local brain (free)

```env
BRAIN_ENABLED=1
BRAIN_PROVIDER=local
LOCAL_BRAIN_URL=http://127.0.0.1:11434
LOCAL_BRAIN_MODEL=qwen2.5:7b-instruct
```

Works with Ollama (`/api/chat`) or OpenAI-compatible local endpoints (`/v1/chat/completions`).
