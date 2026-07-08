from models.domain import ExplainabilityReport

def generate_explainability(decision) -> ExplainabilityReport:
    return ExplainabilityReport(
        confidence_level="High",
        reason_for_recommendation="Strong financials"
    )
