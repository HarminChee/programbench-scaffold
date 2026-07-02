# ProgramBench Codebase Notes

Last updated: 2026-07-01

## Current Research Direction

After the 2026-06-26 meeting, the immediate goal is not to build a complex
black-box probing scaffold first. The immediate goal is to implement an
upper-bound setting:

1. Keep the original ProgramBench no-internet coding-agent environment.
2. Change the task framing from reverse engineering to greenfield
   implementation.
3. Give the agent the same docs available in the original setting.
4. Additionally give the agent executable oracle tests, but without leaking the
   original source code.
5. Rerun the same agent/model/budget and compare with the original baseline.

This tests the hypothesis: if high-quality executable specs are available, how
much better can the same coding agent perform?

## Original ProgramBench Workflow

### Inference

The original mini-swe-agent ProgramBench runner lives in the installed
mini-swe-agent package:

- `/Users/harmin/.cache/uv/archive-v0/bHGx6bTNUDBZt308/lib/python3.13/site-packages/minisweagent/run/benchmarks/programbench.py`
- `/Users/harmin/.cache/uv/archive-v0/bHGx6bTNUDBZt308/lib/python3.13/site-packages/minisweagent/config/benchmarks/programbench.yaml`

Key behavior:

- The runner loads instances with `load_all_instances(include_tests=False)`.
- The inference image is `<task image>:task_cleanroom_v6`.
- The prompt tells the agent this is a reverse-engineering benchmark.
- The agent sees a compiled `./executable` and bundled docs.
- The agent is told not to use internet, not to fetch source, not to wrap the
  binary, and not to decompile or trace the binary.
- Docker is run with `--network none`.
- After the agent stops, `/workspace` is copied out as `submission.tar.gz`.

So, in the original setting, the coding agent does not see official tests or
test metadata during implementation.

### Evaluation

The official evaluator lives in:

- `external/ProgramBench/src/programbench/eval/eval.py`
- `external/ProgramBench/src/programbench/eval/eval_batch.py`
- `external/ProgramBench/src/programbench/utils/blob_store.py`
- `external/ProgramBench/src/programbench/utils/load_data.py`

Key behavior:

- `programbench eval <run-dir>` expects a
  `<run-dir>/<instance_id>/submission.tar.gz`.
- The evaluator starts from the `<task image>:task` image, not the cleanroom
  inference image.
- It extracts the submitted workspace, runs `./compile.sh`, and expects it to
  produce `/workspace/executable`.
- It stashes the compiled executable to
  `/opt/programbench-stashed-executable-do-not-modify`.
- It downloads or locates per-instance test blobs via `get_blob_dir(instance_id)`.
- For each active branch, it copies in
  `<blob_dir>/tests/<branch>.tar.gz`, restores the compiled candidate
  executable, and runs `./eval/run.sh`.
- Results are written as JUnit XML and converted into the final eval JSON.

This means executable tests are deliberately introduced only after candidate
compilation, not during the original agent run.

## Main Artifacts

| Artifact | Meaning |
| --- | --- |
| `task.yaml` | Instance metadata: repo, commit, image name, difficulty, ignored hashes, etc. |
| `tests.json` | Metadata for official tests: branches, test names, ignored tests. It is not the executable test suite itself. |
| HuggingFace test blob | Per-instance test bundle cache under `ProgramBench-Tests`, containing branch tarballs. |
| `<branch>.tar.gz` | A branch-level test artifact used by the evaluator. |
| `task_cleanroom_v6` image | Original no-internet inference image used by mini-swe-agent. |
| `task` image | Evaluation image used by `programbench eval`. |
| `submission.tar.gz` | Agent-produced workspace archive. |
| `<instance_id>.eval.json` | Official ProgramBench evaluation output. |

## Important Discovery: Raw Test Blobs Leak Source

The official branch tarballs are not clean "tests only" bundles. They may
contain the original project source code as well as tests and fixtures.

Examples inspected locally:

- `sclevine__yj.8016400/tests/edbc931c6777.tar.gz` contains `main.go`,
  `flags.go`, `convert/*.go`, `yaml/*.go`, `toml/*.go`, plus `eval/tests`.
- `multiprocessio__dsq.c3ae0ba/tests/e2fbc4b2ccb6.tar.gz` contains
  `main.go`, `sqlite.go`, `go.mod`, `go.sum`, scripts, fixtures, and
  `eval/tests`.

Therefore, we must not copy the official tarballs directly into the agent
workspace for the test-only upper-bound experiment. Doing so would leak the
original implementation and invalidate the experiment.

The correct implementation needs a sanitization step:

1. Read official test blobs on the host side.
2. Extract only allowed test-facing files.
3. Exclude implementation source files and build files that reveal the original
   project.
4. Package the remaining runnable tests, fixtures, and a helper script into a
   sanitized test bundle.
5. Copy the sanitized bundle into the no-internet agent container before the
   agent starts coding.

