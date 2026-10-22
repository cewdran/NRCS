#!/usr/bin/env Rscript
# Small, read-only-source HPC comparison before the full bacterial restart.
options(stringsAsFactors = FALSE, warn = 1)
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 2L)
run_dir <- normalizePath(args[1], mustWork = TRUE)
source_run <- normalizePath(args[2], mustWork = TRUE)
validation_dir <- file.path(run_dir, "runtime_validation")
dir.create(validation_dir, showWarnings = FALSE)
stopifnot(utils::packageVersion("dada2") >= "1.12.0")
counts <- read.delim(file.path(source_run, "dada2/bacteria/filtering_counts.tsv"),
                     check.names = FALSE, colClasses = "character")
stopifnot(all(c("sample_id", "reads.out") %in% names(counts)))
keep <- which(as.numeric(counts$reads.out) >= 5000)
stopifnot(length(keep) >= 3L)
selected <- counts$sample_id[keep[seq_len(3L)]]
stopifnot(!any(selected %in% c("C108", "C111", "D108", "D111")))
small_dir <- tempfile("nrcs_runtime_subset_")
dir.create(small_dir)
small_fastqs <- setNames(file.path(small_dir, paste0(selected, ".fastq.gz")), selected)
validation_reads <- character()
for (sid in selected) {
  original <- file.path(source_run, "dada2/bacteria/filtered", paste0(sid, ".fastq.gz"))
  stream <- ShortRead::FastqStreamer(original, n = 5000L)
  chunk <- tryCatch(ShortRead::yield(stream, qualityType = "FastqQuality"),
                    finally = close(stream))
  stopifnot(length(chunk) == 5000L)
  if (!length(validation_reads)) validation_reads <- as.character(ShortRead::sread(chunk)[1:2])
  ShortRead::writeFastq(chunk, small_fastqs[[sid]], compress = TRUE)
}
err <- readRDS(file.path(source_run, "dada2/bacteria/error_model.rds"))
writeLines(c(
  paste("DADA2 version:", utils::packageVersion("dada2")),
  paste("Samples:", paste(selected, collapse = ", ")),
  "5,000 already-filtered reads per sample; 15,000 total; validation subset only.",
  "Both calls use the original saved error model and native pseudo-pooling.",
  paste("Installed dada() error-update default:",
        paste(deparse(formals(dada2::dada)$errorEstimationFunction), collapse = " "))
), file.path(validation_dir, "comparison_settings.txt"))
set.seed(20260911L)
legacy_derep <- dada2::derepFastq(small_fastqs, verbose = FALSE)
names(legacy_derep) <- selected
legacy <- dada2::dada(legacy_derep, err = err, BAND_SIZE = 32,
                      multithread = FALSE, pool = "pseudo", verbose = FALSE)
legacy_table <- dada2::makeSequenceTable(legacy)
rm(legacy, legacy_derep)
invisible(gc())
set.seed(20260911L)
streamed <- dada2::dada(small_fastqs, err = err, BAND_SIZE = 32,
                        multithread = FALSE, pool = "pseudo", verbose = FALSE)
streamed_table <- dada2::makeSequenceTable(streamed)
stopifnot(setequal(rownames(legacy_table), selected), setequal(rownames(streamed_table), selected),
          setequal(colnames(legacy_table), colnames(streamed_table)))
sequence_order <- sort(colnames(legacy_table), method = "radix")
legacy_table <- legacy_table[selected, sequence_order, drop = FALSE]
streamed_table <- streamed_table[selected, sequence_order, drop = FALSE]
stopifnot(identical(legacy_table, streamed_table))
writeLines(paste("Exact ASV sequences and sample counts match on the validation subset; ASVs:",
                 length(sequence_order)),
           file.path(validation_dir, "NATIVE_PSEUDO_COMPARISON_PASS.txt"))
rm(streamed, streamed_table, legacy_table)
invisible(gc())

# Exercise the classifier on two actual filtered reads, independent of subset ASV yield.
settings <- read.delim(file.path(run_dir, "inputs/settings.tsv"), colClasses = "character")
reference <- settings$value[settings$key == "reference"]
stopifnot(length(reference) == 1L, file.exists(reference), length(validation_reads) == 2L)
queries <- unique(validation_reads)
tax_levels <- c("Kingdom", "Phylum", "Class", "Order", "Family", "Genus", "Species")
classified <- dada2::assignTaxonomy(queries, reference, taxLevels = tax_levels,
  minBoot = 0, outputBootstraps = TRUE, tryRC = TRUE, multithread = FALSE)
stopifnot(!is.null(classified$tax), !is.null(classified$boot),
          identical(dim(classified$tax), dim(classified$boot)),
          nrow(classified$tax) == length(queries), ncol(classified$tax) == 7L,
          all(queries %in% rownames(classified$tax)))
writeLines("Actual reference loaded for filtered validation reads; taxonomy/bootstrap dimensions and sequence identifiers passed.",
           file.path(validation_dir, "TAXONOMY_RUNTIME_PASS.txt"))
unlink(small_dir, recursive = TRUE)
writeLines(capture.output(utils::sessionInfo()), file.path(validation_dir, "sessionInfo.txt"))
writeLines(format(Sys.time(), tz = "UTC", usetz = TRUE), file.path(validation_dir, "COMPLETE"))
cat("NRCS_RECOVERY_RUNTIME_VALIDATION_PASS\n")
