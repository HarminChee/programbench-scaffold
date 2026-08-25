# ProgramBench Oracle Gym V4

V4 is a new workflow, not a renamed V3 controller.  V3/V3.4 artifacts remain
read-only inputs and historical evidence; V4 has its own output root, schema,
lock and state machine.

## Non-negotiable policy

- Production configuration has no `maximum_rounds` and no fixed
  `coverage_target`.
- Refinement is a sequence of adaptive tranches.  It continues while a
  quality-passing tranche adds source coverage units, behavior families, error
  classes, fixture shapes, state transitions or assertion strength.
- Saturation needs multiple comparable promoted tranches with both low primary
  coverage gain and zero material novelty.  Go primary coverage is statement;
  Rust primary coverage is LLVM region.
- Smoke/pilot may set `pilot_iteration_fuse`, wall time and candidate/suite
  fuses.  Hitting a fuse is incomplete/needs-attention, never a successful
  freeze.
- Candidate and suite counts are safety fuses. Valuable cases are never
  silently truncated; projected raw/retained counts are checked immediately
  after static selection and pause the repository before oracle capture.
- Raw-candidate fuses charge the durable unique static-key reservoir, while
  generation attempts remain a separate diagnostic. Retries and duplicates do
  not consume the safety budget twice.
- A configured adaptive raw fuse may expand its base limit only after enough
  quality-passing promoted observations demonstrate a minimum yield of exact
  coverage/behavior/error/fixture/assertion/state witnesses per generated
  candidate. The evidence, density and effective limit are persisted; the
  explicit hard limit remains an unsuccessful `paused_incomplete` condition.
- Coverage is comparable only when metric, denominator, instrumented binary
  and sample policy match. A same-scope regression rejects and rolls back the
  staged tranche, remeasures the last accepted complete suite, and keeps the
  rejected rows in an auditable reservoir.
- Legacy baseline revalidation has no scalar tolerance. If an exact coverage
  unit appears lost, V4 identifies plausible owner cases from exact case
  witness maps (or conservative behavior/command evidence), performs at most
  three isolated measurements of the unchanged accepted suite, and accepts the
  exact-unit union only when every prior unit is reproduced and the bound
  quality evidence remains valid. Otherwise the repository stays paused.
- AFL++ QEMU never replaces source coverage. An opt-in native-binary bridge
  (`v4.programbench_v4.native_afl_qemu`) supports Rust/C/C++ and reuses the
  audited V3 runner for a pinned source-built binary. It records
  `distinct_path_signatures` (per-call classified bitmap signatures) and
  `absolute_tuple_union` (report-only tuple union), with offline-container
  provenance and scoped checkpoints.  Its path low-marginal signal is only an
  auxiliary stop hint after two consecutive relative gains below 1%; it never
  replaces Rust LLVM region/C/C++ primary coverage, and edge evidence never
  stops a run.
- Go uses a separate report-only protocol (`v4.programbench_v4.go_afl_qemu`):
  target-module and required `main.*` ranges recovered from `.gopclntab` (ELF
  symbols are a fallback), `AFL_CODE_START=1`, `AFL_CODE_END=1`, N=3 aligned
  oracle-passing replays, mean per-call Jaccard >=0.98, and >=95% stable-edge
  retention. Go calls/path/edge are statistics only and can never guide or stop
  generation.

## Stage graph

```text
dependency_prefetch (manifest-scoped, networked, checkpointed)
  -> preflight (strictly offline)
  -> plan_tranche -> generate -> static_select
  -> oracle_capture -> quick_coverage -> quality_gates
  -> evaluate_marginal
       | marginal value remains: next adaptive tranche
       | marginal saturation: final_capture -> full_coverage
                              -> freeze_verification -> freeze
       | budget/infra/quality/security: pause or needs_attention
```

The generation planner starts with a bounded multi-view bootstrap and then
uses a two-theme portfolio per tranche. It never forms the V3 Cartesian
product of matrix groups, perspectives, options and fixtures. Deferred
candidates remain in a durable reservoir and are periodically reranked. Rust
LLVM feedback includes bounded file and uncovered-function clusters, not raw
segments. Strong novelty (coverage/behavior/error/state) keeps refinement
alive; fixture/assertion-only churn does not.

## Parallelism

One Python controller owns the campaign lock and dispatches repositories using
`repo_workers`.  A repository has one canonical tranche at a time.  The stage
adapter can have separate bounded model/capture/coverage slots, but it cannot
write canonical campaign state.  Successful stage receipts are content scoped
and reused on resume.

After each marginal evaluation the controller also writes a repository
recovery checkpoint. Controller/policy changes invalidate executable stage
receipts, but may import accepted domain state only when immutable repository
identity and the exact candidate-state digest still match. Otherwise recovery
fails closed. Already frozen results under the current scope are not rerun.

Every stage consumes a request bound to the campaign scope and emits a scoped
response plus an atomic receipt. The scope covers source tree, commit, runtime
image, controller, policy, adapter and declared harness files; a code or input
change invalidates stale checkpoints instead of reusing them by path.

