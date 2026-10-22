#!/usr/bin/env Rscript
# Scientific rendering is authorized only on a Puma Slurm compute allocation.
# The Python launcher also verifies the allocation against scontrol.
if (!nzchar(Sys.getenv("SLURM_JOB_ID")) ||
    tolower(Sys.getenv("SLURM_CLUSTER_NAME")) != "puma") {
  stop("Scientific rendering requires a Puma Slurm compute allocation.")
}
argv <- commandArgs(trailingOnly = TRUE)
if (length(argv) != 1L) stop("Usage: render_report.R RUN_DIRECTORY")
run_dir <- normalizePath(argv[[1L]], mustWork = TRUE)
results_dir <- file.path(run_dir, "results")
figures_dir <- file.path(run_dir, "figures")
dir.create(figures_dir, recursive = TRUE, showWarnings = FALSE)

read_table <- function(name, required, optional = FALSE) {
  path <- file.path(results_dir, name)
  if (!file.exists(path)) {
    if (optional) return(NULL)
    stop("Missing report input: ", path)
  }
  tab <- read.csv(path, stringsAsFactors = FALSE, check.names = FALSE,
                  na.strings = c("", "NA", "NaN", "null", "None"))
  absent <- setdiff(required, names(tab))
  if (length(absent)) stop(name, " is missing columns: ", paste(absent, collapse = ", "))
  tab
}
ordination <- read_table("ordination.csv", c(
  "domain", "variant", "scope", "farm", "analytic_system", "family",
  "analysis_sample", "biological_plot_id", "collection_date", "treatment",
  "axis1", "axis2", "axis1_variance_fraction", "axis2_variance_fraction"))
summaries <- read_table("feature_summaries.csv", c(
  "domain", "variant", "scope", "farm", "analytic_system", "family",
  "function", "feature_name", "collection_date", "treatment", "n_plots",
  "mean_relative_abundance", "min_relative_abundance", "max_relative_abundance"))
robustness <- read_table("robustness.csv", c(
  "hypothesis_id", "test_family", "domain", "scope", "farm", "analytic_system",
  "family", "function", "feature_name", "contrast", "p_original", "p_filtered",
  "p_conservative", "holm_p_conservative", "direction_agreement", "status",
  "effect_original", "effect_filtered", "conditional_support_in_all_versions"))
global_tests <- read_table("global_tests.csv", character(), optional = TRUE)
targeted_tests <- read_table("targeted_tests.csv", character(), optional = TRUE)
summary_path <- file.path(results_dir, "RUN_SUMMARY.txt")
if (!file.exists(summary_path)) stop("Missing results/RUN_SUMMARY.txt")
run_summary <- readLines(summary_path, warn = FALSE)
panel_paths <- c(file.path(run_dir, "FEATURE_PANEL.csv"),
                 file.path(results_dir, "FEATURE_PANEL.csv"),
                 file.path(run_dir, "code", "FEATURE_PANEL.csv"))
panel_paths <- panel_paths[file.exists(panel_paths)]
if (!length(panel_paths)) stop("Missing FEATURE_PANEL.csv")
panel <- read.csv(panel_paths[[1L]], stringsAsFactors = FALSE, check.names = FALSE)
if (!all(c("family", "function", "feature_name") %in% names(panel))) {
  stop("FEATURE_PANEL.csv must contain family, function and feature_name.")
}
panel$family <- toupper(panel$family)
if ("report_order" %in% names(panel)) {
  panel <- panel[order(panel$family, panel$report_order, panel[["function"]]), , drop = FALSE]
}
if (anyDuplicated(paste(panel$family, panel[["function"]], sep = "|"))) {
  stop("Duplicate family/function identities in FEATURE_PANEL.csv")
}
primary <- function(x) x[!is.na(x$scope) & x$scope == "primary", , drop = FALSE]
ordination <- primary(ordination)
summaries <- primary(summaries)
robustness <- primary(robustness)
ordination$family <- toupper(ordination$family)
summaries$family <- toupper(summaries$family)
robustness$family <- toupper(robustness$family)
ordination$plot_date <- as.Date(ordination$collection_date)
summaries$plot_date <- as.Date(summaries$collection_date)
if (any(is.na(ordination$plot_date)) || any(is.na(summaries$plot_date))) {
  stop("Figure inputs contain missing or invalid ISO collection dates.")
}

