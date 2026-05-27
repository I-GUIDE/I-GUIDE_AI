---
name: run-co2-emissions
description: "Calculate CO2 emissions from wildlife or goods transport routes."
allowed-tools:
  - run_co2_emissions
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Co2 Emissions

Workflow guidance for the `run_co2_emissions` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: Extract paths from uploaded files first. Only ask for genuinely missing inputs.

**Required parameters**:
- input_csv: path to the input CSV file containing route/trip data with animal counts and distances
- capacity_per_trip: maximum number of animals that can be transported per trip (used to calculate the number of trips required)
- co2_per_km_per_trip: CO2 emissions factor in kg CO2 per kilometer per trip

**Optional parameters**:
- animal_count_field: column name in input_csv that holds the number of animals per route (default: 'animal_count')
- length_km_field: column name in input_csv that holds the route distance in kilometers (default: 'length_km')
- id_field: column name to use as a unique row identifier in the output (optional; uses row index if not provided)

**Key notes**:
- The tool computes number of trips = ceil(animal_count / capacity_per_trip) for each row
- Total CO2 per route = trips × length_km × co2_per_km_per_trip
- Trigger words: CO2 emissions, carbon emissions, transport emissions, wildlife transport, animal transport, shipping carbon footprint

---

## Interpreting the outputs

### Output files

| File | Type | Description |
|------|------|-------------|
| co2_emissions_results.csv | CSV | Per-route results including number of trips, distance, and total CO2 emissions (kg) |
| co2_emissions_summary.csv | CSV | Aggregate summary: total trips, total distance, total CO2 emissions across all routes |

### Suggested next steps
- Review co2_emissions_results.csv to identify which routes contribute the most emissions
- Use co2_emissions_summary.csv to report the overall carbon footprint of the transport operation
- Adjust capacity_per_trip or co2_per_km_per_trip to explore emission reduction scenarios (e.g., larger vehicles or lower-emission transport modes)
- Combine with cost-benefit analysis (run_cost_benefit_analysis) to evaluate the economic vs. environmental trade-offs of transport routes