Dependency preparation is a first-class stage rather than ambient host state.
It hashes the pinned dependency manifests, commit, source snapshot, immutable
runtime image and language-specific build knobs; fetches Go modules or Rust
registry/git dependencies in an isolated networked container; and atomically
publishes a per-repository cache manifest plus content hash. Resume reuses only
an exact scope/hash match. Offline preflight verifies that manifest, mounts the
cache read-only, and copies it into the container's ephemeral writable
workspace so tool lock files cannot mutate the published cache.

## PB information boundary

- Generation: pinned source, docs and native tests are visible; target PB
  official oracle tests are forbidden.
- Dependency prefetch: pinned source manifests and public package registries
  are available; no host home, credentials, Docker socket or shared mutable
  language cache is mounted. All later source-build/native-test stages use
  `network=none`.
- Agent tool execution: per-repo isolated environment; credentials stay in the
  host-side model broker and are never mounted into containers.
- Oracle capture: binary and case fixtures only; no source/native tests/network.
- Coverage: separate instrumented binary; source may be mounted read-only for
  attribution, while harness/tests remain read-only.
- Final Gym task: binary, usage documentation and explicitly listed runtime
  assets only.

All target execution provenance must prove an immutable `sha256:` image ID,
`network=none`, read-only root, non-root user, dropped capabilities,
no-new-privileges and no Docker socket.

## Auditor

`v4.tools.audit_campaign` is independent of repository workers. It detects
stale heartbeats, malformed stage responses, isolation violations, abnormal
artifact growth and orphaned V4 containers. Each invocation is advisory-only:
it writes one report to `audit/runs/<audit-id>.json` and never writes a repo
status/control file or acquires the campaign lock. The legacy
`--enforce-safe-pause` option is accepted as a no-op for compatibility; a
single controller or supervisor must decide whether to apply an intervention.
This makes it safe to run several read-only Luna auditors concurrently.

After the auditors finish, one deterministic aggregator is the sole writer of
`audit/latest.json`:

```bash
python -m v4.programbench_v4.auditor \
  --campaign-root OUTPUT --audit-id luna-isolation-001
python -m v4.programbench_v4.auditor \
  --campaign-root OUTPUT --audit-id luna-growth-001
python -m v4.programbench_v4.auditor \
  --campaign-root OUTPUT --aggregate
```

Run identifiers are path-safe and must be unique. The aggregator sorts run
IDs and issues deterministically and derives its timestamp from the source
reports, then atomically replaces `audit/latest.json`. Auditors must not write
`repositories/*/control/request.json`, `status.json`, or `campaign.lock`.

## Entry points

```bash
python -m v4.programbench_v4.controller --config CONFIG.json
python -m v4.programbench_v4.auditor --campaign-root OUTPUT --audit-id audit-001
python -m v4.programbench_v4.auditor --campaign-root OUTPUT --aggregate
```

## Production quick start

1. Pin each source to a full commit, snapshot it read-only, compute its source
   tree SHA-256, and use a content-addressed runtime image (`sha256:...`).
2. Prefetch dependencies through V4's scoped networked stage. Every later
   build/capture/coverage/quality stage is offline.
3. Build a campaign config from the schema. For a capable workstation use up
   to `repo_workers=10` and `generation_workers=8`; lower both when memory is
   constrained. One controller owns one output root and campaign lock.
4. Run the controller and one or more advisory auditors. Only one aggregator
   writes `audit/latest.json`.
5. Treat only a repository with final capture, full coverage, freeze
   verification, quality gates and a successful marginal-saturation decision
   as frozen. Preserve the entire repository output, source snapshot,
   dependency manifest/cache identity, config and runtime-image identity.

Repository-specific adapters must return the same stage contracts. The bundled
adapter is production-ready for Go and Rust. The native AFL evidence API is
also language-neutral for C/C++; a C/C++ campaign must supply a build/coverage
adapter that produces the pinned source-built executable and primary coverage
profile before enabling it.

See [PB_EXTERNAL_REPO_SELECTION.md](PB_EXTERNAL_REPO_SELECTION.md) for cohort
selection and [V4_DESIGN_DECISIONS.md](V4_DESIGN_DECISIONS.md) for the V3/V4
trade-off and the bounded exploration changes.

`v4/tools/smoke_stage_adapter.py` is deterministic test infrastructure and is
not a production generator.

The first controlled real-repository pilot and its limitations are documented
in `PILOT_REPORT_20260816.md`. Witness-preserving replacement and adaptive
witness-density capacity were subsequently validated against the pilot's real
1,802-witness ledger: the minimum safe cover was 67 cases, so the base fuse of
64 expanded to an auditable effective fuse of 81 while retaining all witnesses.

The scoped external20 production campaign was launched on 2026-08-16 with
three repository workers. Its source/config/output roots are under
`/home/programbench/research/pb-v4-external20-20260816`. Four unsafe or
tail-heavy candidates were replaced before launch (`goreleaser`, `syft`, `eza`,
and `qsv`); every admitted source is an immutable pinned-commit snapshot.
