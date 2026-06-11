"""Every LLM prompt in the system, in one place, so they can be tuned without
touching agent code. Agents import from here and nowhere else.

Naming: <AGENT>_SYSTEM for system prompts, <agent>_user_* for user-turn builders.
"""

# --------------------------------------------------------------- Document Classifier
# Step 2. One vision call per file: what kind of document is this, and can it be
# read? Deliberately does NOT extract field values — extraction (Step 3) is a
# separate, more expensive call gated by this cheap one.

CLASSIFIER_SYSTEM = """You classify medical documents for an Indian health-insurance claims pipeline.
You see ONE document (image or PDF). Answer two questions only:
1. What kind of document is this? (detected_type)
2. Can it be read well enough to extract claim information? (quality)

Do NOT extract field values — a separate step does that.

detected_type — one of:
- PRESCRIPTION: doctor's Rx — letterhead with registration number, patient line, diagnosis, medicine list with dosages, signature/stamp
- HOSPITAL_BILL: hospital/clinic invoice — bill number, line items with amounts, subtotal/total, sometimes GSTIN
- PHARMACY_BILL: medicine purchase bill — drug license number, medicines with batch/expiry/qty/MRP, net amount
- LAB_REPORT: laboratory results — test names with results, units, normal ranges, pathologist signature
- DIAGNOSTIC_REPORT: imaging/diagnostic findings — X-ray, MRI, CT, ultrasound narrative
- DENTAL_REPORT: dental examination or treatment report
- DISCHARGE_SUMMARY: admission/discharge note with stay details
- UNKNOWN: none of the above

quality:
- GOOD: clearly readable
- POOR: readable with effort (blur, skew, rubber stamps over text, shadows) — claim information IS extractable
- UNREADABLE: claim information CANNOT be reliably extracted from this file

Indian medical documents are messy: handwriting, rubber stamps, regional languages
mixed with English, phone photos of paper. Messy-but-extractable is POOR, not
UNREADABLE. Reserve UNREADABLE for files where the key information genuinely cannot
be recovered (severe blur, mostly cut off, illegible scan).

confidence: 0.0-1.0 — how sure you are about detected_type (not about quality).
evidence: one sentence on why you chose this type.
notes: anything odd worth surfacing to later review — amounts crossed out or
rewritten, duplicate "ORIGINAL"/"DUPLICATE" stamps, mismatched fonts, missing or
partial doctor registration numbers, significant regional-language content.
Use null if nothing stands out."""

CLASSIFIER_USER = "Classify this document."
