#!/usr/bin/env Rscript
# Initial farm analyses of target-domain DADA2 ASVs. No phylogeny is assumed.
# Usage: Rscript scripts/03_farm_analysis.R FARM RUN_DIR
options(stringsAsFactors = FALSE, warn = 1)
args <- commandArgs(trailingOnly = TRUE)
farms <- c("OatmanFlatsRanch", "PageSpringsCellars", "Whipstone")
if (length(args) != 2L || !args[1] %in% farms) {
  stop("Usage: Rscript scripts/03_farm_analysis.R ", paste(farms, collapse = "|"), " RUN_DIR")
}
farm <- args[1]
run_dir <- normalizePath(args[2], mustWork = TRUE)
required_packages <- c("phyloseq", "vegan", "permute", "ggplot2")
missing_packages <- required_packages[!vapply(required_packages, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing_packages)) stop("Missing R packages: ", paste(missing_packages, collapse = ", "))
suppressPackageStartupMessages(library(ggplot2))
forbidden <- c("C108", "C111", "D108", "D111")
set.seed(20260911)
out_root <- file.path(run_dir, "farm_analysis", farm)
if (dir.exists(out_root) && length(list.files(out_root, all.files = TRUE, no.. = TRUE))) {
  stop("Farm output directory is not empty; use a fresh run to avoid stale results: ", out_root)
}
dir.create(out_root, recursive = TRUE, showWarnings = FALSE)

write_tsv <- function(x, file) {
  write.table(x, file, sep = "\t", row.names = FALSE, quote = FALSE, na = "NA")
}
slug <- function(x) gsub("[^A-Za-z0-9_.-]", "_", x)
pretty_label <- function(x) vapply(gsub("_", " ", x), function(z) paste(strwrap(z, 27), collapse = "\n"), character(1))
theme_nrcs <- function() theme_bw(base_size = 11) + theme(
  panel.grid.minor = element_blank(), legend.position = "right",
  strip.text = element_text(size = 10), plot.caption = element_text(hjust = 0, size = 9))
save_plot <- function(p, file, width = 11, height = 7) {
  ggsave(file, p, width = width, height = height, units = "in", dpi = 300, limitsize = FALSE)
}
capture_notes <- function(expr) {
  notes <- character()
  value <- tryCatch(withCallingHandlers(expr, warning = function(w) {
    notes <<- c(notes, conditionMessage(w)); invokeRestart("muffleWarning")
  }), error = function(e) e)
  list(value = value, notes = paste(unique(notes), collapse = " | "))
}
read_setting <- function(name, default) {
  val <- suppressWarnings(as.integer(Sys.getenv(name, as.character(default))))
  if (length(val) != 1L || is.na(val) || val < 0L) stop(name, " must be a nonnegative integer")
  val
}
min_reads <- read_setting("NRCS_FARM_MIN_TARGET_READS", 0L)
sensitivity_min_reads <- read_setting("NRCS_SENSITIVITY_MIN_READS", 1000L)
nperm_requested <- read_setting("NRCS_FARM_PERMUTATIONS", 999L)
if (nperm_requested < 1L) stop("NRCS_FARM_PERMUTATIONS must be positive")

