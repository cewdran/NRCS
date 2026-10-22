# Scientific execution boundary

This package follows the user's standing requirement: all scientific numerical
work is performed exclusively on allocated Puma compute nodes. The launch,
status, integrity and collection utilities use the Python standard library.
Scientific entrypoints verify the live Slurm job, cluster and hostname before
importing numerical libraries. Each worker verifies its frozen source receipts
and byte hashes before numerical self-checks and inference.

The corrected Python 3.11.4 environment and its existing READY/pinned package
record are reused and verified on the allocated node. The login-node symlink
may be unresolved. No installation, scientific interpreter fallback, or local
numerical test is authorized by this package.

LOCAL_VALIDATION.json describes preparation checks only. The actual numerical
checks are included as source code and must pass on Puma. A failed worker
prevents global inferential consolidation; the collector preserves available
failure evidence and partial outputs.
