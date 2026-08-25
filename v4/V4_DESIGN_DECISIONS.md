# V4 exploration and scalability decisions

The V3/V4 comparison exposed a real weakness: V4's strict evidence machinery
was sound, but a narrow one-theme bootstrap could spend many rounds in a local
behavior region. More generated cases did not guarantee better coverage.

V4 retains isolation, immutable provenance, exact coverage comparison,
deterministic replay, quality gates, witness-preserving replacement, rollback,
recovery checkpoints and final freeze verification. Those mechanisms protect
validity and are not the cause of poor exploration.

The adopted changes are deliberately bounded:

- up to eight independent bootstrap views in one tranche, never a Cartesian
  product;
- a two-theme portfolio after bootstrap so exploration does not collapse to
  one rotating topic;
- bounded file and uncovered-function clusters for LLVM feedback, without
  dumping raw region segments into prompts;
- a persistent deferred-candidate reservoir with periodic reranking;
- strong novelty (coverage, behavior, error and state) separated from weak
  fixture/assertion variation;
- a small structured boundary set for applicable parser/option behaviors;
- adaptive tranche size based on strong-witness yield.

We did not adopt unrestricted V3-style perspective multiplication, coverage
targets, case-count success criteria, or weak novelty as an indefinite reason
to continue. We also did not remove exact witnesses. Per-case measurement may
be moved to bounded epoch/final boundaries in a future version, but only after
an equivalent recovery and witness-preservation proof exists.

Rust/C/C++ path-signature growth may be an auxiliary saturation condition;
edge union is report-only. Go first-party AFL/QEMU is deliberately report-only
because its stability protocol is an evaluation statistic, not a generation
objective.
