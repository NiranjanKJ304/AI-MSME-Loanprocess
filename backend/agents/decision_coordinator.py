from .financial_health import FinancialHealthAgent
from .cash_flow import CashFlowAgent
from .fraud import FraudAgent
from .forecast import ForecastAgent
from .compliance import ComplianceAgent
from .business_stability import BusinessStabilityAgent
from .recommendation import RecommendationAgent

def run_decision_pipeline(scenario_data: dict) -> dict:
    """
    Orchestrates the execution of all agents in the pipeline and aggregates results.
    """
    agents = [
        FinancialHealthAgent(),
        CashFlowAgent(),
        FraudAgent(),
        ForecastAgent(),
        ComplianceAgent(),
        BusinessStabilityAgent()
    ]
    
    results = {}
    aggregated_score = 0
    
    for agent in agents:
        response = agent.evaluate(scenario_data)
        # In a real app we'd map this better, here we use agent name as key
        results[response.agent_name] = response.dict()
        if response.agent_name == "Fraud Risk":
            # Lower fraud score is better, let's say 100 - score
            aggregated_score += (100 - response.score)
        else:
            aggregated_score += response.score
            
    # Simple average for the final "Health Score"
    avg_score = aggregated_score / len(agents)
    
    # Run recommendation agent
    recommendation_agent = RecommendationAgent()
    recommendation_result = recommendation_agent.evaluate(scenario_data)
    results["Recommendation"] = recommendation_result.dict()
    
    # Modify outcome based on scenario data (Simulation effect)
    # E.g. if revenue drop > 10%, decrease health score by 15
    if scenario_data.get("revenue_drop", 0) > 10:
        avg_score -= 15
        
    if scenario_data.get("interest_rate", 0) > 12:
        results["Recommendation"]["suggestions"].append("High interest rate may lead to default risk.")
    
    final_decision = "Approve" if avg_score > 70 else ("Review" if avg_score > 50 else "Reject")
    
    return {
        "overall_health_score": round(avg_score, 2),
        "decision": final_decision,
        "confidence": 0.90,
        "reasoning": "Aggregated across 6 distinct AI risk vectors.",
        "agent_results": results,
        "scenario_used": scenario_data
    }
