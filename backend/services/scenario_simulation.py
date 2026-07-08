from orchestrator.workflow import WorkflowOrchestrator

def run_simulation(base_documents: list, scenario_modifiers: dict):
    # In a real implementation, scenario modifiers would be applied
    # at the appropriate stages (e.g., to the timeline or features)
    # For now, we'll just re-run the full pipeline on base_documents
    orchestrator = WorkflowOrchestrator()
    return orchestrator.run_full_pipeline(base_documents)
