import sys
import os

# Add parent directory to path so imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.monitor import PipelineMonitor
from models.domain import LendingDecisionReport

# Pipeline Services
from services.document_intelligence import process_documents
from services.document_verification import verify_documents
from services.data_extraction import extract_data
from services.validation import validate_data
from services.financial_timeline import generate_timeline
from services.feature_engineering import generate_features
from services.ai_coordinator import coordinate_agents
from services.decision_engine import run_decision_engine
from services.decision_confidence import calculate_confidence
from services.recommendation import generate_recommendation
from services.financial_coach import generate_coaching
from services.explainability import generate_explainability
from services.reporting import generate_report

class WorkflowOrchestrator:
    def __init__(self):
        self.monitor = PipelineMonitor()

    def run_full_pipeline(self, raw_documents: list) -> LendingDecisionReport:
        self.monitor.start_pipeline()
        data_bundle = {}
        
        try:
            # 1. Document Pipeline
            self.monitor.start_stage("Document Pipeline")
            processed_docs = process_documents(raw_documents)
            verification = verify_documents(raw_documents)
            data_bundle['verification'] = verification
            self.monitor.complete_stage("Document Pipeline")

            # 2. Financial Pipeline
            self.monitor.start_stage("Financial Pipeline")
            extracted_data = extract_data(processed_docs)
            validated_data = validate_data(extracted_data)
            timeline = generate_timeline(validated_data)
            data_bundle['timeline'] = timeline
            self.monitor.complete_stage("Financial Pipeline")

            # 3. Feature Pipeline
            self.monitor.start_stage("Feature Pipeline")
            features = generate_features(timeline)
            self.monitor.complete_stage("Feature Pipeline")

            # 4. AI Pipeline
            self.monitor.start_stage("AI Pipeline")
            agent_results = coordinate_agents(features)
            self.monitor.complete_stage("AI Pipeline")

            # 5. Decision Pipeline
            self.monitor.start_stage("Decision Pipeline")
            decision = run_decision_engine(agent_results)
            confidence = calculate_confidence(decision)
            data_bundle['confidence'] = confidence
            self.monitor.complete_stage("Decision Pipeline")

            # 6. Recommendation Pipeline
            self.monitor.start_stage("Recommendation Pipeline")
            recommendation = generate_recommendation(decision)
            coaching = generate_coaching(decision)
            # Combine coaching into recommendation for simplicity
            recommendation.business_improvement_suggestions = coaching
            data_bundle['recommendation'] = recommendation
            self.monitor.complete_stage("Recommendation Pipeline")

            # 7. Reporting Pipeline
            self.monitor.start_stage("Reporting Pipeline")
            explainability = generate_explainability(decision)
            data_bundle['explainability'] = explainability
            final_report = generate_report(data_bundle)
            self.monitor.complete_stage("Reporting Pipeline")

            self.monitor.finish_pipeline()
            return final_report

        except Exception as e:
            # Simple error handling for now
            if self.monitor.status.running_stage:
                self.monitor.fail_stage(self.monitor.status.running_stage)
            self.monitor.finish_pipeline()
            raise e
