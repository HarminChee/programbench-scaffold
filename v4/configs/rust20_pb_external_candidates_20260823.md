# Rust20 PB-external candidate audit (2026-08-23)

This is a selection manifest, not a production launch manifest. Every row was
checked against the local 200-repository ProgramBench test snapshot and the
repositories already used by the V3/V4 campaigns. The pinned revision is the
GitHub `HEAD` observed during this audit.

Common admission properties:

- Rust CLI with a committed `Cargo.lock`.
- No source-tree symlinks in the audited revision.
- Non-empty native Rust test signal and deterministic local behavior suitable
  for an offline oracle harness.
- Not present in the ProgramBench official 200-repository set.
- No known overlap with the existing external20 or earlier replacement pool.

The native-test count is the number of Rust `#[test]`/`#[tokio::test]`
attributes in the pinned source archive. It is a screening signal, not a claim
that every native test is a unit test or that all tests pass in our image.

| Priority | Repository | Pinned revision | CLI surface | Rust source | Native tests | Test/fixture signal | Preflight note |
|---|---|---|---|---:|---:|---:|---|
| A | `facebookincubator/fastmod` | `974e3ef60b784d3eea9b2a214ac0079957415230` | partial text/code replacement | 0.04 MiB | 15 | local unit tests | very small, direct stdin/filesystem behavior |
| A | `mike-engel/jwt-cli` | `6b203a2fc73b09ab50159c2be64939fe76422b6c` | JWT encode/decode | 0.04 MiB | 54 | local unit tests | crypto is pure Rust; use fixed keys/timestamps |
| A | `BurntSushi/bttf` | `fa8b5433f5a4d879d5cd3165d460533f91ecd667` | datetime parse/format/arithmetic | 0.56 MiB | 226 | 27 test source paths | excellent behavior matrix; pin TZ/locale |
| A | `Svetlitski/fcp` | `f8db0603ff0fb66d34d3c461b8e6239b72e1cad2` | file copy | 0.02 MiB | 15 | 17 fixture paths | tiny implementation; tmpfs fixtures only |
| A | `lukas-reineke/cbfmt` | `88a3e46fb15ca855b12bd55712d3641a63aab917` | format fenced code blocks | 0.03 MiB | 2 | formatter fixtures/CI | native count is light; fixture behavior is strong |
| A | `sigoden/projclean` | `2135f417fef41f3da9e312f48bb891d14fcfb7fd` | inspect/clean build artifacts | 0.04 MiB | 9 | local unit tests | run only against synthetic trees |
| A | `ikanago/omekasy` | `a54c47a97a9b3d545aaa075689a1cd2866f8f621` | Unicode text transforms | 0.02 MiB | 15 | local unit tests | especially clean deterministic transformer |
| A | `camdencheek/fre` | `6574ee7045061957de24855567e0abf05f2778d9` | frecency database/query | 0.03 MiB | 49 | 3 test source paths | stateful but isolated under a temp HOME |
| A | `bgreenwell/lstr` | `722cb6317473ac03316dcdac082215959cbb8c40` | directory tree/listing | 0.12 MiB | 81 | 3 test source paths | disable interactive/TUI mode in first pilot |
| A | `greymd/teip` | `07a17777f135e3bdf1b3bf3776be0c01347a0829` | select/transform stdin ranges | 0.10 MiB | 107 | 9 CI workflows | strong pipe-oriented behavior surface |
| A | `juan-leon/lowcharts` | `3e47c2cdfb890c1b4607c6a5a2555bb7b3d72662` | terminal charts | 0.10 MiB | 90 | local unit tests | pin terminal width and color policy |
| A | `theryangeary/choose` | `f1c53eef5706a767b7327889c2bf1c09a3c4006e` | human-friendly cut/awk subset | 0.06 MiB | 214 | 31 test paths | best small text-processing candidate |
| A | `crate-ci/committed` | `800a04ebb4059556c25779bbf8649c4068d2d6f6` | validate commit messages | 0.05 MiB | 22 | fixtures plus 10 CI workflows | feed messages directly; avoid real Git remotes |
| A | `bgreenwell/doxx` | `062819a10f423f2b2ce52be6d264e9069b1b9a40` | inspect/render DOCX files | 0.31 MiB | 69 | 16 test paths/6 CI workflows | local document fixtures only |
| A | `mufeedvh/code2prompt` | `ab4fa06f6fdb9d65c6e713480ba149f8c3fca489` | turn source trees into prompts | 0.48 MiB | 85 | 23 test paths | deterministic filesystem/text surface |
| A | `alexhallam/tv` | `f71935e2e4b310a3f1c87634ea8f837f5e467ba0` | CSV table viewer | 0.20 MiB | 43 | 13 test paths/3 CI workflows | pin terminal width and color policy |
| B | `the-lean-crate/cargo-diet` | `fadfa319f581f9dffc4e8fe83fd41869f42808f7` | minimize Cargo package includes | 0.04 MiB | 20 | 46 fixture paths | excellent fixtures; needs synthetic Cargo projects |
| B | `ribbondz/rsv` | `b2f647fbe37b4787447f7490f7be5d3785eb70f0` | CSV/TXT/Excel analysis | 0.25 MiB | 41 | 17 fixture paths | restrict pilot to bundled local formats |
| B | `pamburus/hl` | `6164b42a30b663587399a6e16099fe373c4e0686` | JSON/logfmt log viewer | 1.53 MiB | 871 | 56 test sources/168 fixture paths | largest candidate but exceptionally strong evidence |
| B | `medialab/xan` | `60a89e5e2644400dec0f227f0d75ef3962ef92ed` | CSV query/filter/transform | 2.11 MiB | 584 | 51 test paths | broadest candidate; pilot local subcommands by strata |

## Recommended launch order

Run the sixteen Priority-A repositories first. Admit Priority-B repositories
only after their per-repository offline preflight validates the exact binary,
fixtures, subprocess limits, and source containment. A failed preflight is a
candidate-level rejection; it must not weaken the shared isolation or quality
policy.

Before production launch, resolve and store for every row: full commit, source
tree SHA-256, Cargo/Rust version, immutable runtime image ID, native test
receipt, native first-party Rust region denominator, and the exact executable
path. This audit deliberately does not clone or start any of these repositories.
