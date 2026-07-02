# Mined Black-Box Behavior Spec: `multiprocessio__dsq.c3ae0ba`

This scaffold was generated without official tests or source code. It only uses docs already in the cleanroom image plus black-box executions of `/workspace/executable`.

## Prioritized Requirements

- Use the bundled documentation as the primary feature inventory, then use probes to pin down process-level behavior.
- Preserve invalid-argument behavior, including nonzero exit codes and stderr/stdout placement.
- Stdin behavior is observable; do not assume the program is file-only.
- File-path behavior is observable; handle existing, empty, and missing files carefully.
- Help-derived option probes found 2 candidate flags; implement common flags before edge-only behavior.

## Bundled Documentation Signals

### `./LICENSE.md`

```text
Copyright 2022 Multiprocess Labs LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

### `./README.md`

```text
# dsq — command-line SQL for data files (minimal user docs)

dsq is a command-line tool for running SQL queries against data files such as JSON, CSV, Excel, and Parquet.

This repository has been cleaned of implementation details. The information below describes what the tool does and how to use prebuilt binaries.

## Synopsis

Run SQL queries against files on disk or data piped to stdin. Supported input formats include common tabular and columnar formats (CSV, TSV, JSON/NDJSON, Parquet, Excel).

Basic usage examples:

'''bash
$ dsq data.csv "SELECT * FROM {} WHERE id > 10"
$ cat data.json | dsq -s json "SELECT name, COUNT(*) FROM {} GROUP BY name"
'''

Common options (user-facing):

- -p, --pretty    : Enable pretty (table) output
- -s <format>     : Specify streamed input format when piping from stdin (e.g. csv, json, parquet)
- -f, --file      : Read SQL query from a file
- --schema        : Dump inferred schema for the input file
- -C, --cache     : Enable on-disk caching of import
...[truncated 284 chars]
```


## Probe Coverage

- Total probes: 26
- Areas: `{"basic_invocation": 1, "errors": 2, "file_io": 5, "flags": 2, "help_usage": 4, "stdin": 10, "terminal": 2}`
- Return codes: `{"0": 7, "1": 19}`

## Representative Evidence

| case | area | args/env | rc | stdout | stderr |
|---|---|---|---:|---|---|
| `no_args` | basic_invocation | `` | 1 |  | No input files.\n |
| `help_long` | help_usage | `--help` | 0 |  | dsq (Version latest) - commandline SQL engine for data files\n\nUsage:  dsq [file...] $query\n        dsq $file [query]\n        cat $file \| dsq -s $filetype [query]\n        dsq  |
| `help_short` | help_usage | `-h` | 0 |  | dsq (Version latest) - commandline SQL engine for data files\n\nUsage:  dsq [file...] $query\n        dsq $file [query]\n        cat $file \| dsq -s $filetype [query]\n        dsq  |
| `version_long` | help_usage | `--version` | 0 |  | dsq latest\n |
| `version_short` | help_usage | `-V` | 1 |  | Unknown mimetype for file: -V.\n |
| `invalid_long_flag` | errors | `--programbench-invalid-flag` | 1 |  | Unknown mimetype for file: --programbench-invalid-flag.\n |
| `invalid_short_flag` | errors | `-Z` | 1 |  | Unknown mimetype for file: -Z.\n |
| `missing_file` | file_io | `does-not-exist.txt` | 1 |  | open does-not-exist.txt: no such file or directory\n |
| `terminal_kitty_no_args` | terminal | `COLORTERM=truecolor TERM=xterm-kitty` | 1 |  | No input files.\n |
| `terminal_sixel_no_args` | terminal | `COLORTERM=truecolor TERM=xterm-256color` | 1 |  | No input files.\n |
| `stdin_empty` | stdin | `` | 1 |  | No input files.\n |
| `dash_stdin_empty` | stdin | `-` | 1 |  | Unknown mimetype for file: -.\n |
| `stdin_plain_text` | stdin | `` | 1 |  | No input files.\n |
| `dash_stdin_plain_text` | stdin | `-` | 1 |  | Unknown mimetype for file: -.\n |
| `stdin_csv` | stdin | `` | 1 |  | No input files.\n |
| `dash_stdin_csv` | stdin | `-` | 1 |  | Unknown mimetype for file: -.\n |
| `stdin_json` | stdin | `` | 1 |  | No input files.\n |
| `dash_stdin_json` | stdin | `-` | 1 |  | Unknown mimetype for file: -.\n |
| `stdin_yamlish` | stdin | `` | 1 |  | No input files.\n |
| `dash_stdin_yamlish` | stdin | `-` | 1 |  | Unknown mimetype for file: -.\n |
| `file_input_txt` | file_io | `input.txt` | 0 | "hello\nworld\n"\n |  |
| `file_table_csv` | file_io | `table.csv` | 0 | [{"a":"1","b":"2","c":"3"},\n{"a":"4","b":"5","c":"6"}]\n |  |
| `file_data_json` | file_io | `data.json` | 0 | {"name":"programbench","value":7}\n\n |  |
| `file_empty_txt` | file_io | `empty.txt` | 0 | ""\n |  |
| `flag_s` | flags | `-s` | 1 |  | Must specify stdin mimetype.\n |
| `flag_f` | flags | `-f` | 1 |  | Must specify a SQL file.\n |

## Agent Instruction

Use this as behavioral evidence, not as implementation source. First implement the high-priority CLI surface and exact process behavior: arguments, stdin/file handling, stdout/stderr placement, exit codes, and formatting.
