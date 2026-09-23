"""
Phase 5 verification script.
  Part A — Re-classify all 5 real samples (with cwc-classify-v1.1 prompt).
  Part B — Run 4 negative-path fixtures through /classify to prove confidence-gate overrides.
"""
import json
import os
import sys
from pathlib import Path

import requests

BASE_URL = "http://localhost:5002"
CLASSIFY  = f"{BASE_URL}/classify"

# ── helpers ────────────────────────────────────────────────────────────────────

def classify(file_path: str, tracking_id: str, sender: str = None, received: str = None) -> dict:
    payload = {"file_path": file_path, "tracking_id": tracking_id}
    if sender:
        payload["sender_fax_number"] = sender
    if received:
        payload["received_at"] = received
    r = requests.post(CLASSIFY, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()

def fmt_row(label, r):
    cat   = r.get("documentCategory", "—")
    sub   = r.get("documentSubtype", "—")
    mc    = r.get("modelConfidence", 0)
    ru    = r.get("runnerUpCategory", "—")
    ruc   = r.get("runnerUpConfidence", 0)
    lat   = r.get("latency_ms", 0)
    band  = r.get("calibrated_confidence", {})
    cal   = band.get("calibrated_confidence", "?") if isinstance(band, dict) else "?"
    cb    = band.get("confidence_band", "?")      if isinstance(band, dict) else "?"
    fmr   = band.get("force_manual_review", "?")  if isinstance(band, dict) else "?"
    ovr   = band.get("override_reason", "")        if isinstance(band, dict) else ""
    evs   = band.get("evidence_score", "?")        if isinstance(band, dict) else "?"
    margin = round(mc - ruc, 3)
    print(f"\n{'-'*80}")
    print(f"FILE         : {label}")
    print(f"category     : {cat}  /  {sub}")
    print(f"modelConf    : {mc:.2f}   runnerUp: {ru} ({ruc:.2f})   margin={margin:.3f}")
    print(f"calibrated   : {cal}   band={cb}   force_review={fmr}   override={ovr!r}")
    print(f"evidence_score: {evs}   latency_ms: {lat}")
    evidence = r.get("evidence", [])
    print("evidence quotes:")
    for q in evidence:
        print(f"   • {q!r}")
    return r

# ══════════════════════════════════════════════════════════════════════════════
# PART A — real samples
# ══════════════════════════════════════════════════════════════════════════════

STORAGE = Path(__file__).parent / "storage" / "incoming"

# Map filename prefix → (E.164, received_at_utc)
SAMPLE_META = {
    "(212)497-8948": ("+12124978948", "2026-08-19T01:43:00Z"),
    "(305)503-8807": ("+13055038807", "2026-08-19T12:02:00Z"),
    "(614)321-2042": ("+16143212042", "2026-08-19T13:45:00Z"),
    "(646)730-2741": ("+16467302741", "2026-08-19T14:20:00Z"),
    "(718)428-2475": ("+17184282475", "2026-08-19T15:00:00Z"),
}

print("\n" + "="*80)
print("PART A -- 5 real samples  (prompt v1.1)")
print("="*80)

part_a_results = []
for pdf in sorted(STORAGE.glob("*.pdf")):
    # find the matching meta by fax-number prefix
    prefix = next((k for k in SAMPLE_META if pdf.name.startswith(k)), None)
    sender, recv = SAMPLE_META.get(prefix, (None, None)) if prefix else (None, None)
    tid = pdf.stem.split("__")[0] if "__" in pdf.stem else pdf.stem
    try:
        r = classify(str(pdf), tid, sender, recv)
        part_a_results.append((pdf.name, r))
        fmt_row(pdf.name, r)
    except Exception as exc:
        print(f"\nERROR on {pdf.name}: {exc}")

# ══════════════════════════════════════════════════════════════════════════════
# PART B — negative-path fixtures
# ══════════════════════════════════════════════════════════════════════════════

FIXTURES_DIR = Path(__file__).parent / "evaluation" / "fixtures" / "negative"

fixtures = [
    ("blank_page.pdf",         "fixture-blank-001",  None, None),
    ("degraded_scan.pdf",      "fixture-degrad-001", None, None),
    ("ambiguous_quality_rx.pdf","fixture-ambig-001", None, None),
    ("prescription_order.pdf", "fixture-rx-001",     None, None),
]

print("\n" + "="*80)
print("PART B -- negative-path fixtures  (confidence-gate override proof)")
print("="*80)

part_b_results = []
for fname, tid, sender, recv in fixtures:
    path = FIXTURES_DIR / fname
    if not path.exists():
        print(f"\nMISSING: {path}")
        continue
    try:
        r = classify(str(path), tid, sender, recv)
        part_b_results.append((fname, r))
        fmt_row(fname, r)
    except Exception as exc:
        print(f"\nERROR on {fname}: {exc}")

# Save raw results
out_file = Path(__file__).parent / "phase5_results.json"
out_file.write_text(
    json.dumps(
        {"part_a": {n: r for n, r in part_a_results},
         "part_b": {n: r for n, r in part_b_results}},
        indent=2,
    ),
    encoding="utf-8",
)
print(f"\n\nFull JSON saved to {out_file}")
