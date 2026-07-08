from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime

class ExtractedFinancialData(BaseModel):
    """Structured JSON output from Document Intelligence"""
    document_type: str
    confidence: float
    data: Dict[str, Any]
    warnings: List[str] = []

class DocumentVerificationReport(BaseModel):
    status: str # e.g. "Verified", "Failed", "Warning"
    confidence: float
    errors: List[str] = []
    warnings: List[str] = []

class TimelineData(BaseModel):
    revenue_timeline: Dict[str, float] = {} # e.g. {"2023-01": 50000, ...}
    expense_timeline: Dict[str, float] = {}
    cash_flow_timeline: Dict[str, float] = {}
    gst_timeline: Dict[str, float] = {}
    payroll_timeline: Dict[str, float] = {}
    upi_timeline: Dict[str, float] = {}
    business_growth_timeline: Dict[str, float] = {}

class FeatureVector(BaseModel):
    revenue_features: Dict[str, float] = {}
    cash_flow_features: Dict[str, float] = {}
    growth_features: Dict[str, float] = {}
    profitability_features: Dict[str, float] = {}
    gst_compliance_features: Dict[str, float] = {}
    digital_payment_features: Dict[str, float] = {}
    payroll_features: Dict[str, float] = {}
    business_stability_features: Dict[str, float] = {}
    credit_behaviour_features: Dict[str, float] = {}

class AgentResponse(BaseModel):
    agent_name: str
    score: float
    confidence: float
    reason: str
    suggestions: List[str] = []

class AgentResults(BaseModel):
    results: Dict[str, AgentResponse]
    aggregated_score: float

class DecisionConfidenceReport(BaseModel):
    confidence_level: str # Low, Medium, High, Very High
    score: float
    factors: Dict[str, float] # e.g. {"document_quality": 0.9, ...}

class RecommendationResult(BaseModel):
    recommended_loan_amount: str
    maximum_safe_loan: str
    suggested_interest_range: str
    suggested_tenure: str
    expected_emi: str
    repayment_capacity: str
    risk_level: str
    business_improvement_suggestions: List[str] = []

class ExplainabilityReport(BaseModel):
    positive_factors: List[str] = []
    negative_factors: List[str] = []
    evidence_used: List[str] = []
    confidence_level: str
    reason_for_recommendation: str

class LendingDecisionReport(BaseModel):
    company_summary: Dict[str, Any]
    document_verification_summary: DocumentVerificationReport
    financial_timeline: TimelineData
    financial_health_score: float
    cash_flow_score: float
    compliance_score: float
    fraud_risk_score: float
    business_stability_score: float
    forecast_score: float
    decision_confidence: str
    loan_recommendation: RecommendationResult
    risk_factors: List[str]
    explainability: ExplainabilityReport
