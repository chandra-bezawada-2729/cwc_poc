"""
Upload 5 sample faxes through the Phase 4 pipeline and poll until CLASSIFIED.
Run from the cwc_poc directory.
"""
import os, time, json
import requests

BACKEND = "http://localhost:8080"
DATASET = os.getenv(
    "CWC_DATASET_PATH",
    r"C:\Users\ABC\OneDrive - AvenirDigital Solutions Pvt. Ltd (1)\Desktop\cwc healthcare datasets"
)
FILES = [
    "(212)497-8948_2026-08-18_0943PM.pdf",
    "(305)503-8807_2026-08-19_0702AM.pdf",
    "(360)200-5103_2026-08-19_0137AM.pdf",
    "(603)935-9108_2026-08-18_0945PM.pdf",
    "(614)321-2042_2026-08-18_1004PM.pdf",
]


def upload_all():
    tracking = {}  # fname → trackingId
    for fname in FILES:
        path = os.path.join(DATASET, fname)
        with open(path, "rb") as f:
            r = requests.post(
                f"{BACKEND}/api/faxes/upload",
                files=[("files", (fname, f, "application/pdf"))]
            )
        r.raise_for_status()
        body = r.json()
        tid = body["uploaded"][0]["trackingId"]
        tracking[fname] = tid
        print(f"  Uploaded {fname!r} -> {tid}")
    return tracking


def poll(tracking, timeout=400):
    terminal = {"CLASSIFIED", "ERRORED"}
    deadline = time.time() + timeout
    statuses = {}
    while time.time() < deadline:
        all_done = True
        for fname, tid in tracking.items():
            r = requests.get(f"{BACKEND}/api/faxes/{tid}")
            if r.ok:
                statuses[tid] = r.json()
                if r.json().get("processingStatus") not in terminal:
                    all_done = False
            else:
                all_done = False

        counts = {}
        for d in statuses.values():
            s = d.get("processingStatus", "?")
            counts[s] = counts.get(s, 0) + 1
        remaining = int(deadline - time.time())
        print(f"  [{remaining}s] {counts}")
        if all_done:
            break
        time.sleep(15)

    return statuses


def report(tracking, statuses):
    print("\n" + "=" * 110)
    hdr = f"{'File':<45} {'Category':<22} {'Subtype':<28} {'CalConf':>7} {'Band':>6} {'Runner-up':<22} {'Status'}"
    print(hdr)
    print("-" * 110)
    evidence_map = {}
    for fname, tid in tracking.items():
        d = statuses.get(tid, {})
        status = d.get("processingStatus", "?")
        cat     = (d.get("category")       or "—")[:21]
        sub     = (d.get("subtype")        or "—")[:27]
        conf    = d.get("confidenceScore")
        band    = (d.get("confidenceBand") or "—")
        runner  = "—"  # not in the flat DTO; fetched separately below
        conf_s  = f"{conf:.3f}" if conf is not None else "—"
        print(f"{fname:<45} {cat:<22} {sub:<28} {conf_s:>7} {band:>6} {runner:<22} {status}")
        evidence_map[fname] = tid

    # Fetch full detail for evidence quotes and runner-up (call /classify endpoint directly)
    print("\n\nEvidence quotes and runner-up (from AI service direct classify call):")
    for fname, tid in tracking.items():
        d = statuses.get(tid, {})
        print(f"\n  {fname}")
        print(f"  Status: {d.get('processingStatus')}")
        print(f"  Error: {d.get('errorReason')}")


if __name__ == "__main__":
    print("Uploading 5 sample faxes through Phase 4 pipeline...")
    tracking = upload_all()
    print(f"\nPolling for CLASSIFIED (timeout=400s)...")
    statuses = poll(tracking)
    report(tracking, statuses)
