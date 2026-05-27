#!/usr/bin/env Rscript
# FAMD / PCA / MCA analysis using FactoMineR
# Args: path to JSON config file

suppressPackageStartupMessages({
  if (!requireNamespace("jsonlite", quietly = TRUE)) install.packages("jsonlite", repos = "https://cran.rstudio.com/")
  if (!requireNamespace("FactoMineR", quietly = TRUE)) install.packages("FactoMineR", repos = "https://cran.rstudio.com/")
  library(jsonlite)
  library(FactoMineR)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript famd.R <config.json>")

cfg <- fromJSON(args[1])
input_csv  <- cfg$input_csv
quant_vars <- cfg$quant_vars
qual_vars  <- cfg$qual_vars
n_comp     <- as.integer(cfg$n_comp)
handle_na  <- as.logical(cfg$handle_na)
out_dir    <- cfg$output_dir

df <- read.csv(input_csv, stringsAsFactors = FALSE)

# Select and type-cast columns
all_vars <- c(quant_vars, qual_vars)
missing_cols <- setdiff(all_vars, colnames(df))
if (length(missing_cols) > 0) {
  stop(paste("Columns not found in CSV:", paste(missing_cols, collapse = ", ")))
}

df_sub <- df[, all_vars, drop = FALSE]

for (v in quant_vars) df_sub[[v]] <- as.numeric(df_sub[[v]])
for (v in qual_vars)  df_sub[[v]] <- as.factor(df_sub[[v]])

# Handle missing values
if (handle_na) {
  has_na <- any(is.na(df_sub))
  if (has_na) {
    if (requireNamespace("missMDA", quietly = TRUE)) {
      library(missMDA)
      if (length(quant_vars) > 0 && length(qual_vars) > 0) {
        n_quant <- length(quant_vars)
        df_sub <- imputeFAMD(df_sub, ncp = min(n_comp, ncol(df_sub) - 1))$completeObs
      } else if (length(quant_vars) > 0) {
        df_quant <- df_sub[, quant_vars, drop = FALSE]
        df_sub[, quant_vars] <- imputePCA(df_quant, ncp = min(n_comp, ncol(df_quant)))$completeObs
      } else {
        df_sub[, qual_vars] <- imputeMCA(df_sub[, qual_vars, drop = FALSE], ncp = min(n_comp, length(qual_vars)))$completeObs
      }
    } else {
      # Fallback: remove NA rows
      df_sub <- na.omit(df_sub)
      warning("missMDA not available; removed rows with NA values.")
    }
  }
} else {
  df_sub <- na.omit(df_sub)
}

if (nrow(df_sub) < 3) stop("Too few complete rows for analysis.")

# Run appropriate analysis
n_comp_use <- min(n_comp, ncol(df_sub) - 1, nrow(df_sub) - 1)

pdf_path <- file.path(out_dir, "famd_plots.pdf")
pdf(pdf_path, width = 12, height = 8)

if (length(quant_vars) > 0 && length(qual_vars) > 0) {
  res <- FAMD(df_sub, ncp = n_comp_use, graph = FALSE)
  plot(res, choix = "ind", title = "FAMD - Individuals")
  plot(res, choix = "var", title = "FAMD - Variables")
  eig <- as.data.frame(res$eig)
  coords_ind <- as.data.frame(res$ind$coord)
} else if (length(quant_vars) > 0) {
  res <- PCA(df_sub[, quant_vars, drop = FALSE], ncp = n_comp_use, graph = FALSE)
  plot(res, choix = "ind", title = "PCA - Individuals")
  plot(res, choix = "var", title = "PCA - Variables")
  eig <- as.data.frame(res$eig)
  coords_ind <- as.data.frame(res$ind$coord)
} else {
  res <- MCA(df_sub[, qual_vars, drop = FALSE], ncp = n_comp_use, graph = FALSE)
  plot(res, choix = "ind", title = "MCA - Individuals")
  plot(res, choix = "var", title = "MCA - Variables")
  eig <- as.data.frame(res$eig)
  coords_ind <- as.data.frame(res$ind$coord)
}

# Eigenvalue barplot
barplot(eig[, 2], names.arg = rownames(eig),
        main = "Eigenvalues (% variance explained)",
        ylab = "% variance", las = 2, col = "steelblue")

dev.off()

# Write CSV outputs
colnames(eig) <- c("eigenvalue", "pct_variance", "cumulative_pct")
eig$component <- rownames(eig)
write.csv(eig[, c("component", "eigenvalue", "pct_variance", "cumulative_pct")],
          file.path(out_dir, "famd_eigenvalues.csv"), row.names = FALSE)

write.csv(coords_ind, file.path(out_dir, "famd_individual_coordinates.csv"), row.names = TRUE)

cat("FAMD analysis complete.\n")