design_notes <- c(
  "These are initial, exploratory 16S analyses; soil nutrient and other environmental measurements have not yet been integrated.",
  "Primary metadata retain all approved samples, including the two unique November 2024 Grenache collections; C108, C111, D108 and D111 are forbidden.",
  "Observed ASV richness and Shannon diversity are calculated from unrarefied target-domain counts and depend on sequencing depth; no alpha-diversity significance tests are supplied.",
  "No abundance filtering or rarefaction is applied here by default. Samples with zero target reads cannot yield relative abundance or Bray dissimilarity and are explicitly reported.",
  "Relative abundances use target-domain read totals as denominators. They are compositional, not absolute bacterial or archaeal abundances; do not compare totals between separately amplified domains.",
  "Treatment tests use one observation per physical plot within each cohort. Repeated plots across cohorts are not counted as additional independent treatment replicates in a pooled test.",
  "Replicate numbers are identifiers, not established blocks linking different treatments. No cross-treatment blocking is inferred from R1/R2/R3 labels.",
  "PERMANOVA tests describe treatment associations under conditional exchangeability; they do not establish causal effects or document field randomization.",
  "For cohorts spanning collection dates, the treatment model adjusts for date and permutations are restricted within date. This does not remove all possible date-by-treatment confounding.",
  "Pooled mixed-date dispersion tests are omitted because date-related variation could compromise their interpretation. Dispersion distances remain descriptive.",
  paste0("Separate sensitivity outputs apply >=", sensitivity_min_reads, " target reads and, for Grenache 2024, August-only sampling. These never silently replace primary analyses."),
  "Benjamini-Hochberg correction is applied separately to treatment and dispersion tests, within domain and analysis variant, over this farm's tested cohorts. Sensitivity analyses are distinct families.",
  "Taxon-specific differential abundance, functional inference and cross-farm inference are not performed in this initial stage. No UniFrac distances are calculated without a phylogeny."
)
farm_notes <- switch(farm,
  OatmanFlatsRanch = c(
    "Oatman 2023-2024 sampled the original field; 2025 sampled a different field. Field and year changed together, so no pure 2025 year effect is estimable.",
    "Oatman treatment tests are separated by field and exact collection date. June and December 2024 represent different crop seasons and are separate cohorts.",
    "Plot trajectories use field-specific biological_plot_id values and do not join the original field to the 2025 field."
  ),
  PageSpringsCellars = c(
    "Page Springs treatments differ between cultivars; treatment comparisons are performed within cultivar and year.",
    "Grenache 2024 includes eight August 5 and two unique November 6 plot collections. Date-related differences can be discussed alongside plot variation; the design cannot fully separate them.",
    "Petite Sirah 2024 also spans August and November sampling dates. Date handling applies to both cultivars.",
    "Petite Sirah 2025 has one control biological plot after technical replicate merging; an omnibus treatment test is skipped when any treatment has fewer than two independent plots."
  ),
  Whipstone = c(
    "Annual and blackberry plots are separate systems at the same farm. Annual crop identity changes with year (lettuce, onion, marigold); crop and year effects cannot be separated.",
    "Blackberry cultivars are analyzed separately. Natchez and Chester occupy distinct physical rows, so apparent cultivar differences can also reflect field position.",
    "Stateland unplanted soil is a single biological collection assembled from technical replicates. It is a descriptive reference only and never enters treatment inference.",
    "Some 2024 annual and blackberry plots were sampled in July and others in August. The date-aware cohort model is used when identifiable."
  )
)
writeLines(c(design_notes, farm_notes,
  paste0("Primary minimum target reads configured for this run: ", min_reads, ".")), file.path(out_root, "INTERPRETATION_AND_DESIGN.txt"))

cohort_metadata <- function(md) {
  md$collection_date <- as.character(md$collection_date)
  parsed_dates <- as.Date(md$collection_date)
  if (anyNA(parsed_dates)) stop("Missing or invalid collection_date")
  md$year <- format(parsed_dates, "%Y")
  md$plot_id <- as.character(md$biological_plot_id)
  md$treatment_label <- as.character(md$treatment_code)
  if (anyNA(md$plot_id) || any(md$plot_id == "")) stop("Missing biological_plot_id")
  md$analytic_system <- if (farm == "OatmanFlatsRanch") {
    as.character(md$field_location_id)
  } else if (farm == "PageSpringsCellars") {
    paste0("Grape_", md$cultivar)
  } else {
    ifelse(md$plant_type == "Blackberry", paste0("Blackberry_", md$cultivar), as.character(md$plant_type))
  }
  if (anyNA(md$analytic_system) || any(md$analytic_system %in% c("", "NA"))) stop("Missing analytical system/field identifier")
  md$descriptive_reference <- md$plant_type == "Unplanted"
  md$treatment_label[is.na(md$treatment_label) | md$treatment_label %in% c("", "NA")] <- "Unspecified_reference"
  md$cohort <- if (farm == "OatmanFlatsRanch") {
    paste(md$analytic_system, md$collection_date, sep = "__")
  } else paste(md$analytic_system, md$year, sep = "__")
  if (farm == "OatmanFlatsRanch") {
    old <- md$year %in% c("2023", "2024")
    new <- md$year == "2025"
    if (length(intersect(unique(md$plot_id[old]), unique(md$plot_id[new])))) {
      stop("Oatman old-field and 2025 plot identifiers overlap; use the corrected metadata")
    }
    if (any(md$field_location_id[new] != "OFR_2025_Field") ||
        any(md$field_location_id[old] != "OFR_2023_2024_Field")) stop("Unexpected Oatman field identifiers")
  }
  md
}