display_label <- function(x) {
  x <- as.character(x)
  mapping <- c(
    "Grape_Grenache" = "Grenache", "Grape_PetiteSirah" = "Petite Sirah",
    "OFR_2023_2024_Field" = "2023-2024 field", "OFR_2025_Field" = "2025 field",
    "Blackberry_Chester" = "Chester blackberry", "Blackberry_Natchez" = "Natchez blackberry",
    "Control_fertigation_only" = "Control (fertigation only)",
    "Cover_crop_only" = "Cover crop only", "Cover_crop_and_sheep" = "Cover crop + sheep",
    "Composted_grape_pomace_and_horse_manure" = "Composted grape pomace + horse manure",
    "Composted_horse_manure" = "Composted horse manure",
    "Fresh_grape_pomace" = "Fresh grape pomace", "Steer_manure" = "Steer manure",
    "Fish_manure" = "Fish manure", "Horse_manure_pomace" = "Horse manure + pomace",
    "Blood_meal_and_Feather_meal" = "Blood meal + feather meal",
    "Blood_meal_and_Feather_meal_and_cover_crop" = "Blood meal + feather meal + cover crop",
    "domain_concordant" = "Ten flagged ASVs excluded",
    "filtered" = "Ten flagged ASVs excluded", "original" = "Original predictions",
    "METACYC" = "MetaCyc", "bacteria" = "Bacteria", "archaea" = "Archaea")
  hit <- match(x, names(mapping))
  use <- !is.na(hit)
  x[use] <- unname(mapping[hit[use]])
  x <- gsub("_", " ", x, fixed = TRUE)
  x
}
wrapped <- function(x, width = 35L) {
  vapply(as.character(x), function(z) paste(strwrap(z, width = width), collapse = "\n"), "")
}
safe <- function(x) gsub("[^A-Za-z0-9_.-]+", "_", as.character(x))
fmt <- function(x) {
  x <- suppressWarnings(as.numeric(x))
  ifelse(is.finite(x), formatC(x, digits = 3, format = "g"), "NA")
}
group_fields <- c("domain", "variant", "farm", "analytic_system")
group_key <- function(x) {
  if (!nrow(x)) return(character())
  do.call(paste, c(x[group_fields], sep = "\034"))
}
all_treatments <- sort(unique(c(as.character(ordination$treatment),
                               as.character(summaries$treatment))))
all_treatments <- all_treatments[!is.na(all_treatments) & nzchar(all_treatments)]
okabe_ito <- c("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000", "#999999")
colors <- if (length(all_treatments) <= length(okabe_ito)) {
  okabe_ito[seq_along(all_treatments)]
} else {
  hcl(h = seq(15, 375, length.out = length(all_treatments) + 1L)[-1L], c = 65, l = 48)
}
names(colors) <- all_treatments
family_label <- c(EC = "EC enzymes", KO = "KEGG orthologs", METACYC = "MetaCyc pathways")
page_title <- function(data) {
  paste(display_label(data$domain[[1L]]), "|", display_label(data$farm[[1L]]), "|",
        display_label(data$analytic_system[[1L]]), "|", display_label(data$variant[[1L]]))
}
figure_manifest <- data.frame(file = character(), description = character(),
                              bytes = numeric(), stringsAsFactors = FALSE)
