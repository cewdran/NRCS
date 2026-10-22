#!/usr/bin/env Rscript

# NRCS 16S processing, one complete domain at a time.
# Usage: Rscript scripts/02_dada2.R archaea /absolute/path/to/run
# Based on the corrected NIFA Organics 39-sample executables (2026-08-22).
# The corrected executables use pseudo-pooling for BOTH domains. The older
# manuscript's statement of full archaeal pooling is not used as executable
# evidence. Reads reaching this script have already had their primers removed.

options(stringsAsFactors = FALSE, warn = 1)
excluded_samples <- c("C108", "C111", "D108", "D111")

write_tsv <- function(x, path) {
  utils::write.table(x, path, sep = "\t", quote = FALSE, row.names = FALSE,
                     col.names = TRUE, na = "NA")
}

read_character_table <- function(path, sep) {
  if (!file.exists(path)) stop("Required file does not exist: ", path)
  utils::read.table(path, header = TRUE, sep = sep, quote = "\"",
                    comment.char = "", check.names = FALSE,
                    colClasses = "character", na.strings = "")
}

require_columns <- function(x, columns, label) {
  missing <- setdiff(columns, names(x))
  if (length(missing)) stop(label, " lacks: ", paste(missing, collapse = ", "))
}

assert_ids <- function(ids, label, domain) {
  if (!length(ids) || anyNA(ids) || any(!nzchar(ids)) || anyDuplicated(ids)) {
    stop(label, " has empty or duplicate sample identifiers.")
  }
  if (any(ids %in% excluded_samples)) {
    stop(label, " contains forbidden November repeat samples: ",
         paste(intersect(ids, excluded_samples), collapse = ", "))
  }
  pattern <- if (domain == "archaea") "^[AC][0-9]{3}$" else "^[BD][0-9]{3}$"
  if (any(!grepl(pattern, ids))) stop(label, " contains invalid domain sample names.")
}

quality_inventory <- function(paths, ids, expected_counts, output_dir) {
  # Inspect every FILTERED read, rather than assuming the first three FASTQs
  # represent the quality-score bins of every sequencing batch.
  counts <- numeric(94L)
  sample_rows <- vector("list", length(paths))
  for (i in seq_along(paths)) {
    message("Quality-bin inventory ", i, "/", length(paths), ": ", ids[i])
    stream <- ShortRead::FastqStreamer(paths[i], n = 5000L)
    per_sample <- numeric(94L)
    nread <- 0
    tryCatch({
      repeat {
        chunk <- ShortRead::yield(stream, qualityType = "FastqQuality")
        if (!length(chunk)) break
        nread <- nread + length(chunk)
        scores <- as.integer(as(Biostrings::quality(chunk), "matrix"))
        scores <- scores[!is.na(scores)]
        if (any(scores < 0L | scores > 93L)) {
          stop("Invalid Phred+33 quality values in ", paths[i])
        }
        per_sample <- per_sample + tabulate(scores + 1L, nbins = 94L)
      }
    }, finally = close(stream))
    if (!identical(as.numeric(nread), as.numeric(expected_counts[i]))) {
      stop("Filtered read-count mismatch during quality inventory: ", ids[i])
    }
    counts <- counts + per_sample
    present <- which(per_sample > 0)
    sample_rows[[i]] <- data.frame(
      sample_id = ids[i], phred_score = present - 1L,
      base_count = per_sample[present], filtered_reads = nread
    )
  }
  present <- which(counts > 0)
  write_tsv(data.frame(phred_score = present - 1L, base_count = counts[present]),
            file.path(output_dir, "quality_bins_all_filtered_reads.tsv"))
  write_tsv(do.call(rbind, sample_rows),
            file.path(output_dir, "quality_bins_by_sample.tsv"))
  scores <- present - 1L
  if (length(scores) < 2L) {
    stop("Fewer than two observed quality scores: cannot fit the prior binned-quality error model.")
  }
  scores
}

write_counts <- function(counts, path) {
  write_tsv(data.frame(sample_id = rownames(counts), counts,
                       check.names = FALSE), path)
}

