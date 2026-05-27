# Network Analysis Grouping R script
# Input:  sys.argv[1] = JSON config string
# Output: JSON result to stdout (last line)

suppressPackageStartupMessages({
  library(igraph)
  library(dplyr)
  library(sf)
  library(RColorBrewer)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  cat(toJSON(list(success = FALSE, error = "No config argument provided"), auto_unbox = TRUE))
  quit(status = 1)
}

config <- fromJSON(args[1])

# Extract parameters
nodes_table                <- config$nodes_table
nodes_table_join           <- config$nodes_join_attri
link_table                 <- config$links_table
in_shapefile               <- config$shapefile_path
in_telecoupling_layer_join <- config$layer_join_attri   # Use variable, never hardcode
clustering_algorithm       <- config$clustering_algorithm
weight_within              <- as.numeric(config$weight_within)
weight_between             <- as.numeric(config$weight_between)
color_set                  <- config$color_set
node_size                  <- as.numeric(config$node_size)
edge_width                 <- as.numeric(config$edge_width)
label_size                 <- as.numeric(config$label_size)
out_pdf                    <- config$out_pdf
out_csv                    <- config$out_csv
out_shp                    <- config$out_shp

# Edge weight function
edge.weights <- function(community, network,
                         weight.within = weight_within,
                         weight.between = weight_between) {
  bridges <- crossing(communities = community, graph = network)
  weights <- ifelse(test = bridges, yes = weight.between, no = weight.within)
  return(weights)
}

message("Reading node and link data...")
nodes_df <- read.csv(file = nodes_table, header = TRUE, stringsAsFactors = FALSE, check.names = FALSE)
links_df <- read.csv(file = link_table, header = TRUE, stringsAsFactors = FALSE, check.names = FALSE)

message("Reading shapefile...")
shp_layer <- st_read(in_shapefile, quiet = TRUE)

# Build directed graph
network_graph <- graph_from_data_frame(d = links_df, vertices = nodes_df, directed = TRUE)

# Community detection
message(paste("Detecting communities using:", clustering_algorithm))
if (clustering_algorithm == "walktrap") {
  MyClusters.community <- cluster_walktrap(network_graph)
  community_df <- data.frame(
    V1 = unique(MyClusters.community)[[4]],
    cluster_N = as.numeric(unique(MyClusters.community)[[3]])
  )
  colnames(community_df)[1] <- nodes_table_join
} else if (clustering_algorithm == "spin_glass") {
  MyClusters.community <- cluster_spinglass(network_graph)
  community_df <- data.frame(
    V1 = unique(MyClusters.community)[[7]],
    cluster_N = as.numeric(unique(MyClusters.community)[[1]])
  )
  colnames(community_df)[1] <- nodes_table_join
} else {
  cat(toJSON(list(success = FALSE, error = paste("Unknown algorithm:", clustering_algorithm)), auto_unbox = TRUE))
  quit(status = 1)
}

community_df[[nodes_table_join]] <- as.character(community_df[[nodes_table_join]])

# Merge community assignments with shapefile using in_telecoupling_layer_join
message("Merging clusters with shapefile...")
shp_layer[[in_telecoupling_layer_join]] <- as.character(shp_layer[[in_telecoupling_layer_join]])
shp_merged <- shp_layer %>%
  left_join(community_df, by = setNames(nodes_table_join, in_telecoupling_layer_join))

dir.create(dirname(out_shp), showWarnings = FALSE, recursive = TRUE)
st_write(shp_merged, out_shp, delete_layer = TRUE, quiet = TRUE)
message(paste("Shapefile written to:", out_shp))

# Assign colors — protect: min 3, max 12
n_communities <- length(unique(MyClusters.community))
n_colors <- min(max(n_communities, 3), 12)
Colors <- brewer.pal(n_colors, color_set)
MyClusters.col <- Colors[membership(MyClusters.community)]

# Edge weights and layout
E(network_graph)$weight <- edge.weights(MyClusters.community, network_graph)
test.Layout.drl <- layout_with_drl(network_graph, weights = E(network_graph)$weight)

xmin <- min(test.Layout.drl[, 1])
xmax <- max(test.Layout.drl[, 1])
ymin <- min(test.Layout.drl[, 2])
ymax <- max(test.Layout.drl[, 2])

# Node sizing
if ("larrivals.sender" %in% vertex_attr_names(network_graph) &&
    "larrivals.receiver" %in% vertex_attr_names(network_graph)) {
  arrivals.sum <- ((exp(V(network_graph)$larrivals.sender) - 1) +
                   (exp(V(network_graph)$larrivals.receiver) - 1))
  V(network_graph)$larrivals.total <- log1p(arrivals.sum)
  V(network_graph)$size <- ((V(network_graph)$larrivals.total)^2) * node_size
} else {
  V(network_graph)$size <- node_size * 10
}

# Edge width
if ("larrivals" %in% edge_attr_names(network_graph)) {
  E(network_graph)$width <- E(network_graph)$larrivals * edge_width
} else {
  E(network_graph)$width <- edge_width
}

V(network_graph)$label.color <- "black"
V(network_graph)$label <- V(network_graph)$name
V(network_graph)$label.cex <- label_size

# Network statistics CSV
message("Computing network statistics...")
deg_stat  <- degree(network_graph)
clo_stat  <- closeness(network_graph, normalized = TRUE)
betw_stat <- betweenness(network_graph)
inte_csv  <- data.frame(degree = deg_stat, closeness = clo_stat, betweenness = betw_stat)
dir.create(dirname(out_csv), showWarnings = FALSE, recursive = TRUE)
write.csv(inte_csv, file = out_csv)
message(paste("CSV written to:", out_csv))

# Render PDF
message("Rendering network plot PDF...")
dir.create(dirname(out_pdf), showWarnings = FALSE, recursive = TRUE)
tryCatch({
  pdf(out_pdf)
  suppressWarnings(plot(
    x = MyClusters.community,
    y = network_graph,
    layout = test.Layout.drl,
    mark.groups = NULL,
    edge.color = c("tomato2", "darkgrey")[crossing(MyClusters.community, network_graph) + 1],
    edge.arrow.size = 0,
    rescale = FALSE,
    xlim = c(xmin, xmax),
    ylim = c(ymin, ymax),
    asp = 0,
    col = MyClusters.col
  ))
}, finally = {
  dev.off()
})
message(paste("PDF written to:", out_pdf))

# Output JSON result
cat(toJSON(list(
  success = TRUE,
  out_pdf = out_pdf,
  out_csv = out_csv,
  out_shp = out_shp,
  n_communities = n_communities
), auto_unbox = TRUE))
