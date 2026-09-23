"""
Upload 5 sample PDFs for Phase 6 demo and poll until all reach CLASSIFIED/ROUTED.
"""
import requests, time, os, sys

BASE = "http://localhost:8080/api"

DATASET = os.environ.get("CWC_DATASET_PATH",
    r"C:\Users\ABC\OneDrive - AvenirDigital Solutions Pvt. Ltd (1)\Desktop\cwc healthcare datasets")

SAMPLES = [
    "(212)497-8948_2026-08-18_0943PM.pdf",
    "(305)503-8807_2026-08-19_0702AM.pdf",
    "(360)200-5103_2026-08-19_0137AM.pdf",
    "(603)935-9108_2026-08-18_0945PM.pdf",
    "(614)321-2042_2026-08-18_1004PM.pdf",
]

def upload():
    print("Uploading 5 sample PDFs...")
    files_payload = []
    for name in SAMPLES:
        path = os.path.join(DATASET, name)
        if not os.path.exists(path):
            print(f"  MISSING: {path}")
            continue
        files_payload.append(("files", (name, open(path, "rb"), "application/pdf")))
    
    resp = requests.post(f"{BASE}/faxes/upload", files=files_payload)
    data = resp.json()
    uploaded = data.get("uploaded", [])
    failed   = data.get("failed", [])
    print(f"  Accepted: {len(uploaded)}, Failed: {len(failed)}")
    for u in uploaded:
        print(f"    {u['trackingId']} <- {u['originalFileName']}")
    return [u["trackingId"] for u in uploaded]

def poll(tracking_ids):
    terminal = {"CLASSIFIED", "ROUTED", "ERRORED"}
    print(f"\nPolling {len(tracking_ids)} faxes until classified...")
    deadline = time.time() + 600   # 10 minute max
    while time.time() < deadline:
        statuses = {}
        for tid in tracking_ids:
            r = requests.get(f"{BASE}/faxes/{tid}")
            d = r.json()
            statuses[tid] = (d["processingStatus"], d.get("category"), d.get("suggestedFolder"))
        
        done = [s for s in statuses.values() if s[0] in terminal]
        print(f"  [{int(time.time()-deadline+600)}s left] {len(done)}/{len(tracking_ids)} done")
        for tid, (st, cat, sf) in statuses.items():
            print(f"    {tid[-8:]}... {st:<14} cat={cat or '—':<22} folder={sf or '—'}")
        
        if all(s[0] in terminal for s in statuses.values()):
            print("\nAll faxes reached terminal status.")
            return statuses
        time.sleep(15)
    
    print("Timeout — some faxes didn't complete.")
    return statuses

if __name__ == "__main__":
    ids = upload()
    if not ids:
        print("No faxes uploaded. Exiting.")
        sys.exit(1)
    poll(ids)
