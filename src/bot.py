import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
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
STABLE_KEYWORDS = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDP", "EUR", "GBP")
EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_fee_usd: float
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
    mode: str = "paper"
    budget_usd: float = 50.0
    scan_interval: int = 20
    risk_per_trade: float = 0.15
    max_positions: int = 4
    buy_threshold: float = 0.008
    sell_threshold: float = -0.006
    top_n_markets: int = 25
    candidates_per_cycle: int = 6
    min_quote_volume_usd: float = 5_000_000.0
    stop_loss_pct: float = 0.025
    take_profit_pct: float = 0.045
    max_position_age_minutes: int = 240
    max_daily_drawdown_pct: float = 0.08
    cooldown_minutes: int = 20
    quote_asset: str = "USDT"
    paper_fee_bps: float = 10.0
    paper_slippage_bps: float = 5.0
    live_trading_ack: str = ""
    exchange_api_key: str = ""
    exchange_api_secret: str = ""


class MarketClient:
    @staticmethod
    def get_ticker_24h() -> List[dict]:
        r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/24hr", timeout=12)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def get_price(symbol: str) -> float:
        r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])

    @staticmethod
    def get_return(symbol: str, interval: str, lookback: int) -> float:
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


class Strategy:
    @staticmethod
    def score(symbol: str, client: MarketClient, quote_volume: float, price: float) -> MarketSignal:
        r_5m = client.get_return(symbol, "1m", 5)
        r_30m = client.get_return(symbol, "5m", 6)
        r_4h = client.get_return(symbol, "15m", 16)
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
        mark = sum(pos.qty * prices.get(pos.symbol, pos.entry_price) for pos in self.positions.values())
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
            cost = fill.qty * fill.avg_price + fill.fee_usd
            if cost > self.cash:
                return False, f"reject BUY {fill.symbol}: insufficient cash", 0.0
            if fill.symbol in self.positions:
                return False, f"reject BUY {fill.symbol}: existing position", 0.0

            self.cash -= cost
            self.positions[fill.symbol] = Position(
                symbol=fill.symbol,
                qty=fill.qty,
                entry_price=fill.avg_price,
                entry_fee_usd=fill.fee_usd,
                opened_at=fill.ts,
            )
            return True, (
                f"BUY {fill.symbol} qty={fill.qty:.6f} @ {fill.avg_price:.6f} "
                f"fee=${fill.fee_usd:.4f} [{fill.source}]"
            ), 0.0

        if fill.side == "SELL":
            pos = self.positions.get(fill.symbol)
            if not pos:
                return False, f"reject SELL {fill.symbol}: no position", 0.0

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

        return False, f"reject {fill.symbol}: unknown side {fill.side}", 0.0


