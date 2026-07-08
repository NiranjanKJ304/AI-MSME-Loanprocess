from models.domain import TimelineData

def generate_timeline(data) -> TimelineData:
    # Example timeline keys using Indian DD/MM/YYYY format
    return TimelineData(
        revenue_timeline={"01/01/2023": 500000.0, "01/02/2023": 550000.0},
        expense_timeline={"01/01/2023": 300000.0, "01/02/2023": 320000.0},
        cash_flow_timeline={"01/01/2023": 200000.0, "01/02/2023": 230000.0},
        gst_timeline={"01/01/2023": 90000.0, "01/02/2023": 99000.0},
        payroll_timeline={"01/01/2023": 150000.0, "01/02/2023": 150000.0},
        upi_timeline={"01/01/2023": 250000.0, "01/02/2023": 280000.0},
        business_growth_timeline={"01/01/2023": 1.0, "01/02/2023": 1.1}
    )
