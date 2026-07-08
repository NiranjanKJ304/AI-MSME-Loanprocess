from .base import BaseAgent, AgentResponse

class ForecastAgent(BaseAgent):
    def evaluate(self, data: dict) -> AgentResponse:
        # TODO: Implement actual model logic here (e.g. Prophet)
        return AgentResponse(
            score=82.0,
            confidence=0.85,
            reason="Projected 15% YoY revenue growth based on historical seasonality trends.",
            suggestions=["Increase marketing spend in Q3 to capture forecasted peak demand"],
            agent_name="Forecast"
        )