make_phyloseq <- function(counts, taxonomy, sequences, metadata) {
  positive_samples <- rowSums(counts) > 0
  positive_taxa <- colSums(counts) > 0
  if (!any(positive_samples) || !any(positive_taxa)) return(NULL)
  counts <- counts[positive_samples, positive_taxa, drop = FALSE]
  metadata <- metadata[rownames(counts), , drop = FALSE]
  phyloseq::phyloseq(
    phyloseq::otu_table(counts, taxa_are_rows = FALSE),
    phyloseq::sample_data(metadata),
    phyloseq::tax_table(taxonomy[colnames(counts), , drop = FALSE]),
    Biostrings::DNAStringSet(sequences[colnames(counts)])
  )
}

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) != 2L || !args[1] %in% c("archaea", "bacteria")) {
    stop("Usage: Rscript scripts/02_dada2.R {archaea|bacteria} RUN_DIR")
  }
  domain <- args[1]
  run_dir <- normalizePath(args[2], mustWork = TRUE)
  required <- c("dada2", "Biostrings", "ShortRead", "phyloseq", "ggplot2")
  absent <- required[!vapply(required, requireNamespace, logical(1), quietly = TRUE)]
  if (length(absent)) stop("Missing R packages: ", paste(absent, collapse = ", "))
  if (!"makeBinnedQualErrfun" %in% getNamespaceExports("dada2")) {
    stop("Installed dada2 does not export makeBinnedQualErrfun; use the validated R environment.")
  }

  settings <- read_character_table(file.path(run_dir, "inputs", "settings.tsv"), "\t")
  require_columns(settings, c("key", "value"), "Settings")
  if (anyDuplicated(settings$key)) stop("Duplicate settings keys.")
  settings <- setNames(settings$value, settings$key)
  needed_settings <- c("reference", "threads", "seed", "taxonomy_bootstrap")
  if (!all(needed_settings %in% names(settings))) stop("Missing required processing settings.")
  reference <- normalizePath(settings[["reference"]], mustWork = TRUE)
  threads_string <- Sys.getenv("SLURM_CPUS_PER_TASK", unset = settings[["threads"]])
  threads <- suppressWarnings(as.integer(threads_string))
  seed <- suppressWarnings(as.integer(settings[["seed"]]))
  bootstrap <- suppressWarnings(as.integer(settings[["taxonomy_bootstrap"]]))
  if (is.na(threads) || threads < 1L || is.na(seed) || is.na(bootstrap) ||
      bootstrap < 0L || bootstrap > 100L) stop("Invalid numeric processing settings.")
  set.seed(seed + if (domain == "bacteria") 1L else 0L)

  inputs <- read_character_table(file.path(run_dir, "inputs", paste0(domain, ".tsv")), "\t")
  require_columns(inputs, c("sample_id", "fastq", "expected_reads", "sha256"), "Input manifest")
  assert_ids(inputs$sample_id, "Input manifest", domain)
  if (anyDuplicated(inputs$fastq)) stop("Duplicate FASTQ paths in input manifest.")
  expected <- suppressWarnings(as.numeric(inputs$expected_reads))
  if (anyNA(expected) || any(expected < 0 | expected != floor(expected))) {
    stop("Invalid expected read counts in input manifest.")
  }
  if (any(!file.exists(inputs$fastq))) stop("Input FASTQ files are missing.")

  metadata_path <- file.path(run_dir, "metadata", paste0("NRCS_", domain, "_metadata.csv"))
  metadata <- read_character_table(metadata_path, ",")
  id_column <- paste0(domain, "_sample")
  require_columns(metadata, c(id_column, "collection_date", "farm",
                              "biological_plot_id", "field_location_id"), "Metadata")
  assert_ids(metadata[[id_column]], "Metadata", domain)
  if (!setequal(inputs$sample_id, metadata[[id_column]])) {
    stop("Metadata identifiers and input manifest identifiers differ.")
  }
  ids <- inputs$sample_id
  metadata <- metadata[match(ids, metadata[[id_column]]), , drop = FALSE]
  rownames(metadata) <- ids
  metadata$analysis_sample <- ids
  if ("microbial_domain" %in% names(metadata) &&
      any(is.na(metadata$microbial_domain) | metadata$microbial_domain != domain)) {
    stop("Metadata microbial_domain disagrees with requested domain.")
  }

  out_dir <- file.path(run_dir, "dada2", domain)
  if (dir.exists(out_dir) && length(list.files(out_dir, all.files = TRUE, no.. = TRUE))) {
    stop("DADA2 output directory is not empty; use a fresh run: ", out_dir)
  }
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  filtered_dir <- file.path(out_dir, "filtered")
  dir.create(filtered_dir, showWarnings = FALSE)
  output_error_path <<- file.path(out_dir, "ERROR.txt")
  on.exit(writeLines(capture.output(utils::sessionInfo()),
                    file.path(out_dir, "sessionInfo.txt")), add = TRUE)
  utils::write.csv(metadata, file.path(out_dir, "metadata_used.csv"), row.names = FALSE, na = "")
  parameters <- data.frame(
    parameter = c("domain", "reference", "seed", "threads", "minQ", "minLen",
                  "maxLen", "maxN", "maxEE", "rm.phix", "truncQ", "truncLen",
                  "trimLeft", "error_function", "BAND_SIZE", "pool",
                  "chimera_method", "tryRC", "taxonomy_bootstrap", "taxLevels",
                  "quality_inventory", "learnErrors_nbases", "learnErrors_randomize",
                  "tree", "primer_removal"),
    value = c(domain, reference, seed + if (domain == "bacteria") 1L else 0L,
              threads, 3, if (domain == "archaea") 800 else 1300,
              if (domain == "archaea") 1000 else 1600, 0, 2, FALSE, 2, 0, 0,
              "makeBinnedQualErrfun", 32, "pseudo",
              if (domain == "archaea") "pooled" else "consensus",
              domain == "bacteria", bootstrap,
              "Kingdom;Phylum;Class;Order;Family;Genus;Species",
              "all filtered reads in every retained file", "100000000", "TRUE",
              "not built", "already completed")
  )
  write_tsv(parameters, file.path(out_dir, "parameters.tsv"))

  nonempty <- expected > 0
  filtered_paths <- file.path(filtered_dir, paste0(ids, ".fastq.gz"))
  filter_counts <- matrix(0, nrow = length(ids), ncol = 2L,
                          dimnames = list(ids, c("reads.in", "reads.out")))
  if (any(nonempty)) {
    message("Filtering ", sum(nonempty), " ", domain, " samples.")
    filtered_result <- dada2::filterAndTrim(
      inputs$fastq[nonempty], filtered_paths[nonempty], minQ = 3,
      minLen = if (domain == "archaea") 800 else 1300,
      maxLen = if (domain == "archaea") 1000 else 1600,
      maxN = 0, rm.phix = FALSE, maxEE = 2,
      truncQ = 2, truncLen = 0, trimLeft = 0,
      compress = TRUE, multithread = threads, verbose = TRUE
    )
    filter_counts[nonempty, ] <- filtered_result[, c("reads.in", "reads.out"), drop = FALSE]
  }
  write_tsv(data.frame(sample_id = ids, filter_counts, check.names = FALSE),
            file.path(out_dir, "filtering_counts.tsv"))
  if (any(filter_counts[, "reads.in"] != expected)) {
    stop("Actual input read counts disagree with the merge audit; inspect filtering_counts.tsv.")
  }
  retained <- filter_counts[, "reads.out"] > 0
  write_tsv(data.frame(sample_id = ids, passed_filter = retained,
                       reason = ifelse(retained, "retained", "zero_reads_after_filtering")),
            file.path(out_dir, "filtering_sample_status.tsv"))
  if (!any(retained)) stop("No reads passed the historical domain-specific filters.")
  if (any(!file.exists(filtered_paths[retained]))) stop("Expected filtered FASTQ files are missing.")

  quality_scores <- quality_inventory(filtered_paths[retained], ids[retained],
                                       filter_counts[retained, "reads.out"], out_dir)
  quality_plot <- dada2::plotQualityProfile(filtered_paths[retained][seq_len(min(6L, sum(retained)))])
  grDevices::pdf(file.path(out_dir, "filtered_quality_examples.pdf"), width = 11, height = 8)
  print(quality_plot)
  grDevices::dev.off()
  errfun <- dada2::makeBinnedQualErrfun(quality_scores)
  message("Learning ", domain, " error model with observed bins: ", paste(quality_scores, collapse = ", "))
  # Randomizing the file order avoids always learning from the first sorted
  # farm/sample. The 1e8-base budget is the historical default; it is explicit
  # here. A fixed domain-specific seed records this deliberate NRCS adaptation.
  err <- dada2::learnErrors(filtered_paths[retained], BAND_SIZE = 32,
                            errorEstimationFunction = errfun, multithread = threads,
                            nbases = 1e8, randomize = TRUE)
  saveRDS(err, file.path(out_dir, "error_model.rds"))
  grDevices::pdf(file.path(out_dir, "error_model.pdf"), width = 11, height = 8)
  print(dada2::plotErrors(err))
  grDevices::dev.off()

  dereplicated <- dada2::derepFastq(filtered_paths[retained], verbose = TRUE)
  # derepFastq returns one derep object for one filename, a list otherwise.
  if (inherits(dereplicated, "derep")) dereplicated <- list(dereplicated)
  names(dereplicated) <- ids[retained]
  denoised <- dada2::dada(dereplicated, err = err, BAND_SIZE = 32,
                         multithread = threads, pool = "pseudo")
  if (inherits(denoised, "dada")) denoised <- list(denoised)
  names(denoised) <- ids[retained]
  seqtab <- dada2::makeSequenceTable(denoised)
  seqtab <- seqtab[ids[retained], , drop = FALSE]
  saveRDS(seqtab, file.path(out_dir, "sequence_table_before_chimeras.rds"))
  if (!ncol(seqtab)) stop("No ASVs inferred after denoising.")
  nochim <- dada2::removeBimeraDenovo(
    seqtab, method = if (domain == "archaea") "pooled" else "consensus",
    multithread = threads, verbose = TRUE
  )
  nochim <- nochim[ids[retained], , drop = FALSE]
  if (!ncol(nochim) || !sum(nochim)) stop("No non-chimeric ASVs remain.")
  nochim <- nochim[, colSums(nochim) > 0, drop = FALSE]
  sequences <- sort(colnames(nochim), method = "radix")
  asv_ids <- sprintf(if (domain == "archaea") "AASV%06d" else "BASV%06d", seq_along(sequences))
  names(sequences) <- asv_ids
  counts <- matrix(0, nrow = length(ids), ncol = length(sequences),
                    dimnames = list(ids, asv_ids))
  counts[ids[retained], ] <- nochim[, unname(sequences), drop = FALSE]
  write_counts(counts, file.path(out_dir, "asv_counts_all.tsv"))
  saveRDS(counts, file.path(out_dir, "asv_counts_all.rds"))
  Biostrings::writeXStringSet(Biostrings::DNAStringSet(sequences),
                             file.path(out_dir, "ASVs_all.fasta"), format = "fasta")
  write_tsv(data.frame(asv_id = asv_ids, sequence = unname(sequences),
                       length = nchar(sequences)), file.path(out_dir, "ASV_catalog.tsv"))

  tax_levels <- c("Kingdom", "Phylum", "Class", "Order", "Family", "Genus", "Species")
  # Python preflight validates ordered lineage headers with 2--7 ranks and at
  # least one species-level lineage; missing trailing ranks remain unassigned.
  classification <- dada2::assignTaxonomy(
    unname(sequences), reference, taxLevels = tax_levels, minBoot = 0,
    outputBootstraps = TRUE, tryRC = domain == "bacteria", multithread = threads
  )
  taxonomy_raw <- classification$tax
  taxonomy_bootstrap <- classification$boot
  if (is.null(taxonomy_raw) || is.null(taxonomy_bootstrap) ||
      nrow(taxonomy_raw) != length(sequences) || ncol(taxonomy_raw) != 7L ||
      !identical(dim(taxonomy_raw), dim(taxonomy_bootstrap))) {
    stop("Unexpected taxonomy/bootstrap result dimensions.")
  }
  match_tax <- match(unname(sequences), rownames(taxonomy_raw))
  if (anyNA(match_tax)) stop("Taxonomy output lost sequence identifiers.")
  taxonomy_raw <- taxonomy_raw[match_tax, , drop = FALSE]
  taxonomy_bootstrap <- taxonomy_bootstrap[match_tax, , drop = FALSE]
  rownames(taxonomy_raw) <- rownames(taxonomy_bootstrap) <- asv_ids
  colnames(taxonomy_raw) <- colnames(taxonomy_bootstrap) <- tax_levels
  taxonomy <- taxonomy_raw
  taxonomy[is.na(taxonomy_bootstrap) | taxonomy_bootstrap < bootstrap] <- NA_character_
  write_tsv(data.frame(asv_id = asv_ids, taxonomy_raw, check.names = FALSE),
            file.path(out_dir, "taxonomy_unmasked.tsv"))
  write_tsv(data.frame(asv_id = asv_ids, taxonomy_bootstrap, check.names = FALSE),
            file.path(out_dir, "taxonomy_bootstrap.tsv"))
  write_tsv(data.frame(asv_id = asv_ids, taxonomy, check.names = FALSE),
            file.path(out_dir, "taxonomy.tsv"))
  saveRDS(list(taxonomy = taxonomy, raw = taxonomy_raw, bootstrap = taxonomy_bootstrap),
          file.path(out_dir, "taxonomy.rds"))

  expected_kingdom <- if (domain == "archaea") "Archaea" else "Bacteria"
  kingdom <- taxonomy[, "Kingdom"]
  # A classified organelle at any rank is never counted as target bacteria.
  organelle <- apply(taxonomy, 1L, function(x) {
    any(grepl("chloroplast|mitochondri|plastid", x, ignore.case = TRUE), na.rm = TRUE)
  })
  target <- !is.na(kingdom) & tolower(kingdom) == tolower(expected_kingdom) & !organelle
  target_counts <- counts[, target, drop = FALSE]
  classification_status <- ifelse(
    organelle, "organelle", ifelse(is.na(kingdom), "unclassified_domain",
                                   ifelse(target, "target_domain", "non_target_domain"))
  )
  write_tsv(data.frame(asv_id = asv_ids, kingdom = kingdom,
                       classification_status = classification_status,
                       total_reads = colSums(counts)),
            file.path(out_dir, "target_domain_classification.tsv"))
  write_counts(target_counts, file.path(out_dir, "asv_counts_target.tsv"))
  saveRDS(target_counts, file.path(out_dir, "asv_counts_target.rds"))
  if (any(target)) {
    Biostrings::writeXStringSet(Biostrings::DNAStringSet(sequences[target]),
                               file.path(out_dir, "ASVs_target.fasta"), format = "fasta")
  } else {
    file.create(file.path(out_dir, "ASVs_target.fasta"))
  }
  class_levels <- c("target_domain", "non_target_domain", "organelle", "unclassified_domain")
  specificity <- data.frame(sample_id = ids, nonchimera_reads = rowSums(counts))
  for (category in class_levels) {
    specificity[[paste0(category, "_reads")]] <- rowSums(counts[, classification_status == category, drop = FALSE])
  }
  specificity$target_fraction <- ifelse(specificity$nonchimera_reads > 0,
                                       specificity$target_domain_reads / specificity$nonchimera_reads, NA_real_)
  write_tsv(specificity, file.path(out_dir, "domain_specificity_by_sample.tsv"))

  denoised_counts <- setNames(numeric(length(ids)), ids)
  denoised_counts[ids[retained]] <- rowSums(seqtab)
  tracking <- data.frame(
    sample_id = ids, input = filter_counts[, "reads.in"],
    filtered = filter_counts[, "reads.out"], denoised = unname(denoised_counts),
    nonchimera = rowSums(counts), target = rowSums(target_counts),
    all_asvs = rowSums(counts > 0), target_asvs = rowSums(target_counts > 0)
  )
  tracking$lost_filtering <- tracking$input - tracking$filtered
  tracking$lost_denoising <- tracking$filtered - tracking$denoised
  tracking$lost_chimera <- tracking$denoised - tracking$nonchimera
  tracking$lost_non_target <- tracking$nonchimera - tracking$target
  tracking$target_fraction_of_input <- ifelse(tracking$input > 0, tracking$target / tracking$input, NA_real_)
  tracking$analysis_status <- ifelse(tracking$target > 0, "retained_no_depth_threshold",
                                    "zero_target_reads_excluded_from_phyloseq")
  if (any(as.matrix(tracking[, c("lost_filtering", "lost_denoising", "lost_chimera", "lost_non_target")]) < 0)) {
    stop("Read counts increased unexpectedly between processing stages.")
  }
  write_tsv(tracking, file.path(out_dir, "read_tracking.tsv"))
  grDevices::pdf(file.path(out_dir, "read_retention.pdf"), width = 12, height = 7)
  totals <- colSums(tracking[, c("input", "filtered", "denoised", "nonchimera", "target")])
  graphics::barplot(totals, las = 2, ylab = "Reads", main = paste(domain, "read retention"),
                    col = c("#93AFC4", "#7298AD", "#48788F", "#285B73", "#124157"))
  graphics::boxplot(log10(tracking$target + 1), ylab = "log10(target reads + 1)",
                    main = paste(domain, "target reads per sample"), col = "#7298AD")
  grDevices::dev.off()

  assert_ids(rownames(counts), "Final all-domain count table", domain)
  assert_ids(rownames(target_counts), "Final target-domain count table", domain)
  all_object <- make_phyloseq(counts, taxonomy, sequences, metadata)
  if (is.null(all_object)) stop("No positive-count samples for the all-domain phyloseq object.")
  saveRDS(all_object, file.path(out_dir, "phyloseq_all.rds"))
  target_object <- make_phyloseq(target_counts, taxonomy, sequences, metadata)
  if (is.null(target_object)) {
    stop("No target-domain ASVs/samples remain. QC and all-domain outputs were saved; review taxonomy and specificity.")
  }
  assert_ids(phyloseq::sample_names(target_object), "Final target phyloseq object", domain)
  saveRDS(target_object, file.path(out_dir, "phyloseq_target.rds"))
  writeLines(c(
    paste("Domain:", domain), paste("Metadata samples:", length(ids)),
    paste("Samples with target reads:", phyloseq::nsamples(target_object)),
    paste("Non-chimeric ASVs:", ncol(counts)), paste("Target ASVs:", ncol(target_counts)),
    "Count tables preserve zero-read rows; phyloseq objects omit zero-read samples.",
    "No minimum sequencing-depth threshold or rarefaction has been applied.",
    "ASV names are shared across farms within this domain and this run.",
    "Sequences, rather than run-specific ASV numbers, identify features across separate runs.",
    "No phylogenetic tree was constructed; these outputs support Bray-Curtis analyses.",
    "C108, C111, D108, and D111 are prohibited at input and output."
  ), file.path(out_dir, "SUMMARY.txt"))
  writeLines(format(Sys.time(), tz = "UTC", usetz = TRUE), file.path(out_dir, "COMPLETE"))
  message("Completed ", domain, ": ", out_dir)
}

output_error_path <- NULL
tryCatch(main(), error = function(e) {
  message("ERROR: ", conditionMessage(e))
  if (!is.null(output_error_path)) writeLines(conditionMessage(e), output_error_path)
  quit(save = "no", status = 1L)
})
