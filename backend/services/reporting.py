from models.domain import LendingDecisionReport

def generate_report(data_bundle) -> LendingDecisionReport:
    # Construct the final report based on gathered pieces
    return LendingDecisionReport(
        company_summary={"name": "Test Indian MSME", "assessment_bank": "IDBI Bank", "loan_type": "Working Capital"},
        document_verification_summary=data_bundle['verification'],
        financial_timeline=data_bundle['timeline'],
        financial_health_score=85,
        cash_flow_score=80,
        compliance_score=90,
        fraud_risk_score=10,
        business_stability_score=88,
        forecast_score=82,
        decision_confidence=data_bundle['confidence'].confidence_level,
        loan_recommendation=data_bundle['recommendation'],
        risk_factors=[],
        explainability=data_bundle['explainability']
    )
