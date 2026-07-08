from .base import BaseAgent, AgentResponse

class FraudAgent(BaseAgent):
    def evaluate(self, data: dict) -> AgentResponse:
        # TODO: Implement actual model logic here (e.g. Isolation Forest / NetworkX)
        return AgentResponse(
            score=12.0, # Lower is better for risk, let's treat score as "Risk Score" here
            confidence=0.95,
            reason="No anomalies detected in recent transactions. Identity verified across GST and UPI data.",
            suggestions=["Continue regular monitoring"],
            agent_name="Fraud Risk"
        )
