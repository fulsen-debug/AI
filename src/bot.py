import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


BINANCE_BASE = "https://api.binance.com"
DEXSCREENER_BASE = "https://api.dexscreener.com"
JUPITER_BASE = "https://lite-api.jup.ag"

STABLE_KEYWORDS = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDP", "EUR", "GBP")
EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

DEFAULT_SOLANA_MINTS = [
    "So11111111111111111111111111111111111111112",  # SOL
]

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry_price: float
    entry_fee_usd: float
    margin_usd: float
    opened_at: float


@dataclass
class MarketSignal:
    symbol: str
    price: float
    r_5m: float
    r_30m: float
    r_4h: float
    score: float
    confidence: float
    quote_volume: float


@dataclass
class TradeFill:
    symbol: str
    side: str
    qty: float
    avg_price: float
    fee_usd: float
    ts: float
    order_id: str
    source: str


@dataclass
class Config:
    app_name: str = "AIG Trader OS"
    mode: str = "paper"
    trading_venue: str = "solana_jupiter"
    budget_usd: float = 50.0
    scan_interval: int = 20
    risk_per_trade: float = 0.15
    max_positions: int = 4
    buy_threshold: float = 0.008
    sell_threshold: float = -0.006
    top_n_markets: int = 25
    candidates_per_cycle: int = 6
    min_quote_volume_usd: float = 500_000.0
    stop_loss_pct: float = 0.025
    take_profit_pct: float = 0.045
    max_position_age_minutes: int = 240
    max_daily_drawdown_pct: float = 0.08
    cooldown_minutes: int = 20
    quote_asset: str = "USDC"
    paper_fee_bps: float = 10.0
    paper_slippage_bps: float = 5.0
    live_trading_ack: str = ""

    exchange_api_key: str = ""
    exchange_api_secret: str = ""

    solana_rpc_url: str = ""
    solana_rpc_fallback_urls: List[str] = None
    helius_api_key: str = ""
    jupiter_api_key: str = ""
    solana_wallet_address: str = ""
    solana_wallet_private_key: str = ""
    solana_token_mints: List[str] = None
    solana_quote_mint: str = USDC_MINT
    jupiter_slippage_bps: int = 50
    allow_paper_shorts: bool = True
    sol_only_mode: bool = True
    drift_gateway_url: str = ""
    drift_api_key: str = ""
    drift_market_symbol: str = "SOL"
    drift_market_index: int = 0
    drift_taker_fee_bps: float = 10.0
    drift_estimated_tx_fee_usd: float = 0.02
    brain_enabled: bool = True
    brain_provider: str = "openai"
    brain_model: str = "gpt-4o-mini"
    brain_temperature: float = 0.2
    brain_max_memory_items: int = 25
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    local_brain_url: str = "http://127.0.0.1:11434"
    local_brain_model: str = "qwen2.5:7b-instruct"
    local_brain_api_key: str = ""


class Strategy:
    @staticmethod
    def from_returns(symbol: str, price: float, quote_volume: float, r_5m: float, r_30m: float, r_4h: float) -> MarketSignal:
        score = 0.55 * r_5m + 0.30 * r_30m + 0.15 * r_4h
        confidence = min(0.95, max(0.50, 0.50 + abs(score) * 55))
        return MarketSignal(
            symbol=symbol,
            price=price,
            r_5m=r_5m,
            r_30m=r_30m,
            r_4h=r_4h,
            score=score,
            confidence=confidence,
            quote_volume=quote_volume,
        )


