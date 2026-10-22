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
  perms <- capture_notes(stable_permutations(md, ctl))
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
