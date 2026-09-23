"""
Phase 5b verification:
  A) senderCallbackFax for all 5 unique real samples (v1.1).
  B) All 5 negative-path fixtures through /classify with fixed confidence_service.
"""
import json
from pathlib import Path
import requests

BASE = "http://localhost:5002"

def classify(file_path, tid):
    r = requests.post(f"{BASE}/classify",
                      json={"file_path": file_path, "tracking_id": tid},
                      timeout=120)
    r.raise_for_status()
    return r.json()

STORAGE  = Path(__file__).parent / "storage" / "incoming"
FIXTURES = Path(__file__).parent / "evaluation" / "fixtures" / "negative"

# ── A: unique real-sample files ───────────────────────────────────────────────
print("=" * 70)
print("PART A -- senderCallbackFax for each unique real sample (v1.1)")
print("=" * 70)

seen = {}
for pdf in sorted(STORAGE.glob("*.pdf")):
    # deduplicate by fax-number prefix in filename
    prefix = None
    for p in ["(212)497-8948","(305)503-8807","(614)321-2042",
              "(360)200-5103","(603)935-9108","(718)428-2475"]:
        if pdf.name.startswith(p):
            prefix = p
            break
    if prefix is None or prefix in seen:
        continue
    seen[prefix] = 1
    try:
        r = classify(str(pdf), "cb-check-" + prefix.replace("(","").replace(")","").replace("-",""))
        cat  = r.get("documentCategory","?")
        cbfx = r.get("senderCallbackFax")
        sfx  = r.get("senderFaxNumber")
        print(f"  {prefix}")
        print(f"    category         : {cat}")
        print(f"    senderFaxNumber  : {sfx}")
        print(f"    senderCallbackFax: {cbfx!r}")
        print()
    except Exception as e:
        print(f"  ERROR on {prefix}: {e}")

# ── B: all 5 fixtures ─────────────────────────────────────────────────────────
print("=" * 70)
print("PART B -- 5 fixtures (fixed UNKNOWN short-circuit + independent band)")
print("=" * 70)

fixtures = [
    ("blank_page.pdf",          "fx-blank"),
    ("degraded_scan.pdf",       "fx-degrad"),
    ("ambiguous_quality_rx.pdf","fx-ambig"),
    ("prescription_order.pdf",  "fx-rx"),
    ("cover_sheet_only.pdf",    "fx-cover"),
]

results = {}
for fname, tid in fixtures:
    path = FIXTURES / fname
    try:
        r = classify(str(path), tid)
        cat  = r.get("documentCategory","?")
        cal  = r.get("calibratedConfidence","?")
        band = r.get("confidenceBand","?")
        fmr  = r.get("forceManualReview","?")
        ovr  = r.get("overrideReason","")
        mc   = r.get("modelConfidence",0)
        ruc  = r.get("runnerUpConfidence",0)
        margin = round(mc - ruc, 3)
        results[fname] = r
        print(f"  {fname}")
        print(f"    category       : {cat}")
        print(f"    calibrated     : {cal}")
        print(f"    band           : {band}")
        print(f"    forceReview    : {fmr}")
        print(f"    overrideReason : {ovr!r}")
        print(f"    margin         : {margin}  (modelConf={mc} runnerUp={ruc})")
        print()
    except Exception as e:
        print(f"  ERROR on {fname}: {e}")

out = Path(__file__).parent / "phase5b_results.json"
out.write_text(json.dumps(results, indent=2), encoding="utf-8")
print(f"Full JSON -> {out}")
