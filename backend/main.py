from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="LendingMind AI", description="AI Lending Decision Intelligence Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ScenarioInput(BaseModel):
    loan_amount: float
    interest_rate: float
    revenue_growth: float
    revenue_drop: float
    employee_reduction: int
    cash_flow_change: float

class CompanyData(BaseModel):
    name: str
    registration_type: str
    industry: str
    annual_revenue: float
    
@app.get("/")
def read_root():
    return {"status": "ok", "message": "LendingMind AI API is running"}

@app.post("/api/company")
def create_company(data: CompanyData):
    # Mock saving company
    return {"status": "success", "company_id": "CMP-12345", "name": data.name}

@app.post("/api/simulate")
def run_simulation(data: ScenarioInput):
    # TODO: Connect to decision coordinator
    from agents.decision_coordinator import run_decision_pipeline
    result = run_decision_pipeline(data.dict())
    return result
