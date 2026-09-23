"""
Phase 6 end-to-end demo:
1. Check existing faxes + routing suggestions
2. Confirm each suggestion; override one with a reason
3. Show storage/routed tree and psql routing_decisions rows
"""
import requests
import json
import subprocess
import os

BASE = "http://localhost:8080/api"

def main():
    # ── Step 1: fetch faxes ────────────────────────────────────────────────────
    resp = requests.get(f"{BASE}/faxes?size=50")
    data = resp.json()
    faxes = data["content"]
    print(f"Total faxes: {data['totalElements']}")
    print()

    classified = [f for f in faxes if f.get("processingStatus") == "CLASSIFIED"]
    print(f"Classified faxes: {len(classified)}")
    print()

    # Print current state
    print(f"{'File':<45} {'Category':<22} {'Band':<6} {'Suggested Folder':<22} {'Route Status'}")
    print("-" * 130)
    for f in faxes:
        name = f["originalFileName"][:44]
        cat  = (f.get("category") or "—")[:21]
        band = (f.get("confidenceBand") or "—")[:5]
        sfol = (f.get("suggestedFolder") or "—")[:21]
        rs   = (f.get("routingStatus") or "—")[:14]
        print(f"{name:<45} {cat:<22} {band:<6} {sfol:<22} {rs}")
    print()

    # ── Step 2: confirm each; override one ────────────────────────────────────
    OVERRIDE_FILE = "(212)497-8948"  # override Healthfirst to Manual-Review for demo

    for f in classified:
        tid  = f["trackingId"]
        name = f["originalFileName"]
        suggested = f.get("suggestedFolder")
        rs        = f.get("routingStatus")

        if rs in ("CONFIRMED", "OVERRIDDEN", "FAILED"):
            print(f"[SKIP] {name[:50]} already {rs}")
            continue

        if not suggested:
            print(f"[SKIP] {name[:50]} — no suggestion yet")
            continue

        # Override one fax for demo
        is_override = OVERRIDE_FILE in name

        if is_override:
            payload = {
                "folder":         "Manual-Review",
                "overrideReason": "Demo override: routing to manual review for CWC review",
                "decidedBy":      "demo-user",
            }
            print(f"[OVERRIDE] {name[:50]} -> Manual-Review (reason provided)")
        else:
            payload = {
                "folder":    suggested,
                "decidedBy": "demo-user",
            }
            print(f"[CONFIRM] {name[:50]} -> {suggested}")

        r = requests.post(f"{BASE}/faxes/{tid}/confirm", json=payload)
        if r.status_code == 200:
            result = r.json()
            print(f"          status={result['status']} override={result['isOverride']}")
        else:
            print(f"          ERROR {r.status_code}: {r.text[:200]}")
        print()

    # ── Step 3: show storage/routed tree ──────────────────────────────────────
    print()
    print("=" * 60)
    print("storage/routed tree:")
    print("=" * 60)
    storage_root = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "storage", "routed")
    )
    if os.path.exists(storage_root):
        for root, dirs, files in os.walk(storage_root):
            level = root.replace(storage_root, "").count(os.sep)
            indent = "  " * level
            folder_name = os.path.basename(root) if root != storage_root else "routed"
            print(f"{indent}{folder_name}/")
            sub_indent = "  " * (level + 1)
            for file in files:
                size = os.path.getsize(os.path.join(root, file))
                print(f"{sub_indent}{file}  ({size:,} bytes)")
    else:
        print(f"  (path does not exist: {storage_root})")
    print()

    # ── Step 4: psql routing_decisions rows ───────────────────────────────────
    print("=" * 60)
    print("routing_decisions rows from psql:")
    print("=" * 60)
    psql = r"C:\Program Files\PostgreSQL\16\bin\psql.exe"
    query = (
        "SELECT rd.id, fd.original_file_name, rd.suggested_folder, rd.final_folder, "
        "rd.routing_status, rd.decided_by, rd.override_reason, rd.rule_matched "
        "FROM cwc.routing_decisions rd "
        "JOIN cwc.fax_documents fd ON fd.id = rd.fax_document_id "
        "ORDER BY rd.decided_at DESC;"
    )
    result = subprocess.run(
        [psql, "-U", "cwc", "-d", "cwc", "-c", query],
        capture_output=True, text=True,
        env={**os.environ, "PGPASSWORD": "cwc_dev_password"}
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr[:300])


if __name__ == "__main__":
    main()