ordination <- function(counts, md, folder, tag) {
  if (nrow(counts) < 3L || ncol(counts) < 1L) return("Skipped ordination: fewer than three samples or no ASVs")
  rel <- counts / rowSums(counts)
  distance <- vegan::vegdist(rel, method = "bray")
  if (!any(as.numeric(distance) > 0)) return("Skipped ordination: all community distances are zero")
  ord <- capture_notes(vegan::wcmdscale(distance, k = 2, eig = TRUE, add = "lingoes"))
  if (inherits(ord$value, "error")) return(paste("Ordination failed:", conditionMessage(ord$value)))
  coordinates <- ord$value$points
  if (is.null(coordinates) || ncol(coordinates) < 1L) return("Skipped ordination: no positive coordinate dimensions")
  if (ncol(coordinates) == 1L) coordinates <- cbind(coordinates, 0)
  eig <- ord$value$eig
  denominator <- sum(eig[eig > 0])
  percent <- if (denominator > 0) 100 * pmax(eig[seq_len(min(2, length(eig)))], 0) / denominator else c(NA, NA)
  if (length(percent) < 2L) percent <- c(percent, 0)
  coords <- data.frame(sample_name = rownames(coordinates), PCoA1 = coordinates[, 1], PCoA2 = coordinates[, 2])
  coords <- cbind(coords, md[match(coords$sample_name, md$sample_name), setdiff(names(md), "sample_name"), drop = FALSE])
  write_tsv(coords, file.path(folder, paste0(tag, "_ordination_coordinates.tsv")))
  write_tsv(data.frame(axis = seq_along(eig), eigenvalue = eig), file.path(folder, paste0(tag, "_ordination_eigenvalues.tsv")))
  writeLines(c("Bray-Curtis on target-domain relative abundance; PCoA with Lingoes correction.",
    paste0("Lingoes additive constant: ", ord$value$ac), ord$notes), file.path(folder, paste0(tag, "_ordination_method.txt")))
  trajectory <- coords[order(coords$plot_id, coords$collection_date), , drop = FALSE]
  repeated <- names(which(table(trajectory$plot_id) > 1))
  trajectory <- trajectory[trajectory$plot_id %in% repeated, , drop = FALSE]
  p <- ggplot(coords, aes(PCoA1, PCoA2, color = treatment_label))
  if (nrow(trajectory)) p <- p + geom_path(data = trajectory, aes(group = plot_id), alpha = 0.45, linewidth = 0.5)
  p <- p + geom_point(aes(shape = year), size = 2.6, alpha = 0.85) +
    facet_wrap(~analytic_system, scales = "fixed") +
    scale_color_discrete(labels = pretty_label) +
    labs(title = paste(farm, tag, "community composition"), color = "Treatment", shape = "Year",
      x = sprintf("PCoA1 (%.1f%%)", percent[1]), y = sprintf("PCoA2 (%.1f%%)", percent[2]),
      caption = "Bray-Curtis; Lingoes correction. Lines follow the same physical plot in date order.\nAxes summarize a descriptive within-farm ordination; field/crop and year may be confounded.") + theme_nrcs()
  save_plot(p, file.path(folder, paste0(tag, "_ordination.png")), height = 7.5)
  if (nzchar(ord$notes)) ord$notes else "Completed"
}

