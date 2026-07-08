from models.domain import DecisionConfidenceReport

def calculate_confidence(data) -> DecisionConfidenceReport:
    return DecisionConfidenceReport(confidence_level="High", score=0.88, factors={})
