"""
Generates four synthetic negative-path fixture PDFs for confidence-gate testing.
No PHI. Safe to commit.
Run once: python make_fixtures.py
"""
import io
import random
import textwrap
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).parent
OUT.mkdir(parents=True, exist_ok=True)

# ── 1. Blank / near-blank page ────────────────────────────────────────────────
def make_blank():
    doc = fitz.open()
    doc.new_page(width=612, height=792)   # standard US letter, no content
    doc.save(str(OUT / "blank_page.pdf"))
    doc.close()
    print("Created blank_page.pdf")


# ── 2. Heavily degraded scan ──────────────────────────────────────────────────
def make_degraded():
    """
    Create a synthetic fax-like document, render it to a PNG, then heavily
    downsample (to ~40 % linear scale) and re-save as low-quality JPEG before
    embedding in a PDF.  Tesseract will struggle; vision should still see text.
    """
    W, H = 1700, 2200
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    lines = [
        "FAX TRANSMISSION",
        "",
        "To: Provider Office",
        "From: Regional Lab Services",
        "Date: 07/15/2026",
        "",
        "RE: LABORATORY RESULTS — PATIENT SPECIMEN",
        "",
        "CBC WITH DIFFERENTIAL",
        "WBC:  6.2  K/uL   [4.0 – 11.0]",
        "RBC:  4.51 M/uL   [4.20 – 5.80]",
        "HGB:  13.8 g/dL   [12.0 – 16.0]",
        "HCT:  41.2 %      [37.0 – 47.0]",
        "",
        "BASIC METABOLIC PANEL",
        "Sodium:     138  mEq/L  [136 – 145]",
        "Potassium:  4.1  mEq/L  [3.5 – 5.1]",
        "Creatinine: 0.9  mg/dL  [0.6 – 1.2]",
        "Glucose:    102  mg/dL  [70 – 99]  (H)",
        "",
        "IMPRESSION: Results within normal limits except mildly",
        "elevated fasting glucose.  Clinical correlation advised.",
        "",
        "Reported by: J. Smith, MD  |  Lab Director",
    ]

    y = 120
    for line in lines:
        draw.text((100, y), line, fill="black")
        y += 36

    # Add salt-and-pepper noise to simulate a degraded fax
    pixels = img.load()
    rng = random.Random(42)
    for _ in range(W * H // 20):
        x = rng.randint(0, W - 1)
        y2 = rng.randint(0, H - 1)
        v = rng.choice([0, 255])
        pixels[x, y2] = (v, v, v)

    # Downsample to ~40 % and save as low-quality JPEG
    small = img.resize((int(W * 0.40), int(H * 0.40)), Image.BILINEAR)
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=12)

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(OUT / "degraded_scan.pdf"))
    doc.close()
    print("Created degraded_scan.pdf")


# ── 3. Ambiguous: health-plan gap notice + explicit prescription request ───────
def make_ambiguous():
    """
    Mixes PAYER_CARE_GAP signals (health-plan letterhead, STARS quality measure,
    claims data) with equally strong PHARMACY_REQUEST signals (named drug,
    dosage, quantity, "please prescribe").  Intended to produce margin < 0.15.
    """
    body = textwrap.dedent("""\
        UNITED HEALTHCARE
        Quality Improvement & Member Engagement

        QUALITY CARE GAP — PRESCRIPTION ACTION REQUESTED

        Date: August 15, 2026

        Dear Treating Provider,

        Based on pharmacy claims data for the member listed below, United Healthcare
        has identified an open STARS quality measure: Statin Therapy for Patients
        with Cardiovascular Disease (SPC measure).

        ACTION REQUIRED — PLEASE PRESCRIBE:
          Medication:  Atorvastatin Calcium 40 mg Tablet
          Quantity:    90 tablets
          Directions:  Take one tablet by mouth daily
          Preferred:   Send eRX to CVS Pharmacy (NCPDP: 1234567)

        This prescription is needed to close the member's STARS gap before the
        measurement year closes on December 31, 2026.

        Please select one option and fax this form back:
          [ ] Prescription transmitted via eRX
          [ ] Prescription faxed to pharmacy
          [ ] Patient declined — reason: ________________
          [ ] Medically inappropriate — reason: ___________

        Response requested by: September 30, 2026
        Return fax: 1-800-555-0199

        Member information on reverse side.
    """)

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        fitz.Point(50, 60),
        body,
        fontsize=11,
        color=(0, 0, 0),
    )
    doc.save(str(OUT / "ambiguous_quality_rx.pdf"))
    doc.close()
    print("Created ambiguous_quality_rx.pdf")