taxonomy_outputs <- function(counts, taxonomy, md, folder) {
  ranks <- c("Phylum", "Genus")
  for (rank in ranks) {
    col <- which(tolower(colnames(taxonomy)) == tolower(rank))
    if (length(col) != 1L) {
      writeLines(paste("Taxonomy rank unavailable:", rank), file.path(folder, paste0(tolower(rank), "_unavailable.txt")))
      next
    }
    labels <- as.character(taxonomy[colnames(counts), col])
    labels[is.na(labels) | labels == ""] <- paste0("Unclassified_", rank)
    totals <- t(rowsum(t(counts), group = labels, reorder = FALSE))
    totals <- totals[, colSums(totals) > 0, drop = FALSE]
    if (!ncol(totals)) next
    rel <- totals / rowSums(totals)
    long <- data.frame(sample_name = rep(rownames(rel), times = ncol(rel)),
      taxon = rep(colnames(rel), each = nrow(rel)), read_count = as.vector(totals), relative_abundance = as.vector(rel))
    long <- cbind(long, md[match(long$sample_name, md$sample_name),
      c("cohort", "collection_date", "year", "analytic_system", "treatment_label", "plot_id"), drop = FALSE])
    rownames(long) <- NULL
    write_tsv(long, file.path(folder, paste0(tolower(rank), "_relative_abundance.tsv")))
    top <- names(sort(colMeans(rel), decreasing = TRUE))[seq_len(min(15L, ncol(rel)))]
    plot_rel <- rel[, top, drop = FALSE]
    if (length(top) < ncol(rel)) plot_rel <- cbind(plot_rel, Other_taxa = rowSums(rel[, setdiff(colnames(rel), top), drop = FALSE]))
    pd <- data.frame(sample_name = rep(rownames(plot_rel), times = ncol(plot_rel)),
      taxon = rep(colnames(plot_rel), each = nrow(plot_rel)), relative_abundance = as.vector(plot_rel))
    pd$cohort <- md$cohort[match(pd$sample_name, md$sample_name)]
    sample_order <- md$sample_name[order(md$cohort, md$treatment_label, md$collection_date, md$plot_id)]
    pd$sample_name <- factor(pd$sample_name, levels = sample_order)
    p <- ggplot(pd, aes(sample_name, relative_abundance, fill = taxon)) + geom_col(width = 0.9) +
      facet_wrap(~cohort, scales = "free_x", ncol = 2) +
      labs(title = paste(farm, rank, "composition"), x = "Sample", y = "Proportion of target-domain reads", fill = rank,
        caption = "Top 15 taxa by mean sample relative abundance within this farm/domain; remaining taxa grouped as Other.\nUnclassified reads are retained. Different target domains have separate denominators.") +
      theme_nrcs() + theme(axis.text.x = element_text(angle = 90, hjust = 1, vjust = 0.5, size = 7))
    save_plot(p, file.path(folder, paste0(tolower(rank), "_relative_abundance.png")), width = 13,
      height = max(6, 2.6 * ceiling(length(unique(md$cohort)) / 2)))
  }
}

