---
name: run-network-analysis
description: "Run Network Analysis Grouping using R + igraph to detect community clusters in flow networks."
allowed-tools:
  - run_network_analysis_grouping
tags:
  - telecoupling
  - telecoupling-toolbox
  - invest
---

# Run Network Analysis

Workflow guidance for the `run_network_analysis_grouping` tool from the Telecoupling Toolbox. Load this skill when the user wants to run this model so you collect the right parameters and interpret the outputs correctly.

## When to use & parameters to collect

**Uploaded files**: If nodes CSV, links CSV, or shapefile have been uploaded, extract their paths directly — do not ask for them again. Only ask for genuinely missing inputs.

**Parameters to collect**:
- nodes_table: path to nodes CSV file (must contain node attributes and a join column)
- links_table: path to edges/links CSV file (source and target node identifiers)
- shapefile_path: path to basemap shapefile for geographic visualization
- nodes_join_attri: column name in nodes CSV used to join with shapefile (e.g. "CODE")
- layer_join_attri: column name in shapefile attribute table used for joining (e.g. "ISO_3_CODE")
- clustering_algorithm: "walktrap" or "spin_glass" — if not specified, recommend "walktrap" and confirm
- Optional (have defaults, only ask if user wants to adjust): weight_within_clusters=10, weight_between_clusters=2, color_set="Set3", node_size=0.05, edge_width=0.833333, label_size=0.8

**Key notes**:
- nodes_join_attri and layer_join_attri are JOIN column names, not data values — ask explicitly if not provided

---

## Interpreting the outputs

### Output file reference

| File | Type | Description |
|------|------|-------------|
| network_plot_*.pdf | Download | Full network visualization with nodes colored by community; suitable for publication |
| network_stats_*.csv | Table | Per-node statistics: degree, closeness centrality, betweenness centrality |
| output_*_preview.png | Preview image | Map of geographic units colored by community membership, overlaid on satellite basemap |
| output_*.shp | Download | Shapefile with cluster_id column; can be used for further GIS analysis |

### Domain knowledge — how to interpret results

**Community detection in the telecoupling framework**:
This tool is grounded in the telecoupling framework, which studies flows (trade, migration, remittances, species movement, information) between distant coupled human-nature systems. Communities in this context are groups of nodes (countries, regions, cities) that are more tightly connected to each other than to the rest of the network — they reveal hidden systemic dependencies.

**Algorithm choice**:
- **Walktrap**: Detects communities via short random walks. Performs well when communities are dense and well-separated. Recommended for most global trade or migration networks.
- **Spin Glass**: Uses a statistical physics model (energy minimization). Better when community boundaries are fuzzy or partially overlapping.

**Metrics — what to tell the user**:
- **Degree**: Number of direct connections. High-degree nodes are network hubs — key trading partners, major migration destinations, or information gateways. A country with degree 200+ participates heavily in the global network.
- **Closeness centrality**: How quickly a node can reach all other nodes via shortest paths. High values (close to 1.0) indicate highly central, well-connected nodes. Countries like Germany and France typically score high in trade networks because they bridge many regional connections.
- **Betweenness centrality**: How often a node lies on the shortest path between any two other nodes. High betweenness = critical bridge or gateway. A country with betweenness = 0 is not on any shortest path — it is a peripheral leaf node.
- **Betweenness = 0**: Not unusual. Peripheral countries (small island states, landlocked developing nations) may be directly connected to just one or two partners and never act as intermediaries.

**Interpreting community structure**:
- Community membership (cluster_id in the shapefile) reveals trade blocs, regional migration systems, or geopolitical groupings that emerge naturally from the data — without pre-defined regions.
- Nodes within the same community have stronger mutual flows; nodes in different communities interact more weakly.
- Visualize the shapefile output in GIS to see geographic patterns of community membership.

### Suggested next steps
- Use `read_file_content` to analyze the network_stats CSV and identify top hubs, bridges, and peripheral nodes
- Try the other clustering algorithm (walktrap vs spin_glass) to compare community structures
- Adjust weight_within_clusters / weight_between_clusters to tune visual cluster separation
- Overlay the output shapefile with socioeconomic or environmental data in GIS
