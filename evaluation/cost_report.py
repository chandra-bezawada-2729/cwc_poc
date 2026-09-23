#!/usr/bin/env python3
"""
Per-fax AI cost, read from the usage ledger the AI service writes.

Every classify and extract call appends one line to
cwc-ai-service/logs/ai_usage.jsonl with its exact token counts. This groups
those lines by tracking ID, so each fax gets one row: what classification cost,
what extraction cost, and the total.

It cannot price anything that ran before the ledger existed — the console total
for those days has no per-fax breakdown and nothing here can reconstruct one.

Usage:
    python evaluation/cost_report.py
    python evaluation/cost_report.py --since 2026-09-21
    python evaluation/cost_report.py --ledger path/to/ai_usage.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

DEFAULT_LEDGER = Path(__file__).resolve().parent.parent / "cwc-ai-service" / "logs" / "ai_usage.jsonl"


def load(ledger: Path, since: str | None) -> list[dict]:
    rows = []
    with open(ledger, encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(f"  skipping malformed line {n}", file=sys.stderr)
                continue
            if since and (row.get("at") or "") < since:
                continue
            rows.append(row)
    return rows


def per_fax(rows: list[dict]) -> list[dict]:
    """
    One row per tracking ID.

    A fax re-run under the same tracking ID (Re-Extract, say) contributes every
    run: that money was spent, and averaging it away would understate the cost
    of the documents that needed a second pass.
    """
    by = defaultdict(lambda: {
        "file": None, "category": None,
        "classify_in": 0, "classify_out": 0, "classify_usd": 0.0, "classify_calls": 0,
        "extract_in": 0, "extract_out": 0, "extract_usd": 0.0, "extract_calls": 0,
        "repairs": 0, "unknown_cost": False,
        "classify_model": set(), "extract_model": set(), "cache_read": 0,
    })
    for r in rows:
        tid = r.get("trackingId") or "unknown"
        d = by[tid]
        d["file"] = d["file"] or r.get("fileName")
        d["category"] = d["category"] or r.get("category")
        stage = "extract" if r.get("stage") == "extract" else "classify"
        d[f"{stage}_in"]    += r.get("inputTokens", 0)
        d[f"{stage}_out"]   += r.get("outputTokens", 0)
        d[f"{stage}_calls"] += len(r.get("calls", []))
        if r.get("costUsd") is None:
            d["unknown_cost"] = True
        else:
            d[f"{stage}_usd"] += r["costUsd"]
        d[f"{stage}_model"].update(c.get("model") for c in r.get("calls", []) if c.get("model"))
        d["cache_read"] += r.get("cacheReadTokens", 0) or 0
        d["repairs"] += sum(1 for c in r.get("calls", []) if c.get("call") == "repair")

    out = []
    for tid, d in by.items():
        total_in  = d["classify_in"] + d["extract_in"]
        total_out = d["classify_out"] + d["extract_out"]
        out.append({
            "Tracking ID":        tid,
            "File":               d["file"] or "",
            "Category":           d["category"] or "",
            "Classify model":     ", ".join(sorted(d["classify_model"])),
            "Extract model":      ", ".join(sorted(d["extract_model"])),
            "API calls":          d["classify_calls"] + d["extract_calls"],
            "Repair retries":     d["repairs"],
            "Classify input":     d["classify_in"],
            "Classify output":    d["classify_out"],
            "Classify USD":       round(d["classify_usd"], 4),
            "Extract input":      d["extract_in"],
            "Extract output":     d["extract_out"],
            "Extract USD":        round(d["extract_usd"], 4),
            "Total input":        total_in,
            "Total output":       total_out,
            "Cache read tokens":  d["cache_read"],
            "Total USD":          None if d["unknown_cost"] else round(d["classify_usd"] + d["extract_usd"], 4),
        })
    out.sort(key=lambda r: -(r["Total USD"] or 0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-fax AI cost from the usage ledger")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--since", help="ISO date, e.g. 2026-09-21")
    ap.add_argument("--out", default="evaluation/reports")
    args = ap.parse_args()

    ledger = Path(args.ledger)
    if not ledger.exists():
        print(f"No ledger at {ledger}", file=sys.stderr)
        print("It is created by the AI service on its first classify call after the "
              "usage tracker was added. Process a fax, then run this again.", file=sys.stderr)
        return 1

    rows = load(ledger, args.since)
    if not rows:
        print("Ledger has no rows in range.")
        return 0

    faxes = per_fax(rows)
    priced = [f for f in faxes if f["Total USD"] is not None]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ai-cost-{time.strftime('%Y%m%d-%H%M')}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(faxes[0].keys()))
        w.writeheader()
        w.writerows(faxes)

    # ── Console summary ────────────────────────────────────────────────────
    print(f"\n{'File':38s} {'Calls':>5s} {'In':>8s} {'Out':>6s} {'USD':>8s}")
    print("-" * 70)
    for fx in faxes:
        usd = f"${fx['Total USD']:.4f}" if fx["Total USD"] is not None else "unknown"
        print(f"{(fx['File'] or fx['Tracking ID'])[:38]:38s} "
              f"{fx['API calls']:>5d} {fx['Total input']:>8,} {fx['Total output']:>6,} {usd:>8s}")

    calls  = sum(len(r.get("calls", [])) for r in rows)
    tin    = sum(r.get("inputTokens", 0) for r in rows)
    tout   = sum(r.get("outputTokens", 0) for r in rows)
    spend  = sum(f["Total USD"] for f in priced)
    costs  = [f["Total USD"] for f in priced]

    print("\n── Summary ──────────────────────────────────────────────")
    print(f"  Faxes              : {len(faxes)}")
    print(f"  API calls          : {calls}  ({calls / len(faxes):.2f} per fax)")
    print(f"  Input tokens       : {tin:,}  (avg {tin // len(faxes):,} per fax)")
    print(f"  Output tokens      : {tout:,}  (avg {tout // len(faxes):,} per fax)")
    print(f"  Total spend        : ${spend:.2f}")
    if costs:
        print(f"  Per fax            : mean ${statistics.mean(costs):.4f} · "
              f"median ${statistics.median(costs):.4f} · "
              f"min ${min(costs):.4f} · max ${max(costs):.4f}")
        cls_share = sum(f["Classify USD"] for f in priced) / (spend or 1)
        print(f"  Split              : classify {cls_share:.0%} · extract {1 - cls_share:.0%}")
    repairs = sum(f["Repair retries"] for f in faxes)
    if repairs:
        print(f"  Repair retries     : {repairs} — each is a whole extra call")
    if len(priced) < len(faxes):
        print(f"  Unpriced           : {len(faxes) - len(priced)} fax(es) used a model with no price row")
    print(f"\nCSV  {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
