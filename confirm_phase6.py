"""
Phase 6 end-to-end confirmation demo:
- Confirm 4 suggestions as-is
- Override 1 (PAYER_CARE_GAP / Healthfirst) to Manual-Review with a reason
- Show storage/routed tree and psql routing_decisions rows
"""
import requests, os, subprocess

BASE = "http://localhost:8080/api"

# tracking IDs from the upload above
TRACKING_IDS = {
    "(212)497-8948": "f1e857c6-3077-4bfd-8e32-520466d5db03",   # PAYER_CARE_GAP → override
    "(305)503-8807": "c48b6fa9-d6b8-4adf-b589-b02b24f767ac",   # FOLLOW_UP → confirm
    "(360)200-5103": "463749d1-4d47-4abf-94dd-9c19b8c19849",   # MEDICATION_REVIEW → confirm
    "(603)935-9108": "d746520f-e8c3-4bc8-a604-5883777cb813",   # PHARMACY_REQUEST → confirm
    "(614)321-2042": "dccc0951-879e-482f-9fb1-322f9b3d3fd6",   # PRIOR_AUTHORIZATION → confirm
}

OVERRIDE_KEY = "(212)497-8948"   # Healthfirst fax — demonstrate override

def main():
    print("=" * 70)
    print("PHASE 6 END-TO-END: Confirm routing for 5 faxes (1 override)")
    print("=" * 70)
    print()

    for key, tid in TRACKING_IDS.items():
        detail = requests.get(f"{BASE}/faxes/{tid}").json()
        category = detail.get("category", "—")
        suggested = detail.get("suggestedFolder", "—")
        rs = detail.get("routingStatus", "—")
        band = detail.get("confidenceBand", "—")
        force = detail.get("forceManualReview", False)

        print(f"File : {key}...")
        print(f"  category={category}  band={band}  forceManualReview={force}")
        print(f"  suggestedFolder={suggested}  routingStatus={rs}")

        if rs in ("CONFIRMED", "OVERRIDDEN"):
            print(f"  >> already {rs}, skip")
            print()
            continue

        if key == OVERRIDE_KEY:
            payload = {
                "folder":         "Manual-Review",
                "overrideReason": "Demo override: clinician wants manual review before Care-Gaps filing",
                "decidedBy":      "clinician-01",
            }
            print(f"  >> OVERRIDE to Manual-Review with reason")
        else:
            payload = {
                "folder":    suggested,
                "decidedBy": "clinician-01",
            }
            print(f"  >> CONFIRM to {suggested}")

        r = requests.post(f"{BASE}/faxes/{tid}/confirm", json=payload)
        if r.status_code == 200:
            result = r.json()
            print(f"  >> {result['status']} — path: {result['path']}")
        else:
            print(f"  >> ERROR {r.status_code}: {r.text[:300]}")
        print()

    # ── storage/routed tree ────────────────────────────────────────────────────
    print("=" * 70)
    print("(a) storage/routed tree on disk:")
    print("=" * 70)
    storage_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "storage", "routed"))
    for root_dir, dirs, files in os.walk(storage_root):
        dirs[:] = sorted(dirs)
        level = root_dir.replace(storage_root, "").count(os.sep)
        indent = "  " * level
        folder_name = os.path.basename(root_dir) if root_dir != storage_root else "storage/routed"
        pdfs = [f for f in files if f.endswith(".pdf")]
        if root_dir == storage_root or pdfs or any(f for f in files):
            print(f"{indent}{folder_name}/")
        sub_indent = "  " * (level + 1)
        for f in sorted(files):
            if f.endswith(".pdf"):
                sz = os.path.getsize(os.path.join(root_dir, f))
                print(f"{sub_indent}{f}  ({sz:,} bytes)")
    print()

    # ── psql routing_decisions ─────────────────────────────────────────────────
    print("=" * 70)
    print("(b) routing_decisions rows from psql:")
    print("=" * 70)
    psql = r"C:\Program Files\PostgreSQL\17\bin\psql.exe"
    query = (
        "SELECT fd.original_file_name, rd.suggested_folder, rd.final_folder, "
        "rd.routing_status, rd.decided_by, rd.override_reason, rd.rule_matched "
        "FROM cwc.routing_decisions rd "
        "JOIN cwc.fax_documents fd ON fd.id = rd.fax_document_id "
        "WHERE fd.tracking_id IN ("
        + ",".join(f"'{v}'" for v in TRACKING_IDS.values())
        + ") ORDER BY rd.decided_at DESC;"
    )
    result = subprocess.run(
        [psql, "-U", "cwc", "-d", "cwc", "-c", query],
        capture_output=True, text=True,
        env={**os.environ, "PGPASSWORD": "cwc_dev_password"}
    )
    print(result.stdout)
    if result.returncode != 0:
        print("STDERR:", result.stderr[:300])


if __name__ == "__main__":
    main()
