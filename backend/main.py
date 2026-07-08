from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="IDBI AI Credit Assessment Platform", description="Indian MSME Lending Decision Intelligence Platform")

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
def run_simulation_endpoint(data: ScenarioInput):
    from services.scenario_simulation import run_simulation
    # Dummy document list for the simulation
    base_documents = ["doc1.pdf", "doc2.pdf"]
    result = run_simulation(base_documents, data.dict())
    return result

class DocumentUpload(BaseModel):
    documents: list[str]

@app.post("/api/workflow/process-documents")
def process_documents_endpoint(data: DocumentUpload):
    from orchestrator.workflow import WorkflowOrchestrator
    orchestrator = WorkflowOrchestrator()
    report = orchestrator.run_full_pipeline(data.documents)
    return {
        "status": "success",
        "monitor_status": orchestrator.monitor.get_status().dict(),
        "report": report.dict()
    }

@app.get("/api/workflow/status")
def get_workflow_status():
    # In a real app, this would query a database or cache for the status of an active pipeline run
    from orchestrator.monitor import PipelineMonitor
    dummy_monitor = PipelineMonitor()
    return dummy_monitor.get_status().dict()
