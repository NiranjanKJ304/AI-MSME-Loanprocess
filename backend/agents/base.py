from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import List, Optional

class AgentResponse(BaseModel):
    score: float
    confidence: float
    reason: str
    suggestions: List[str]
    agent_name: str

class BaseAgent(ABC):
    @abstractmethod
    def evaluate(self, data: dict) -> AgentResponse:
        pass