record_figure <- function(path, description) {
  if (!file.exists(path) || file.info(path)$size <= 0) stop("Figure was not created: ", path)
  figure_manifest <<- rbind(figure_manifest, data.frame(
    file = substring(path, nchar(run_dir) + 2L), description = description,
    bytes = file.info(path)$size, stringsAsFactors = FALSE))
}
open_pdf <- function(path, width = 13.2, height = 8.5) {
  pdf(path, width = width, height = height, family = "Helvetica",
      pointsize = 11, useDingbats = FALSE, onefile = TRUE, paper = "special")
}
audit_footer <- function(text = "Predicted functional potential; primary scope. Generated on Puma; visual review pending.") {
  mtext(text, side = 1, outer = TRUE, line = 0.5, cex = 0.72, col = "#444444")
}
empty_panel <- function(message) {
  plot.new()
  text(0.5, 0.5, wrapped(message, 40), cex = 0.92, col = "#555555")
}

# Presentation only: tick labels remain in the plotted units. Tiny scales use
# compact scientific notation; ordinary scales omit redundant trailing zeros.
trim_decimal <- function(x) {
  x <- sub("(\\.[0-9]*?)0+$", "\\1", x, perl = TRUE)
  sub("\\.$", "", x)
}
compact_y_ticks <- function(ticks) {
  if (!length(ticks) || any(!is.finite(ticks))) stop("Invalid y-axis ticks.")
  nonzero <- abs(ticks[ticks != 0])
  scientific <- length(nonzero) > 0L &&
    (max(nonzero) < 0.01 || max(nonzero) >= 100000)
  if (scientific) {
    raw <- strsplit(formatC(ticks, format = "e", digits = 3L), "e", fixed = TRUE)
    labels <- vapply(raw, function(x) {
      paste0(trim_decimal(x[[1L]]), "e", as.integer(x[[2L]]))
    }, "")
  } else {
    steps <- diff(sort(unique(ticks)))
    steps <- steps[steps > 0]
    digits <- if (length(steps)) max(0L, ceiling(-log10(min(steps))) + 1L) else 3L
    labels <- trim_decimal(formatC(ticks, format = "f", digits = min(10L, digits)))
  }
  labels[ticks == 0] <- "0"
  labels
}

# Measure text on the active PDF device before opening the panel. The left
# margin reserves tick width, a clear gap, each rotated title line, and an outer
# buffer. Explicit title positions prevent multiline labels intruding on ticks.
prepare_y_axis <- function(values, title, base_mar, cex.axis = 1, cex.lab = 1) {
  values <- values[is.finite(values)]
  if (!length(values)) stop("Cannot plan an axis without finite coordinates.")
  ticks <- pretty(range(values), n = par("lab")[[2L]])
  labels <- compact_y_ticks(ticks)
  title_lines <- strsplit(title, "\n", fixed = TRUE)[[1L]]
  line_inches <- par("csi") * par("mex")
  if (!is.finite(line_inches) || line_inches <= 0) stop("Invalid graphics line height.")
  tick_width <- max(strwidth(labels, units = "inches", cex = cex.axis)) / line_inches
  title_height <- max(strheight(title_lines, units = "inches", cex = cex.lab)) / line_inches
  tick_line <- 0.70
  title_start <- tick_line + tick_width + 0.75 + title_height / 2
  title_step <- max(cex.lab, title_height) * 1.15
  title_at <- title_start + rev(seq_along(title_lines) - 1L) * title_step
  mar <- base_mar
  mar[[2L]] <- max(base_mar[[2L]], max(title_at) + title_height / 2 + 0.65)
  par(mar = mar)
  list(ticks = ticks, labels = labels, title = title_lines, title_at = title_at,
       tick_line = tick_line, cex.axis = cex.axis, cex.lab = cex.lab)
}
draw_y_axis <- function(plan) {
  visible <- plan$ticks >= par("usr")[[3L]] & plan$ticks <= par("usr")[[4L]]
  axis(2, at = plan$ticks[visible], labels = plan$labels[visible], las = 1,
       cex.axis = plan$cex.axis, mgp = c(0, plan$tick_line, 0))
  for (i in seq_along(plan$title)) {
    mtext(plan$title[[i]], side = 2, line = plan$title_at[[i]],
          cex = plan$cex.lab, las = 0, padj = 0.5)
  }
}

