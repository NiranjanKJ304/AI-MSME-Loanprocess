from .base import BaseAgent, AgentResponse
from typing import Dict

class RecommendationAgent(BaseAgent):
    def evaluate(self, data: dict) -> AgentResponse:
        # TODO: Implement actual model logic here
        # Return structured recommendations encoded in 'suggestions' or a separate dict
        # We will parse this in the coordinator.
        return AgentResponse(
            score=90.0,
            confidence=0.94,
            reason="Applicant shows strong repayment capacity and stable business environment.",
            suggestions=["Approve Loan Amount: 50,000 USD", "Interest Rate Suggestion: 8.5%"],
            agent_name="Recommendation"
        )
