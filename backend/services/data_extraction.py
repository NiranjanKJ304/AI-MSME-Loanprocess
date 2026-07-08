from models.domain import ExtractedFinancialData

def extract_data(processed_docs: dict) -> ExtractedFinancialData:
    return ExtractedFinancialData(document_type="Mixed", confidence=0.9, data={})
