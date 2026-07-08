from .base import BaseAgent, AgentResponse

class CashFlowAgent(BaseAgent):
    def evaluate(self, data: dict) -> AgentResponse:
        # TODO: Implement actual model logic here
        return AgentResponse(
            score=70.0,
            confidence=0.88,
            reason="Positive net operating cash flow, but high volatility observed in Q2.",
            suggestions=["Negotiate better payment terms with suppliers", "Maintain higher working capital buffer"],
            agent_name="Cash Flow"
        )