class AgentBrain:
    def __init__(self, cfg: Config, logs_dir: Path):
        self.cfg = cfg
        self.logs_dir = logs_dir
        self.memory_file = logs_dir / "brain_memory.jsonl"

    def _append_memory(self, row: dict):
        with open(self.memory_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def _load_recent_memory(self) -> List[dict]:
        if not self.memory_file.exists():
            return []
        rows: List[dict] = []
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return rows[-self.cfg.brain_max_memory_items :]

    @staticmethod
    def _extract_json(text: str) -> Any:
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(text[first : last + 1])
            except Exception:
                return None
        return None

    def _call_openai(self, prompt: str) -> Optional[dict]:
        if not self.cfg.openai_api_key:
            return None
        body = {
            "model": self.cfg.brain_model,
            "temperature": self.cfg.brain_temperature,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the trading brain for AIG. Output strict JSON only. "
                        "Conservative risk-first behavior."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self.cfg.openai_api_key}", "Content-Type": "application/json"}
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        parsed = self._extract_json(content)
        return parsed if isinstance(parsed, dict) else None

    def _call_anthropic(self, prompt: str) -> Optional[dict]:
        if not self.cfg.anthropic_api_key:
            return None
        body = {
            "model": self.cfg.brain_model,
            "max_tokens": 1200,
            "temperature": self.cfg.brain_temperature,
            "system": "You are the trading brain for AIG. Output strict JSON only.",
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.cfg.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        blocks = data.get("content", [])
        text = ""
        for b in blocks:
            if b.get("type") == "text":
                text += b.get("text", "")
        parsed = self._extract_json(text)
        return parsed if isinstance(parsed, dict) else None

    def _call_local(self, prompt: str) -> Optional[dict]:
        base = self.cfg.local_brain_url.rstrip("/")
        model = self.cfg.local_brain_model
        headers = {"Content-Type": "application/json"}
        if self.cfg.local_brain_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.local_brain_api_key}"

        # 1) Try OpenAI-compatible endpoint (many local gateways expose this)
        try:
            body = {
                "model": model,
                "temperature": self.cfg.brain_temperature,
                "messages": [
                    {"role": "system", "content": "Output strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
            }
            r = requests.post(f"{base}/v1/chat/completions", headers=headers, json=body, timeout=45)
            if r.status_code < 400:
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                parsed = self._extract_json(content)
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            pass

        # 2) Try Ollama native endpoint
        try:
            body = {
                "model": model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": "Output strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "options": {"temperature": self.cfg.brain_temperature},
            }
            r = requests.post(f"{base}/api/chat", headers=headers, json=body, timeout=45)
            r.raise_for_status()
            data = r.json()
            content = (data.get("message") or {}).get("content", "")
            parsed = self._extract_json(content)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _heuristic_decisions(self, signals: List[MarketSignal], has_short: bool) -> Dict[str, dict]:
        decisions: Dict[str, dict] = {}
        for s in signals:
            if s.score >= self.cfg.buy_threshold and s.confidence >= 0.60:
                decisions[s.symbol] = {
                    "symbol": s.symbol,
                    "action": "LONG",
                    "size_fraction": min(0.20, self.cfg.risk_per_trade),
                    "confidence": s.confidence,
                    "reason": "momentum_up",
                }
            elif s.score <= self.cfg.sell_threshold and s.confidence >= 0.55 and has_short:
                decisions[s.symbol] = {
                    "symbol": s.symbol,
                    "action": "SHORT",
                    "size_fraction": min(0.20, self.cfg.risk_per_trade),
                    "confidence": s.confidence,
                    "reason": "momentum_down",
                }
            else:
                decisions[s.symbol] = {
                    "symbol": s.symbol,
                    "action": "HOLD",
                    "size_fraction": 0.0,
                    "confidence": s.confidence,
                    "reason": "no_edge",
                }
        return decisions

    def decide(self, signals: List[MarketSignal], positions: Dict[str, Position], portfolio: "PortfolioEngine") -> Dict[str, dict]:
        has_short = self.cfg.trading_venue in {"drift_gateway"} or self.cfg.mode == "paper"
        if not self.cfg.brain_enabled:
            return self._heuristic_decisions(signals, has_short)

        snapshot = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "venue": self.cfg.trading_venue,
            "mode": self.cfg.mode,
            "has_short": has_short,
            "risk": {
                "risk_per_trade": self.cfg.risk_per_trade,
                "max_positions": self.cfg.max_positions,
                "stop_loss_pct": self.cfg.stop_loss_pct,
                "take_profit_pct": self.cfg.take_profit_pct,
            },
            "portfolio": {
                "cash": portfolio.cash,
                "positions": [
                    {
                        "symbol": p.symbol,
                        "side": p.side,
                        "qty": p.qty,
                        "entry_price": p.entry_price,
                        "margin_usd": p.margin_usd,
                    }
                    for p in positions.values()
                ],
                "wins": portfolio.wins,
                "losses": portfolio.losses,
                "realized_pnl": portfolio.realized_pnl,
            },
            "signals": [
                {
                    "symbol": s.symbol,
                    "price": s.price,
                    "score": s.score,
                    "confidence": s.confidence,
                    "r_5m": s.r_5m,
                    "r_30m": s.r_30m,
                    "r_4h": s.r_4h,
                    "quote_volume": s.quote_volume,
                }
                for s in signals
            ],
            "recent_memory": self._load_recent_memory(),
        }

        prompt = (
            "Return JSON object with key 'decisions' containing array of per-symbol actions. "
            "Actions allowed: LONG, SHORT, EXIT_LONG, EXIT_SHORT, HOLD. "
            "Include size_fraction between 0 and 0.25. Keep risk conservative.\n"
            f"{json.dumps(snapshot)}"
        )

        parsed: Optional[dict] = None
        try:
            if self.cfg.brain_provider == "anthropic":
                parsed = self._call_anthropic(prompt)
            elif self.cfg.brain_provider == "local":
                parsed = self._call_local(prompt)
            else:
                parsed = self._call_openai(prompt)
        except Exception:
            parsed = None

        if not parsed or "decisions" not in parsed or not isinstance(parsed["decisions"], list):
            decisions = self._heuristic_decisions(signals, has_short)
            self._append_memory({"type": "brain_fallback", "at": snapshot["ts"], "reason": "invalid_or_unavailable_llm"})
            return decisions

        decisions: Dict[str, dict] = {}
        for row in parsed["decisions"]:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).strip()
            action = str(row.get("action", "HOLD")).upper()
            if symbol == "" or action not in {"LONG", "SHORT", "EXIT_LONG", "EXIT_SHORT", "HOLD"}:
                continue
            size_fraction = row.get("size_fraction", self.cfg.risk_per_trade)
            try:
                size_fraction = float(size_fraction)
            except Exception:
                size_fraction = self.cfg.risk_per_trade
            size_fraction = max(0.0, min(0.25, size_fraction))
            confidence = row.get("confidence", 0.5)
            try:
                confidence = float(confidence)
            except Exception:
                confidence = 0.5
            decisions[symbol] = {
                "symbol": symbol,
                "action": action,
                "size_fraction": size_fraction,
                "confidence": max(0.0, min(1.0, confidence)),
                "reason": str(row.get("reason", "llm_decision"))[:200],
            }

        if not decisions:
            return self._heuristic_decisions(signals, has_short)

        self._append_memory({"type": "brain_decision", "at": snapshot["ts"], "decisions": list(decisions.values())})
        return decisions


class PortfolioEngine:
    def __init__(self, starting_cash: float):
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.positions: Dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.peak_equity = starting_cash
        self.cooldowns: Dict[str, float] = {}
        self.total_fees = 0.0
        self.closed_trades = 0
        self.wins = 0
        self.losses = 0

    def equity(self, prices: Dict[str, float]) -> float:
        mark = 0.0
        for pos in self.positions.values():
            px = prices.get(pos.symbol, pos.entry_price)
            if pos.side == "LONG":
                mark += pos.qty * px
            else:
                mark += pos.margin_usd + ((pos.entry_price - px) * pos.qty)
        return self.cash + mark

    def update_peak(self, equity: float):
        if equity > self.peak_equity:
            self.peak_equity = equity

    def in_cooldown(self, symbol: str) -> bool:
        until = self.cooldowns.get(symbol)
        return bool(until and time.time() < until)

    def set_cooldown(self, symbol: str, minutes: int):
        self.cooldowns[symbol] = time.time() + (minutes * 60)

    def apply_fill(self, fill: TradeFill) -> Tuple[bool, str, float]:
        self.total_fees += fill.fee_usd
        if fill.side == "BUY":
            pos = self.positions.get(fill.symbol)
            if pos and pos.side == "SHORT":
                gross = (pos.entry_price - fill.avg_price) * pos.qty
                pnl = gross - pos.entry_fee_usd - fill.fee_usd
                self.cash += pos.margin_usd + gross - fill.fee_usd
                self.realized_pnl += pnl
                self.closed_trades += 1
                if pnl >= 0:
                    self.wins += 1
                else:
                    self.losses += 1
                del self.positions[fill.symbol]
                return True, (
                    f"CLOSE SHORT {fill.symbol} qty={fill.qty:.6f} @ {fill.avg_price:.6f} "
                    f"fee=${fill.fee_usd:.4f} pnl={pnl:+.2f} [{fill.source}]"
                ), pnl

            cost = fill.qty * fill.avg_price + fill.fee_usd
            if cost > self.cash:
                return False, f"reject BUY {fill.symbol}: insufficient cash", 0.0
            if fill.symbol in self.positions:
                return False, f"reject BUY {fill.symbol}: existing position", 0.0

            self.cash -= cost
            self.positions[fill.symbol] = Position(
                symbol=fill.symbol,
                side="LONG",
                qty=fill.qty,
                entry_price=fill.avg_price,
                entry_fee_usd=fill.fee_usd,
                margin_usd=0.0,
                opened_at=fill.ts,
            )
            return True, (
                f"BUY {fill.symbol} qty={fill.qty:.6f} @ {fill.avg_price:.6f} "
                f"fee=${fill.fee_usd:.4f} [{fill.source}]"
            ), 0.0

        if fill.side == "SHORT":
            if fill.symbol in self.positions:
                return False, f"reject SHORT {fill.symbol}: existing position", 0.0
            margin = fill.qty * fill.avg_price
            total_lock = margin + fill.fee_usd
            if total_lock > self.cash:
                return False, f"reject SHORT {fill.symbol}: insufficient cash", 0.0
            self.cash -= total_lock
            self.positions[fill.symbol] = Position(
                symbol=fill.symbol,
                side="SHORT",
                qty=fill.qty,
                entry_price=fill.avg_price,
                entry_fee_usd=fill.fee_usd,
                margin_usd=margin,
                opened_at=fill.ts,
            )
            return True, (
                f"OPEN SHORT {fill.symbol} qty={fill.qty:.6f} @ {fill.avg_price:.6f} "
                f"fee=${fill.fee_usd:.4f} [{fill.source}]"
            ), 0.0

        if fill.side == "SELL":
            pos = self.positions.get(fill.symbol)
            if not pos:
                return False, f"reject SELL {fill.symbol}: no position", 0.0
            if pos.side != "LONG":
                return False, f"reject SELL {fill.symbol}: use BUY to close short", 0.0

            proceeds = fill.qty * fill.avg_price - fill.fee_usd
            basis = pos.qty * pos.entry_price + pos.entry_fee_usd
            pnl = proceeds - basis

            self.cash += proceeds
            self.realized_pnl += pnl
            self.closed_trades += 1
            if pnl >= 0:
                self.wins += 1
            else:
                self.losses += 1
            del self.positions[fill.symbol]
            return True, (
                f"SELL {fill.symbol} qty={fill.qty:.6f} @ {fill.avg_price:.6f} "
                f"fee=${fill.fee_usd:.4f} pnl={pnl:+.2f} [{fill.source}]"
            ), pnl

        if fill.side == "COVER":
            pos = self.positions.get(fill.symbol)
            if not pos:
                return False, f"reject COVER {fill.symbol}: no position", 0.0
            if pos.side != "SHORT":
                return False, f"reject COVER {fill.symbol}: position is not short", 0.0
            gross = (pos.entry_price - fill.avg_price) * pos.qty
            pnl = gross - pos.entry_fee_usd - fill.fee_usd
            self.cash += pos.margin_usd + gross - fill.fee_usd
            self.realized_pnl += pnl
            self.closed_trades += 1
            if pnl >= 0:
                self.wins += 1
            else:
                self.losses += 1
            del self.positions[fill.symbol]
            return True, (
                f"CLOSE SHORT {fill.symbol} qty={fill.qty:.6f} @ {fill.avg_price:.6f} "
                f"fee=${fill.fee_usd:.4f} pnl={pnl:+.2f} [{fill.source}]"
            ), pnl

        return False, f"reject {fill.symbol}: unknown side {fill.side}", 0.0

    def open_paper_short(self, symbol: str, usd_size: float, market_price: float, fee_rate: float, slip_rate: float) -> Tuple[bool, str]:
        if symbol in self.positions:
            return False, f"reject SHORT {symbol}: existing position"
        fee = usd_size * fee_rate
        total_lock = usd_size + fee
        if total_lock > self.cash:
            return False, f"reject SHORT {symbol}: insufficient cash"
        entry_price = market_price * (1 - slip_rate)
        qty = usd_size / entry_price if entry_price > 0 else 0.0
        self.cash -= total_lock
        self.total_fees += fee
        self.positions[symbol] = Position(
            symbol=symbol,
            side="SHORT",
            qty=qty,
            entry_price=entry_price,
            entry_fee_usd=fee,
            margin_usd=usd_size,
            opened_at=time.time(),
        )
        return True, f"OPEN SHORT {symbol} qty={qty:.6f} @ {entry_price:.6f} fee=${fee:.4f} [paper]"


class MarketDataAdapter:
    def scan_markets(self, cfg: Config) -> List[MarketSignal]:
        raise NotImplementedError


class BinanceMarketDataAdapter(MarketDataAdapter):
    def get_return(self, symbol: str, interval: str, lookback: int) -> float:
        r = requests.get(
            f"{BINANCE_BASE}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": lookback + 1},
            timeout=10,
        )
        r.raise_for_status()
        klines = r.json()
        first = float(klines[0][4])
        last = float(klines[-1][4])
        return (last - first) / first if first else 0.0

    def scan_markets(self, cfg: Config) -> List[MarketSignal]:
        r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/24hr", timeout=12)
        r.raise_for_status()
        tickers = r.json()
        candidates = []

        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if any(symbol.endswith(sfx) for sfx in EXCLUDED_SUFFIXES):
                continue
            base = symbol[: -4]
            if any(k in base for k in STABLE_KEYWORDS):
                continue
            try:
                qv = float(t.get("quoteVolume", 0.0))
                price = float(t.get("lastPrice", 0.0))
            except (TypeError, ValueError):
                continue
            if qv < cfg.min_quote_volume_usd or price <= 0:
                continue
            candidates.append((symbol, qv, price))

        candidates.sort(key=lambda x: x[1], reverse=True)
        candidates = candidates[: cfg.top_n_markets]

        signals = []
        for symbol, qv, price in candidates:
            try:
                sig = Strategy.from_returns(
                    symbol=symbol,
                    price=price,
                    quote_volume=qv,
                    r_5m=self.get_return(symbol, "1m", 5),
                    r_30m=self.get_return(symbol, "5m", 6),
                    r_4h=self.get_return(symbol, "15m", 16),
                )
                signals.append(sig)
            except Exception:
                continue

        signals.sort(key=lambda s: (s.score * s.confidence), reverse=True)
        return signals[: cfg.candidates_per_cycle]