plot_ordination <- function(data) {
  par(oma = c(2.0, 0.4, 3.2, 0.4), mar = c(4.4, 4.5, 2.2, 1.0),
      mgp = c(2.8, 0.8, 0), las = 1, tcl = -0.25, col.axis = "#333333")
  layout(matrix(c(1, 2, 3, 4, 4, 4), nrow = 2, byrow = TRUE), heights = c(4.2, 1.4))
  par(cex = 1)
  base_mar <- c(4.4, 4.5, 2.2, 1.0)
  families <- c("EC", "KO", "METACYC")
  dates <- sort(unique(data$plot_date))
  date_symbols <- setNames(rep(c(16L, 17L, 15L, 18L, 3L, 4L, 8L, 0L, 1L, 2L),
                              length.out = length(dates)), as.character(dates))
  for (family in families) {
    par(mar = base_mar)
    part <- data[data$family == family, , drop = FALSE]
    part <- part[is.finite(part$axis1) & is.finite(part$axis2), , drop = FALSE]
    if (!nrow(part)) {
      empty_panel(paste(family_label[[family]], "ordination unavailable"))
      next
    }
    fraction <- function(field) {
      val <- part[[field]][is.finite(part[[field]])]
      if (!length(val)) return("variance unavailable")
      if (diff(range(val)) > 1e-8) stop("Inconsistent PCA variance fractions within a plotted cohort.")
      paste0(formatC(100 * val[[1L]], digits = 1, format = "f"), "%")
    }
    y_axis <- prepare_y_axis(part$axis2,
                             paste0("PC2 (", fraction("axis2_variance_fraction"), ")"),
                             base_mar)
    plot(part$axis1, part$axis2, type = "n", bty = "l", yaxt = "n",
         xlab = paste0("PC1 (", fraction("axis1_variance_fraction"), ")"),
         ylab = "",
         main = family_label[[family]], cex.main = 1.1)
    draw_y_axis(y_axis)
    abline(h = 0, v = 0, col = "#E7E7E7", lwd = 0.8)
    plot_ids <- unique(part$biological_plot_id)
    plot_ids <- plot_ids[!is.na(plot_ids) & nzchar(plot_ids)]
    for (plot_id in plot_ids) {
      trajectory <- part[part$biological_plot_id == plot_id & !is.na(part$biological_plot_id), , drop = FALSE]
      trajectory <- trajectory[order(trajectory$plot_date), , drop = FALSE]
      if (anyDuplicated(trajectory$plot_date)) {
        stop("Duplicate physical plot/date in ordination data: ", plot_id)
      }
      if (nrow(trajectory) > 1L) lines(trajectory$axis1, trajectory$axis2, col = "#A7A7A7", lwd = 0.75)
    }
    points(part$axis1, part$axis2, col = colors[part$treatment],
           pch = unname(date_symbols[as.character(part$plot_date)]), cex = 1.04)
  }
  par(mar = c(0.3, 0.8, 0.2, 0.8))
  plot.new()
  treatments <- all_treatments[all_treatments %in% data$treatment]
  legend(0.01, 0.98, legend = wrapped(display_label(treatments), 36), col = colors[treatments],
         pch = 16L, bty = "n", cex = 0.8, xjust = 0, yjust = 1, ncol = min(2L, length(treatments)),
         x.intersp = 0.7, y.intersp = 1.0)
  legend(0.70, 0.98, legend = format(dates, "%d %b %Y"), pch = unname(date_symbols),
         col = "#333333", bty = "n", cex = 0.8, xjust = 0, yjust = 1, y.intersp = 1.05)
  mtext(page_title(data), side = 3, outer = TRUE, line = 1.5, font = 2, cex = 1.14)
  mtext("PCA of Hellinger-transformed relative profiles; gray lines join the same physical plot in date order.",
        side = 3, outer = TRUE, line = 0.1, cex = 0.82)
  audit_footer()
  layout(1L)
}