cohort_test <- function(counts, md, variant, folder, expected_treatments = NULL) {
  cohort <- unique(md$cohort)
  if (length(cohort) != 1L) stop("Internal error: more than one cohort")
  tr <- factor(md$treatment_label)
  dates <- factor(md$collection_date)
  n_by_treatment <- table(tr)
  missing_treatments <- setdiff(expected_treatments, names(n_by_treatment))
  mixed <- nlevels(dates) > 1L
  result <- data.frame(cohort = cohort, analysis_variant = variant, n_samples = nrow(md),
    n_treatments = nlevels(tr), n_collection_dates = nlevels(dates),
    treatment_counts = paste(names(n_by_treatment), as.integer(n_by_treatment), sep = ":", collapse = ";"),
    expected_treatments = paste(expected_treatments, collapse = ";"),
    treatments_not_represented = paste(missing_treatments, collapse = ";"),
    treatment_status = "not_tested", treatment_reason = "", formula = if (mixed) "Bray ~ collection_date + treatment" else "Bray ~ treatment",
    permutations_used = NA_integer_, residual_df = NA_integer_, treatment_R2 = NA_real_,
    treatment_F = NA_real_, treatment_p = NA_real_, treatment_q_BH = NA_real_,
    dispersion_status = "not_tested", dispersion_reason = "", dispersion_F = NA_real_,
    dispersion_p = NA_real_, dispersion_q_BH = NA_real_, warnings = "")
  reasons <- character()
  if (any(md$descriptive_reference)) reasons <- c(reasons, "Unplanted soil is a single descriptive reference")
  if (nlevels(tr) < 2L) reasons <- c(reasons, "Fewer than two represented treatments")
  if (any(n_by_treatment < 2L)) reasons <- c(reasons, "At least one treatment has fewer than two independent biological plots")
  if (length(missing_treatments)) reasons <- c(reasons, "At least one expected treatment is absent after sequence/depth selection")
  if (anyDuplicated(md$plot_id)) reasons <- c(reasons, "Repeated physical plot within cohort; independent-plot treatment test is not appropriate")
  if (nrow(counts) < 3L) reasons <- c(reasons, "Fewer than three nonzero-target samples")
  if (length(reasons)) {
    result$treatment_reason <- paste(reasons, collapse = "; ")
    result$dispersion_reason <- "Treatment cohort does not meet minimum design requirements"
    return(result)
  }
  model_data <- data.frame(treatment = tr, collection_date = dates)
  model_formula <- if (mixed) ~collection_date + treatment else ~treatment
  mm <- model.matrix(model_formula, model_data)
  result$residual_df <- nrow(mm) - qr(mm)$rank
  if (qr(mm)$rank < ncol(mm)) {
    result$treatment_reason <- "Treatment and collection date are not independently identifiable (rank-deficient model)"
    result$dispersion_reason <- "Confounded design; no pooled inference"
    return(result)
  }
  if (result$residual_df < 1L) {
    result$treatment_reason <- "No residual degrees of freedom"
    result$dispersion_reason <- "No residual degrees of freedom"
    return(result)
  }
  rel <- counts / rowSums(counts)
  distance <- vegan::vegdist(rel, method = "bray")
  if (!any(as.numeric(distance) > 0)) {
    result$treatment_reason <- "All community distances are zero"
    result$dispersion_reason <- result$treatment_reason
    return(result)
  }
  ctl <- if (mixed) permute::how(nperm = nperm_requested, blocks = dates) else permute::how(nperm = nperm_requested)
  perms <- capture_notes(permute::shuffleSet(nrow(md), nset = nperm_requested, control = ctl))
  if (inherits(perms$value, "error") || !nrow(perms$value)) {
    result$treatment_reason <- "No usable permutations for this design"
    result$dispersion_reason <- result$treatment_reason
    return(result)
  }
  permutation_matrix <- perms$value
  result$permutations_used <- nrow(permutation_matrix)
  notes <- perms$notes
  if (result$permutations_used < 99L) notes <- c(notes, "Few distinct permutations; p-value resolution is limited")
  fit <- capture_notes(if (mixed) {
    vegan::adonis2(distance ~ collection_date + treatment, data = model_data, permutations = permutation_matrix, by = "margin")
  } else {
    vegan::adonis2(distance ~ treatment, data = model_data, permutations = permutation_matrix, by = "margin")
  })
  notes <- c(notes, fit$notes)
  if (inherits(fit$value, "error")) {
    result$treatment_reason <- conditionMessage(fit$value)
  } else {
    tab <- as.data.frame(fit$value)
    if (mixed && "collection_date" %in% rownames(tab)) tab["collection_date", "Pr(>F)"] <- NA_real_
    interpretation <- ifelse(rownames(tab) == "treatment", "Exploratory treatment association",
      ifelse(rownames(tab) == "collection_date", "Adjustment term only; no date p-value is valid under within-date permutations", "Descriptive model accounting"))
    write_tsv(data.frame(term = rownames(tab), tab, test_interpretation = interpretation, check.names = FALSE), file.path(folder, paste0(slug(cohort), "_permanova.tsv")))
    if ("treatment" %in% rownames(tab) && is.finite(tab["treatment", "Pr(>F)"]) && is.finite(tab["treatment", "F"])) {
      result$treatment_status <- "tested_exploratory"
      result$treatment_reason <- if (mixed) "Date-adjusted marginal treatment association; permutations restricted within collection date" else "Treatment association within one collection occasion"
      result$treatment_R2 <- tab["treatment", "R2"]
      result$treatment_F <- tab["treatment", "F"]
      result$treatment_p <- tab["treatment", "Pr(>F)"]
    } else result$treatment_reason <- "Treatment statistic or p-value was not estimable"
  }
  dispersion <- capture_notes(vegan::betadisper(distance, tr, type = "median", bias.adjust = TRUE))
  notes <- c(notes, dispersion$notes)
  if (inherits(dispersion$value, "error")) {
    result$dispersion_reason <- conditionMessage(dispersion$value)
  } else {
    write_tsv(data.frame(sample_name = md$sample_name, treatment = as.character(tr), collection_date = md$collection_date,
      distance_to_treatment_median = dispersion$value$distances), file.path(folder, paste0(slug(cohort), "_dispersion_distances.tsv")))
    dispersion_distances <- as.numeric(dispersion$value$distances)
    invalid_dispersion_distances <- any(!is.finite(dispersion_distances))
    within_ss <- if (invalid_dispersion_distances) NA_real_ else sum((dispersion_distances - ave(dispersion_distances, tr, FUN = mean))^2)
    negligible_within_variation <- invalid_dispersion_distances || within_ss <= 100 * .Machine$double.eps * max(1, sum(dispersion_distances^2))
    if (mixed) {
      result$dispersion_status <- "descriptive_only"
      result$dispersion_reason <- "Multiple collection dates; pooled dispersion permutation test omitted because it would not adjust for date"
    } else if (all(n_by_treatment <= 2L) || negligible_within_variation) {
      result$dispersion_status <- "descriptive_only"
      result$dispersion_reason <- "Two samples per group, nonfinite distances, or negligible within-group variation makes the dispersion F test undefined or degenerate"
    } else {
      dp <- capture_notes(vegan::permutest(dispersion$value, permutations = permutation_matrix))
      notes <- c(notes, dp$notes)
      if (inherits(dp$value, "error")) {
        result$dispersion_reason <- conditionMessage(dp$value)
      } else {
        dtab <- as.data.frame(dp$value$tab)
        write_tsv(data.frame(term = rownames(dtab), dtab, check.names = FALSE), file.path(folder, paste0(slug(cohort), "_dispersion_test.tsv")))
        result$dispersion_F <- dtab[1, "F"]
        result$dispersion_p <- dtab[1, "Pr(>F)"]
        estimable_dispersion <- is.finite(result$dispersion_p) && is.finite(result$dispersion_F)
        result$dispersion_status <- if (estimable_dispersion) "tested_exploratory" else "not_estimable"
        result$dispersion_reason <- if (!estimable_dispersion) {
          result$dispersion_p <- NA_real_
          "Dispersion F statistic or p-value is nonfinite; no inference reported"
        } else if (length(unique(n_by_treatment)) > 1L) {
          "Unequal treatment group sizes; bias-adjusted distances to group medians; interpret with PERMANOVA"
        } else "Bias-adjusted distances to group medians; a nonsignificant test does not establish equal dispersion"
      }
    }
  }
  result$warnings <- paste(unique(notes[nzchar(notes)]), collapse = " | ")
  result
}

