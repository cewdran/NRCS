#!/usr/bin/env Rscript

# Check the active R environment and parse package R scripts without running them.
# Usage: Rscript scripts/check_r_packages.R [PACKAGE_DIRECTORY]
options(stringsAsFactors = FALSE, warn = 1)
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 1L) stop("Usage: Rscript scripts/check_r_packages.R [PACKAGE_DIRECTORY]")
script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
if (length(args)) {
  package_dir <- normalizePath(args[1], mustWork = TRUE)
} else if (length(script_arg) == 1L) {
  package_dir <- dirname(dirname(normalizePath(sub("^--file=", "", script_arg), mustWork = TRUE)))
} else {
  stop("Supply PACKAGE_DIRECTORY when this script is not run with Rscript.")
}

required <- c("dada2", "Biostrings", "ShortRead", "phyloseq", "ggplot2", "vegan", "permute")
available <- vapply(required, requireNamespace, logical(1), quietly = TRUE)
versions <- vapply(required, function(x) {
  if (!available[[x]]) return("MISSING")
  as.character(utils::packageVersion(x))
}, character(1))
print(data.frame(package = required, available = unname(available), version = unname(versions)),
      row.names = FALSE)

r_files <- sort(list.files(file.path(package_dir, "scripts"), pattern = "\\.[Rr]$",
                            recursive = TRUE, full.names = TRUE))
if (!length(r_files)) stop("No R scripts found under: ", package_dir)
parse_errors <- character()
for (path in r_files) {
  err <- tryCatch({ parse(file = path); NULL }, error = function(e) conditionMessage(e))
  if (is.null(err)) {
    cat("PARSE_OK ", basename(path), "\n", sep = "")
  } else {
    parse_errors <- c(parse_errors, paste(basename(path), err, sep = ": "))
  }
}
function_ok <- available[["dada2"]] &&
  "makeBinnedQualErrfun" %in% getNamespaceExports("dada2")
cat("makeBinnedQualErrfun exported: ", function_ok, "\n", sep = "")
if (function_ok) {
  test_errfun <- dada2::makeBinnedQualErrfun(c(20L, 30L, 40L, 93L))
  if (!is.function(test_errfun)) stop("makeBinnedQualErrfun did not return an error-estimation function.")
}
cat("Rscript: ", file.path(R.home("bin"), "Rscript"), "\n", sep = "")
cat("R libraries:\n", paste(.libPaths(), collapse = "\n"), "\n", sep = "")
print(utils::sessionInfo())
problems <- character()
if (any(!available)) problems <- c(problems, paste("Missing packages:", paste(required[!available], collapse = ", ")))
if (!function_ok) problems <- c(problems, "dada2 must export makeBinnedQualErrfun; no alternative error model is substituted.")
if (length(parse_errors)) problems <- c(problems, parse_errors)
if (length(problems)) stop(paste(problems, collapse = "\n"))
cat("R_ENVIRONMENT_AND_SCRIPT_SYNTAX_OK\n")