class SolanaDexMarketDataAdapter(MarketDataAdapter):
    def __init__(self, token_mints: List[str]):
        self.token_mints = token_mints
        self.symbol_to_mint: Dict[str, str] = {}

    def _fetch_pair(self, mint: str) -> Optional[dict]:
        r = requests.get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{mint}", timeout=12)
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs:
            return None

        def rank(p):
            v = p.get("volume", {}).get("h24", 0) or 0
            return float(v)

        sol_pairs.sort(key=rank, reverse=True)
        return sol_pairs[0]

    def scan_markets(self, cfg: Config) -> List[MarketSignal]:
        self.symbol_to_mint = {}
        signals = []
        active_mints = self.token_mints
        if cfg.sol_only_mode:
            active_mints = [m for m in self.token_mints if m == "So11111111111111111111111111111111111111112"] or active_mints[:1]
        for mint in active_mints[: cfg.top_n_markets]:
            try:
                pair = self._fetch_pair(mint)
                if not pair:
                    continue
                symbol = pair.get("baseToken", {}).get("symbol") or mint[:6]
                if symbol in self.symbol_to_mint and self.symbol_to_mint[symbol] != mint:
                    symbol = f"{symbol}_{mint[:4]}"
                self.symbol_to_mint[symbol] = mint
                price = float(pair.get("priceUsd") or 0.0)
                vol = float((pair.get("volume") or {}).get("h24") or 0.0)
                if price <= 0 or vol < cfg.min_quote_volume_usd:
                    continue

                price_change = pair.get("priceChange") or {}
                r_5m = float(price_change.get("m5") or 0.0) / 100
                r_30m = float(price_change.get("h1") or 0.0) / 100 / 2
                r_4h = float(price_change.get("h6") or 0.0) / 100 * (4 / 6)

                signals.append(
                    Strategy.from_returns(
                        symbol=symbol,
                        price=price,
                        quote_volume=vol,
                        r_5m=r_5m,
                        r_30m=r_30m,
                        r_4h=r_4h,
                    )
                )
            except Exception:
                continue

        signals.sort(key=lambda s: (s.score * s.confidence), reverse=True)
        return signals[: cfg.candidates_per_cycle]