plot_profiles <- function(data, selected_panel, family, report = FALSE) {
  feature_ids <- as.character(selected_panel[["function"]])
  n <- length(feature_ids)
  if (!n) stop("No curated features provided to plot_profiles")
  if (report) {
    if (n > 5L) stop("Review profile pages accept at most five curated features.")
    nc <- min(3L, n)
    nr <- ceiling(n / nc)
    par(oma = c(2.3, 0.3, 3.2, 0.3), mar = c(3.7, 4.3, 4.4, 0.7),
        mgp = c(2.8, 0.65, 0), las = 1, tcl = -0.2)
  } else {
    nc <- 2L
    nr <- ceiling(n / nc)
    par(oma = c(2.3, 0.3, 3.2, 0.3), mar = c(3.7, 4.4, 4.2, 0.8),
        mgp = c(2.8, 0.65, 0), las = 1, tcl = -0.2)
  }
  # A separate layout row reserves legend space; it never overlays plot axes.
  panel_layout <- matrix(seq_len(nr * nc), nrow = nr, ncol = nc, byrow = TRUE)
  panel_layout <- rbind(panel_layout, rep(nr * nc + 1L, nc))
  layout(panel_layout, heights = c(rep(if (report) 3.0 else 2.8, nr), 1.15))
  par(cex = 1)
  base_mar <- if (report) c(3.7, 4.3, 4.4, 0.7) else c(3.7, 4.4, 4.2, 0.8)
  dates <- sort(unique(data$plot_date))
  # Actual dates remain labelled; equal event spacing avoids overlapping close visits.
  date_at <- seq_along(dates)
  treatments <- all_treatments[all_treatments %in% data$treatment]
  for (i in seq_along(feature_ids)) {
    par(mar = base_mar)
    fid <- feature_ids[[i]]
    part <- data[data$family == family & as.character(data[["function"]]) == fid, , drop = FALSE]
    title <- paste0(if (family == "EC") paste0("EC ", fid) else fid, "\n",
                    wrapped(selected_panel$feature_name[[i]], if (report) 34L else 48L))
    unavailable <- "in_original_table" %in% names(part) && nrow(part) > 0L &&
      all(tolower(as.character(part$in_original_table)) %in% c("false", "0"))
    if (!nrow(part) || unavailable) {
      empty_panel(paste(title, if (unavailable) "Prespecified ID absent from this table; untested" else "No profile rows returned", sep = "\n"))
      next
    }
    values <- c(part$mean_relative_abundance, part$min_relative_abundance,
                part$max_relative_abundance)
    values <- values[is.finite(values)]
    if (!length(values) || any(values < -1e-12)) stop("Invalid relative abundance in feature summaries.")
    # A fixed percentage scale is used for all panels; the vertical range varies by feature.
    upper <- max(100 * values, 0.000001) * 1.08
    xlim <- range(date_at) + c(-0.16, 0.16)
    if (length(date_at) == 1L) xlim <- c(0.6, 1.4)
    y_axis <- prepare_y_axis(c(0, upper), "Predicted relative\nabundance (%)", base_mar,
                             cex.axis = if (report) 0.82 else 0.84,
                             cex.lab = if (report) 0.84 else 0.88)
    plot(NA_real_, NA_real_, xlim = xlim, ylim = c(0, upper), type = "n", bty = "l",
         xaxt = "n", yaxt = "n", xlab = "Collection event", ylab = "",
         main = title, cex.main = if (report) 0.90 else 0.95,
         cex.lab = if (report) 0.84 else 0.88, cex.axis = if (report) 0.82 else 0.84)
    draw_y_axis(y_axis)
    axis(1, at = date_at, labels = format(dates, "%d %b\n%Y"),
         cex.axis = if (report) 0.73 else 0.81, mgp = c(2.3, 0.55, 0))
    abline(h = pretty(c(0, upper)), col = "#EEEEEE", lwd = 0.65)
    for (treatment in treatments) {
      sub <- part[!is.na(part$treatment) & part$treatment == treatment, , drop = FALSE]
      sub <- sub[order(sub$plot_date), , drop = FALSE]
      if (!nrow(sub)) next
      if (anyDuplicated(sub$plot_date)) stop("Duplicate treatment/date in feature summaries.")
      x <- match(sub$plot_date, dates)
      y <- 100 * sub$mean_relative_abundance
      col <- colors[[treatment]]
      segments(x, 100 * sub$min_relative_abundance, x, 100 * sub$max_relative_abundance,
               col = adjustcolor(col, alpha.f = 0.6), lwd = 1.0)
      lines(x, y, col = col, lwd = 1.2)
      points(x, y, col = col, pch = 16L, cex = if (report) 0.7 else 0.9)
    }
  }
  if (n < nr * nc) for (i in seq_len(nr * nc - n)) plot.new()
  par(mar = c(0.2, 0.6, 0.2, 0.6))
  plot.new()
  legend("center", legend = wrapped(display_label(treatments), if (report) 42L else 48L),
         col = colors[treatments], pch = 16L, lty = 1L, bty = "n", cex = 0.76,
         ncol = min(2L, length(treatments)), x.intersp = 0.7, y.intersp = 1.0)
  mtext(paste(page_title(data), "|", family_label[[family]]), side = 3, outer = TRUE,
        line = 1.5, font = 2, cex = 1.05)
  mtext("Points are treatment means; ranges are plot minima-maxima. Actual dated events are equally spaced.",
        side = 3, outer = TRUE, line = 0.1, cex = 0.78)
  audit_footer("Predicted functional potential; connecting treatment means does not identify a plot trajectory. Generated on Puma; visual review pending.")
  layout(1L)
}

