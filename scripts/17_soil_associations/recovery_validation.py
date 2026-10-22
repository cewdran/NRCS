"""Strict recovery provenance verification; standard library and Puma guard only.

Importing this module is inert. Public verification entry points require a live
Puma allocation before they inspect scientific output receipts or data. A
submission alias never manufactures a worker scheduler or runtime identity.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import re
import time
from pathlib import Path

from hpc_guard import require_hpc
from orchestration_common import (json_file, load_shards, require, safe_relative,
                                  save, scheduler_main_rows, sha, utc,
                                  verify_manifest)
from recovery_common import recovery_plan

FEATURE_CLASSES = ("EC", "KO", "METACYC")
SELF_CHECK_NAMES = ("ADAPTER_SELF_CHECKS.json", "ASSOCIATION_SELF_CHECKS.json",
                    "CATEGORICAL_SELF_CHECKS.json")
REQUIRED_WORKER_OUTPUTS = ("associations.csv.gz", "feature_screen.csv.gz",
    "match_audit.csv.gz", "cohorts.csv", "diagnostics.csv.gz",
    "FEATURE_REFERENCE.json", "matched_endpoints.csv.gz",
    "sample_metadata.csv.gz", "scenario_cohorts.csv")
COUNTER_FIELDS = ("tests_rows", "inferential_rows", "failed_eligible_rows",
                  "descriptive_rows", "skipped_rows")
COMMON_GZIP = ("sample_metadata.csv.gz", "matched_endpoints.csv.gz",
               "match_audit.csv.gz")
MERGED_TABLES = ("associations.csv.gz", "feature_screen.csv.gz",
                 "diagnostics.csv.gz", "scenario_cohorts.csv")
EXPECTED_READY_SHA256 = "6acaa39f5023c9f61a7cbe69d6c5687f634efb2b56e3907158003a41e1f6bf30"


def _fingerprint(path):
    stat = Path(path).stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def remember_file(records, path, expected=None):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(),
            "Missing or symlinked immutable input: " + str(path))
    path = path.resolve()
    key = str(path)
    fingerprint = _fingerprint(path)
    previous = records.get(key)
    if previous is not None:
        wanted = previous["expected_sha256"] if expected is None else expected
        require(wanted == previous["expected_sha256"],
                "Conflicting immutable input receipts: " + key)
        require(list(fingerprint) == previous.get("observed_file_stat"),
                "Immutable input metadata changed during verification: " + key)
        # Only an already SHA-verified, unchanged observation in this phase is cached.
        # finish_sources still hashes every unique input after all work completes.
        return previous["before_sha256"]
    before = sha(path)
    require(_fingerprint(path) == fingerprint,
            "Immutable input metadata changed while hashing: " + key)
    expected = before if expected is None else expected
    require(isinstance(expected, str) and re.fullmatch(r"[a-f0-9]{64}", expected),
            "Invalid expected SHA256: " + key)
    require(before == expected, "Immutable input checksum differs: " + key)
    records[key] = {"path": key, "expected_sha256": expected,
                    "before_sha256": before, "after_sha256": None,
                    "observed_file_stat": list(fingerprint)}
    return before


def finish_sources(records, execution, path):
    require_hpc()
    destination = Path(path)
    require(not destination.is_symlink(), "Source evidence destination cannot be a symlink")
    require(bool(records), "Empty recovery source evidence")
    require(str(destination.resolve()) not in records,
            "Source evidence destination must not overwrite an immutable input")
    for record in records.values():
        input_path = Path(record["path"])
        require(input_path.is_file() and not input_path.is_symlink(),
                "Recovery immutable input disappeared or became a symlink: " + record["path"])
        fingerprint = _fingerprint(input_path)
        if record.get("observed_file_stat") is not None:
            require(list(fingerprint) == record["observed_file_stat"],
                    "Recovery immutable input metadata changed: " + record["path"])
        after = sha(input_path)
        require(_fingerprint(input_path) == fingerprint,
                "Recovery immutable input changed during final hashing: " + record["path"])
        record["after_sha256"] = after
        require(after == record["expected_sha256"] == record["before_sha256"],
                "Read-only recovery input changed: " + record["path"])
    result = {"passed": True, "source_files_changed": False,
              "execution": execution, "files": list(records.values()),
              "checked_utc": utc()}
    save(destination, result)
    return result


def scheduler_completed(job_ids, audit_path=None):
    require_hpc()
    jobs = sorted(set(str(value) for value in job_ids))
    require(bool(jobs) and all(value.isdigit() for value in jobs),
            "Missing actual scheduler job IDs")
    rows, raw, last_error = {}, "", None
    for attempt in range(6):
        try:
            rows, raw = scheduler_main_rows(jobs)
            if all(rows.get(job, {}).get("state") == "COMPLETED" and
                   rows[job].get("exit_code") == "0:0" for job in jobs):
                break
        except Exception as error:
            last_error = str(error)
        if attempt < 5:
            time.sleep(2)
    if audit_path is not None:
        path = Path(audit_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw)
    require(all(rows.get(job, {}).get("state") == "COMPLETED" and
                rows[job].get("exit_code") == "0:0" for job in jobs),
            "Every prerequisite main job must actually be COMPLETED with 0:0" +
            ("; " + last_error if last_error else ""))
    return rows, raw


def verify_execution(actual, job, scheduling=None):
    require(isinstance(actual, dict) and actual.get("job_id") == str(job) and
            actual.get("cluster") == "puma" and bool(actual.get("node")),
            "Actual Puma execution identity differs for job " + str(job))
    if scheduling is not None:
        row = scheduling.get(str(job), {})
        require(row.get("state") == "COMPLETED" and row.get("exit_code") == "0:0",
                "Prerequisite scheduler completion differs for job " + str(job))
        require(row.get("node_list") == actual["node"],
                "Actual execution node differs from main scheduler row")


def verify_runtime(directory, job, actual, records, scheduling=None,
                   expected_ready_sha256=None):
    verify_execution(actual, job, scheduling)
    for name in ("EXECUTION_START.json", "EXECUTION_END.json", "ENVIRONMENT_STATUS.json"):
        path = Path(directory) / name
        remember_file(records, path)
        value = json_file(path)
        recorded = value.get("execution", {})
        verify_execution(recorded, job, scheduling)
        require(recorded.get("node") == actual["node"],
                "Runtime/environment node differs from output execution")
        if name == "EXECUTION_START.json":
            require(value.get("event") == "start", "Invalid runtime START")
        elif name == "EXECUTION_END.json":
            require(value.get("event") == "end" and value.get("pipeline_exit_code") == 0,
                    "Runtime END does not establish a successful pipeline")
        else:
            require(value.get("status") == "COMPUTE_RUNTIME_VERIFIED" and
                    value.get("package_integrity_passed") is True and
                    value.get("runtime_installation_performed") is False and
                    isinstance(value.get("versions"), dict) and bool(value["versions"]) and
                    re.fullmatch(r"[a-f0-9]{64}", value.get("ready_sha256", "")),
                    "Frozen compute environment verification failed")
            if expected_ready_sha256 is not None:
                require(value["ready_sha256"] == expected_ready_sha256,
                        "Frozen runtime READY SHA256 differs")


def verify_self_checks(directory, job, actual, records):
    for name in SELF_CHECK_NAMES:
        path = Path(directory) / name
        remember_file(records, path)
        value = json_file(path)
        checks = value.get("checks")
        require(value.get("passed") is True and isinstance(checks, list) and
                bool(checks) and all(isinstance(check, dict) and
                check.get("passed") is True for check in checks),
                "Actual numerical self-check receipt did not pass: " + str(path))
        verify_execution(value.get("execution", {}), job)
        require(value["execution"]["node"] == actual["node"],
                "Numerical self-check execution node differs")


def verify_source_receipt(path, job, records):
    remember_file(records, path)
    value = json_file(path)
    require(value.get("passed") is True and value.get("source_files_changed") is False,
            "Source verification did not pass: " + str(path))
    verify_execution(value.get("execution", {}), job)
    require(isinstance(value.get("files"), list) and bool(value["files"]),
            "Empty source checksum evidence")
    seen = set()
    for record in value["files"]:
        require(isinstance(record, dict), "Malformed source checksum evidence")
        expected = record.get("expected_sha256", "")
        require(record.get("before_sha256") == expected == record.get("after_sha256"),
                "Source before/after evidence disagrees")
        require(record.get("path") not in seen, "Duplicate source evidence path")
        seen.add(record["path"])
        remember_file(records, record["path"], expected)
    return value


def verify_expected_scientific_sources(run, shard, source, snapshot_directory):
    """Require the exact frozen original/private inputs used by the old adapter."""
    specs = json_file(Path(run) / "SOURCES.json")
    domain = shard["domain"]
    needed = {"corrected_source_status", "corrected_workbook",
              "selected_observations", "record_disposition"}
    if shard["feature_type"] == "taxa":
        needed.update({"corrected_counts_" + domain, "taxonomy_" + domain})
    else:
        needed.update({"functional_source_status", "functional_metadata_" + domain,
                       "functional_qc_" + domain})
        needed.update("functional_" + domain + "_" + klass for klass in FEATURE_CLASSES)
        if domain == "archaea":
            needed.update({"sensitivity_source_status", "archaeal_sensitivity_qc",
                           "sensitivity_pipeline_status"})
            needed.update("archaeal_filtered_" + klass for klass in FEATURE_CLASSES)
    observed = {item["path"]: item["expected_sha256"] for item in source["files"]}
    for role in sorted(needed):
        require(role in specs, "Missing frozen scientific source role: " + role)
        spec = specs[role]
        original = Path(spec["path"])
        snapshot = Path(snapshot_directory) / (role + "".join(original.suffixes))
        for path in (original, snapshot):
            require(observed.get(str(path)) == spec["sha256"],
                    "Source receipt omits or changes required original/private input: " + str(path))


def verify_output_manifest(directory, records, expected_manifest_sha):
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(),
            "Missing or symlinked worker output directory")
    manifest = directory / "OUTPUT_SHA256SUMS.txt"
    remember_file(records, manifest, expected_manifest_sha)
    entries = {}
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        relative = str(safe_relative(relative))
        require(relative not in entries and relative not in
                {"WORKER_STATUS.json", "OUTPUT_SHA256SUMS.txt"},
                "Duplicate or self-referential worker output manifest")
        require("source_snapshot" not in Path(relative).parts,
                "Scientific source snapshot cannot be an output")
        entries[relative] = digest
        remember_file(records, directory / relative, digest)
    require(bool(entries) and set(REQUIRED_WORKER_OUTPUTS).issubset(entries),
            "Required complete worker outputs are not manifested")
    actual = {str(p.relative_to(directory)) for p in directory.rglob("*")
              if p.is_file() and "source_snapshot" not in p.parts and
              p.name not in {"WORKER_STATUS.json", "OUTPUT_SHA256SUMS.txt"}}
    require(actual == set(entries), "Worker output inventory differs from its manifest")
    return entries


def verify_worker_status(status, shard, job, scheduling):
    for key in ("completed", "package_integrity_passed", "numerical_self_checks_passed",
                "scientific_review_pending", "visual_review_pending"):
        require(status.get(key) is True, "Worker required status failed: " + key)
    require(status.get("source_files_changed") is False, "Worker source integrity failed")
    require(status.get("shard") == shard, "Worker logical shard differs")
    verify_execution(status.get("execution", {}), job, scheduling)
    for name in COUNTER_FIELDS + ("cohort_count", "source_file_count"):
        require(isinstance(status.get(name), int) and not isinstance(status[name], bool) and
                status[name] >= 0, "Missing nonnegative integer worker counter: " + name)
    require(sum(status[name] for name in COUNTER_FIELDS[1:]) == status["tests_rows"],
            "Worker row counters do not sum to all tests")


def _compare_copied_tree(source, destination, records, label):
    source, destination = Path(source), Path(destination)
    require(source.is_dir() and destination.is_dir() and
            not source.is_symlink() and not destination.is_symlink(),
            "Missing or symlinked preserved " + label + " directory")
    require(all(not path.is_symlink() for root in (source, destination)
                for path in root.rglob("*")), "Symlink in preserved " + label + " tree")
    incoming = {str(path.relative_to(source)): path for path in source.rglob("*") if path.is_file()}
    outgoing = {str(path.relative_to(destination)): path for path in destination.rglob("*") if path.is_file()}
    require(bool(incoming) and set(incoming) == set(outgoing),
            "Preserved " + label + " inventory differs")
    for name, path in incoming.items():
        digest = remember_file(records, path)
        remember_file(records, outgoing[name], digest)
        require(outgoing[name].stat().st_mode & 0o222 == 0,
                "Preserved " + label + " copy is not read only")


def verify_chunk(run, spec, records, scheduling):
    require_hpc()
    run = Path(run)
    receipt = json_file(run / "submission.json")
    config = json_file(run / "CONFIG.json")
    repair = config.get("join_repair", {})
    shard = next((s for s in load_shards(run) if s["id"] == spec["shard_id"]), None)
    require(shard is not None, "Chunk has no original logical shard")
    job = receipt.get("jobs", {}).get("chunk:" + spec["id"], "")
    if repair:
        require(job == repair.get("source_chunk_jobs", {}).get(spec["id"]),
                "Imported chunk alias differs from its actual historical computation")
    directory = run / "workers/chunks" / spec["id"]
    original = (Path(repair["source_run"]) / "workers/chunks" / spec["id"]
                if repair else directory)
    expected_status = repair.get("source_chunk_status_sha256", {}).get(spec["id"]) if repair else None
    remember_file(records, original / "WORKER_STATUS.json", expected_status)
    remember_file(records, directory / "WORKER_STATUS.json", expected_status)
    status = json_file(directory / "WORKER_STATUS.json")
    verify_worker_status(status, shard, job, scheduling)
    require(status.get("chunk_id") == spec["id"] and
            status.get("feature_class") == spec["feature_class"] and
            spec["feature_class"] in FEATURE_CLASSES,
            "Chunk identity or source feature class differs")
    manifest_sha = (repair.get("source_chunk_output_manifest_sha256", {}).get(spec["id"], "")
                    if repair else status.get("output_manifest_sha256", ""))
    require(status.get("output_manifest_sha256") == manifest_sha,
            "Historical chunk completion/output manifest pin differs")
    entries = verify_output_manifest(directory, records, manifest_sha)
    old_entries = verify_output_manifest(original, records, manifest_sha)
    require(entries == old_entries, "Imported complete chunk manifest differs from source")
    runtime = run / "audit/chunks" / spec["id"]
    old_runtime = (Path(repair["source_run"]) / "audit/chunks" / spec["id"] if repair else runtime)
    verify_runtime(runtime, job, status["execution"], records, scheduling, EXPECTED_READY_SHA256)
    if repair:
        _compare_copied_tree(old_runtime, runtime, records, "chunk runtime audit")
    verify_self_checks(directory, job, status["execution"], records)
    source = verify_source_receipt(directory / "SOURCE_VERIFICATION.json", job, records)
    require(source["execution"]["node"] == status["execution"]["node"],
            "Chunk source receipt node differs")
    require(status["source_file_count"] == len(source["files"]),
            "Chunk source receipt count differs")
    # Keep the historical receipt unchanged: its private snapshot paths are historical.
    verify_expected_scientific_sources(run, shard, source, original / "source_snapshot")
    if repair:
        _compare_copied_tree(original / "source_snapshot", directory / "source_snapshot",
                             records, "chunk private source snapshot")
    reference = json_file(directory / "FEATURE_REFERENCE.json")
    require(set(reference) == {spec["feature_class"]},
            "Chunk frozen reference is not exactly its assigned class")
    for name in entries:
        if re.fullmatch(r"(?:EC|KO|METACYC)_.+\.csv\.gz", name):
            require(name.startswith(spec["feature_class"] + "_"),
                    "Chunk class-specific output escaped its assigned class")
    return {"chunk": spec, "shard": shard, "status": status,
            "scheduler": scheduling[str(job)], "entries": entries,
            "source": source, "directory": str(directory),
            "historical_directory": str(original), "reused_byte_identical": True}


def decoded_sha(path):
    digest = hashlib.sha256()
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def merged_decoded_sha(paths):
    """Digest the byte-preserving single-header concatenation used by JOIN."""
    digest = hashlib.sha256()
    header = None
    for path in paths:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rb") as handle:
            current = handle.readline()
            require(bool(current), "Empty chunk CSV header")
            if header is None:
                header = current
                digest.update(current)
            else:
                require(current == header, "Chunk CSV byte headers differ")
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def verify_assembly(run, shard, status, records, chunks):
    directory = Path(run) / "workers" / shard["id"]
    expected = [item for item in chunks if item["chunk"]["shard_id"] == shard["id"]]
    expected.sort(key=lambda item: FEATURE_CLASSES.index(item["chunk"]["feature_class"]))
    require(len(expected) == 3 and {item["chunk"]["feature_class"] for item in expected}
            == set(FEATURE_CLASSES), "Incomplete logical class assembly")
    require(status.get("aggregation_kind") == "CLASS_CHUNK_ASSEMBLY" and
            status.get("chunk_ids") == [item["chunk"]["id"] for item in expected] and
            status.get("chunk_job_ids") == [item["status"]["execution"]["job_id"] for item in expected],
            "Logical assembly does not identify its actual three chunk jobs")
    provenance_path = directory / "ASSEMBLY_PROVENANCE.json"
    remember_file(records, provenance_path)
    provenance = json_file(provenance_path)
    require(provenance.get("passed") is True and provenance.get("shard_id") == shard["id"] and
            provenance.get("chunk_ids") == status["chunk_ids"] and
            provenance.get("chunk_job_ids") == status["chunk_job_ids"] and
            provenance.get("feature_classes") == list(FEATURE_CLASSES) and
            provenance.get("common_context_derived") is False and
            provenance.get("historical_partial_output_read") is False and
            provenance.get("chunk_content_verification_sha256") ==
            sha(Path(run) / "audit/join/CHUNK_VERIFICATION.json"),
            "Logical assembly provenance differs")
    verify_execution(provenance.get("execution", {}), status["execution"]["job_id"])
    require(provenance["execution"]["node"] == status["execution"]["node"],
            "Assembly provenance execution node differs")
    outputs = provenance.get("output_provenance")
    require(isinstance(outputs, dict) and bool(outputs), "Empty assembled-output provenance")
    for name in MERGED_TABLES:
        paths = [Path(item["directory"]) / name for item in expected]
        expected_decoded = merged_decoded_sha(paths)
        require(decoded_sha(directory / name) == expected_decoded ==
                outputs.get(name, {}).get("decoded_sha256"),
                "Assembled table differs from the complete class-chunk union: " + name)
        require(outputs[name].get("kind") == "SINGLE_HEADER_CLASS_UNION" and
                outputs[name].get("source_paths") == [str(path) for path in paths],
                "Assembled table input provenance differs")
    for name in COMMON_GZIP + ("cohorts.csv",):
        paths = [Path(item["directory"]) / name for item in expected]
        digests = [decoded_sha(path) for path in paths]
        require(len(set(digests)) == 1 and decoded_sha(directory / name) == digests[0] ==
                outputs.get(name, {}).get("decoded_sha256"),
                "Shared class-independent context differs: " + name)
        require(sha(directory / name) == sha(paths[0]) and
                outputs[name].get("source_paths") == [str(path) for path in paths],
                "Shared context was not copied byte-for-byte from verified chunk")
    reference = {}
    for item in expected:
        klass = item["chunk"]["feature_class"]
        incoming = json_file(Path(item["directory"]) / "FEATURE_REFERENCE.json")
        require(set(incoming) == {klass}, "Chunk feature reference has an extra class")
        reference.update(incoming)
        for name, digest in item["entries"].items():
            if name.startswith(klass + "_") and name.endswith(".csv.gz"):
                require(sha(directory / name) == digest and
                        outputs.get(name, {}).get("source_paths") ==
                        [str(Path(item["directory"]) / name)] and
                        outputs[name].get("sha256") == digest,
                        "Class-specific original numerical output differs: " + name)
                require((directory / name).stat().st_mode & 0o222 == 0,
                        "Copied class-specific numerical output is not read-only")
    require(json_file(directory / "FEATURE_REFERENCE.json") == reference and
            set(reference) == set(FEATURE_CLASSES), "Merged frozen feature references differ")
    for name in COUNTER_FIELDS:
        require(status[name] == sum(item["status"][name] for item in expected),
                "Assembled worker row counters differ from complete chunks")
    require(len({item["status"]["cohort_count"] for item in expected}) == 1 and
            status["cohort_count"] == expected[0]["status"]["cohort_count"],
            "Assembled cohort count differs from shared cohorts")
    return provenance


def verify_recovery_stages(run, records=None, require_join=True):
    require_hpc()
    run = Path(run).resolve()
    records = {} if records is None else records
    config = json_file(run / "CONFIG.json")
    plan = recovery_plan(config)
    repair = config.get("join_repair", {})
    require(isinstance(repair, dict) and bool(repair),
            "Missing pinned join-repair source context")
    package_entries = verify_manifest(run)
    remember_file(records, run / "PACKAGE_SHA256SUMS.txt")
    for name in package_entries:
        remember_file(records, run / name)
    remember_file(records, run / "submission.json")
    receipt = json_file(run / "submission.json")
    old = Path(plan["old_run"])
    source_run = Path(repair["source_run"])
    require(source_run.is_absolute() and source_run != run and source_run != old,
            "Join-repair source must be the distinct immutable completed-chunk run")
    remember_file(records, old / "PACKAGE_SHA256SUMS.txt", plan["original_package_manifest_sha256"])
    remember_file(records, old / "submission.json", plan["original_submission_sha256"])
    remember_file(records, old / "HPC_STATUS.json", config["recovery"]["original_status_sha256"])
    for name in verify_manifest(old):
        remember_file(records, old / name)
    original_receipt = json_file(old / "submission.json")
    for key, expected in config["recovery"].get("original_code_hashes", {}).items():
        remember_file(records, old / str(safe_relative(key)), expected)
    remember_file(records, source_run / "PACKAGE_SHA256SUMS.txt", repair["source_manifest_sha256"])
    remember_file(records, source_run / "submission.json", repair["source_submission_sha256"])
    remember_file(records, source_run / "HPC_STATUS.json", repair["source_status_sha256"])
    for name in verify_manifest(source_run):
        remember_file(records, source_run / name)
    for name, expected in repair.get("source_code_sha256", {}).items():
        remember_file(records, source_run / str(safe_relative(name)), expected)
    source_receipt = json_file(source_run / "submission.json")
    source_config = json_file(source_run / "CONFIG.json")
    require(receipt.get("run_directory") == str(run) and
            source_receipt.get("run_directory") == str(source_run),
            "Join-repair or historical receipt run identity differs")
    require(config.get("analysis") == source_config.get("analysis") ==
            json_file(old / "CONFIG.json").get("analysis") and
            config.get("recovery") == source_config.get("recovery"),
            "Join repair changed frozen numerical analysis or historical recovery configuration")
    require(config["recovery"].get("expected_runtime_ready_sha256") == EXPECTED_READY_SHA256,
            "Join repair must retain the frozen runtime READY SHA256")
    for name in ("SOURCES.json", "SHARDS.json", "association_core.py", "categorical_core.py",
                 "association_figures.py", "adapter_self_checks.py", "association_self_checks.py",
                 "categorical_self_checks.py"):
        digest = remember_file(records, old / name)
        require(remember_file(records, run / name) == digest ==
                remember_file(records, source_run / name),
                "Join repair changed frozen scientific source/code: " + name)
    shards = load_shards(run)
    require(len(shards) == 12 and set(s["id"] for s in shards) ==
            set(plan["preserved_worker_ids"] + plan["failed_worker_ids"]),
            "Join repair does not retain the twelve original logical workers")
    jobs = receipt.get("jobs", {})
    actual_roles = ["stage", "join", "combine", "collector"]
    actual_ids = [jobs.get(role, "") for role in actual_roles]
    require(receipt.get("scheduled_job_roles") == actual_roles and
            all(isinstance(job, str) and job.isdigit() for job in actual_ids) and
            len(set(actual_ids)) == len(actual_ids),
            "Join-repair four actual roles require distinct scheduler allocations")
    historical_ids = [repair["source_stage_job_id"]] + list(repair["source_chunk_jobs"].values())
    historical_ids += list(plan["reuse_origin_worker_jobs"].values())
    require(not set(actual_ids).intersection(historical_ids),
            "Join repair invented new allocations for historical computation identities")
    for shard_id, job in plan["reuse_origin_worker_jobs"].items():
        require(jobs.get("worker:" + shard_id) == job ==
                original_receipt.get("jobs", {}).get("worker:" + shard_id) ==
                source_receipt.get("jobs", {}).get("worker:" + shard_id),
                "Preserved worker alias changed its original actual job")
    require(set(repair["source_chunk_jobs"]) == {item["id"] for item in plan["retry_chunks"]},
            "Join-repair does not preserve exactly the six completed source chunks")
    for spec in plan["retry_chunks"]:
        role = "chunk:" + spec["id"]
        require(jobs.get(role) == repair["source_chunk_jobs"][spec["id"]] ==
                source_receipt.get("jobs", {}).get(role),
                "Imported chunk alias differs from the actual historical computation")
    for role in ("stage", "join", "combine", "collector"):
        require(source_receipt.get("jobs", {}).get(role) == repair["source_" + role + "_job_id"],
                "Pinned historical stage role differs: " + role)
    logical_provenance = receipt.get("logical_worker_provenance", {})
    require(set(logical_provenance) == {shard["id"] for shard in shards},
            "Submission logical-worker provenance inventory differs")
    for shard_id, job in plan["reuse_origin_worker_jobs"].items():
        proof = logical_provenance[shard_id]
        require(proof.get("kind") == "reused_original_worker" and
                proof.get("original_job_id") == job and proof.get("original_run") == str(old),
                "Preserved worker submission provenance differs")
    for shard_id in plan["failed_worker_ids"]:
        require(jobs.get("worker:" + shard_id) == jobs.get("join"),
                "Assembled logical worker must truthfully alias the actual new JOIN job")
        proof = logical_provenance[shard_id]
        specs = [spec for spec in plan["retry_chunks"] if spec["shard_id"] == shard_id]
        require(proof.get("kind") == "assembled_from_completed_source_chunks" and
                proof.get("assembly_job_id") == jobs["join"] and
                proof.get("source_run") == str(source_run) and
                proof.get("chunk_jobs_reused_without_rerun") is True and
                proof.get("chunk_ids") == [spec["id"] for spec in specs] and
                proof.get("chunk_job_ids") == [repair["source_chunk_jobs"][spec["id"]] for spec in specs],
                "Assembled worker submission provenance invented a computation identity")
    needed = [jobs["stage"]] + historical_ids
    if require_join:
        needed += [jobs["join"]]
    scheduling, raw = scheduler_completed(needed)
    historical_stage_path = source_run / "audit/stage/STAGE_STATUS.json"
    remember_file(records, historical_stage_path, repair["source_stage_status_sha256"])
    historical_stage = json_file(historical_stage_path)
    require(historical_stage.get("completed") is True and
            historical_stage.get("source_files_changed") is False and
            historical_stage.get("package_integrity_passed") is True and
            historical_stage.get("imported_worker_count") == 10,
            "Historical source stage did not establish ten completed original workers")
    verify_runtime(source_run / "audit/stage", repair["source_stage_job_id"],
                   historical_stage.get("execution", {}), records, scheduling, EXPECTED_READY_SHA256)
    historical_source = verify_source_receipt(source_run / "audit/stage/SOURCE_VERIFICATION.json",
                                              repair["source_stage_job_id"], records)
    _compare_copied_tree(source_run / "audit/stage", run / "audit/history/source_stage",
                         records, "historical source-stage audit")
    for name in ("CONFIG.json", "submission.json", "HPC_STATUS.json", "PACKAGE_SHA256SUMS.txt"):
        digest = remember_file(records, source_run / name)
        destination = run / "audit/history/recovery_source" / name
        remember_file(records, destination, digest)
        require(destination.stat().st_mode & 0o222 == 0,
                "Historical source orchestration evidence copy is not read only")
    require(historical_source["execution"]["node"] == historical_stage["execution"]["node"],
            "Historical source-stage receipt node differs")
    stage_path = run / "audit/stage/STAGE_STATUS.json"
    remember_file(records, stage_path)
    stage = json_file(stage_path)
    require(stage.get("completed") is True and stage.get("source_files_changed") is False and
            stage.get("package_integrity_passed") is True and
            stage.get("imported_complete_worker_count") == 10 and
            stage.get("imported_complete_chunk_count") == 6 and
            stage.get("historical_partial_join_outputs_read") is False and
            stage.get("historical_partial_join_outputs_imported") is False and
            stage.get("historical_source_stage_job_id") == repair["source_stage_job_id"],
            "Join-repair source import did not establish the complete immutable inputs")
    verify_runtime(run / "audit/stage", jobs["stage"], stage.get("execution", {}),
                   records, scheduling, EXPECTED_READY_SHA256)
    source = verify_source_receipt(run / "audit/stage/SOURCE_VERIFICATION.json", jobs["stage"], records)
    require(source["execution"]["node"] == stage["execution"]["node"],
            "Stage source receipt node differs")
    stage_inputs = {item["path"]: item["expected_sha256"] for item in source["files"]}
    historical_inputs = {item["path"]: item["expected_sha256"] for item in historical_source["files"]}
    for spec in json_file(run / "SOURCES.json").values():
        require(stage_inputs.get(spec["path"]) == historical_inputs.get(spec["path"]) == spec["sha256"],
                "Stage receipt omits a required frozen scientific original source")
    for path, digest in [(old / "PACKAGE_SHA256SUMS.txt", plan["original_package_manifest_sha256"]),
                         (old / "submission.json", plan["original_submission_sha256"]),
                         (source_run / "PACKAGE_SHA256SUMS.txt", repair["source_manifest_sha256"]),
                         (source_run / "submission.json", repair["source_submission_sha256"]),
                         (source_run / "HPC_STATUS.json", repair["source_status_sha256"]),
                         (historical_stage_path, repair["source_stage_status_sha256"])]:
        require(stage_inputs.get(str(path)) == digest,
                "Stage receipt omits pinned historical run provenance: " + str(path))
    for record in historical_source["files"]:
        require(stage_inputs.get(record["path"]) == record["expected_sha256"],
                "Join-repair stage omits immutable historical source-stage evidence")
    result = {"config": config, "receipt": receipt, "shards": shards,
              "preserved_worker_ids": plan["preserved_worker_ids"],
              "retry_chunks": plan["retry_chunks"], "stage": stage,
              "historical_stage": historical_stage, "source_run": str(source_run),
              "source_receipt": source_receipt, "scheduling": scheduling,
              "raw_accounting": raw, "plan": plan,
              "original_receipt": original_receipt, "chunks": []}
    if require_join:
        result["chunks"] = [verify_chunk(run, item, records, scheduling)
                            for item in plan["retry_chunks"]]
        join_path = run / "audit/join/JOIN_STATUS.json"
        remember_file(records, join_path)
        join = json_file(join_path)
        require(join.get("completed") is True and join.get("source_files_changed") is False and
                join.get("package_integrity_passed") is True and
                join.get("numerical_self_checks_passed") is True and
                join.get("logical_worker_count") == 2 and
                join.get("chunk_count") == 6 and
                join.get("chunk_content_verification_passed") is True and
                join.get("engine_self_checks_passed") is True,
                "JOIN did not establish complete verified class assemblies")
        verify_runtime(run / "audit/join", jobs["join"], join.get("execution", {}),
                       records, scheduling, EXPECTED_READY_SHA256)
        verify_self_checks(run / "audit/join", jobs["join"], join["execution"], records)
        engine_path = run / "audit/join/JOIN_ENGINE_SELF_CHECKS.json"
        remember_file(records, engine_path, join.get("engine_self_checks_sha256", ""))
        engine = json_file(engine_path)
        verify_execution(engine.get("execution", {}), jobs["join"], scheduling)
        require(engine["execution"]["node"] == join["execution"]["node"] and
                engine.get("passed") is True and engine.get("synthetic_fixtures_only") is True and
                engine.get("scientific_statistics_recomputed") is False and
                engine.get("engine") == "NODE_LOCAL_BATCHED_DEFERRED_UNIQUE_INDEX_V1" and
                isinstance(engine.get("checks"), list) and bool(engine["checks"]) and
                all(isinstance(item, dict) and item.get("passed") is True
                    for item in engine["checks"]),
                "New JOIN engine numerical fixtures did not establish correctness")
        source = verify_source_receipt(run / "audit/join/SOURCE_VERIFICATION.json", jobs["join"], records)
        require(source["execution"]["node"] == join["execution"]["node"],
                "JOIN source receipt node differs")
        require(join.get("source_verification_sha256") == sha(run / "audit/join/SOURCE_VERIFICATION.json"),
                "JOIN source verification digest differs")
        verification_path = run / "audit/join/CHUNK_VERIFICATION.json"
        remember_file(records, verification_path)
        content = json_file(verification_path)
        verify_execution(content.get("execution", {}), jobs["join"])
        require(content["execution"]["node"] == join["execution"]["node"] and
                content.get("passed") is True and content.get("shared_context_verified") is True and
                content.get("chunk_count") == 6 and len(content.get("chunks", [])) == 6,
                "Actual JOIN chunk content verification did not pass")
        for item, proof in zip(result["chunks"], content["chunks"]):
            require(proof.get("chunk_id") == item["chunk"]["id"] and
                    proof.get("shard_id") == item["shard"]["id"] and
                    proof.get("feature_class") == item["chunk"]["feature_class"] and
                    proof.get("execution") == item["status"]["execution"] and
                    proof.get("output_manifest_sha256") == item["status"]["output_manifest_sha256"] and
                    proof.get("counters") == {key: item["status"][key] for key in COUNTER_FIELDS} and
                    all(proof.get(key) is True for key in ("passed", "canonical_test_ids_verified",
                        "unique_test_ids_verified", "source_class_verified")),
                    "JOIN chunk proof differs from actual immutable chunk input")
        require(content.get("unique_test_ids_verified") == sum(item["status"]["tests_rows"]
                for item in result["chunks"]), "JOIN complete unique test-ID count differs")
        join_inputs = {item["path"]: item["expected_sha256"] for item in source["files"]}
        for item in result["chunks"]:
            directory = Path(item["directory"])
            for name, digest in item["entries"].items():
                require(join_inputs.get(str(directory / name)) == digest,
                        "JOIN receipt omits verified complete chunk output provenance")
            for record in item["source"]["files"]:
                require(join_inputs.get(record["path"]) == record["expected_sha256"],
                        "JOIN receipt omits chunk original/private source provenance")
        require(join_inputs.get(str(verification_path)) == remember_file(records, verification_path) and
                join_inputs.get(str(engine_path)) == remember_file(records, engine_path),
                "JOIN receipt omits actual chunk-content or engine-fixture evidence")
        result["join"] = join
    return result


def verify_worker(run, shard, records, scheduling, stages=None):
    require_hpc()
    run = Path(run)
    stages = verify_recovery_stages(run, records) if stages is None else stages
    plan, receipt = stages["plan"], stages["receipt"]
    job = receipt["jobs"].get("worker:" + shard["id"], "")
    directory = run / "workers" / shard["id"]
    assembled = shard["id"] in plan["failed_worker_ids"]
    status_pin = (None if assembled else stages.get("config", json_file(run / "CONFIG.json"))
                  ["recovery"]["preserved_worker_status_sha256"].get(shard["id"], ""))
    remember_file(records, directory / "WORKER_STATUS.json", status_pin)
    status = json_file(directory / "WORKER_STATUS.json")
    verify_worker_status(status, shard, job, scheduling)
    entries = verify_output_manifest(directory, records, status.get("output_manifest_sha256", ""))
    runtime = run / "audit/join" if assembled else run / "audit/workers" / shard["id"]
    verify_runtime(runtime, job, status["execution"], records, scheduling,
                   EXPECTED_READY_SHA256)
    verify_self_checks(directory, job, status["execution"], records)
    source = verify_source_receipt(directory / "SOURCE_VERIFICATION.json", job, records)
    require(source["execution"]["node"] == status["execution"]["node"] and
            len(source["files"]) == status["source_file_count"],
            "Worker source receipt node/count differs")
    if assembled:
        require(status["execution"] == stages["join"]["execution"],
                "Logical assembled worker invented a runtime identity")
        verify_assembly(run, shard, status, records, stages["chunks"])
        require(source["files"] == json_file(run / "audit/join/SOURCE_VERIFICATION.json")["files"],
                "Logical assembled source receipt omits actual JOIN/chunk evidence")
        verify_expected_scientific_sources(run, shard, source,
            Path(stages["source_run"]) / "workers/chunks" / status["chunk_ids"][0] / "source_snapshot")
        for name in SELF_CHECK_NAMES:
            require(sha(directory / name) == sha(run / "audit/join" / name),
                    "Assembled numerical receipt is not the actual JOIN receipt")
    else:
        old_directory = Path(plan["old_run"]) / "workers" / shard["id"]
        verify_expected_scientific_sources(run, shard, source, old_directory / "source_snapshot")
        for name in ["WORKER_STATUS.json", "OUTPUT_SHA256SUMS.txt"] + list(entries):
            digest = remember_file(records, old_directory / name)
            require(remember_file(records, directory / name) == digest,
                    "Preserved worker bytes differ from the complete original: " + name)
        old_runtime = Path(plan["old_run"]) / "audit/workers" / shard["id"]
        old_audit_files = {str(path.relative_to(old_runtime)): path
                           for path in old_runtime.rglob("*") if path.is_file()}
        current_audit_files = {str(path.relative_to(runtime)) for path in runtime.rglob("*") if path.is_file()}
        require(set(old_audit_files) == current_audit_files, "Preserved complete runtime audit inventory differs")
        for name, path in old_audit_files.items():
            digest = remember_file(records, path)
            require(remember_file(records, runtime / name) == digest, "Preserved runtime audit was altered")
    return {"shard": shard, "status": status, "scheduler": scheduling[str(job)],
            "recovery_kind": "CLASS_CHUNK_ASSEMBLY" if assembled else "PRESERVED_ORIGINAL_WORKER"}