domain_status <- list()
for (domain in c("archaea", "bacteria")) {
  source <- file.path(run_dir, "dada2", domain, "phyloseq_target.rds")
  if (!file.exists(source)) stop("Required processed input missing: ", source)
  ps <- readRDS(source)
  if (!inherits(ps, "phyloseq")) stop("Input is not a phyloseq object: ", source)
  md <- as(phyloseq::sample_data(ps), "data.frame")
  md$sample_name <- rownames(md)
  if (any(md$sample_name %in% forbidden)) stop("Forbidden sample in processed input: ", paste(intersect(md$sample_name, forbidden), collapse = ", "))
  domain_pattern <- if (domain == "archaea") "^[AC][0-9]+$" else "^[BD][0-9]+$"
  if (any(!grepl(domain_pattern, md$sample_name))) stop("Unexpected or wrong-domain sample names in ", source)
  needed <- c("farm", "collection_date", "biological_plot_id", "treatment_code", "plant_type", "cultivar", "field_location_id")
  if (!all(needed %in% names(md))) stop("Missing metadata columns: ", paste(setdiff(needed, names(md)), collapse = ", "))
  md <- md[!is.na(md$farm) & md$farm == farm, , drop = FALSE]
  folder <- file.path(out_root, domain)
  dir.create(folder, recursive = TRUE, showWarnings = FALSE)
  full_meta_path <- file.path(run_dir, "metadata", paste0("NRCS_", domain, "_metadata.csv"))
  tracking_path <- file.path(run_dir, "dada2", domain, "read_tracking.tsv")
  if (!file.exists(full_meta_path) || !file.exists(tracking_path)) stop("Complete metadata and read tracking are required to report zero-retention samples")
  full_meta <- read.csv(full_meta_path, colClasses = "character", na.strings = "", check.names = FALSE)
  tracking <- read.delim(tracking_path, check.names = FALSE)
  id_col <- paste0(domain, "_sample")
  if (!all(c(id_col, needed) %in% names(full_meta)) || !all(c("sample_id", "target") %in% names(tracking))) stop("Incomplete metadata/read tracking columns")
  if (anyDuplicated(full_meta[[id_col]]) || anyDuplicated(tracking$sample_id) ||
      !setequal(full_meta[[id_col]], tracking$sample_id)) stop("Read tracking and approved metadata identities differ")
  if (any(full_meta[[id_col]] %in% forbidden) || any(!grepl(domain_pattern, full_meta[[id_col]]))) stop("Forbidden or wrong-domain sample in complete metadata")
  full_farm <- full_meta[full_meta$farm == farm, , drop = FALSE]
  full_farm$sample_name <- full_farm[[id_col]]
  full_farm <- cbind(full_farm, tracking[match(full_farm$sample_name, tracking$sample_id), setdiff(names(tracking), "sample_id"), drop = FALSE])
  if (anyNA(full_farm$target) || any(full_farm$target < 0)) stop("Invalid target read counts in complete farm tracking")
  if (!setequal(md$sample_name, full_farm$sample_name[full_farm$target > 0])) stop("Positive-target tracking and phyloseq sample identities disagree")
  full_farm <- cohort_metadata(full_farm)
  write_tsv(full_farm, file.path(folder, "farm_read_tracking_all_approved_samples.tsv"))
  write_tsv(full_farm[full_farm$target == 0, , drop = FALSE], file.path(folder, "samples_lost_before_farm_analysis.tsv"))
  complete_counts <- aggregate(list(n_approved_biological_collections = full_farm$sample_name),
    by = full_farm[c("analytic_system", "cohort", "collection_date", "treatment_label")], FUN = length)
  write_tsv(complete_counts, file.path(folder, "cohort_sample_counts_before_sequence_QC.tsv"))
  if (!nrow(md)) {
    domain_status[[domain]] <- data.frame(domain = domain, status = "no_retained_target_samples", sample_count = 0L, ordination_status = "not_applicable")
    next
  }
  md <- cohort_metadata(md)
  counts <- as(phyloseq::otu_table(ps), "matrix")
  if (phyloseq::taxa_are_rows(ps)) counts <- t(counts)
  counts <- counts[md$sample_name, , drop = FALSE]
  if (anyNA(counts) || any(counts < 0) || any(!is.finite(counts)) || any(abs(counts - round(counts)) > 1e-8)) stop("Invalid ASV counts")
  counts <- counts[, colSums(counts) > 0, drop = FALSE]
  if (anyDuplicated(md$sample_name)) stop("Duplicate sample names in metadata")
  md$target_read_count <- rowSums(counts)
  if (any(md$target_read_count != full_farm$target[match(md$sample_name, full_farm$sample_name)])) stop("Target phyloseq counts disagree with the read-tracking table")
  md$observed_ASVs <- rowSums(counts > 0)
  md$Shannon <- NA_real_
  positive <- md$target_read_count > 0
  if (any(positive)) md$Shannon[positive] <- vegan::diversity(counts[positive, , drop = FALSE], index = "shannon")
  md$depth_interpretation <- "Unrarefied; richness and Shannon are depth-dependent"
  md$primary_included <- positive & md$target_read_count >= min_reads
  md$primary_exclusion_reason <- ifelse(!positive, "Zero target-domain reads", ifelse(!md$primary_included, paste0("Below configured primary minimum of ", min_reads, " target reads"), ""))
  md$depth_sensitivity_included <- positive & md$target_read_count >= sensitivity_min_reads
  write_tsv(md, file.path(folder, "sample_readdepth_alpha_and_design.tsv"))
  write_tsv(md[!md$primary_included, , drop = FALSE], file.path(folder, "explicit_primary_exclusions.tsv"))
  write_tsv(md[order(md$plot_id, md$collection_date), , drop = FALSE], file.path(folder, "physical_plot_trajectories.tsv"))
  sample_counts <- aggregate(list(n_biological_collections = md$sample_name),
    by = md[c("analytic_system", "cohort", "collection_date", "treatment_label")], FUN = length)
  write_tsv(sample_counts, file.path(folder, "cohort_sample_counts_after_sequence_QC_before_depth_selection.tsv"))
  depth_plot <- ggplot(md, aes(treatment_label, target_read_count, color = treatment_label)) +
    geom_point(position = position_jitter(width = 0.12, height = 0, seed = 19), size = 2.1) +
    facet_wrap(~cohort, scales = "free_x", ncol = 2) + scale_y_continuous(trans = "log1p") +
    scale_x_discrete(labels = pretty_label) +
    labs(title = paste(farm, domain, "target-domain read depth"), x = "Treatment", y = "Target reads (log1p scale)",
      caption = "Counts after upstream quality processing and domain selection. All retained metadata samples are shown.") +
    theme_nrcs() + theme(legend.position = "none", axis.text.x = element_text(size = 7))
  save_plot(depth_plot, file.path(folder, "target_read_depth.png"), 12,
    max(6, 2.8 * ceiling(length(unique(md$cohort)) / 2)))
  if (any(positive)) {
    alpha_plot <- ggplot(md[positive, , drop = FALSE], aes(target_read_count, observed_ASVs, color = treatment_label)) +
    geom_point(size = 2.4) + scale_x_log10() + facet_wrap(~analytic_system) +
    scale_color_discrete(labels = pretty_label) +
    labs(title = paste(farm, domain, "observed richness and sequencing depth"), x = "Target reads (log10 scale)", y = "Observed ASVs",
      color = "Treatment", caption = "Unrarefied richness is descriptive and depth-dependent; it is not an estimate standardized across samples.") + theme_nrcs()
    save_plot(alpha_plot, file.path(folder, "richness_vs_read_depth.png"))
    shannon_plot <- ggplot(md[positive, , drop = FALSE], aes(target_read_count, Shannon, color = treatment_label)) +
    geom_point(size = 2.4) + scale_x_log10() + facet_wrap(~analytic_system) +
    scale_color_discrete(labels = pretty_label) +
    labs(title = paste(farm, domain, "Shannon diversity and sequencing depth"), x = "Target reads (log10 scale)", y = "Shannon diversity",
      color = "Treatment", caption = "Shannon diversity uses unrarefied target-domain ASV counts and remains sensitive to sampling depth.") + theme_nrcs()
    save_plot(shannon_plot, file.path(folder, "Shannon_vs_read_depth.png"))
  } else writeLines("All farm samples have zero target reads; alpha-diversity plots are not defined.", file.path(folder, "NO_POSITIVE_TARGET_READS.txt"))
  include <- md$primary_included
  ord_status <- "No samples passed the configured primary depth selection"
  if (any(include)) {
    taxonomy <- as(phyloseq::tax_table(ps), "matrix")
    taxonomy_outputs(counts[include, , drop = FALSE], taxonomy, md[include, , drop = FALSE], folder)
    ord_status <- ordination(counts[include, , drop = FALSE], md[include, , drop = FALSE], folder, domain)
  }
  selections <- list(primary = include)
  selections[[paste0("sensitivity_min", sensitivity_min_reads)]] <- md$depth_sensitivity_included
  if (farm == "PageSpringsCellars") {
    selections$sensitivity_Grenache2024_AugustOnly <- md$cultivar == "Grenache" & md$collection_date == "2024-08-05" & positive & md$target_read_count >= min_reads
  }
  tests <- list()
  for (variant in names(selections)) {
    selected <- selections[[variant]]
    variant_dir <- file.path(folder, variant)
    dir.create(variant_dir, showWarnings = FALSE)
    write_tsv(data.frame(sample_name = md$sample_name, included = selected,
      target_read_count = md$target_read_count, cohort = md$cohort), file.path(variant_dir, "sample_selection.tsv"))
    variant_md <- md[selected, , drop = FALSE]
    if (!nrow(variant_md)) {
      writeLines("No samples meet this explicitly labeled selection; no tests performed.", file.path(variant_dir, "NO_SAMPLES.txt"))
      next
    }
    for (cohort in unique(variant_md$cohort)) {
      inds <- which(selected & md$cohort == cohort)
      expected_treatments <- sort(unique(full_farm$treatment_label[full_farm$cohort == cohort]))
      tests[[length(tests) + 1L]] <- cohort_test(counts[inds, , drop = FALSE], md[inds, , drop = FALSE], variant, variant_dir, expected_treatments)
    }
  }
  if (length(tests)) {
    status <- do.call(rbind, tests)
    for (variant in unique(status$analysis_variant)) {
      for (kind in c("treatment", "dispersion")) {
        p_col <- paste0(kind, "_p")
        q_col <- paste0(kind, "_q_BH")
        inds <- which(status$analysis_variant == variant & is.finite(status[[p_col]]))
        if (length(inds)) status[[q_col]][inds] <- p.adjust(status[[p_col]][inds], method = "BH")
      }
    }
    status$domain <- domain
    status$farm <- farm
    write_tsv(status, file.path(folder, "analyses_status.tsv"))
  } else writeLines("No testable cohorts after selection.", file.path(folder, "NO_TESTABLE_COHORTS.txt"))
  domain_status[[domain]] <- data.frame(domain = domain, status = "completed_initial_farm_analysis",
    sample_count = nrow(md), ordination_status = ord_status)
}
write_tsv(do.call(rbind, domain_status), file.path(out_root, "farm_stage_status.tsv"))
capture.output(sessionInfo(), file = file.path(out_root, "sessionInfo.txt"))
writeLines(format(Sys.time(), tz = "UTC", usetz = TRUE), file.path(out_root, "FARM_ANALYSIS_COMPLETE.txt"))
message("Completed initial farm analyses: ", out_root)
