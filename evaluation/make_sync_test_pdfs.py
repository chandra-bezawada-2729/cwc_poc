"""
Generates synthetic, non-PHI PDFs for the OneDrive hydration / stability test.

The inbound scanner's readiness probe only looks at size, mtime and the first 8
magic bytes, so placeholder and stability behaviour can be exercised without
putting real patient faxes into cloud storage. Each file gets distinct content:
identical bytes would collapse to one ledger row under the content-hash key and
the test would silently measure nothing.

Usage: python evaluation/make_sync_test_pdfs.py <dest-dir> [count]
"""
import sys
from pathlib import Path

import fitz  # PyMuPDF

dest = Path(sys.argv[1])
count = int(sys.argv[2]) if len(sys.argv) > 2 else 17
dest.mkdir(parents=True, exist_ok=True)

for i in range(1, count + 1):
    doc = fitz.open()
    # Vary page count so file sizes differ the way a real fax batch does.
    for page_no in range(1, (i % 4) + 2):
        page = doc.new_page()
        page.insert_text((72, 100), f"SYNTHETIC TEST DOCUMENT {i}", fontsize=18)
        page.insert_text((72, 130), f"page {page_no} - no patient data", fontsize=11)
        # Padding text makes each file a few KB rather than a few hundred bytes.
        for line in range(40):
            page.insert_text((72, 160 + line * 14),
                             f"filler line {line:02d} for document {i:02d}", fontsize=9)
    out = dest / f"synthetic_fax_{i:02d}.pdf"
    doc.save(out)
    doc.close()
    print(f"  {out.name}  {out.stat().st_size} bytes")

print(f"generated {count} synthetic PDFs in {dest}")
