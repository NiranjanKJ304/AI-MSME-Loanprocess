"""Generate the synthetic sample MSME application (+ negative test documents).

    python -m scripts.generate_samples                       # writes PDFs to samples/generated/
    python -m scripts.generate_samples --upload http://localhost:8000   # also creates an application
                                                                        # and uploads the documents

All data is fictitious.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from scripts import synthetic_docs as S

OUT = Path(__file__).resolve().parent.parent / "samples" / "generated"


def sample_application(b: S.Business) -> dict[str, bytes]:
    return {
        "01_PAN_card.pdf": S.pan_card_pdf(b.pan, b.legal_name),
        "02_GST_REG06_certificate.pdf": S.gst_certificate_pdf(b),
        "03_GSTR9_annual_return_FY2024-25.pdf": S.gst_return_pdf(b),
        "04_Udyam_certificate.pdf": S.udyam_pdf(b),
        "05_Certificate_of_Incorporation.pdf": S.incorporation_certificate_pdf(b),
        "06_ITR_acknowledgement_AY2025-26.pdf": S.itr_pdf(b),
        "07_Profit_and_Loss_FY2024-25.pdf": S.profit_loss_pdf(b),
        "08_Balance_Sheet_31-03-2025.pdf": S.balance_sheet_pdf(b),
        "09_Bank_statement_Apr24-Mar25.pdf": S.bank_statement_pdf(S.default_bank_spec(b)),
    }


def negative_documents(b: S.Business) -> dict[str, bytes]:
    bad_balance = S.default_bank_spec(b)
    bad_balance.txns[6].balance_text = S.inr(bad_balance.txns[6].balance + Decimal("1000"))
    messy = S.default_bank_spec(b)
    messy.txns[3].balance_text = "1,2O,000.00"
    messy.txns.insert(8, S.Txn(messy.txns[8].date, "", "", None, None, None, date_text="##/##/####"))
    gst = S.gst_certificate_pdf(b)
    return {
        "bank_statement_balance_mismatch.pdf": S.bank_statement_pdf(bad_balance),
        "bank_statement_unparseable_rows.pdf": S.bank_statement_pdf(messy),
        "bank_statement_other_holder.pdf": S.bank_statement_pdf(S.default_bank_spec(b, holder="SUNRISE TEXTILES")),
        "itr_turnover_mismatch.pdf": S.itr_pdf(b, turnover=Decimal("4500000")),
        "pan_invalid_format.pdf": S.pan_card_pdf("AAEC4821K", b.legal_name),
        "gst_missing_legal_name.pdf": S.gst_certificate_pdf(b, omit_legal_name=True),
        "gst_invalid_checksum.pdf": S.gst_certificate_pdf(b, gstin=b.gstin[:-1] + ("A" if b.gstin[-1] != "A" else "B")),
        "gst_scanned_no_text_layer.pdf": S.scanned_pdf_from(gst),
        "gst_scanned_low_quality.pdf": S.scanned_pdf_from(gst, dpi=70, blur=True),
        "password_protected.pdf": S.password_protected_pdf(gst),
        "corrupted.pdf": S.corrupted_pdf(gst),
        "unknown_document.pdf": S.unknown_document_pdf(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", metavar="API_URL", help="create an application and upload the sample set")
    ap.add_argument("--with-negative", action="store_true", help="also upload the negative documents")
    args = ap.parse_args()

    b = S.Business()
    sets = {"application": sample_application(b), "negative": negative_documents(b)}
    # PDF bytes embed unique IDs, so a true duplicate must reuse the exact same bytes
    sets["negative"]["duplicate_of_PAN_card.pdf"] = sets["application"]["01_PAN_card.pdf"]
    for folder, files in sets.items():
        d = OUT / folder
        d.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (d / name).write_bytes(data)
        print(f"wrote {len(files)} files to {d}")

    if args.upload:
        import httpx

        with httpx.Client(base_url=args.upload, timeout=600) as client:
            app = client.post("/api/applications", json={
                "business_name": "Sri Lakshmi Precision Components Pvt Ltd",
                "applicant_type": "PRIVATE_LIMITED",
                "loan_type": "MACHINERY_LOAN",
                "requested_amount": "2500000",
                "loan_purpose": "Purchase of a CNC turning centre",
            }).raise_for_status().json()
            files = dict(sets["application"])
            if args.with_negative:
                files.update(sets["negative"])
            payload = [("files", (n, data, "application/pdf")) for n, data in files.items()]
            res = client.post(f"/api/applications/{app['id']}/documents", files=payload).raise_for_status().json()
            print(f"application {app['application_number']} ({app['id']}): "
                  f"{res['accepted']} accepted, {res['rejected']} rejected; processing scheduled")


if __name__ == "__main__":
    main()
