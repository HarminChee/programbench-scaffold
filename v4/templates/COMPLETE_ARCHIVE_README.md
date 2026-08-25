# Protected V4 Rust15 complete archive

This directory is the permanent, complete archive for the PB-external Rust15
cohort (historically selected from the Rust20 campaign). **Do not delete or
compact the `campaign/` tree.**

Contents:

- `campaign/sources/<instance>/`: immutable pinned source snapshots and source
  metadata;
- `campaign/output/repositories/<instance>/`: complete V4 repository outputs,
  including candidates, runnable tests and fixtures, bundles, reference and
  coverage binaries, native measurements, AFL maps/metrics, exact witnesses,
  coverage profiles, quality evidence, logs, stage receipts, checkpoints and
  frozen summaries;
- `campaign/recovery_*` and campaign JSON files: controller configuration and
  recovery/closeout history;
- `v4-workflow-3daa9a2.tar.gz`: exact released workflow source used to preserve
  the implementation, plus its SHA-256 file;
- `COMPLETE_ARCHIVE_MANIFEST.json`: verified source/output pairing, counts,
  required-artifact hashes and settlement fields for all 15 repositories;
- `PROTECTED_DO_NOT_DELETE.txt`: cleanup protection marker.

The original campaign path is retained as a symbolic link to this directory so
historical absolute paths continue to resolve. The smaller Rust15 tarballs in
the adjacent protected collection are portable derivatives; they do not
replace this complete archive.
