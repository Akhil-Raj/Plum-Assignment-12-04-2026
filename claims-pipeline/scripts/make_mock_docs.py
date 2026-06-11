"""Render sample Indian medical documents as images for testing the real-file
(vision) path by hand and in the demo — a clean prescription, a hospital bill, a
pharmacy bill, a blurry (unreadable) pharmacy bill, and a second prescription to
reproduce TC001's wrong-document scenario. Not part of the pipeline.

Usage: .venv/bin/python scripts/make_mock_docs.py [output_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "mock_documents"

PRESCRIPTION = """\
  Dr. Arun Sharma, MBBS, MD (Internal Medicine)
  Reg. No: KA/45678/2015
  City Medical Centre, 12 MG Road, Bengaluru
  Ph: +91-80-2255-1100
-------------------------------------------------------
  Patient: Rajesh Kumar            Date: 01-Nov-2024
  Age: 39 years   Gender: M
  Chief Complaint: Fever since 3 days, body ache
-------------------------------------------------------
  Diagnosis: Viral Fever

  Rx:
  1. Tab Paracetamol 650mg  -  1-1-1 x 5 days
  2. Tab Vitamin C 500mg    -  0-0-1 x 7 days

  Investigations: CBC, Dengue NS1
  Follow-up: After 5 days if no improvement

                              (signed) Dr. Arun Sharma
                              [Registration Stamp]
"""

SECOND_PRESCRIPTION = """\
  Dr. Meena Pillai, MBBS
  Reg. No: KA/89012/2018
  Jayanagar Family Clinic, Bengaluru
-------------------------------------------------------
  Patient: Rajesh Kumar            Date: 02-Nov-2024
  Age: 39 years   Gender: M
-------------------------------------------------------
  Diagnosis: Viral Fever (follow-up)

  Rx:
  1. Tab Paracetamol 650mg  -  1-0-1 x 3 days
  2. ORS sachets            -  as needed

                              (signed) Dr. Meena Pillai
"""

HOSPITAL_BILL = """\
  CITY MEDICAL CENTRE
  12 MG Road, Bengaluru - 560001
  GSTIN: 29ABCDE1234F1ZX        Ph: 080-2255-1100
-------------------------------------------------------
  BILL / RECEIPT
  Bill No: CMC/2024/08321       Date: 01-Nov-2024
-------------------------------------------------------
  Patient Name: Rajesh Kumar
  Age/Gender: 39 / Male
  Referring Doctor: Dr. Arun Sharma
-------------------------------------------------------
  DESCRIPTION                 QTY   RATE     AMOUNT
  Consultation Fee (OPD)       1   1000.00  1000.00
  CBC (Complete Blood Count)   1    300.00   300.00
  Dengue NS1 Antigen Test      1    200.00   200.00

  Subtotal:                                 1500.00
  GST (0% on medical):                         0.00
  Total Amount:                             1500.00
-------------------------------------------------------
  Payment Mode: UPI
  Received by: S. Rao            [Cashier Stamp]
"""

PHARMACY_BILL = """\
  HEALTH FIRST PHARMACY
  Drug Lic. No: KA-BLR-20-114
  22 Brigade Road, Bengaluru
-------------------------------------------------------
  Bill No: HFP-24-09821         Date: 01-Nov-2024
  Patient: Rajesh Kumar         Dr: Dr. Arun Sharma
-------------------------------------------------------
  MEDICINE         BATCH   EXP    QTY  MRP    AMT
  Paracetamol 650  A2341   03/26   15  2.50   37.50
  Vitamin C 500    B7821   06/26   10  4.00   40.00

  Subtotal:                               77.50
  Discount (5%):                          -3.88
  Net Amount:                             73.62
-------------------------------------------------------
  Pharmacist: R. Sharma   [Stamp]
"""


def render(text: str, path: Path, blur: float = 0.0) -> None:
    lines = text.splitlines()
    width, line_height, margin = 760, 22, 28
    height = margin * 2 + line_height * len(lines)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Monaco.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((margin, margin + i * line_height), line, fill="black", font=font)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    image.save(path, "JPEG", quality=85)
    print(f"wrote {path}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    render(PRESCRIPTION, OUT / "prescription.jpg")
    render(SECOND_PRESCRIPTION, OUT / "another_prescription.jpg")
    render(HOSPITAL_BILL, OUT / "hospital_bill.jpg")
    render(PHARMACY_BILL, OUT / "pharmacy_bill.jpg")
    render(PHARMACY_BILL, OUT / "blurry_pharmacy_bill.jpg", blur=7.0)


if __name__ == "__main__":
    main()