# ── 4. PRESCRIPTION category → AUTO_ROUTE_DISABLED override ──────────────────
def make_prescription():
    """
    A plain prescription order — triggers AUTO_ROUTE_DISABLED:PRESCRIPTION.
    """
    body = textwrap.dedent("""\
        PRESCRIPTION ORDER

        Prescriber: Dr. A. Patel, MD  |  NPI: 1234567890
        DEA: AP1234563
        Address: 100 Main Street, Springfield, IL 62701
        Phone: (217) 555-0100  |  Fax: (217) 555-0101

        Date: August 28, 2026

        Patient: [PATIENT NAME]
        DOB: [DOB]

        Rx:
          Metformin HCl 500 mg Tablets
          Sig: Take one tablet twice daily with meals
          Dispense: #60 (sixty)
          Refills: 5

        [ ] Brand medically necessary
        [x] Substitution permitted

        _______________________________
        Prescriber Signature
    """)

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(fitz.Point(50, 60), body, fontsize=11, color=(0, 0, 0))
    doc.save(str(OUT / "prescription_order.pdf"))
    doc.close()
    print("Created prescription_order.pdf")


def make_cover_sheet():
    """
    A cover sheet only — sender letterhead, 'Pages: 1 of 4', patient name,
    no body content indicating purpose. Designed to split the model between
    OTHER, PHARMACY_REQUEST and FOLLOW_UP because the purpose is unknowable.
    """
    body = textwrap.dedent("""\
        COVER SHEET

        --------------------------------------------------------
        CONFIDENTIAL FAX TRANSMISSION
        --------------------------------------------------------

        TO:     [Provider Name / Office]
        FROM:   [Sender Organization]
        DATE:   [Date]
        PAGES:  1 of 4    (including this cover sheet)
        FAX:    [Recipient Fax Number]

        PATIENT: [Patient Name]
        DOB:     [DOB]

        --------------------------------------------------------
        If you did not receive all pages, please call the sender.
        --------------------------------------------------------

        This facsimile contains information intended only for the
        use of the individual or entity named above and may
        contain information that is privileged and confidential.
        If you are not the intended recipient, please contact the
        sender immediately and destroy this facsimile.
    """)

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(fitz.Point(50, 60), body, fontsize=11, color=(0, 0, 0))
    doc.save(str(OUT / "cover_sheet_only.pdf"))
    doc.close()
    print("Created cover_sheet_only.pdf")


# ── 6. Readable-but-degraded scan → MEDIUM band ───────────────────────────────
def make_readable_degraded():
    """
    Same content structure as degraded_scan.pdf but downsampled to only ~70 %
    linear scale and JPEG quality=45 instead of 12.  Text should still be mostly
    legible to Tesseract and the vision model, producing a real category at
    MEDIUM confidence rather than UNKNOWN.
    """
    W, H = 1700, 2200
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    lines = [
        "PRIOR AUTHORIZATION REQUEST",
        "",
        "To:   Dr. Rivera, MD  |  Family Practice Associates",
        "From: Acme Health Plan — Utilization Management",
        "Date: 08/05/2026",
        "",
        "RE: PRIOR AUTHORIZATION — MEDICATION",
        "",
        "Member: [Member Name]",
        "Member ID: [ID]",
        "Plan: Commercial PPO",
        "",
        "Requested Medication: Humira (adalimumab) 40 mg/0.4 mL",
        "Quantity: 2 pens per 28 days",
        "ICD-10 Diagnosis: M06.9 (Rheumatoid Arthritis)",
        "",
        "This request is currently PENDING review.",
        "Additional clinical information may be required.",
        "",
        "Please fax supporting documentation to:",
        "Utilization Management Fax: (800) 555-0200",
        "",
        "Acme Health Plan  |  PO Box 9000  |  Cityville, ST 00001",
    ]

    y = 120
    for line in lines:
        draw.text((100, y), line, fill="black")
        y += 36

    # Mild noise — less than the 40%-JPEG fixture
    pixels = img.load()
    rng = random.Random(99)
    for _ in range(W * H // 80):   # far fewer noise pixels
        x = rng.randint(0, W - 1)
        y2 = rng.randint(0, H - 1)
        v = rng.choice([0, 200])
        pixels[x, y2] = (v, v, v)

    # 70 % downsample, JPEG quality=45 — still readable
    small = img.resize((int(W * 0.70), int(H * 0.70)), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=45)

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(OUT / "readable_degraded.pdf"))
    doc.close()
    print("Created readable_degraded.pdf")


if __name__ == "__main__":
    make_blank()
    make_degraded()
    make_ambiguous()
    make_prescription()
    make_cover_sheet()
    make_readable_degraded()
    print("Done. Files in:", OUT)
