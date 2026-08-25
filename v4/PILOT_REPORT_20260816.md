# V4 controlled pilot report — 2026-08-16

## Outcome

The controlled pilot used `mvdan/gofumpt` at pinned commit
`5dca7d819315c5c6338d290ad2e7847f07438693`. The final exercised run is:

`/home/programbench/research/pb-v4-pilot/gofumpt-20260816/output-r16`

The pilot produced a fully recaptured, quality-verified suite of 63 behavioral
tests with 71.4859437751004% Go statement coverage (2670/3735 statements). It
then stopped safely when the next tranche could not fit all exact witnesses
inside the pilot-only 64-case retained-suite circuit breaker.

This is a usable, verified pilot snapshot, but it is deliberately recorded as
`paused_incomplete`, not as marginal saturation or a production freeze. No
20-repository campaign was started.

## Marginal history

| Observation | Cumulative raw candidates | Retained suite | Go statement coverage | New coverage units | New behavior families | New error classes | New fixture shapes |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 24 | 24 | 56.4391% | 1347 | 8 | 2 | 2 |
| 2 | 56 | 52 | 67.9518% | 270 | 15 | 0 | 0 |
| 3 | 88 | 53 | 69.3708% | 37 | 6 | 0 | 0 |
| 4 | 121 | 63 | 71.4859% | 63 | 4 | 0 | 0 |

At observations 3 and 4, the selector performed witness-preserving suite
replacement rather than appending indefinitely:

- 79 staged cases became 53 retained cases while preserving 1710/1710 exact
  coverage/behavior/error/fixture/assertion/state witnesses.
- 82 staged cases became 63 retained cases while preserving 1778/1778 exact
  witnesses.

At observation 5, the quality-passing pool contained 89 captured cases. Exact
replacement proved that a 64-case suite could cover only 1798 of 1802 required
witnesses. The controller therefore refused lossy truncation and transactionally
restored the prior 63-case accepted suite. It did not classify a budget stop as
success or saturation.

## Final verification

- Retained behavioral tests: 63.
- Final oracle capture: 63/63, scope-bound and checkpointed.
- Full retained-suite coverage: 2670/3735 statements, 71.4859437751004%.
- Coverage policy: complete retained suite, not a rotating quick sample.
- Repeat determinism: passed.
- Reference, clean-source, and instrumented-binary consistency: passed.
- Dummy rejection: every retained test rejected every dummy; zero dummy passes.
- Assertion lint: passed.
- Source-leak gate: passed.
- All target executions isolated: true.
- Independent campaign audit: zero critical issues.

The authoritative terminal artifact is `paused_suite_summary.json`. Its stop
reason is `suite_witness_capacity_exhausted`, `successful=false`, candidate
SHA-256 is `d425d351fadf0046e8d7989157d90221b5e41d18f1a8ba2ee34241665987b8b5`,
and scope SHA-256 is
`bdbc3dad9888c02da6a73f81643d1f098176812e8564f5e1639f04d92105f892`.

The original controller status remains `needs_attention` because the pilot
encountered a read-only staging defect during its capacity branch. A separate
scope-validated, campaign-locked finalizer recaptured and reverified the rolled
back 63-case suite and published the authoritative paused summary. The original
failure record was preserved rather than overwritten.

## Witness-preserving replacement

V4 now builds an exact witness universe across these independent dimensions:

- source coverage units;
- normalized behavior families;
- error classes;
- fixture archetypes;
- state/interaction witnesses;
- assertion-strength witnesses.

Cases that are the sole provider of a witness are mandatory. Remaining cases
are selected with deterministic rare-witness weighted set cover, followed by a
bounded diversity fill. If the configured safety fuse cannot preserve every
witness, selection fails closed and keeps the last verified suite. Witness
dimensions are not collapsed into one gameable scalar score.

## Workflow defects found and fixed

The pilot found and fixed generic defects rather than adding repository-specific
exceptions:

1. Exact-output hashes made behavior witnesses too granular; behavior families
   are now normalized while exact coverage witnesses remain exact.
2. Rotating five-case quick samples made coverage observations incomparable;
   refinement now measures the complete retained union and fails closed on
   same-scope regression.
3. Suite replacement accidentally reset the cumulative raw-candidate ledger;
   raw accounting is now independent of retained-suite size.
4. Agent case names and captured names could drift by hyphen/underscore
   normalization; names are canonicalized before capture and manifests are
   validated against the current candidate set.
5. Capacity failure could leave staged cases or a read-only executable in the
   active tree; each iteration now has a transactional snapshot and rollback,
   and staging replacement is atomic.
6. Recovery could ambiguously inherit the failed controller scope; paused-suite
   finalization now has an explicit scope hash, campaign lock, complete recapture,
   full coverage, and freeze-quality re-verification.
7. Candidate growth is controlled by adaptive tranches and replacement. Raw and
   retained limits remain emergency circuit breakers only; reaching either is
   unsuccessful/incomplete, never an accepting quality truncation.

## Isolation and orchestration evidence

- Pinned source and commit are content checked.
- Source build and native tests run offline and non-root.
- Cleanroom execution uses an immutable image ID.
- Oracle capture is binary-only, network-none, read-only, non-root,
  capability-dropped, no-new-privileges, and has no Docker socket.
- Coverage and quality execute the whole harness inside the hardened container;
  tests/harness/source inputs are read-only and only scoped output/tmpfs is
  writable.
- Agent credentials remain host-side and are not mounted or logged.
- The generation agent may inspect pinned source/docs/native tests, but no
  ProgramBench official oracle suite is exposed.
- The unified controller owns campaign locking, atomic state, scoped checkpoints,
  adaptive tranches, audit events, and per-repository isolation.
- Final audit found no live V4 runner and no V4-labeled container.

## Verification and release decision

The final focused regression suite passed 152 tests. Python compilation checks
passed for all V4 modules/tools and the shared oracle generator.

Decision: **controlled pilot complete; verified suite retained; broad production
scale remains paused**. The pilot establishes that witness-preserving replacement,
full-union coverage, fail-closed isolation, recovery, and honest stop semantics
work end to end. Before broad scale, the retained-suite fuse should be calibrated
from observed witness density or approved to grow adaptively; it must never be
silently raised or used to discard witnesses.

## Post-pilot adaptive-capacity gate

The final r16 iteration-5 witness ledger was replayed through the production
adaptive-capacity policy before external scaling. For 89 quality-passing cases
and 1,802 exact witnesses, the fixed 64-case fuse was insufficient; the
deterministic search found a minimum safe cover of 67 cases and set an effective
capacity of 81 with 20% headroom. It selected 67 cases and preserved every
witness. The external20 policy uses the same algorithm with base 128 and hard
emergency ceiling 384; crossing the hard ceiling remains unsuccessful and
fail-closed.
