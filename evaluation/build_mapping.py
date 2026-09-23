#!/usr/bin/env python3
"""
Build the CWC routing mapping document.

    File | CBWCHC Manual Routed | AI Routed Folder Name | AI Generated FileName

One row per PDF in the dataset, so CWC can read their own filing decision and
the pipeline's side by side.

────────────────────────────────────────────────────────────────────────────────
WHERE THE MANUAL COLUMN COMES FROM - and where it does not

"Sample for Avenir Digital" IS CWC's manual answer: the folder a document sits
in is the folder a person put it in. For those 36 documents the manual column
fills itself and the comparison is real.

The documents in the fax-line folders (212-334-6887_Walker/ and the rest) are a
different set. Not one of them shares a SHA-256, a byte length or a page count
with anything in the Avenir tree, and the dataset ships no spreadsheet pairing
them up. So CWC's manual routing for those files is not in what they shared, and
this script will not invent it: their manual column is emitted as NEEDS CWC
INPUT for a person to fill in.

Guessing there - matching on a filename, or assuming the AI answer is also the
manual one - would produce a document that looks like an agreement rate and is
actually the pipeline agreeing with itself.
────────────────────────────────────────────────────────────────────────────────

Usage:
    python evaluation/build_mapping.py
    python evaluation/build_mapping.py --dataset "C:/Users/ABC/Downloads/CWC_DATASET_PATH"
    python evaluation/build_mapping.py --only-manual     # just the 36 comparable rows
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

AI_BASE = os.environ.get("CWC_AI_BASE", "http://127.0.0.1:5002")
MANUAL_ROOT = "Sample for Avenir Digital"
NEEDS_INPUT = "NEEDS CWC INPUT"


# ── Discovery ─────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def discover(dataset: Path) -> list[dict]:
    """
    Every PDF in the dataset, tagged with where its manual answer comes from.

    The relative path is the identifier, not the basename: six different files
    are called "Sample 1.pdf", and collapsing them would silently merge rows for
    documents that arrived on different fax lines.
    """
    rows: list[dict] = []
    for path in sorted(dataset.rglob("*.pdf")):
        rel = path.relative_to(dataset).as_posix()
        parts = rel.split("/")

        if parts[0] == MANUAL_ROOT:
            # The folder IS the manual decision.
            manual = parts[1] if len(parts) > 2 else NEEDS_INPUT
            source = "avenir"
            fax_line = None
        elif len(parts) > 1:
            manual = NEEDS_INPUT
            source = "fax-line"
            fax_line = parts[0]
        else:
            manual = NEEDS_INPUT
            source = "inbound-root"
            fax_line = None

        rows.append({
            "file": rel,
            "manual_folder": manual,
            "source": source,
            "fax_line": fax_line,
            "abs_path": str(path),
            "sha256": _sha256(path),
        })
    return rows


# ── Classification ────────────────────────────────────────────────────────────

_FAX_RE = re.compile(r"\(?(\d{3})\)?[-. ]?(\d{3})[-. ]?(\d{4})")


def _sender_from(row: dict) -> str | None:
    """
    The fax-line folder name carries the number the fax arrived ON, not the
    number it came FROM, so it is deliberately not used as sender_fax_number.
    Only a number in the filename itself is a sender.
    """
    m = _FAX_RE.search(Path(row["file"]).name)
    return f"+1{m.group(1)}{m.group(2)}{m.group(3)}" if m else None


def classify(row: dict, retries: int = 2) -> dict:
    payload = {
        "file_path": row["abs_path"],
        "tracking_id": f"map-{row['sha256'][:12]}",
        "sender_fax_number": _sender_from(row),
        "received_at": None,
    }
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            t0 = time.perf_counter()
            r = requests.post(f"{AI_BASE}/classify", json=payload, timeout=float(os.environ.get("CWC_EVAL_CLASSIFY_TIMEOUT", "600")))
            r.raise_for_status()
            out = r.json()
            out["_latency_ms"] = int((time.perf_counter() - t0) * 1000)
            return out
        except Exception as exc:              # noqa: BLE001 - reported per row
            last = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    return {"_error": str(last)}


# ── Output ────────────────────────────────────────────────────────────────────

COLUMNS = [
    "File",
    "CBWCHC Manual Routed",
    "AI Routed Folder Name",
    "AI Generated FileName",
    "Match",
    "Confidence",
    "Held for review",
    "Fax line",
    "Category",
    "Subtype",
]


def to_row(row: dict, result: dict) -> dict:
    if "_error" in result:
        return {
            "File": row["file"],
            "CBWCHC Manual Routed": row["manual_folder"],
            "AI Routed Folder Name": "ERROR",
            "AI Generated FileName": result["_error"][:120],
            "Match": "",
            "Confidence": "",
            "Held for review": "",
            "Fax line": row["fax_line"] or "",
            "Category": "",
            "Subtype": "",
        }

    ai_folder = result.get("suggestedFolder") or ""
    ai_name = result.get("suggestedFileName") or ""
    conf = result.get("calibratedConfidence")
    manual = row["manual_folder"]

    # Only a row with both answers can agree or disagree. Anything else is
    # blank, so a reader never mistakes "not comparable" for "agreed".
    if manual == NEEDS_INPUT:
        match = ""
    else:
        match = "YES" if manual.strip().lower() == ai_folder.strip().lower() else "NO"

    return {
        "File": row["file"],
        "CBWCHC Manual Routed": manual,
        "AI Routed Folder Name": ai_folder,
        "AI Generated FileName": ai_name,
        "Match": match,
        "Confidence": f"{conf * 100:.0f}%" if isinstance(conf, (int, float)) else "",
        "Held for review": "YES" if result.get("forceManualReview") else "",
        "Fax line": row["fax_line"] or "",
        "Category": result.get("documentCategory") or "",
        "Subtype": result.get("documentSubtype") or "",
    }


def write_csv(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def write_xlsx(rows: list[dict], path: Path) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return False

    wb = Workbook()
    ws = wb.active
    ws.title = "Routing mapping"

    head_fill = PatternFill("solid", fgColor="0B1A20")
    head_font = Font(color="FFFFFF", bold=True, size=10)
    ws.append(COLUMNS)
    for c in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font = head_fill, head_font
        cell.alignment = Alignment(vertical="center")

    no_fill = PatternFill("solid", fgColor="FBE9E7")
    pending_fill = PatternFill("solid", fgColor="FBF0DC")
    for r in rows:
        ws.append([r[c] for c in COLUMNS])
        i = ws.max_row
        if r["Match"] == "NO":
            for c in range(1, len(COLUMNS) + 1):
                ws.cell(row=i, column=c).fill = no_fill
        elif r["CBWCHC Manual Routed"] == NEEDS_INPUT:
            ws.cell(row=i, column=2).fill = pending_fill

    widths = [46, 24, 24, 42, 8, 11, 14, 20, 22, 24]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    wb.save(path)
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Build the CWC routing mapping document")
    ap.add_argument("--dataset", default=os.environ.get(
        "CWC_DATASET_PATH",
        str(Path.home() / "Downloads" / "CWC_DATASET_PATH")))
    ap.add_argument("--out", default="evaluation/reports")
    ap.add_argument("--only-manual", action="store_true",
                    help="Only the documents CWC filed by hand, where a comparison is possible")
    args = ap.parse_args()

    dataset = Path(args.dataset)
    if not dataset.is_dir():
        print(f"Dataset not found: {dataset}", file=sys.stderr)
        return 1

    try:
        h = requests.get(f"{AI_BASE}/health", timeout=5)
        h.raise_for_status()
        model = str(h.json().get("model") or "")
        print(f"AI service model: {model or 'unknown'}")
    except Exception as exc:                  # noqa: BLE001
        print(f"AI service not reachable at {AI_BASE}: {exc}", file=sys.stderr)
        print("Start it first:  cd cwc-ai-service; .\\venv\\Scripts\\python.exe server.py", file=sys.stderr)
        return 1

    rows = discover(dataset)
    if args.only_manual:
        rows = [r for r in rows if r["source"] == "avenir"]

    comparable = sum(1 for r in rows if r["manual_folder"] != NEEDS_INPUT)
    print(f"{len(rows)} documents · {comparable} with a manual answer · "
          f"{len(rows) - comparable} needing CWC input\n")

    out_rows: list[dict] = []
    for i, row in enumerate(rows, start=1):
        print(f"  [{i:>2}/{len(rows)}] {row['file']}", flush=True)
        out_rows.append(to_row(row, classify(row)))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    # The model goes in the file name so a Qwen mapping never gets mistaken
    # for (or compared as) the Claude one.
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", model) if model else ""
    stamp = time.strftime("%Y%m%d-%H%M") + (f"-{slug}" if slug else "")

    csv_path = out_dir / f"routing-mapping-{stamp}.csv"
    write_csv(out_rows, csv_path)
    print(f"\nCSV   {csv_path}")

    xlsx_path = out_dir / f"routing-mapping-{stamp}.xlsx"
    if write_xlsx(out_rows, xlsx_path):
        print(f"XLSX  {xlsx_path}")
    else:
        print("XLSX  skipped (pip install openpyxl)")

    json_path = out_dir / f"routing-mapping-{stamp}.json"
    json_path.write_text(json.dumps(out_rows, indent=2), encoding="utf-8")
    print(f"JSON  {json_path}")

    scored = [r for r in out_rows if r["Match"] in ("YES", "NO")]
    agreed = sum(1 for r in scored if r["Match"] == "YES")
    held = sum(1 for r in out_rows if r["Held for review"] == "YES")
    errors = sum(1 for r in out_rows if r["AI Routed Folder Name"] == "ERROR")

    print("\n── Summary ─────────────────────────────────────────────")
    if scored:
        print(f"  Agreement with CWC : {agreed}/{len(scored)} ({agreed / len(scored) * 100:.0f}%)")
    print(f"  Held for review    : {held}/{len(out_rows)}")
    if errors:
        print(f"  Errors             : {errors}")
    pending = sum(1 for r in out_rows if r["CBWCHC Manual Routed"] == NEEDS_INPUT)
    if pending:
        print(f"  Awaiting CWC       : {pending} rows - manual column left blank on purpose")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
