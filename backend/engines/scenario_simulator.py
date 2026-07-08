def simulate_scenario(base_data: dict, scenario_params: dict) -> dict:
    """
    Apply scenario modifiers to base data.
    """
    modified_data = base_data.copy()
    for k, v in scenario_params.items():
        modified_data[k] = v
    return modified_data
