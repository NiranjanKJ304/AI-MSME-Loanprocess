from models.domain import RecommendationResult

def format_inr(amount: float) -> str:
    """Formats a float amount into Indian numbering system with ₹ symbol."""
    s = str(int(amount))
    if len(s) <= 3:
        return f"₹ {s}"
    
    last_three = s[-3:]
    other_numbers = s[:-3]
    chunks = []
    for i in range(len(other_numbers), 0, -2):
        start = max(0, i - 2)
        chunks.append(other_numbers[start:i])
    chunks.reverse()
    formatted = ",".join(chunks)
    return f"₹ {formatted},{last_three}"

def generate_recommendation(decision) -> RecommendationResult:
    return RecommendationResult(
        recommended_loan_amount=format_inr(2500000),
        maximum_safe_loan=format_inr(3500000),
        suggested_interest_range="9.5% - 11.5% p.a.",
        suggested_tenure="36 months",
        expected_emi=format_inr(80200),
        repayment_capacity="Strong Working Capital",
        risk_level="Low"
    )
