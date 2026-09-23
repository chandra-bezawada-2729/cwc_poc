"""
Call /classify directly on the AI service for all 5 sample files.
Uses the actual stored files from storage/incoming.
Reports the full Phase 4 classification results.
"""
import os, json, time
import requests

AI_SERVICE = "http://localhost:5002"

STORAGE = r"C:\Users\ABC\OneDrive - AvenirDigital Solutions Pvt. Ltd (1)\Desktop\cwc_poc\storage\incoming"

# The 5 files from the most recent upload (Phase 4 run)
DOCS = [
    {
        "file":    "(212)497-8948_2026-08-18_0943PM.pdf",
        "tid":     "f71c5e10-c204-4688-85e6-da65934ea1a7",
        "sender":  "+12124978948",
        "recv_at": "2026-08-19T01:43:00Z",
    },
    {
        "file":    "(305)503-8807_2026-08-19_0702AM.pdf",
        "tid":     "036cc6d9-3d09-49ca-88cf-ee3135db1637",
        "sender":  "+13055038807",
        "recv_at": "2026-08-19T11:02:00Z",
    },
    {
        "file":    "(360)200-5103_2026-08-19_0137AM.pdf",
        "tid":     "e731ca53-e6a6-4e5e-8ea2-7adc3cfcf849",
        "sender":  "+13602005103",
        "recv_at": "2026-08-19T05:37:00Z",
    },
    {
        "file":    "(603)935-9108_2026-08-18_0945PM.pdf",
        "tid":     "692706ce-4572-43cd-88b6-11dada7a1eeb",
        "sender":  "+16039359108",
        "recv_at": "2026-08-19T01:45:00Z",
    },
    {
        "file":    "(614)321-2042_2026-08-18_1004PM.pdf",
        "tid":     "4b5b2ba1-63b0-4d70-bae2-8565f880a8fd",
        "sender":  "+16143212042",
        "recv_at": "2026-08-19T02:04:00Z",
    },
]


def find_stored_path(fname_suffix):
    for f in os.listdir(STORAGE):
        if fname_suffix in f and f.endswith(".pdf"):
            return os.path.join(STORAGE, f)
    return None


def classify_one(doc):
    path = find_stored_path(doc["file"])
    if not path:
        return {"error": f"File not found for {doc['file']}"}

    payload = {
        "tracking_id":       doc["tid"],
        "file_path":         path,
        "sender_fax_number": doc["sender"],
        "received_at":       doc["recv_at"],
    }
    t0 = time.time()
    r = requests.post(f"{AI_SERVICE}/classify", json=payload, timeout=300)
    elapsed = int((time.time() - t0) * 1000)
    if not r.ok:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    result = r.json()
    result["_wall_ms"] = elapsed
    return result


print("Calling /classify on the AI service directly for all 5 files...\n")
results = {}
for doc in DOCS:
    print(f"  [{doc['file']}] calling... ", end="", flush=True)
    res = classify_one(doc)
    results[doc["file"]] = res
    cat = res.get("documentCategory", res.get("error", "ERROR"))
    conf = res.get("calibratedConfidence")
    ms = res.get("_wall_ms", res.get("latencyMs", 0))
    print(f"{cat}  conf={conf}  {ms}ms")

# ── Summary table ──────────────────────────────────────────────────────────────
print("\n" + "=" * 120)
print(f"{'File':<45} {'Category':<22} {'Subtype':<30} {'ModelConf':>9} {'CalConf':>7} {'Band':>6} {'Runner-up':<22} {'Latency'}")
print("-" * 120)
for doc in DOCS:
    r = results[doc["file"]]
    fname = doc["file"][:44]
    cat   = (r.get("documentCategory") or "ERROR")[:21]
    sub   = (r.get("documentSubtype")  or "—")[:29]
    mc    = r.get("modelConfidence")
    cc    = r.get("calibratedConfidence")
    band  = r.get("confidenceBand", "—")
    run   = (r.get("runnerUpCategory") or "—")[:21]
    ms    = r.get("_wall_ms", 0)
    mc_s  = f"{mc:.2f}" if mc else "—"
    cc_s  = f"{cc:.3f}" if cc else "—"
    print(f"{fname:<45} {cat:<22} {sub:<30} {mc_s:>9} {cc_s:>7} {band:>6} {run:<22} {ms}ms")

# ── Evidence quotes ────────────────────────────────────────────────────────────
print("\n\nEvidence quotes (verbatim from body text, from page images):")
print("=" * 80)
for doc in DOCS:
    r = results[doc["file"]]
    print(f"\n{doc['file']}")
    print(f"  Category    : {r.get('documentCategory')} / {r.get('documentSubtype')}")
    print(f"  Reason      : {r.get('reason', '')}")
    print(f"  Action      : {'YES' if r.get('actionRequired') else 'no'}  Deadline: {r.get('responseDeadline')}")
    print(f"  Sender org  : {r.get('senderOrganization')}")
    print(f"  Form present: {r.get('containsFillableForm')}")
    print(f"  Manual review: {r.get('forceManualReview')}  Override: {r.get('overrideReason')}")
    ev = r.get("evidence") or []
    for q in ev:
        print(f"    - {q!r}")

# ── Save full JSON ─────────────────────────────────────────────────────────────
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase4_classify_results.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"\nFull results saved to {out_path}")