class ExecutionAdapter:
    def buy(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        raise NotImplementedError

    def sell(self, symbol: str, qty: float, market_price: float) -> TradeFill:
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


class BinanceLiveExecutionAdapter(ExecutionAdapter):
    def __init__(self, cfg: Config, market_client: MarketClient):
        self.key = cfg.exchange_api_key
        self.secret = cfg.exchange_api_secret
        self.market_client = market_client
        if not self.key or not self.secret:
            raise ValueError("live mode requires EXCHANGE_API_KEY and EXCHANGE_API_SECRET")
        if cfg.live_trading_ack != "I_UNDERSTAND_LIVE_RISK":
            raise ValueError("set LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK to enable live trading")

    def _signed_request(self, method: str, path: str, params: Dict[str, str]) -> dict:
        params = dict(params)
        params["timestamp"] = str(int(time.time() * 1000))
        qs = urlencode(params)
        sig = hmac.new(self.secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{BINANCE_BASE}{path}?{qs}&signature={sig}"
        headers = {"X-MBX-APIKEY": self.key}

        if method == "POST":
            r = requests.post(url, headers=headers, timeout=15)
        else:
            r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()

    def _commission_to_usd(self, commission_asset: str, commission_amount: float) -> float:
        if commission_amount <= 0:
            return 0.0
        if commission_asset == "USDT":
            return commission_amount
        pair = f"{commission_asset}USDT"
        try:
            px = self.market_client.get_price(pair)
            return commission_amount * px
        except Exception:
            return 0.0

    def _parse_fill(self, symbol: str, side: str, resp: dict) -> TradeFill:
        fills = resp.get("fills", [])
        if not fills:
            executed_qty = float(resp.get("executedQty", 0.0))
            cumm_quote = float(resp.get("cummulativeQuoteQty", 0.0))
            avg_price = (cumm_quote / executed_qty) if executed_qty else 0.0
            fee_usd = 0.0
        else:
            executed_qty = 0.0
            cumm_quote = 0.0
            fee_usd = 0.0
            for f in fills:
                q = float(f.get("qty", 0.0))
                p = float(f.get("price", 0.0))
                executed_qty += q
                cumm_quote += q * p
                fee_usd += self._commission_to_usd(f.get("commissionAsset", "USDT"), float(f.get("commission", 0.0)))
            avg_price = (cumm_quote / executed_qty) if executed_qty else 0.0

        return TradeFill(
            symbol=symbol,
            side=side,
            qty=executed_qty,
            avg_price=avg_price,
            fee_usd=fee_usd,
            ts=time.time(),
            order_id=str(resp.get("orderId", "live-order")),
            source="binance-live",
        )

    def buy(self, symbol: str, usd_size: float, market_price: float) -> TradeFill:
        resp = self._signed_request(
            "POST",
            "/api/v3/order",
            {"symbol": symbol, "side": "BUY", "type": "MARKET", "quoteOrderQty": f"{usd_size:.8f}"},
        )
        return self._parse_fill(symbol, "BUY", resp)

    def sell(self, symbol: str, qty: float, market_price: float) -> TradeFill:
        qty_str = f"{qty:.8f}"
        resp = self._signed_request(
            "POST",
            "/api/v3/order",
            {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_str},
        )
        return self._parse_fill(symbol, "SELL", resp)


class BotApp:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = MarketClient()
        self.portfolio = PortfolioEngine(cfg.budget_usd)
        self.console = Console()
        self.logs: List[str] = []
        self.last_signals: List[MarketSignal] = []
        self.tick = 0
        self.kill_switch = False
        self.logs_dir = Path("logs")
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        if cfg.mode == "live":
            self.executor = BinanceLiveExecutionAdapter(cfg, self.client)
        else:
            self.executor = PaperExecutionAdapter(cfg)

    def log(self, message: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        self.logs.append(line)
        self.logs = self.logs[-30:]

    def event(self, event_type: str, payload: dict):
        row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event_type, "payload": payload}
        with open(self.logs_dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def discover_universe(self) -> List[Tuple[str, float, float]]:
        tickers = self.client.get_ticker_24h()
        candidates: List[Tuple[str, float, float]] = []

        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith(self.cfg.quote_asset):
                continue
            if any(symbol.endswith(sfx) for sfx in EXCLUDED_SUFFIXES):
                continue

            base = symbol[: -len(self.cfg.quote_asset)]
            if any(k in base for k in STABLE_KEYWORDS):
                continue

            try:
                quote_volume = float(t.get("quoteVolume", 0.0))
                last_price = float(t.get("lastPrice", 0.0))
            except (TypeError, ValueError):
                continue

            if quote_volume < self.cfg.min_quote_volume_usd or last_price <= 0:
                continue
            candidates.append((symbol, quote_volume, last_price))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[: self.cfg.top_n_markets]

    def scan_markets(self) -> List[MarketSignal]:
        universe = self.discover_universe()
        signals: List[MarketSignal] = []

        for symbol, qv, px in universe:
            try:
                signals.append(Strategy.score(symbol, self.client, qv, px))
            except Exception as exc:
                self.log(f"scan error {symbol}: {exc}")

        signals.sort(key=lambda s: (s.score * s.confidence), reverse=True)
        return signals[: self.cfg.candidates_per_cycle]

    def apply_risk_controls(self, prices: Dict[str, float]):
        now = time.time()
        for symbol, pos in list(self.portfolio.positions.items()):
            mark = prices.get(symbol)
            if not mark:
                continue

            move = (mark - pos.entry_price) / pos.entry_price
            age_mins = (now - pos.opened_at) / 60

            if move <= -self.cfg.stop_loss_pct:
                self.execute_exit(symbol, pos.qty, mark, "stop_loss")
                continue
            if move >= self.cfg.take_profit_pct:
                self.execute_exit(symbol, pos.qty, mark, "take_profit")
                continue
            if age_mins >= self.cfg.max_position_age_minutes:
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

    def check_kill_switch(self, equity: float) -> bool:
        self.portfolio.update_peak(equity)
        dd = 0.0
        if self.portfolio.peak_equity > 0:
            dd = (self.portfolio.peak_equity - equity) / self.portfolio.peak_equity
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
                    "qty": p.qty,
                    "entry_price": p.entry_price,
                    "entry_fee_usd": p.entry_fee_usd,
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
        prices = {s.symbol: s.price for s in self.last_signals}

        self.apply_risk_controls(prices)

        equity = self.portfolio.equity(prices)
        if self.check_kill_switch(equity):
            for symbol, pos in list(self.portfolio.positions.items()):
                mark = prices.get(symbol, pos.entry_price)
                self.execute_exit(symbol, pos.qty, mark, "kill_switch")
            self.write_cycle_report(prices)
            return

        if not self.kill_switch:
            for signal in self.last_signals:
                if len(self.portfolio.positions) >= self.cfg.max_positions:
                    break

                if signal.score >= self.cfg.buy_threshold and signal.confidence >= 0.60:
                    if signal.symbol in self.portfolio.positions:
                        self.log(f"hold {signal.symbol}: already open")
                        continue
                    if self.portfolio.in_cooldown(signal.symbol):
                        self.log(f"hold {signal.symbol}: in cooldown")
                        continue
                    usd_size = self.portfolio.cash * self.cfg.risk_per_trade
                    self.execute_entry(signal.symbol, usd_size, signal.price, "signal_buy")
                elif signal.score <= self.cfg.sell_threshold:
                    pos = self.portfolio.positions.get(signal.symbol)
                    if pos:
                        self.execute_exit(signal.symbol, pos.qty, signal.price, "signal_reversal")
                    else:
                        self.log(f"hold {signal.symbol}: sell signal without position")
                else:
                    self.log(f"hold {signal.symbol}: score={signal.score:+.3%} conf={signal.confidence:.2f}")

        equity = self.portfolio.equity(prices)
        self.log(f"equity=${equity:.2f} cash=${self.portfolio.cash:.2f} realized={self.portfolio.realized_pnl:+.2f}")
        self.write_cycle_report(prices)

    def render(self):
        layout = Layout()
        layout.split_column(Layout(name="header", size=3), Layout(name="body"), Layout(name="footer", size=11))
        layout["body"].split_row(Layout(name="signals"), Layout(name="positions"))

        header = Text(
            f"AI Trader OS ({self.cfg.mode.upper()}) | tick={self.tick} | universe({self.cfg.top_n_markets}) -> candidates({self.cfg.candidates_per_cycle})",
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
        pos_table.add_column("Qty", justify="right")
        pos_table.add_column("Entry", justify="right")
        pos_table.add_column("Mark", justify="right")
        pos_table.add_column("U-PnL", justify="right")
        pos_table.add_column("Age(m)", justify="right")

        prices = {s.symbol: s.price for s in self.last_signals}
        now = time.time()
        for pos in self.portfolio.positions.values():
            mark = prices.get(pos.symbol, pos.entry_price)
            pnl = (mark - pos.entry_price) * pos.qty - pos.entry_fee_usd
            age = (now - pos.opened_at) / 60
            style = "green" if pnl >= 0 else "red"
            pos_table.add_row(
                pos.symbol,
                f"{pos.qty:.6f}",
                f"{pos.entry_price:.6f}",
                f"{mark:.6f}",
                f"[{style}]{pnl:+.2f}[/{style}]",
                f"{age:.1f}",
            )
        if not self.portfolio.positions:
            pos_table.add_row("-", "-", "-", "-", "-", "-")

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
        self.log("discovery mode: dynamic USDT universe")
        self.log(f"execution mode: {self.cfg.mode}")

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


def load_config() -> Config:
    load_dotenv()
    return Config(
        mode=os.getenv("BOT_MODE", "paper").lower(),
        budget_usd=parse_float("BUDGET_USD", 50.0),
        scan_interval=parse_int("SCAN_INTERVAL", 20),
        risk_per_trade=parse_float("RISK_PER_TRADE", 0.15),
        max_positions=parse_int("MAX_POSITIONS", 4),
        buy_threshold=parse_float("BUY_THRESHOLD", 0.008),
        sell_threshold=parse_float("SELL_THRESHOLD", -0.006),
        top_n_markets=parse_int("TOP_N_MARKETS", 25),
        candidates_per_cycle=parse_int("CANDIDATES_PER_CYCLE", 6),
        min_quote_volume_usd=parse_float("MIN_QUOTE_VOLUME_USD", 5000000),
        stop_loss_pct=parse_float("STOP_LOSS_PCT", 0.025),
        take_profit_pct=parse_float("TAKE_PROFIT_PCT", 0.045),
        max_position_age_minutes=parse_int("MAX_POSITION_AGE_MINUTES", 240),
        max_daily_drawdown_pct=parse_float("MAX_DAILY_DRAWDOWN_PCT", 0.08),
        cooldown_minutes=parse_int("COOLDOWN_MINUTES", 20),
        quote_asset=os.getenv("QUOTE_ASSET", "USDT").upper(),
        paper_fee_bps=parse_float("PAPER_FEE_BPS", 10),
        paper_slippage_bps=parse_float("PAPER_SLIPPAGE_BPS", 5),
        live_trading_ack=os.getenv("LIVE_TRADING_ACK", ""),
        exchange_api_key=os.getenv("EXCHANGE_API_KEY", ""),
        exchange_api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
    )


if __name__ == "__main__":
    cfg = load_config()
    app = BotApp(cfg)
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nShutting down bot.")