text_page <- function(lines, title, subtitle = NULL) {
  par(mfrow = c(1L, 1L), oma = c(0, 0, 0, 0), mar = c(1, 1, 1, 1))
  plot.new()
  plot.window(xlim = c(0, 1), ylim = c(0, 1))
  text(0.04, 0.94, title, adj = c(0, 1), cex = 1.55, font = 2)
  if (!is.null(subtitle)) text(0.04, 0.875, subtitle, adj = c(0, 1), cex = 0.94, col = "#444444")
  y <- seq(0.80, 0.10, length.out = 40L)
  text(rep(0.04, length(lines)), y[seq_along(lines)], lines, adj = c(0, 1), cex = 0.87)
  text(0.04, 0.045, "Generated on Puma; visual review pending. Predictions describe functional potential, not measured activity or flux.",
       adj = c(0, 0), cex = 0.79, col = "#444444")
}

robust_table_page <- function(tab, page_number, total_pages) {
  par(mfrow = c(1L, 1L), oma = c(0, 0, 0, 0), mar = c(1, 1, 1, 1))
  plot.new()
  plot.window(xlim = c(0, 1), ylim = c(0, 1))
  text(0.02, 0.965, "Primary hypotheses supported across prediction versions", adj = c(0, 1), font = 2, cex = 1.34)
  text(0.02, 0.915, paste0("Primary scope | Table ", page_number, " of ", total_pages,
                          " | Conservative Holm p <= 0.05; targeted effect directions agree."),
       adj = c(0, 1), cex = 0.86, col = "#444444")
  widths <- c(0.075, 0.12, 0.075, 0.235, 0.185, 0.105, 0.08, 0.085)
  edges <- c(0.01, 0.01 + cumsum(widths) * 0.98)
  centers <- head(edges, -1) + diff(edges) / 2
  headers <- c("Domain", "System", "Family", "Feature", "Contrast", "Effect\noriginal / filtered", "Holm p", "Direction /\nversions")
  rect(edges[[1L]], 0.825, tail(edges, 1L), 0.882, col = "#EEF2F6", border = NA)
  text(centers, 0.853, headers, cex = 0.77, font = 2)
  ytop <- 0.812
  for (i in seq_len(nrow(tab))) {
    row <- tab[i, , drop = FALSE]
    feature <- if (!is.na(row$feature_name) && nzchar(row$feature_name)) {
      paste(display_label(row$feature_name), row[["function"]], sep = "\n")
    } else {
      if (is.na(row[["function"]]) || !nzchar(row[["function"]])) "Whole profile" else as.character(row[["function"]])
    }
    direction_map <- c("NOT_APPLICABLE_GLOBAL" = "Global test",
                       "SINGLE_BACTERIAL_VERSION" = "Single version",
                       "AGREE" = "Agree", "DISAGREE_OR_NOT_ESTIMABLE" = "Disagree / unavailable")
    status_map <- c("TESTED_BOTH" = "Both tested", "TESTED_BACTERIA" = "Bacteria tested",
                    "NOT_ESTIMABLE" = "Unavailable")
    direction <- as.character(row$direction_agreement)
    status <- as.character(row$status)
    if (direction %in% names(direction_map)) direction <- unname(direction_map[[direction]])
    if (status %in% names(status_map)) status <- unname(status_map[[status]])
    contrast <- display_label(row$contrast)
    if ("collection_date" %in% names(row) && !is.na(row$collection_date) && nzchar(row$collection_date)) {
      contrast <- paste(contrast, row$collection_date, sep = "\n")
    }
    cells <- c(wrapped(display_label(row$domain), 11L), wrapped(display_label(row$analytic_system), 19L),
               display_label(row$family), wrapped(feature, 34L), wrapped(contrast, 27L),
               paste(fmt(row$effect_original), fmt(row$effect_filtered), sep = "\n"),
               fmt(row$holm_p_conservative), wrapped(paste(direction, status, sep = "\n"), 13L))
    lines_n <- max(lengths(strsplit(cells, "\n", fixed = TRUE)))
    height <- max(0.069, 0.023 * lines_n + 0.015)
    if (ytop - height < 0.10) stop("Robustness table labels exceed the page bounds.")
    if (i %% 2L == 0L) rect(edges[[1L]], ytop - height, tail(edges, 1L), ytop,
                           col = "#F7F8FA", border = NA)
    text(centers, rep(ytop - 0.012, length(cells)), cells, adj = c(0.5, 1), cex = 0.77)
    segments(edges[[1L]], ytop - height, tail(edges, 1L), ytop - height, col = "#DDDDDD", lwd = 0.6)
    ytop <- ytop - height
  }
  text(0.02, 0.067, "Small p values do not establish measured activity; inspect effect direction, replication and ASV sensitivity before interpretation.",
       adj = c(0, 0), cex = 0.8, col = "#444444")
  text(0.02, 0.028, "Generated on Puma; visual review pending.", adj = c(0, 0), cex = 0.79, col = "#444444")
}

