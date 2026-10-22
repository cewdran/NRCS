#!/usr/bin/env python3
"""Assemble two original logical function workers from six verified class jobs.

All decoded-output inspection and numerical self-checks run only inside the
actual JOIN allocation on Puma. Inputs are complete successful chunk outputs;
the two historical failed partial directories are never opened.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback

from hpc_guard import require_hpc
from orchestration_common import json_file, require, save, sha, utc, verify_manifest
from recovery_validation import (COMMON_GZIP, COUNTER_FIELDS, FEATURE_CLASSES,
    MERGED_TABLES, SELF_CHECK_NAMES, decoded_sha, finish_sources,
    remember_file, scheduler_completed, verify_chunk,
    verify_recovery_stages, verify_self_checks)


def _reader(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    handle = opener(path, "rt", newline="")
    return handle, csv.DictReader(handle)


def _valid_row(row):
    require(None not in row and all(value is not None for value in row.values()),
            "Malformed chunk CSV row")


INSERT_BATCH_ROWS = 10000
PROGRESS_ROWS = 250000
SQLITE_CACHE_KIB = 524288


def _progress(audit, execution, phase, **details):
    """Durable progress is observation only; it never permits partial resumption."""
    require_hpc()
    audit = Path(audit)
    audit.mkdir(parents=True, exist_ok=True)
    record = {"execution": execution, "phase": phase, "utc": utc(),
              "completed": phase == "COMPLETE", "partial_resume_allowed": False,
              **details}
    payload = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = audit / (".JOIN_PROGRESS." + str(os.getpid()) + ".tmp")
    with temporary.open("w") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, audit / "JOIN_PROGRESS.json")
    with (audit / "JOIN_PROGRESS.jsonl").open("a") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    print("JOIN_PROGRESS " + payload.strip(), flush=True)


def _local_workspace():
    """Place disposable indexes on compute-node /tmp, never on /xdisk."""
    execution = require_hpc()
    path = Path("/tmp")
    require(path.is_dir() and os.access(path, os.W_OK | os.X_OK),
            "No writable compute-node /tmp workspace")
    require(shutil.disk_usage(path).free >= 2 * 1024 ** 3,
            "Compute-node temporary filesystem has less than 2 GiB free")
    return tempfile.TemporaryDirectory(prefix="nrcs_join_" + execution["job_id"] + "_", dir=str(path))


def _insert_many(database, table, rows):
    if not rows:
        return
    placeholders = ",".join("?" for unused in rows[0])
    database.executemany("INSERT INTO " + table + " VALUES (" + placeholders + ")", rows)
    rows.clear()


def _unique_index(database, table, columns):
    """Bulk-build the same unique keys; fail closed before any output is accepted."""
    try:
        database.execute("CREATE UNIQUE INDEX unique_" + table + " ON " + table +
                         "(" + ",".join(columns) + ")")
    except sqlite3.IntegrityError as error:
        raise RuntimeError("Duplicate chunk row identity in " + table) from error


def _canonical_id(shard_id, klass, row):
    # This tuple and serialization are identical to the original worker/JOIN.
    identity = (shard_id, klass, row["cohort_id"], row["feature_id"], row["scenario"])
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()[:24]


def validate_chunk_tables(run, chunks, execution, emit_progress=True):
    """Stream the original identity/count checks using disposable local indexes.

    Inserts are bounded batches into heap tables. The original unique keys are
    bulk-indexed once, then diagnostic references are checked with one join.
    No statistic, value, decision, test ID or inferential family is recomputed.
    """
    require_hpc()
    audit = Path(run) / "audit/join"
    audit.mkdir(parents=True, exist_ok=True)
    progress = lambda phase, **details: _progress(audit, execution, phase, **details) if emit_progress else None
    evidence = []
    shared_context = {}
    started = time.monotonic()
    with _local_workspace() as local:
        old_sqlite_tmp = os.environ.get("SQLITE_TMPDIR")
        os.environ["SQLITE_TMPDIR"] = local
        database_path = Path(local) / "chunk_id_audit.sqlite"
        database = sqlite3.connect(str(database_path))
        try:
            database.execute("PRAGMA page_size=32768")
            database.execute("PRAGMA cache_size=-" + str(SQLITE_CACHE_KIB))
            database.execute("PRAGMA journal_mode=OFF")
            database.execute("PRAGMA synchronous=OFF")
            database.execute("PRAGMA temp_store=FILE")
            # Only disposable validation indexes disable journalling. Completed
            # evidence is fsynced separately; no checkpoint/index is resumed.
            database.execute("CREATE TABLE ids(test_id TEXT NOT NULL, feature_class TEXT NOT NULL)")
            database.execute("CREATE TABLE diagnostics(test_id TEXT NOT NULL, kind TEXT NOT NULL, feature_class TEXT NOT NULL)")
            database.execute("CREATE TABLE screens(shard TEXT NOT NULL, feature_class TEXT NOT NULL, cohort TEXT NOT NULL, kind TEXT NOT NULL, feature TEXT NOT NULL)")
            database.execute("CREATE TABLE scenarios(shard TEXT NOT NULL, feature_class TEXT NOT NULL, scenario TEXT NOT NULL, variant TEXT NOT NULL, cohort TEXT NOT NULL, kind TEXT NOT NULL)")
            for item in chunks:
                spec, status, shard = item["chunk"], item["status"], item["shard"]
                directory = Path(item["directory"])
                klass = spec["feature_class"]
                counters = {field: 0 for field in COUNTER_FIELDS}
                table_counts = {}
                progress("ASSOCIATIONS", chunk_id=spec["id"],
                          feature_class=klass, expected_rows=status["tests_rows"],
                          local_index_path=str(database_path), sqlite_cache_kib=SQLITE_CACHE_KIB)
                handle, reader = _reader(directory / "associations.csv.gz")
                batch = []
                with handle:
                    require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)) and
                            {"test_id", "shard_id", "farm", "domain", "feature_class", "feature_id",
                             "cohort_id", "association_type", "scenario", "source_variant", "status"}
                            .issubset(reader.fieldnames), "Missing chunk association identity columns")
                    for row in reader:
                        _valid_row(row)
                        require(row["feature_class"] == klass and row["shard_id"] == shard["id"] and
                                row["farm"] == shard["farm"] and row["domain"] == shard["domain"],
                                "Chunk association escaped its original logical shard/class")
                        require(row["test_id"] == _canonical_id(shard["id"], klass, row),
                                "Chunk changed the original logical canonical test ID")
                        batch.append((row["test_id"], klass))
                        counters["tests_rows"] += 1
                        field = {"INFERENTIAL": "inferential_rows", "FAILED_ELIGIBLE": "failed_eligible_rows",
                                 "DESCRIPTIVE_ONLY": "descriptive_rows", "SKIPPED": "skipped_rows"}.get(row["status"])
                        require(field is not None, "Unknown chunk association status")
                        counters[field] += 1
                        if len(batch) >= INSERT_BATCH_ROWS:
                            _insert_many(database, "ids", batch)
                        if counters["tests_rows"] % PROGRESS_ROWS == 0:
                            database.commit()
                            progress("ASSOCIATIONS", chunk_id=spec["id"],
                                      rows=counters["tests_rows"], expected_rows=status["tests_rows"],
                                      elapsed_seconds=round(time.monotonic() - started, 3))
                    _insert_many(database, "ids", batch)
                require(counters == {field: status[field] for field in COUNTER_FIELDS},
                        "Chunk association row counts differ from the actual successful worker receipt")
                database.commit()
                table_counts["associations.csv.gz"] = counters["tests_rows"]
                for name, fields, table in [
                    ("feature_screen.csv.gz", ["feature_class", "cohort_id", "association_type", "feature_id"], "screens"),
                    ("scenario_cohorts.csv", ["feature_class", "scenario", "source_variant", "cohort_id", "association_type"], "scenarios"),
                    ("diagnostics.csv.gz", ["feature_class", "test_id", "diagnostic_type"], "diagnostics")]:
                    progress("IDENTITY_TABLE", chunk_id=spec["id"], table=name)
                    handle, reader = _reader(directory / name)
                    count, batch = 0, []
                    with handle:
                        require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)) and
                                set(fields).issubset(reader.fieldnames),
                                "Chunk source feature_class/identity columns are missing: " + name)
                        for row in reader:
                            _valid_row(row)
                            require(row["feature_class"] == klass,
                                    "Chunk row escaped its source class: " + name)
                            if table == "diagnostics":
                                values = (row["test_id"], row["diagnostic_type"], klass)
                            else:
                                values = (shard["id"],) + tuple(row[field] for field in fields)
                            batch.append(values)
                            count += 1
                            if len(batch) >= INSERT_BATCH_ROWS:
                                _insert_many(database, table, batch)
                            if count % PROGRESS_ROWS == 0:
                                database.commit()
                                progress("IDENTITY_TABLE", chunk_id=spec["id"],
                                          table=name, rows=count,
                                          elapsed_seconds=round(time.monotonic() - started, 3))
                        _insert_many(database, table, batch)
                    database.commit()
                    table_counts[name] = count
                handle, reader = _reader(directory / "cohorts.csv")
                with handle:
                    require(reader.fieldnames and "cohort_id" in reader.fieldnames,
                            "Missing shared cohort identity")
                    cohort_ids = set()
                    for row in reader:
                        _valid_row(row)
                        require(bool(row["cohort_id"]) and row["cohort_id"] not in cohort_ids,
                                "Duplicate or empty shared cohort identity")
                        cohort_ids.add(row["cohort_id"])
                require(len(cohort_ids) == status["cohort_count"], "Chunk shared cohort count differs")
                for table in ("screens", "scenarios"):
                    observed = {row[0] for row in database.execute(
                        "SELECT DISTINCT cohort FROM " + table + " WHERE shard=? AND feature_class=?",
                        (shard["id"], klass))}
                    require(observed.issubset(cohort_ids), "Class table escaped shared cohort identities")
                evidence.append({"chunk_id": spec["id"], "shard_id": shard["id"],
                    "feature_class": klass, "job_id": status["execution"]["job_id"],
                    "execution": status["execution"], "output_manifest_sha256": status["output_manifest_sha256"],
                    "passed": True, "canonical_test_ids_verified": True,
                    "unique_test_ids_verified": True, "source_class_verified": True,
                    "counters": counters, "table_rows": table_counts, "cohort_count": len(cohort_ids)})
                progress("CLASS_ROWS_SCANNED", chunk_id=spec["id"],
                          table_rows=table_counts, uniqueness_and_references_pending=True)
            # Deferred checks preserve every original key and fail before the
            # CHUNK_VERIFICATION receipt, numerical self-checks or assemblies.
            for table, columns in [("ids", ["test_id"]), ("diagnostics", ["test_id", "kind"]),
                ("screens", ["shard", "feature_class", "cohort", "kind", "feature"]),
                ("scenarios", ["shard", "feature_class", "scenario", "variant", "cohort", "kind"])]:
                progress("UNIQUE_INDEX", table=table)
                _unique_index(database, table, columns)
                database.commit()
            progress("DIAGNOSTIC_REFERENCES")
            bad = database.execute(
                "SELECT d.test_id FROM diagnostics d LEFT JOIN ids i ON i.test_id=d.test_id "
                "WHERE i.test_id IS NULL OR i.feature_class<>d.feature_class LIMIT 1").fetchone()
            require(bad is None, "Diagnostic refers to a missing or wrong-class canonical association")
            total = database.execute("SELECT count(*) FROM ids").fetchone()[0]
            require(total == sum(item["status"]["tests_rows"] for item in chunks),
                    "Global chunk unique test-ID count differs")
        finally:
            database.close()
            if old_sqlite_tmp is None:
                os.environ.pop("SQLITE_TMPDIR", None)
            else:
                os.environ["SQLITE_TMPDIR"] = old_sqlite_tmp
    # All independent context must agree before numerical self-checks. These
    # decoded digests are reused during assembly; source byte after-hashes still
    # establish that none of the inputs changed during that later read.
    for shard_id in sorted({item["chunk"]["shard_id"] for item in chunks}):
        subset = [item for item in chunks if item["chunk"]["shard_id"] == shard_id]
        shared_context[shard_id] = {}
        for name in COMMON_GZIP + ("cohorts.csv",):
            progress("SHARED_CONTEXT", shard_id=shard_id, table=name)
            digests = {decoded_sha(Path(item["directory"]) / name) for item in subset}
            require(len(digests) == 1,
                    "Shared metadata/endpoints/match audit/cohorts differ among classes: " + name)
            shared_context[shard_id][name] = next(iter(digests))
        require(len({item["status"]["cohort_count"] for item in subset}) == 1,
                "Shared cohort counts differ among class jobs")
    result = {"passed": True, "execution": execution, "chunk_count": len(chunks),
              "unique_test_ids_verified": total, "chunks": evidence,
              "shared_context_verified": True, "shared_context_decoded_sha256": shared_context,
              "engine": "NODE_LOCAL_BATCHED_DEFERRED_UNIQUE_INDEX_V1",
              "insert_batch_rows": INSERT_BATCH_ROWS, "sqlite_cache_kib": SQLITE_CACHE_KIB,
              "checked_utc": utc()}
    save(audit / "CHUNK_VERIFICATION.json", result)
    progress("CHUNK_CONTENT_VERIFIED", unique_test_ids=total)
    return result


def _union_table(paths, destination):
    # Hash exactly the decoded bytes while writing the original class union.
    # Low gzip compression changes only encoding, never any decoded CSV byte.
    opener = gzip.open if str(destination).endswith(".gz") else open
    options = {"compresslevel": 1} if str(destination).endswith(".gz") else {}
    digest = hashlib.sha256()
    with opener(destination, "wb", **options) as output:
        header = None
        for path in paths:
            incoming = gzip.open if str(path).endswith(".gz") else open
            with incoming(path, "rb") as source:
                current = source.readline()
                require(bool(current), "Empty chunk CSV")
                if header is None:
                    header = current
                    output.write(current)
                    digest.update(current)
                else:
                    require(current == header, "Chunk headers differ during single-header union")
                while True:
                    data = source.read(1024 * 1024)
                    if not data:
                        break
                    output.write(data)
                    digest.update(data)
    expected_decoded = digest.hexdigest()
    require(decoded_sha(destination) == expected_decoded, "Byte-preserving chunk union differs")
    destination.chmod(0o444)
    return {"kind": "SINGLE_HEADER_CLASS_UNION", "source_paths": [str(path) for path in paths],
            "decoded_sha256": expected_decoded, "sha256": sha(destination)}


def _copy_verified(source, destination):
    digest = sha(source)
    shutil.copyfile(source, destination)
    require(sha(destination) == digest, "Read-only verified chunk copy differs")
    destination.chmod(0o444)
    return {"kind": "BYTE_COPY", "source_paths": [str(source)], "sha256": digest}


def _write_fixture_csv(path, fields, rows):
    require_hpc()
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_fixture_csv(path):
    require_hpc()
    handle, reader = _reader(path)
    with handle:
        return reader.fieldnames, list(reader)


def _fixture_chunks(base, execution):
    """Synthetic receipts and tables; guarded and never scientific input."""
    require_hpc()
    chunks = []
    for farm in ("fixture_a", "fixture_b"):
        shard = {"id": farm + "_bacteria_functions", "farm": farm,
                 "domain": "bacteria", "feature_type": "functions"}
        for klass in FEATURE_CLASSES:
            spec = {"id": shard["id"] + "_" + klass.lower(),
                    "shard_id": shard["id"], "feature_class": klass}
            directory = Path(base) / "workers/chunks" / spec["id"]
            directory.mkdir(parents=True)
            row = {"test_id": "", "shard_id": shard["id"], "farm": farm,
                   "domain": "bacteria", "feature_class": klass,
                   "feature_id": klass + "_fixture", "cohort_id": "fixture_cohort",
                   "association_type": "NUMERIC", "scenario": "PRIMARY",
                   "source_variant": "PRIMARY", "status": "INFERENTIAL"}
            row["test_id"] = _canonical_id(shard["id"], klass, row)
            _write_fixture_csv(directory / "associations.csv.gz", list(row), [row])
            screen = {"feature_class": klass, "cohort_id": "fixture_cohort",
                      "association_type": "NUMERIC", "feature_id": klass + "_fixture"}
            _write_fixture_csv(directory / "feature_screen.csv.gz", list(screen), [screen])
            scenario = {"feature_class": klass, "scenario": "PRIMARY", "source_variant": "PRIMARY",
                        "cohort_id": "fixture_cohort", "association_type": "NUMERIC"}
            _write_fixture_csv(directory / "scenario_cohorts.csv", list(scenario), [scenario])
            diagnostic = {"feature_class": klass, "test_id": row["test_id"],
                          "diagnostic_type": "FIXTURE"}
            _write_fixture_csv(directory / "diagnostics.csv.gz", list(diagnostic), [diagnostic])
            _write_fixture_csv(directory / "cohorts.csv", ["cohort_id"],
                               [{"cohort_id": "fixture_cohort"}])
            for name in COMMON_GZIP:
                _write_fixture_csv(directory / name, ["synthetic_context"],
                                   [{"synthetic_context": farm}])
            status = {field: 0 for field in COUNTER_FIELDS}
            status.update(tests_rows=1, inferential_rows=1, cohort_count=1,
                          execution=execution, output_manifest_sha256="0" * 64)
            chunks.append({"chunk": spec, "status": status, "shard": shard,
                           "directory": str(directory)})
    return chunks


def _fixture_change(chunks, name, field=None, value=None, duplicate=False):
    path = Path(chunks[0]["directory"]) / name
    fields, rows = _read_fixture_csv(path)
    if duplicate:
        rows.append(dict(rows[0]))
    else:
        rows[0][field] = value
    _write_fixture_csv(path, fields, rows)


def run_engine_self_checks(audit, execution):
    """Run optimization equivalence/integrity fixtures only on live Puma."""
    require_hpc()
    checks = []
    audit = Path(audit)
    with _local_workspace() as local:
        base = Path(local)
        valid = _fixture_chunks(base / "valid", execution)
        proof = validate_chunk_tables(base / "valid", valid, execution, emit_progress=False)
        require(proof["passed"] is True and proof["unique_test_ids_verified"] == 6,
                "JOIN fixture valid-table validation differs")
        checks.append({"check": "Valid six-class tables preserve exact counters and canonical identities", "passed": True})
        cases = (
            ("Canonical ID mutation rejected", "associations.csv.gz", "test_id", "incorrect", False),
            ("Unknown association status rejected", "associations.csv.gz", "status", "UNKNOWN", False),
            ("Wrong logical farm rejected", "associations.csv.gz", "farm", "other", False),
            ("Wrong association feature class rejected", "associations.csv.gz", "feature_class", "OTHER", False),
            ("Duplicate canonical test ID rejected", "associations.csv.gz", None, None, True),
            ("Missing diagnostic reference rejected", "diagnostics.csv.gz", "test_id", "missing", False),
            ("Duplicate diagnostic identity rejected", "diagnostics.csv.gz", None, None, True),
            ("Duplicate feature-screen identity rejected", "feature_screen.csv.gz", None, None, True),
            ("Duplicate scenario identity rejected", "scenario_cohorts.csv", None, None, True),
            ("Duplicate shared cohort rejected", "cohorts.csv", None, None, True),
            ("Scenario outside shared cohorts rejected", "scenario_cohorts.csv", "cohort_id", "missing", False),
            ("Inconsistent shared context rejected", COMMON_GZIP[0], "synthetic_context", "different", False),
        )
        for index, (label, name, field, value, duplicate) in enumerate(cases):
            trial = base / ("invalid_" + str(index))
            chunks = _fixture_chunks(trial, execution)
            _fixture_change(chunks, name, field, value, duplicate)
            if name == "associations.csv.gz" and duplicate:
                chunks[0]["status"].update(tests_rows=2, inferential_rows=2)
            try:
                validate_chunk_tables(trial, chunks, execution, emit_progress=False)
            except RuntimeError:
                checks.append({"check": label, "passed": True})
            else:
                raise RuntimeError("JOIN fixture did not reject: " + label)
        # The diagnostic class field is correct; only its referenced association
        # belongs to KO. This specifically exercises the deferred SQL join.
        trial = base / "cross_class_diagnostic"
        chunks = _fixture_chunks(trial, execution)
        unused, ko_rows = _read_fixture_csv(Path(chunks[1]["directory"]) / "associations.csv.gz")
        _fixture_change(chunks, "diagnostics.csv.gz", "test_id", ko_rows[0]["test_id"])
        try:
            validate_chunk_tables(trial, chunks, execution, emit_progress=False)
        except RuntimeError:
            checks.append({"check": "Wrong-class diagnostic association rejected by deferred SQL join", "passed": True})
        else:
            raise RuntimeError("JOIN fixture accepted a wrong-class diagnostic association")
        trial = base / "counter_mismatch"
        chunks = _fixture_chunks(trial, execution)
        chunks[0]["status"]["inferential_rows"] = 0
        try:
            validate_chunk_tables(trial, chunks, execution, emit_progress=False)
        except RuntimeError:
            checks.append({"check": "Successful worker counter mismatch rejected", "passed": True})
        else:
            raise RuntimeError("JOIN fixture accepted a counter mismatch")
        # Exercise a duplicate that crosses an executemany batch boundary.
        database = sqlite3.connect(":memory:")
        try:
            database.execute("CREATE TABLE ids(test_id TEXT NOT NULL, feature_class TEXT NOT NULL)")
            rows = [("fixture_" + str(index), "EC") for index in range(INSERT_BATCH_ROWS)]
            _insert_many(database, "ids", rows)
            _insert_many(database, "ids", [("fixture_0", "EC")])
            try:
                _unique_index(database, "ids", ["test_id"])
            except RuntimeError:
                checks.append({"check": "Duplicate crossing bounded insertion batches rejected", "passed": True})
            else:
                raise RuntimeError("JOIN fixture accepted a duplicate across insert batches")
        finally:
            database.close()
        paths = [Path(item["directory"]) / "associations.csv.gz" for item in valid[:3]]
        expected = bytearray()
        header = None
        for path in paths:
            with gzip.open(path, "rb") as incoming:
                current = incoming.readline()
                if header is None:
                    header = current
                    expected.extend(current)
                else:
                    require(current == header, "Fixture header mismatch")
                expected.extend(incoming.read())
        destination = base / "fixture_union.csv.gz"
        union = _union_table(paths, destination)
        with gzip.open(destination, "rb") as incoming:
            require(incoming.read() == bytes(expected), "JOIN streaming union changed decoded fixture bytes")
        require(union["decoded_sha256"] == hashlib.sha256(expected).hexdigest(),
                "JOIN streaming union hash differs from literal reference bytes")
        checks.append({"check": "Low-compression streaming union equals literal single-header reference bytes", "passed": True})
        wrong = base / "wrong_header.csv.gz"
        with gzip.open(wrong, "wb") as output:
            output.write(b"different_header\nvalue\n")
        try:
            _union_table([paths[0], wrong], base / "invalid_union.csv.gz")
        except RuntimeError:
            checks.append({"check": "Mismatched union header rejected", "passed": True})
        else:
            raise RuntimeError("JOIN fixture accepted mismatched headers")
    result = {"passed": True, "execution": execution, "synthetic_fixtures_only": True,
              "scientific_statistics_recomputed": False, "checks": checks,
              "engine": "NODE_LOCAL_BATCHED_DEFERRED_UNIQUE_INDEX_V1", "checked_utc": utc()}
    save(audit / "JOIN_ENGINE_SELF_CHECKS.json", result)
    _progress(audit, execution, "ENGINE_SELF_CHECKS_PASSED", check_count=len(checks))
    return result


def assemble_worker(run, shard, chunks, execution, source, verification):
    require_hpc()
    chunks = sorted(chunks, key=lambda item: FEATURE_CLASSES.index(item["chunk"]["feature_class"]))
    require(len(chunks) == 3 and [item["chunk"]["feature_class"] for item in chunks] == list(FEATURE_CLASSES),
            "Logical function worker needs exactly EC/KO/METACYC complete chunks")
    out = Path(run) / "workers" / shard["id"]
    require(not out.exists() or (out.is_dir() and not any(out.iterdir())),
            "Refusing to read or overwrite existing logical output, including historical partial output")
    temporary = out.with_name("." + out.name + ".join." + str(os.getpid()))
    require(not temporary.exists(), "Preexisting temporary assembly directory")
    temporary.mkdir()
    outputs = {}
    for name in MERGED_TABLES:
        _progress(Path(run) / "audit/join", execution, "ASSEMBLE_UNION", shard_id=shard["id"], table=name)
        paths = [Path(item["directory"]) / name for item in chunks]
        outputs[name] = _union_table(paths, temporary / name)
    for name in COMMON_GZIP + ("cohorts.csv",):
        paths = [Path(item["directory"]) / name for item in chunks]
        digest = verification["shared_context_decoded_sha256"][shard["id"]][name]
        _progress(Path(run) / "audit/join", execution, "ASSEMBLE_SHARED_COPY", shard_id=shard["id"], table=name)
        outputs[name] = _copy_verified(paths[0], temporary / name)
        require(decoded_sha(temporary / name) == digest, "Shared independent context changed after validation")
        outputs[name].update(source_paths=[str(path) for path in paths], decoded_sha256=digest,
                             kind="VERIFIED_IDENTICAL_SHARED_CONTEXT_COPY")
    references = {}
    for item in chunks:
        klass = item["chunk"]["feature_class"]
        path = Path(item["directory"]) / "FEATURE_REFERENCE.json"
        reference = json_file(path)
        require(set(reference) == {klass}, "Unexpected class in chunk frozen reference")
        references.update(reference)
        for name, digest in item["entries"].items():
            if name.startswith(klass + "_") and name.endswith(".csv.gz"):
                outputs[name] = _copy_verified(Path(item["directory"]) / name, temporary / name)
                require(outputs[name]["sha256"] == digest, "Class numerical copy differs from input manifest")
    require(set(references) == set(FEATURE_CLASSES), "Missing frozen reference class")
    save(temporary / "FEATURE_REFERENCE.json", references)
    (temporary / "FEATURE_REFERENCE.json").chmod(0o444)
    outputs["FEATURE_REFERENCE.json"] = {"kind": "EXACT_THREE_CLASS_JSON_UNION",
        "source_paths": [str(Path(item["directory"]) / "FEATURE_REFERENCE.json") for item in chunks],
        "sha256": sha(temporary / "FEATURE_REFERENCE.json")}
    for name in SELF_CHECK_NAMES:
        outputs[name] = _copy_verified(Path(run) / "audit/join" / name, temporary / name)
    save(temporary / "SOURCE_VERIFICATION.json", source)
    (temporary / "SOURCE_VERIFICATION.json").chmod(0o444)
    provenance = {"passed": True, "aggregation_kind": "CLASS_CHUNK_ASSEMBLY",
        "execution": execution, "shard_id": shard["id"],
        "chunk_ids": [item["chunk"]["id"] for item in chunks],
        "chunk_job_ids": [item["status"]["execution"]["job_id"] for item in chunks],
        "feature_classes": list(FEATURE_CLASSES), "output_provenance": outputs,
        "chunk_content_verification_sha256": sha(Path(run) / "audit/join/CHUNK_VERIFICATION.json"),
        "common_context_derived": False, "historical_partial_output_read": False,
        "assembled_utc": utc()}
    save(temporary / "ASSEMBLY_PROVENANCE.json", provenance)
    (temporary / "ASSEMBLY_PROVENANCE.json").chmod(0o444)
    paths = sorted(path for path in temporary.rglob("*") if path.is_file() and
                   path.name not in {"WORKER_STATUS.json", "OUTPUT_SHA256SUMS.txt"})
    (temporary / "OUTPUT_SHA256SUMS.txt").write_text("".join(
        sha(path) + "  " + str(path.relative_to(temporary)) + "\n" for path in paths))
    counters = {field: sum(item["status"][field] for item in chunks) for field in COUNTER_FIELDS}
    status = {"completed": True, "source_files_changed": False, "package_integrity_passed": True,
        "numerical_self_checks_passed": True, "scientific_review_pending": True,
        "visual_review_pending": True, "execution": execution, "shard": shard,
        "aggregation_kind": "CLASS_CHUNK_ASSEMBLY", "chunk_ids": provenance["chunk_ids"],
        "chunk_job_ids": provenance["chunk_job_ids"], "feature_classes": list(FEATURE_CLASSES),
        "chunk_content_verification_passed": True, "cohort_count": chunks[0]["status"]["cohort_count"],
        "source_file_count": len(source["files"]),
        "output_manifest_sha256": sha(temporary / "OUTPUT_SHA256SUMS.txt"),
        "predictive_validation_established": False, "ten_year_forecast_generated": False,
        "completed_utc": utc(), **counters}
    save(temporary / "WORKER_STATUS.json", status)
    for path in temporary.iterdir():
        path.chmod(0o444)
    if out.exists():
        out.rmdir()
    os.replace(temporary, out)
    return {"shard_id": shard["id"], "status": status,
            "output_manifest_sha256": status["output_manifest_sha256"]}


def main():
    execution = require_hpc()
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    args = parser.parse_args()
    run = Path(args.run_root).resolve()
    audit = run / "audit/join"
    audit.mkdir(parents=True, exist_ok=True)
    records = {}
    status_path = audit / "JOIN_STATUS.json"
    try:
        _progress(audit, execution, "PREREQUISITE_PROVENANCE")
        stages = verify_recovery_stages(run, records, require_join=False)
        receipt = stages["receipt"]
        require(receipt["jobs"].get("join") == execution["job_id"],
                "Live JOIN allocation differs from submitted actual JOIN job")
        specs = stages["retry_chunks"]
        scheduling, raw = scheduler_completed([receipt["jobs"].get("chunk:" + item["id"], "")
            for item in specs], audit / "prerequisite_sacct.txt")
        chunks = []
        for item in specs:
            _progress(audit, execution, "CHUNK_SOURCE_AND_OUTPUT_MANIFEST", chunk_id=item["id"])
            chunks.append(verify_chunk(run, item, records, scheduling))
            _progress(audit, execution, "CHUNK_SOURCE_AND_OUTPUT_MANIFEST_VERIFIED", chunk_id=item["id"])
        # The actual successful chunk jobs, full output manifests, source bytes,
        # runtime records and corrected environment pass first. The new guarded
        # optimization fixtures and original three numerical checks then run
        # once on Puma before inspecting the complete large class tables.
        _progress(audit, execution, "ENGINE_SELF_CHECKS")
        engine_checks = run_engine_self_checks(audit, execution)
        remember_file(records, audit / "JOIN_ENGINE_SELF_CHECKS.json")
        _progress(audit, execution, "ORIGINAL_NUMERICAL_SELF_CHECKS")
        from adapter_self_checks import run_adapter_self_checks
        run_adapter_self_checks(audit / "ADAPTER_SELF_CHECKS.json")
        for script, name in [("association_self_checks.py", "ASSOCIATION_SELF_CHECKS.json"),
                             ("categorical_self_checks.py", "CATEGORICAL_SELF_CHECKS.json")]:
            subprocess.run([sys.executable, str(run / script), "--output", str(audit / name)], check=True)
        verify_self_checks(audit, execution["job_id"], execution, records)
        _progress(audit, execution, "ORIGINAL_NUMERICAL_SELF_CHECKS_PASSED")
        verification = validate_chunk_tables(run, chunks, execution)
        remember_file(records, audit / "CHUNK_VERIFICATION.json")
        _progress(audit, execution, "SOURCE_BEFORE_ASSEMBLY_CHECK")
        source = finish_sources(records, execution, audit / "SOURCE_VERIFICATION.json")
        workers = []
        for shard in stages["shards"]:
            selected = [item for item in chunks if item["chunk"]["shard_id"] == shard["id"]]
            if selected:
                workers.append(assemble_worker(run, shard, selected, execution, source, verification))
        require(len(workers) == 2, "JOIN did not assemble exactly the two failed original workers")
        verify_manifest(run)
        # Assembly reads inputs again; establish the after hash after all such reads.
        _progress(audit, execution, "SOURCE_AFTER_ASSEMBLY_CHECK")
        source_after = finish_sources(records, execution, audit / "SOURCE_VERIFICATION.json")
        require(source_after["files"] == source["files"], "JOIN input evidence changed during assembly")
        save(status_path, {"completed": True, "source_files_changed": False,
            "package_integrity_passed": True, "execution": execution,
            "numerical_self_checks_passed": True, "scientific_review_pending": True,
            "engine_self_checks_passed": engine_checks["passed"],
            "engine_self_checks_sha256": sha(audit / "JOIN_ENGINE_SELF_CHECKS.json"),
            "visual_review_pending": True, "chunk_count": len(chunks), "logical_worker_count": len(workers),
            "chunk_content_verification_passed": True, "workers": workers,
            "source_file_count": len(records), "source_verification_sha256": sha(audit / "SOURCE_VERIFICATION.json"),
            "historical_partial_output_read": False, "completed_utc": utc()})
        _progress(audit, execution, "COMPLETE", logical_workers=2, chunks=6)
        print("JOIN_COMPLETED_LOGICAL_WORKERS=2 CHUNKS=6", flush=True)
    except Exception as error:
        _progress(audit, execution, "FAILED", error=str(error))
        save(status_path, {"completed": False, "source_files_changed": None,
            "package_integrity_passed": False, "execution": execution,
            "numerical_self_checks_passed": False, "scientific_review_pending": True,
            "visual_review_pending": True, "error": str(error), "traceback": traceback.format_exc(),
            "failed_utc": utc()})
        raise


if __name__ == "__main__":
    main()
