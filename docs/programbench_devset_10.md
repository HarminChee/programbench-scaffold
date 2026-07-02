# ProgramBench 10-Project Dev Set

Last updated: 2026-07-01

## Goal

This dev set is for the next phase of the ProgramBench scaffolding project:
the greenfield executable-test upper-bound experiment.

The set should support both:

- engineering development of the runner/test-injection pipeline
- manual case studies explaining why agents fail and when executable specs help

## Selection Policy

Robin's main constraint is that the dev set should not be random. It should be
small enough for manual inspection while roughly reflecting the original
benchmark distribution.

The original ProgramBench task language distribution is:

| language | count | share |
| --- | ---: | ---: |
| Rust | 107 / 201 | 53.2% |
| Go | 46 / 201 | 22.9% |
| C | 33 / 201 | 16.4% |
| C++ | 12 / 201 | 6.0% |
| other | 3 / 201 | 1.5% |

The selected 10-project set is:

| language | count | share |
| --- | ---: | ---: |
| Rust | 5 / 10 | 50% |
| Go | 3 / 10 | 30% |
| C | 2 / 10 | 20% |

This is close to the original Rust-heavy benchmark. Go is slightly
overrepresented because `yj`, `dsq`, and `jplot` already have useful baseline
and oracle-spec trajectory data from the previous phase. C++ is kept as an
alternate because the only easy C++ candidate found so far is `json-tui`, which
has higher TUI/interactive risk.

## Main Dev Set

| # | instance | repo | lang | active tests | branches | why included |
| ---: | --- | --- | --- | ---: | ---: | --- |
| 1 | `sirwart__ripsecrets.34c9e03` | `sirwart/ripsecrets` | Rust | 611 | 10 | Security scanner; concrete CLI/file behavior; prior trajectory data exists. |
| 2 | `wfxr__csview.8ac4de0` | `wfxr/csview` | Rust | 335 | 7 | CSV/table formatting; small enough for manual inspection. |
| 3 | `wfxr__code-minimap.0ddeea5` | `wfxr/code-minimap` | Rust | 313 | 8 | Text-rendering CLI with deterministic output and manageable tests. |
| 4 | `clog-tool__clog-cli.7066cba` | `clog-tool/clog-cli` | Rust | 575 | 10 | Changelog CLI; understandable behavior around help/output/commit fixtures. |
| 5 | `drew-alleman__datasurgeon.d257cee` | `Drew-Alleman/DataSurgeon` | Rust | 502 | 8 | Data extraction CLI; good for spec-gap case studies. |
| 6 | `sclevine__yj.8016400` | `sclevine/yj` | Go | 767 | 9 | Format converter; existing baseline/scaffold data; good continuity task. |
| 7 | `multiprocessio__dsq.c3ae0ba` | `multiprocessio/dsq` | Go | 542 | 10 | SQL-over-data CLI; strong prior oracle-spec signal. |
| 8 | `rs__jplot.2a54bcc` | `rs/jplot` | Go | 583 | 8 | Terminal plotting CLI; existing trajectory data and clear flag/spec failures. |
| 9 | `cmatsuoka__figlet.202a0a8` | `cmatsuoka/figlet` | C | 872 | 12 | Classic docs-rich CLI; tests cover layout, encoding, errors, font loading. |
| 10 | `cslarsen__jp2a.61d205f` | `cslarsen/jp2a` | C | 631 | 11 | Image-to-ASCII CLI; useful C case with fixtures and formatting behavior. |

## Alternates

| instance | repo | lang | reason |
| --- | --- | --- | --- |
| `arthursonzogni__json-tui.17a22b6` | `ArthurSonzogni/json-tui` | C++ | Use if Robin wants C++ represented; default-excluded due TUI risk. |
| `eliukblau__pixterm.1a93fd5` | `eliukblau/pixterm` | Go | Substitute if `jplot` is too slow/flaky on local Docker. |
| `rbakbashev__elfcat.52f8cc7` | `rbakbashev/elfcat` | Rust | Substitute if one Rust task has unusable sanitized tests. |

## How This Set Supports The Next Experiment

For each task, we will run two comparable settings:

1. **Original baseline**
   - Original ProgramBench cleanroom prompt.
   - Docs plus reference executable only.
   - No official tests visible during coding.

2. **Greenfield executable-test upper bound**
   - Same model, same task, same budget.
   - Greenfield prompt instead of reverse-engineering prompt.
   - Docs plus sanitized executable oracle tests.
   - No internet and no original source leakage.

The primary output table should be:

| task | language | baseline score | test-only score | delta | main failure reduction |
| --- | --- | ---: | ---: | ---: | --- |

The trajectory analyzer should also report whether executable tests reduce:

- flag/argument misunderstandings
- stdin/file behavior mistakes
- stdout/stderr/exit-code mismatches
- edge-case omissions
- compile/dependency failures
- repeated low-information probing

## Immediate Execution Order

Start with these smoke cases before running all 10:

1. `sclevine__yj.8016400`
2. `multiprocessio__dsq.c3ae0ba`
3. `sirwart__ripsecrets.34c9e03`

Reason:

- They have already been used in previous runs.
- Local test blobs are cached.
- They expose the main sanitizer problems quickly.
- They cover Go and Rust before expanding to C tasks.

Then expand to the remaining 7 tasks once the sanitized-test bundle builder and
greenfield runner are stable.