ordination_keys <- unique(group_key(ordination))
profile_keys <- unique(group_key(summaries))
ordination_pages <- list()
profile_pages <- list()
for (key in ordination_keys) {
  data <- ordination[group_key(ordination) == key, , drop = FALSE]
  stem <- paste(vapply(data[1L, group_fields, drop = FALSE], safe, ""), collapse = "_")
  path <- file.path(figures_dir, paste0("ordination_", stem, ".pdf"))
  open_pdf(path)
  tryCatch(plot_ordination(data), finally = dev.off())
  record_figure(path, paste("Primary EC, KO and MetaCyc PCA:", page_title(data)))
  ordination_pages[[length(ordination_pages) + 1L]] <- data
}
for (key in profile_keys) {
  data <- summaries[group_key(summaries) == key, , drop = FALSE]
  stem <- paste(vapply(data[1L, group_fields, drop = FALSE], safe, ""), collapse = "_")
  for (family in unique(panel$family)) {
    selected_panel <- panel[panel$family == family, , drop = FALSE]
    if (!nrow(selected_panel)) next
    path <- file.path(figures_dir, paste0("targeted_profiles_", stem, "_", safe(family), ".pdf"))
    open_pdf(path, width = 11.7, height = max(8.5, 2.65 * ceiling(nrow(selected_panel) / 2L) + 3.15))
    tryCatch(plot_profiles(data, selected_panel, family, report = FALSE), finally = dev.off())
    record_figure(path, paste("Curated predicted relative-abundance means and plot ranges:", page_title(data), family))
    profile_pages[[length(profile_pages) + 1L]] <- list(data = data, panel = selected_panel, family = family)
  }
}

