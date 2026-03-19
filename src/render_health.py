import json
from pathlib import Path


def main():
    path = Path("logs/latest_cycle.json")
    if not path.exists():
        print(json.dumps({"ok": False, "reason": "no_cycle_yet"}))
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "tick": data.get("tick"), "mode": data.get("mode")}))


if __name__ == "__main__":
    main()
