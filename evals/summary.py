"""Print one line per eval session: time, turns, tool calls, tokens and cost.

python3 evals/summary.py [SESSION FILES]   # defaults to evals/sessions/*.jsonl
"""

import json
import sys
from datetime import datetime
from pathlib import Path

paths = [Path(p) for p in sys.argv[1:]] or sorted(
    (Path(__file__).parent / "sessions").glob("*.jsonl")
)
rows = [
    [
        "session",
        "model",
        "time_s",
        "turns",
        "calls",
        "errors",
        "input",
        "output",
        "cache_read",
        "cost_usd",
        "file",
    ]
]
for path in paths:
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    name = next((e["name"] for e in entries if e["type"] == "session_info"), "?")
    messages = [(e, e["message"]) for e in entries if e["type"] == "message"]
    replies = [(e, m) for e, m in messages if m["role"] == "assistant"]
    if not replies:
        continue
    usage = [m["usage"] for _, m in replies]
    start = next(m["timestamp"] for _, m in messages if m["role"] == "user") / 1000
    end = datetime.fromisoformat(replies[-1][0]["timestamp"]).timestamp()
    row = [
        name,
        replies[-1][1]["model"],
        round(end - start),
        len(replies),
        sum(c["type"] == "toolCall" for _, m in replies for c in m["content"]),
        sum(m["role"] == "toolResult" and bool(m.get("isError")) for _, m in messages),
        *(sum(u.get(k, 0) for u in usage) for k in ("input", "output", "cacheRead")),
        round(sum(u.get("cost", {}).get("total", 0) for u in usage), 4),
        path.name,
    ]
    rows.append([str(v) for v in row])
widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
for r in rows:
    print("  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)).rstrip())  # noqa: T201