conditional_support <- !is.na(robustness$conditional_support_in_all_versions) &
  tolower(as.character(robustness$conditional_support_in_all_versions)) %in% c("true", "1")
significant <- robustness[conditional_support & is.finite(robustness$holm_p_conservative) &
                           robustness$holm_p_conservative <= 0.05, , drop = FALSE]
significant <- significant[order(significant$holm_p_conservative, significant$hypothesis_id), , drop = FALSE]
count_message <- c(
  paste("Ordination cohorts rendered:", length(ordination_pages)),
  paste("Curated profile figures rendered:", length(profile_pages)),
  paste("Primary hypotheses in robustness audit:", nrow(robustness)),
  paste("Primary hypotheses supported across applicable versions (conservative Holm p <= 0.05):", nrow(significant)),
  if (!is.null(global_tests)) paste("Global-test records (all scopes/variants):", nrow(global_tests)),
  if (!is.null(targeted_tests)) paste("Targeted-test records (all scopes/variants):", nrow(targeted_tests)),
  "",
  "Point and treatment-mean lines are descriptive. Plot-range bars are not confidence intervals.",
  "Original and filtered archaeal predictions are reported separately; the conservative audit retains both.",
  "The report plots primary scope only. Depth-sensitivity results and untestable hypotheses remain in the CSV audit.",
  "Treatment, date and repeated-plot inference use the frozen Python protocol. No tests are recomputed by this report.")
summary_lines <- unlist(lapply(c(run_summary, "", count_message), function(z) {
  if (!nzchar(z)) "" else strwrap(z, width = 136L)
}), use.names = FALSE)
summary_chunks <- split(summary_lines, ceiling(seq_along(summary_lines) / 40L))
report_path <- file.path(results_dir, "functional_comparison_review.pdf")
open_pdf(report_path)
tryCatch({
  for (i in seq_along(summary_chunks)) {
    text_page(summary_chunks[[i]], if (i == 1L) "NRCS functional-comparison review" else "Execution and analysis summary",
              paste0("Puma Slurm job ", Sys.getenv("SLURM_JOB_ID"), " | ",
                     basename(run_dir), " | Summary ", i, "/", length(summary_chunks)))
  }
  if (!nrow(significant)) {
    text_page(c("No primary hypothesis met the conditional-support display criterion across applicable prediction versions.",
                "The criterion requires conservative Holm p <= 0.05 and agreeing directions for targeted archaeal effects.",
                "This page does not establish equivalence or the absence of a biological difference.",
                "Review the full effect estimates, permutation resolution, replication and hold reasons in results/robustness.csv.",
                "All eligible and untestable hypotheses remain in the analysis audit."),
              "Conservative inference display", "Primary scope; effect estimates and uncertainty require review.")
  } else {
    # Five rows per page allow four-line feature and contrast labels without clipping.
    table_chunks <- split(seq_len(nrow(significant)), ceiling(seq_len(nrow(significant)) / 5L))
    for (i in seq_along(table_chunks)) {
      robust_table_page(significant[table_chunks[[i]], , drop = FALSE], i, length(table_chunks))
    }
  }
  for (data in ordination_pages) plot_ordination(data)
  for (item in profile_pages) {
    page_chunks <- split(seq_len(nrow(item$panel)), ceiling(seq_len(nrow(item$panel)) / 5L))
    for (indices in page_chunks) {
      plot_profiles(item$data, item$panel[indices, , drop = FALSE], item$family, report = TRUE)
    }
  }
}, finally = dev.off())
record_figure(report_path, "Puma-generated review report: execution summary, conservative inference audit, primary ordinations and curated profiles")
write.csv(figure_manifest, file.path(results_dir, "figure_manifest.csv"), row.names = FALSE, na = "")
cat("REPORT_CREATED=", report_path, "\n", sep = "")
cat("FIGURE_COUNT=", nrow(figure_manifest), "\n", sep = "")
