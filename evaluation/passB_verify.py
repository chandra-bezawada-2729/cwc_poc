import json, urllib.request
from pathlib import Path

mapping = json.loads(Path("evaluation/passB_mapping.json").read_text())
data = json.loads(urllib.request.urlopen(
    "http://localhost:8080/api/ingest/ledger?state=FILED").read())
got = {i["sourceFileName"]: i for i in data["items"]}

def norm(s):
    return " ".join((s or "").lower().replace("-", " ").replace("_", " ").split())

folder_ok = name_ok = 0
misses = []
for syn, rel in sorted(mapping.items(), key=lambda kv: kv[1]):
    cwc_folder, cwc_name = rel.rsplit("/", 1)
    r = got.get(syn, {})
    f, n = r.get("routedFolder"), r.get("routedFileName")
    fm, nm = (f == cwc_folder), (norm(n) == norm(cwc_name))
    folder_ok += fm; name_ok += nm
    if not (fm and nm):
        misses.append(f"  {rel:<50} -> {f}/{n}")

t = len(mapping)
print(f"Folder matches CWC : {folder_ok}/{t}")
print(f"Name matches CWC   : {name_ok}/{t}")
print("\nDifferences:"); print("\n".join(misses) if misses else "  none")
