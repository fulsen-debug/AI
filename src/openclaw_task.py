import json
from pathlib import Path


def main():
    report_path = Path("logs/latest_cycle.json")
    if not report_path.exists():
        print(json.dumps({"status": "error", "message": "latest_cycle.json not found"}))
        return

    report = json.loads(report_path.read_text(encoding="utf-8"))
    top = report.get("top_signals", [])
    best = top[0] if top else None

    result = {
        "status": "ok",
        "mode": report.get("mode"),
        "kill_switch": report.get("kill_switch"),
        "equity": report.get("equity"),
        "cash": report.get("cash"),
        "realized_pnl": report.get("realized_pnl"),
        "total_fees": report.get("total_fees"),
        "closed_trades": report.get("closed_trades"),
        "wins": report.get("wins"),
        "losses": report.get("losses"),
        "win_rate": report.get("win_rate"),
        "open_positions": len(report.get("open_positions", [])),
        "best_signal": best,
        "recommended_next_action": (
            "halt_and_review" if report.get("kill_switch") else "continue_scan"
        ),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
