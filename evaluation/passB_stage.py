import json, shutil
from pathlib import Path
DS  = Path(r"C:\Users\ABC\Downloads\CWC_DATASET_PATH\Sample for Avenir Digital")
INB = Path(r"C:\CWC_TEST\Inbound")
mapping = {}
for i, p in enumerate(sorted(DS.rglob("*.pdf")), start=1):
    # Arrive like a real fax: opaque name, flat, no folder hint whatsoever.
    syn = f"(718)555-{i:04d}_2026-09-12_{(700+i)%1200:04d}AM.pdf"
    shutil.copy2(p, INB / syn)
    mapping[syn] = str(p.relative_to(DS)).replace("\\", "/")
Path("evaluation/passB_mapping.json").write_text(json.dumps(mapping, indent=2))
print(f"staged {len(mapping)} documents")   # expect 36