class ExecutionAdapter:
    def buy(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        raise NotImplementedError

    def sell(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        raise NotImplementedError

    def open_short(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        raise NotImplementedError

    def close_short(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        raise NotImplementedError


class PaperExecutionAdapter(ExecutionAdapter):
    def __init__(self, cfg: Config):
        self.fee_rate = cfg.paper_fee_bps / 10_000
        self.slip_rate = cfg.paper_slippage_bps / 10_000

    def buy(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        exec_price = market_price * (1 + self.slip_rate)
        qty = usd_size / exec_price
        notional = qty * exec_price
        fee = notional * self.fee_rate
        return TradeFill(
            symbol=symbol,
            side="BUY",
            qty=qty,
            avg_price=exec_price,
            fee_usd=fee,
            ts=time.time(),
            order_id=f"paper-buy-{int(time.time() * 1000)}",
            source="paper",
        )

    def sell(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        exec_price = market_price * (1 - self.slip_rate)
        notional = qty * exec_price
        fee = notional * self.fee_rate
        return TradeFill(
            symbol=symbol,
            side="SELL",
            qty=qty,
            avg_price=exec_price,
            fee_usd=fee,
            ts=time.time(),
            order_id=f"paper-sell-{int(time.time() * 1000)}",
            source="paper",
        )

    def open_short(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        exec_price = market_price * (1 - self.slip_rate)
        qty = usd_size / exec_price if exec_price > 0 else 0.0
        fee = usd_size * self.fee_rate
        return TradeFill(
            symbol=symbol,
            side="SHORT",
            qty=qty,
            avg_price=exec_price,
            fee_usd=fee,
            ts=time.time(),
            order_id=f"paper-short-{int(time.time() * 1000)}",
            source="paper",
        )

    def close_short(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        exec_price = market_price * (1 + self.slip_rate)
        notional = qty * exec_price
        fee = notional * self.fee_rate
        return TradeFill(
            symbol=symbol,
            side="COVER",
            qty=qty,
            avg_price=exec_price,
            fee_usd=fee,
            ts=time.time(),
            order_id=f"paper-cover-{int(time.time() * 1000)}",
            source="paper",
        )


class BinanceLiveExecutionAdapter(ExecutionAdapter):
    def __init__(self, cfg: Config):
        self.key = cfg.exchange_api_key
        self.secret = cfg.exchange_api_secret
        if not self.key or not self.secret:
            raise ValueError("live binance mode requires EXCHANGE_API_KEY and EXCHANGE_API_SECRET")
        if cfg.live_trading_ack != "I_UNDERSTAND_LIVE_RISK":
            raise ValueError("set LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK")

    def _signed_request(self, method: str, path: str, params: Dict[str, str]) -> dict:
        params = dict(params)
        params["timestamp"] = str(int(time.time() * 1000))
        qs = urlencode(params)
        sig = hmac.new(self.secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{BINANCE_BASE}{path}?{qs}&signature={sig}"
        headers = {"X-MBX-APIKEY": self.key}
        r = requests.post(url, headers=headers, timeout=15) if method == "POST" else requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()

    def _parse_fill(self, symbol: str, side: str, resp: dict) -> TradeFill:
        fills = resp.get("fills", [])
        executed_qty = float(resp.get("executedQty", 0.0))
        cumm_quote = float(resp.get("cummulativeQuoteQty", 0.0))
        avg_price = (cumm_quote / executed_qty) if executed_qty else 0.0
        fee_usd = 0.0
        for f in fills:
            if f.get("commissionAsset") == "USDT":
                fee_usd += float(f.get("commission", 0.0))
        return TradeFill(symbol, side, executed_qty, avg_price, fee_usd, time.time(), str(resp.get("orderId", "live-order")), "binance-live")

    def buy(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        resp = self._signed_request("POST", "/api/v3/order", {"symbol": symbol, "side": "BUY", "type": "MARKET", "quoteOrderQty": f"{usd_size:.8f}"})
        return self._parse_fill(symbol, "BUY", resp)

    def sell(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        resp = self._signed_request("POST", "/api/v3/order", {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": f"{qty:.8f}"})
        return self._parse_fill(symbol, "SELL", resp)

    def open_short(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        raise NotImplementedError("binance spot adapter does not support opening shorts")

    def close_short(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        raise NotImplementedError("binance spot adapter does not support closing shorts")


class DriftGatewayExecutionAdapter(ExecutionAdapter):
    def __init__(self, cfg: Config):
        if cfg.live_trading_ack != "I_UNDERSTAND_LIVE_RISK":
            raise ValueError("set LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK")
        if not cfg.drift_gateway_url:
            raise ValueError("DRIFT_GATEWAY_URL is required for drift_gateway venue")
        self.base_url = cfg.drift_gateway_url.rstrip("/")
        self.api_key = cfg.drift_api_key
        self.market_symbol = cfg.drift_market_symbol.upper()
        self.market_index = cfg.drift_market_index
        self.fee_bps = cfg.drift_taker_fee_bps
        self.tx_fee_usd = cfg.drift_estimated_tx_fee_usd

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post_order(self, direction: str, base_amount: float, reduce_only: bool) -> str:
        payload = {
            "marketType": "perp",
            "marketIndex": self.market_index,
            "direction": direction,
            "orderType": "market",
            "baseAssetAmount": str(base_amount),
            "reduceOnly": reduce_only,
        }
        endpoints = ["/v2/orders", "/orders"]
        last_error: Optional[Exception] = None
        for ep in endpoints:
            try:
                r = requests.post(f"{self.base_url}{ep}", headers=self._headers(), json=payload, timeout=20)
                if r.status_code >= 400:
                    last_error = RuntimeError(f"{ep} {r.status_code}: {r.text[:300]}")
                    continue
                data = r.json()
                sig = (
                    data.get("signature")
                    or data.get("txSig")
                    or data.get("result", {}).get("signature")
                    or f"drift-{int(time.time()*1000)}"
                )
                return str(sig)
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"drift order failed: {last_error}")

    def _fee_usd(self, notional: float) -> float:
        return (notional * self.fee_bps / 10_000) + self.tx_fee_usd

    def buy(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        qty = usd_size / market_price if market_price > 0 else 0.0
        sig = self._post_order("long", qty, reduce_only=False)
        return TradeFill(symbol, "BUY", qty, market_price, self._fee_usd(usd_size), time.time(), sig, "drift-gateway-live")

    def sell(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        sig = self._post_order("short", qty, reduce_only=True)
        return TradeFill(symbol, "SELL", qty, market_price, self._fee_usd(qty * market_price), time.time(), sig, "drift-gateway-live")

    def open_short(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        qty = usd_size / market_price if market_price > 0 else 0.0
        sig = self._post_order("short", qty, reduce_only=False)
        return TradeFill(symbol, "SHORT", qty, market_price, self._fee_usd(usd_size), time.time(), sig, "drift-gateway-live")

    def close_short(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        sig = self._post_order("long", qty, reduce_only=True)
        return TradeFill(symbol, "COVER", qty, market_price, self._fee_usd(qty * market_price), time.time(), sig, "drift-gateway-live")


class SolanaJupiterLiveExecutionAdapter(ExecutionAdapter):
    def __init__(self, cfg: Config, symbol_to_mint: Dict[str, str]):
        if cfg.live_trading_ack != "I_UNDERSTAND_LIVE_RISK":
            raise ValueError("set LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK")
        if not cfg.solana_wallet_private_key or not cfg.solana_wallet_address:
            raise ValueError("SOLANA_WALLET_PRIVATE_KEY and SOLANA_WALLET_ADDRESS are required")
        if not cfg.solana_rpc_url:
            raise ValueError("SOLANA_RPC_URL is required")

        self.rpc_urls = [cfg.solana_rpc_url] + (cfg.solana_rpc_fallback_urls or [])
        self.jupiter_api_key = cfg.jupiter_api_key
        self.wallet_address = cfg.solana_wallet_address
        self.wallet_private_key = cfg.solana_wallet_private_key
        self.symbol_to_mint = symbol_to_mint
        self.slippage_bps = cfg.jupiter_slippage_bps
        self.quote_mint = cfg.solana_quote_mint
        self.decimals_cache: Dict[str, int] = {self.quote_mint: 6}
        self.sol_price_cache: Tuple[float, float] = (0.0, 0.0)

        from solders.keypair import Keypair  # lazy import

        self.keypair = Keypair.from_base58_string(self.wallet_private_key)

    def _jup_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.jupiter_api_key:
            headers["x-api-key"] = self.jupiter_api_key
        return headers

    def _rpc_call(self, method: str, params: list) -> dict:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        last_err = None
        for url in self.rpc_urls:
            try:
                r = requests.post(url, json=payload, timeout=20)
                r.raise_for_status()
                data = r.json()
                if data.get("error"):
                    last_err = RuntimeError(str(data["error"]))
                    continue
                return data["result"]
            except Exception as e:
                last_err = e
        raise RuntimeError(f"RPC call failed: {last_err}")

    def _get_sol_price_usd(self) -> float:
        now = time.time()
        cached_ts, cached_px = self.sol_price_cache
        if now - cached_ts < 30 and cached_px > 0:
            return cached_px
        r = requests.get(
            f"{DEXSCREENER_BASE}/latest/dex/tokens/So11111111111111111111111111111111111111112",
            timeout=10,
        )
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana" and p.get("priceUsd")]
        if not sol_pairs:
            return cached_px if cached_px > 0 else 0.0
        sol_pairs.sort(key=lambda p: float((p.get("volume") or {}).get("h24") or 0.0), reverse=True)
        px = float(sol_pairs[0]["priceUsd"])
        self.sol_price_cache = (now, px)
        return px

    def _get_tx_fee_usd(self, signature: str) -> float:
        for _ in range(8):
            try:
                result = self._rpc_call(
                    "getTransaction",
                    [
                        signature,
                        {
                            "encoding": "json",
                            "maxSupportedTransactionVersion": 0,
                            "commitment": "confirmed",
                        },
                    ],
                )
                if result and result.get("meta") and result["meta"].get("fee") is not None:
                    lamports = int(result["meta"]["fee"])
                    sol_fee = lamports / 1_000_000_000
                    sol_px = self._get_sol_price_usd()
                    return sol_fee * sol_px if sol_px > 0 else 0.0
            except Exception:
                pass
            time.sleep(1)
        return 0.0

    def _get_token_decimals(self, mint: str) -> int:
        if mint in self.decimals_cache:
            return self.decimals_cache[mint]
        result = self._rpc_call("getTokenSupply", [mint])
        dec = int(result["value"]["decimals"])
        self.decimals_cache[mint] = dec
        return dec

    def _quote(self, input_mint: str, output_mint: str, amount_raw: int) -> dict:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_raw),
            "slippageBps": str(self.slippage_bps),
            "restrictIntermediateTokens": "true",
        }
        r = requests.get(f"{JUPITER_BASE}/swap/v1/quote", params=params, headers=self._jup_headers(), timeout=20)
        r.raise_for_status()
        return r.json()

    def _swap_tx(self, quote_resp: dict) -> str:
        body = {
            "quoteResponse": quote_resp,
            "userPublicKey": self.wallet_address,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        }
        r = requests.post(f"{JUPITER_BASE}/swap/v1/swap", headers=self._jup_headers(), json=body, timeout=20)
        r.raise_for_status()
        data = r.json()
        tx = data.get("swapTransaction")
        if not tx:
            raise RuntimeError(f"Jupiter swap response missing transaction: {data}")
        return tx

    def _sign_and_send(self, swap_tx_b64: str) -> str:
        from solders.message import to_bytes_versioned
        from solders.transaction import VersionedTransaction

        raw_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_tx_b64))
        sig = self.keypair.sign_message(to_bytes_versioned(raw_tx.message))
        signed_tx = VersionedTransaction.populate(raw_tx.message, [sig])
        tx_b64 = base64.b64encode(bytes(signed_tx)).decode("utf-8")
        sig_str = self._rpc_call("sendTransaction", [tx_b64, {"skipPreflight": False, "maxRetries": 3}])
        return str(sig_str)

    def buy(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        output_mint = self.symbol_to_mint.get(symbol)
        if not output_mint:
            raise ValueError(f"unknown token symbol for buy: {symbol}")

        amount_raw = int(usd_size * (10 ** self._get_token_decimals(self.quote_mint)))
        quote = self._quote(self.quote_mint, output_mint, amount_raw)
        tx = self._swap_tx(quote)
        sig = self._sign_and_send(tx)
        fee_usd = self._get_tx_fee_usd(sig)

        out_amount_raw = int(quote.get("outAmount", "0"))
        out_dec = self._get_token_decimals(output_mint)
        qty = out_amount_raw / (10 ** out_dec) if out_dec >= 0 else 0.0
        avg_price = (usd_size / qty) if qty > 0 else market_price

        return TradeFill(symbol, "BUY", qty, avg_price, fee_usd, time.time(), sig, "solana-jupiter-live")

    def sell(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        input_mint = self.symbol_to_mint.get(symbol)
        if not input_mint:
            raise ValueError(f"unknown token symbol for sell: {symbol}")

        in_dec = self._get_token_decimals(input_mint)
        amount_raw = int(qty * (10 ** in_dec))
        quote = self._quote(input_mint, self.quote_mint, amount_raw)
        tx = self._swap_tx(quote)
        sig = self._sign_and_send(tx)
        fee_usd = self._get_tx_fee_usd(sig)

        out_amount_raw = int(quote.get("outAmount", "0"))
        out_dec = self._get_token_decimals(self.quote_mint)
        out_usd = out_amount_raw / (10 ** out_dec) if out_dec >= 0 else 0.0
        avg_price = (out_usd / qty) if qty > 0 else market_price

        return TradeFill(symbol, "SELL", qty, avg_price, fee_usd, time.time(), sig, "solana-jupiter-live")

    def open_short(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        raise NotImplementedError("jupiter spot adapter does not support opening shorts")

    def close_short(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        raise NotImplementedError("jupiter spot adapter does not support closing shorts")


class BotApp:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.portfolio = PortfolioEngine(cfg.budget_usd)
        self.console = Console()
        self.logs: List[str] = []
        self.last_signals: List[MarketSignal] = []
        self.tick = 0
        self.kill_switch = False
        self.logs_dir = Path(os.getenv("LOG_DIR", "logs"))
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.brain = AgentBrain(cfg, self.logs_dir)

        self.symbol_to_mint: Dict[str, str] = {}
        if cfg.trading_venue in {"solana_jupiter", "drift_gateway"}:
            self.market_data = SolanaDexMarketDataAdapter(cfg.solana_token_mints or DEFAULT_SOLANA_MINTS)
        else:
            self.market_data = BinanceMarketDataAdapter()

        if cfg.mode == "live":
            if cfg.trading_venue == "solana_jupiter":
                self.executor = SolanaJupiterLiveExecutionAdapter(cfg, self.symbol_to_mint)
            elif cfg.trading_venue == "drift_gateway":
                self.executor = DriftGatewayExecutionAdapter(cfg)
            else:
                self.executor = BinanceLiveExecutionAdapter(cfg)
        else:
            self.executor = PaperExecutionAdapter(cfg)

    def refresh_symbol_map_from_signals(self):
        if self.cfg.trading_venue not in {"solana_jupiter", "drift_gateway"}:
            return
        if isinstance(self.market_data, SolanaDexMarketDataAdapter):
            self.symbol_to_mint.update(self.market_data.symbol_to_mint)

    def log(self, message: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        self.logs.append(line)
        self.logs = self.logs[-30:]

    def event(self, event_type: str, payload: dict):
        row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event_type, "payload": payload}
        with open(self.logs_dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def scan_markets(self) -> List[MarketSignal]:
        return self.market_data.scan_markets(self.cfg)

    def apply_risk_controls(self, prices: Dict[str, float]):
        now = time.time()
        for symbol, pos in list(self.portfolio.positions.items()):
            mark = prices.get(symbol)
            if not mark:
                continue
            move = (mark - pos.entry_price) / pos.entry_price
            if pos.side == "SHORT":
                move = -move
            age_mins = (now - pos.opened_at) / 60

            if move <= -self.cfg.stop_loss_pct:
                if pos.side == "SHORT":
                    self.execute_close_short(symbol, pos.qty, mark, "stop_loss")
                else:
                    self.execute_exit(symbol, pos.qty, mark, "stop_loss")
                continue
            if move >= self.cfg.take_profit_pct:
                if pos.side == "SHORT":
                    self.execute_close_short(symbol, pos.qty, mark, "take_profit")
                else:
                    self.execute_exit(symbol, pos.qty, mark, "take_profit")
                continue
            if age_mins >= self.cfg.max_position_age_minutes:
                if pos.side == "SHORT":
                    self.execute_close_short(symbol, pos.qty, mark, "max_age")
                else:
                    self.execute_exit(symbol, pos.qty, mark, "max_age")

    def execute_entry(self, symbol: str, usd_size: float, price: float, reason: str):
        try:
            fill = self.executor.buy(symbol, usd_size, price)
            ok, msg, _ = self.portfolio.apply_fill(fill)
            self.log(msg)
            if ok:
                self.event("entry", {"symbol": symbol, "reason": reason, "fill": fill.__dict__})
        except Exception as exc:
            self.log(f"entry fail {symbol}: {exc}")

    def execute_exit(self, symbol: str, qty: float, price: float, reason: str):
        try:
            fill = self.executor.sell(symbol, qty, price)
            ok, msg, pnl = self.portfolio.apply_fill(fill)
            self.log(f"{msg} reason={reason}")
            if ok:
                self.portfolio.set_cooldown(symbol, self.cfg.cooldown_minutes)
                self.event("exit", {"symbol": symbol, "reason": reason, "pnl": pnl, "fill": fill.__dict__})
        except Exception as exc:
            self.log(f"exit fail {symbol}: {exc}")

    def execute_open_short(self, symbol: str, usd_size: float, price: float, reason: str):
        try:
            fill = self.executor.open_short(symbol, usd_size, price)
            ok, msg, _ = self.portfolio.apply_fill(fill)
            self.log(msg)
            if ok:
                self.event("entry_short", {"symbol": symbol, "reason": reason, "fill": fill.__dict__})
        except NotImplementedError:
            self.log(f"short skipped {symbol}: venue/executor has no short entry")
        except Exception as exc:
            self.log(f"short entry fail {symbol}: {exc}")

    def execute_close_short(self, symbol: str, qty: float, price: float, reason: str):
        try:
            fill = self.executor.close_short(symbol, qty, price)
            ok, msg, pnl = self.portfolio.apply_fill(fill)
            self.log(f"{msg} reason={reason}")
            if ok:
                self.portfolio.set_cooldown(symbol, self.cfg.cooldown_minutes)
                self.event("exit_short", {"symbol": symbol, "reason": reason, "pnl": pnl, "fill": fill.__dict__})
        except NotImplementedError:
            self.log(f"short close skipped {symbol}: venue/executor has no short close")
        except Exception as exc:
            self.log(f"short close fail {symbol}: {exc}")

    def check_kill_switch(self, equity: float) -> bool:
        self.portfolio.update_peak(equity)
        dd = (self.portfolio.peak_equity - equity) / self.portfolio.peak_equity if self.portfolio.peak_equity else 0.0
        if dd >= self.cfg.max_daily_drawdown_pct:
            self.kill_switch = True
            self.log(f"KILL SWITCH ON: drawdown {dd:.2%} >= {self.cfg.max_daily_drawdown_pct:.2%}")
            return True
        return False

    def write_cycle_report(self, prices: Dict[str, float]):
        equity = self.portfolio.equity(prices)
        win_rate = (self.portfolio.wins / self.portfolio.closed_trades) if self.portfolio.closed_trades else 0.0
        report = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tick": self.tick,
            "mode": self.cfg.mode,
            "trading_venue": self.cfg.trading_venue,
            "kill_switch": self.kill_switch,
            "equity": equity,
            "cash": self.portfolio.cash,
            "realized_pnl": self.portfolio.realized_pnl,
            "total_fees": self.portfolio.total_fees,
            "closed_trades": self.portfolio.closed_trades,
            "wins": self.portfolio.wins,
            "losses": self.portfolio.losses,
            "win_rate": win_rate,
            "open_positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "qty": p.qty,
                    "entry_price": p.entry_price,
                    "entry_fee_usd": p.entry_fee_usd,
                    "margin_usd": p.margin_usd,
                    "mark_price": prices.get(p.symbol, p.entry_price),
                }
                for p in self.portfolio.positions.values()
            ],
            "top_signals": [
                {
                    "symbol": s.symbol,
                    "price": s.price,
                    "score": s.score,
                    "confidence": s.confidence,
                    "quote_volume": s.quote_volume,
                }
                for s in self.last_signals[:5]
            ],
        }
        with open(self.logs_dir / "latest_cycle.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    def step(self):
        self.tick += 1
        self.last_signals = self.scan_markets()
        self.refresh_symbol_map_from_signals()
        prices = {s.symbol: s.price for s in self.last_signals}

        self.apply_risk_controls(prices)

        equity = self.portfolio.equity(prices)
        if self.check_kill_switch(equity):
            for symbol, pos in list(self.portfolio.positions.items()):
                mark = prices.get(symbol, pos.entry_price)
                if pos.side == "SHORT":
                    self.execute_close_short(symbol, pos.qty, mark, "kill_switch")
                else:
                    self.execute_exit(symbol, pos.qty, mark, "kill_switch")
            self.write_cycle_report(prices)
            return

        if not self.kill_switch:
            decisions = self.brain.decide(self.last_signals, self.portfolio.positions, self.portfolio)
            signal_by_symbol = {s.symbol: s for s in self.last_signals}

            for symbol, signal in signal_by_symbol.items():
                d = decisions.get(symbol, {"action": "HOLD", "size_fraction": 0.0, "confidence": 0.5, "reason": "default_hold"})
                action = str(d.get("action", "HOLD")).upper()
                size_fraction = float(d.get("size_fraction", self.cfg.risk_per_trade) or 0.0)
                size_fraction = max(0.0, min(0.25, size_fraction))
                reason = str(d.get("reason", "brain"))
                pos = self.portfolio.positions.get(symbol)

                if action == "LONG":
                    if len(self.portfolio.positions) >= self.cfg.max_positions and not pos:
                        self.log(f"hold {symbol}: max positions reached")
                        continue
                    if pos and pos.side == "SHORT":
                        self.execute_close_short(symbol, pos.qty, signal.price, f"{reason}|flip_to_long")
                        pos = self.portfolio.positions.get(symbol)
                    if pos:
                        self.log(f"hold {symbol}: already long")
                        continue
                    if self.portfolio.in_cooldown(symbol):
                        self.log(f"hold {symbol}: in cooldown")
                        continue
                    usd_size = self.portfolio.cash * (size_fraction or self.cfg.risk_per_trade)
                    self.execute_entry(symbol, usd_size, signal.price, reason)
                elif action == "SHORT":
                    if len(self.portfolio.positions) >= self.cfg.max_positions and not pos:
                        self.log(f"hold {symbol}: max positions reached")
                        continue
                    if not self.cfg.allow_paper_shorts and self.cfg.trading_venue != "drift_gateway":
                        self.log(f"short disabled {symbol}: ALLOW_PAPER_SHORTS=0")
                        continue
                    if pos and pos.side == "LONG":
                        self.execute_exit(symbol, pos.qty, signal.price, f"{reason}|flip_to_short")
                        pos = self.portfolio.positions.get(symbol)
                    if pos:
                        self.log(f"hold {symbol}: already short")
                        continue
                    if self.portfolio.in_cooldown(symbol):
                        self.log(f"hold {symbol}: in cooldown")
                        continue
                    usd_size = self.portfolio.cash * (size_fraction or self.cfg.risk_per_trade)
                    self.execute_open_short(symbol, usd_size, signal.price, reason)
                elif action == "EXIT_LONG":
                    if pos and pos.side == "LONG":
                        self.execute_exit(symbol, pos.qty, signal.price, reason)
                    else:
                        self.log(f"hold {symbol}: no long to exit")
                elif action == "EXIT_SHORT":
                    if pos and pos.side == "SHORT":
                        self.execute_close_short(symbol, pos.qty, signal.price, reason)
                    else:
                        self.log(f"hold {symbol}: no short to exit")
                else:
                    self.log(f"hold {symbol}: brain_hold reason={reason}")

        equity = self.portfolio.equity(prices)
        self.log(f"equity=${equity:.2f} cash=${self.portfolio.cash:.2f} realized={self.portfolio.realized_pnl:+.2f}")
        self.write_cycle_report(prices)

    def render(self):
        layout = Layout()
        layout.split_column(Layout(name="header", size=3), Layout(name="body"), Layout(name="footer", size=11))
        layout["body"].split_row(Layout(name="signals"), Layout(name="positions"))

        header = Text(
            f"{self.cfg.app_name} ({self.cfg.mode.upper()}) {self.cfg.trading_venue} | tick={self.tick}",
            style="bold cyan",
        )
        layout["header"].update(Panel(header))

        table = Table(title="Opportunity Ranking", expand=True)
        table.add_column("Symbol")
        table.add_column("Price", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("Conf", justify="right")
        table.add_column("Vol(24h)", justify="right")
        table.add_column("Flow")

        for sig in self.last_signals:
            if sig.score >= self.cfg.buy_threshold:
                flow, color = "SCAN->MODEL->BUY", "green"
            elif sig.score <= self.cfg.sell_threshold:
                flow, color = "SCAN->MODEL->SELL", "red"
            else:
                flow, color = "SCAN->MODEL->HOLD", "yellow"
            table.add_row(
                sig.symbol,
                f"{sig.price:.6f}",
                f"[{color}]{sig.score:+.2%}[/{color}]",
                f"{sig.confidence:.2f}",
                f"{sig.quote_volume:,.0f}",
                flow,
            )
        if not self.last_signals:
            table.add_row("-", "-", "-", "-", "-", "waiting")

        pos_table = Table(title="Portfolio + Risk", expand=True)
        pos_table.add_column("Symbol")
        pos_table.add_column("Side")
        pos_table.add_column("Qty", justify="right")
        pos_table.add_column("Entry", justify="right")
        pos_table.add_column("Mark", justify="right")
        pos_table.add_column("U-PnL", justify="right")
        pos_table.add_column("Age(m)", justify="right")

        prices = {s.symbol: s.price for s in self.last_signals}
        now = time.time()
        for pos in self.portfolio.positions.values():
            mark = prices.get(pos.symbol, pos.entry_price)
            if pos.side == "SHORT":
                pnl = (pos.entry_price - mark) * pos.qty - pos.entry_fee_usd
                side_style = "red"
            else:
                pnl = (mark - pos.entry_price) * pos.qty - pos.entry_fee_usd
                side_style = "green"
            age = (now - pos.opened_at) / 60
            style = "green" if pnl >= 0 else "red"
            pos_table.add_row(
                pos.symbol,
                f"[{side_style}]{pos.side}[/{side_style}]",
                f"{pos.qty:.6f}",
                f"{pos.entry_price:.6f}",
                f"{mark:.6f}",
                f"[{style}]{pnl:+.2f}[/{style}]",
                f"{age:.1f}",
            )
        if not self.portfolio.positions:
            pos_table.add_row("-", "-", "-", "-", "-", "-", "-")

        equity = self.portfolio.equity(prices)
        dd = (self.portfolio.peak_equity - equity) / self.portfolio.peak_equity if self.portfolio.peak_equity else 0.0
        wr = (self.portfolio.wins / self.portfolio.closed_trades) if self.portfolio.closed_trades else 0.0
        status = "HALTED" if self.kill_switch else "ACTIVE"
        account = Text(
            (
                f"Status {status} | Cash ${self.portfolio.cash:.2f} | Equity ${equity:.2f} | "
                f"Realized {self.portfolio.realized_pnl:+.2f} | Fees ${self.portfolio.total_fees:.2f} | "
                f"WR {wr:.1%} ({self.portfolio.wins}/{self.portfolio.closed_trades}) | DD {dd:.2%}"
            ),
            style="bold",
        )

        footer = Group(
            Panel(account, title="Account"),
            Panel("\n".join(self.logs[-9:]) if self.logs else "booting...", title="Execution Log"),
        )

        layout["signals"].update(Panel(table, border_style="bright_blue"))
        layout["positions"].update(Panel(pos_table, border_style="magenta"))
        layout["footer"].update(footer)
        return layout

    def run(self):
        self.log("booting autonomous runtime")
        self.log(f"venue: {self.cfg.trading_venue}")
        self.log(f"execution mode: {self.cfg.mode}")
        self.log(f"brain: {'enabled' if self.cfg.brain_enabled else 'disabled'} provider={self.cfg.brain_provider}")
        if self.cfg.mode == "live" and self.cfg.trading_venue == "solana_jupiter":
            self.log("live short entries not available on Jupiter spot (long/flat mode)")
        if self.cfg.mode == "live" and self.cfg.trading_venue == "drift_gateway":
            self.log("drift gateway live long/short mode enabled")

        with Live(self.render(), refresh_per_second=8, console=self.console) as live:
            while True:
                start = time.time()
                self.step()
                live.update(self.render())
                elapsed = time.time() - start
                delay = max(1, self.cfg.scan_interval - int(elapsed))
                for sec in range(delay):
                    self.log(f"next scan in {delay - sec}s")
                    live.update(self.render())
                    time.sleep(1)


def parse_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def parse_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def parse_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


def parse_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> Config:
    load_dotenv()
    fallback_urls = parse_list("SOLANA_RPC_FALLBACK_URLS", [])
    return Config(
        app_name=os.getenv("APP_NAME", "AIG Trader OS"),
        mode=os.getenv("BOT_MODE", "paper").lower(),
        trading_venue=os.getenv("TRADING_VENUE", "solana_jupiter").lower(),
        budget_usd=parse_float("BUDGET_USD", 50.0),
        scan_interval=parse_int("SCAN_INTERVAL", 20),
        risk_per_trade=parse_float("RISK_PER_TRADE", 0.15),
        max_positions=parse_int("MAX_POSITIONS", 4),
        buy_threshold=parse_float("BUY_THRESHOLD", 0.008),
        sell_threshold=parse_float("SELL_THRESHOLD", -0.006),
        top_n_markets=parse_int("TOP_N_MARKETS", 25),
        candidates_per_cycle=parse_int("CANDIDATES_PER_CYCLE", 6),
        min_quote_volume_usd=parse_float("MIN_QUOTE_VOLUME_USD", 500000),
        stop_loss_pct=parse_float("STOP_LOSS_PCT", 0.025),
        take_profit_pct=parse_float("TAKE_PROFIT_PCT", 0.045),
        max_position_age_minutes=parse_int("MAX_POSITION_AGE_MINUTES", 240),
        max_daily_drawdown_pct=parse_float("MAX_DAILY_DRAWDOWN_PCT", 0.08),
        cooldown_minutes=parse_int("COOLDOWN_MINUTES", 20),
        quote_asset=os.getenv("QUOTE_ASSET", "USDC").upper(),
        paper_fee_bps=parse_float("PAPER_FEE_BPS", 10),
        paper_slippage_bps=parse_float("PAPER_SLIPPAGE_BPS", 5),
        live_trading_ack=os.getenv("LIVE_TRADING_ACK", ""),
        exchange_api_key=os.getenv("EXCHANGE_API_KEY", ""),
        exchange_api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
        solana_rpc_url=os.getenv("SOLANA_RPC_URL", "").strip().strip('"'),
        solana_rpc_fallback_urls=fallback_urls,
        helius_api_key=os.getenv("HELIUS_API_KEY", ""),
        jupiter_api_key=os.getenv("JUPITER_API_KEY", ""),
        solana_wallet_address=os.getenv("SOLANA_WALLET_ADDRESS", ""),
        solana_wallet_private_key=os.getenv("SOLANA_WALLET_PRIVATE_KEY", ""),
        solana_token_mints=parse_list("SOLANA_TOKEN_MINTS", DEFAULT_SOLANA_MINTS),
        solana_quote_mint=os.getenv("SOLANA_QUOTE_MINT", USDC_MINT),
        jupiter_slippage_bps=parse_int("JUPITER_SLIPPAGE_BPS", 50),
        allow_paper_shorts=parse_bool("ALLOW_PAPER_SHORTS", True),
        sol_only_mode=parse_bool("SOL_ONLY_MODE", True),
        drift_gateway_url=os.getenv("DRIFT_GATEWAY_URL", ""),
        drift_api_key=os.getenv("DRIFT_API_KEY", ""),
        drift_market_symbol=os.getenv("DRIFT_MARKET_SYMBOL", "SOL"),
        drift_market_index=parse_int("DRIFT_MARKET_INDEX", 0),
        drift_taker_fee_bps=parse_float("DRIFT_TAKER_FEE_BPS", 10.0),
        drift_estimated_tx_fee_usd=parse_float("DRIFT_ESTIMATED_TX_FEE_USD", 0.02),
        brain_enabled=parse_bool("BRAIN_ENABLED", True),
        brain_provider=os.getenv("BRAIN_PROVIDER", "openai").lower(),
        brain_model=os.getenv("BRAIN_MODEL", "gpt-4o-mini"),
        brain_temperature=parse_float("BRAIN_TEMPERATURE", 0.2),
        brain_max_memory_items=parse_int("BRAIN_MAX_MEMORY_ITEMS", 25),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        local_brain_url=os.getenv("LOCAL_BRAIN_URL", "http://127.0.0.1:11434"),
        local_brain_model=os.getenv("LOCAL_BRAIN_MODEL", "qwen2.5:7b-instruct"),
        local_brain_api_key=os.getenv("LOCAL_BRAIN_API_KEY", ""),
    )


if __name__ == "__main__":
    cfg = load_config()
    app = BotApp(cfg)
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nShutting down bot.")