## What Can Be Exposed To The Agent

Allowed for the first test-only upper-bound MVP:

- Original cleanroom docs already available in the ProgramBench image.
- Sanitized Python pytest files under `eval/tests/`, if they only interact with
  `./executable` as a black-box candidate.
- Test fixture data such as `testdata/`, sample input files, expected golden
  files, and config files that tests directly need.
- A small README explaining how to run the provided tests.
- A helper script such as `oracle_tests/run.sh` that compiles and runs selected
  tests against the agent's current `./executable`.

Not allowed:

- Original source files such as `.go`, `.rs`, `.c`, `.h`, `.py` implementation
  files from the upstream project.
- Original build files if they reveal the upstream implementation route or
  dependency set too directly, such as `go.mod`, `Cargo.toml`, package lock
  files, Makefiles, and project scripts not needed for test execution.
- Any full branch tarball copied raw.

Some Python test files may themselves encode expected behavior in assertions.
That is acceptable for this upper-bound experiment: the point is to give
executable oracle specs. The important boundary is that we should not leak the
original implementation.

## Where To Hook The New Setting

The cleanest MVP is a new local runner rather than patching installed
mini-swe-agent files in-place.

Proposed flow:

1. Load ProgramBench instances with `include_tests=True`.
2. Start the same cleanroom Docker environment used by the original runner.
3. Before `agent.run()`, copy a sanitized test bundle into `/workspace`.
4. Use a new greenfield prompt/config:
   - Keep no-internet, no wrapping, no fetching source, no decompile rules.
   - Remove the claim that behavior must be discovered solely from the binary.
   - Tell the agent it can use the provided executable tests as behavioral
     requirements.
   - Require `./compile.sh` to produce `./executable`, same as original eval.
5. Run the same mini-swe-agent model and budget.
6. Copy `submission.tar.gz`.
7. Evaluate with unchanged `programbench eval`.

This preserves the original official evaluator and changes only the information
available to the agent before coding.

## Implementation Plan For The Test-Only MVP

### 1. Sanitized test bundle builder

Create a tool that takes:

- `instance_id`
- local ProgramBench task metadata
- local HuggingFace test blob directory
- selected active branches

and writes:

- `oracle_tests/<branch>/eval/tests/...`
- `oracle_tests/<branch>/testdata/...`
- `oracle_tests/run_branch.sh`
- `oracle_tests/README.md`
- `oracle_tests/manifest.json`

The first version should use a conservative allowlist:

- include: `eval/`, `testdata/`, `tests/`, `fixtures/`, `examples/`, small
  static data files
- exclude: source/build files such as `*.go`, `*.rs`, `*.c`, `*.h`, `*.cpp`,
  `*.py` outside `eval/tests`, `Cargo.toml`, `go.mod`, `package.json`,
  `Makefile`, `build.sh`, `scripts/`

The manifest should record every included and excluded path so we can audit
leakage.

### 2. Test runner helper

Provide a simple in-container helper:

```bash
./oracle_tests/run_branch.sh <branch>
```

Expected behavior:

- If needed, run `./compile.sh`.
- Ensure `./executable` exists.
- Copy the selected branch's sanitized test files into a temporary run area or
  workspace-compatible location.
- Run the branch's `eval/run.sh` or a generated pytest command.
- Save JUnit/XML or plain logs under `oracle_tests/results/`.

### 3. Greenfield mini-swe-agent config

Create a config override that changes the task framing:

- "You are implementing a program from scratch."
- "You have docs plus executable tests."
- "Use tests to guide implementation."
- "Do not search internet, install the original project, copy source, or wrap
  the reference binary."

This should be a separate config file so baseline and test-only runs are
reproducible.

### 4. Runner wrapper

Create a local runner that mirrors mini-swe-agent's ProgramBench runner but adds
one pre-run step:

```python
inject_sanitized_tests(env, instance_id, bundle_dir)
```

The injection must happen after Docker environment creation and before
`agent.run()`.

## Open Risks

- Sanitization may remove files needed by tests. We need branch-level smoke
  checks to see which fixtures are required.
- Some official tests might import upstream-specific helpers or assume original
  repo layout. Those branches may need task-specific sanitizer rules.
- If we expose too many expected outputs, the upper-bound may become too easy;
  however, that is acceptable at this stage because Robin's stated purpose is
  to estimate an upper bound.
- Full official evaluation still needs Linux x86-64 for reliable results. The
  MacBook can support bundle-building, smoke checks, and limited Docker
  emulation.

## Immediate Next Steps

1. Build a 10-project dev set using the original benchmark language
   distribution as much as possible while keeping tasks human-inspectable.
2. Implement the sanitized test bundle builder.
3. Test it first on `yj` and `dsq`.
4. Implement the greenfield prompt/config and local runner wrapper.
5. Run baseline vs test-only upper-bound on the 10-project dev set when Linux
   x86-64 compute is available; use Mac Docker only for smoke tests.
