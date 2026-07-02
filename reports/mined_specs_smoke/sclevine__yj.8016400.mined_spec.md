# Mined Black-Box Behavior Spec: `sclevine__yj.8016400`

This scaffold was generated without official tests or source code. It only uses docs already in the cleanroom image plus black-box executions of `/workspace/executable`.

## Prioritized Requirements

- Preserve help/version behavior; help probes produced stdout and define the visible CLI surface.
- Preserve invalid-argument behavior, including nonzero exit codes and stderr/stdout placement.
- Stdin behavior is observable; do not assume the program is file-only.
- File-path behavior is observable; handle existing, empty, and missing files carefully.
- Help-derived option probes found 7 candidate flags; implement common flags before edge-only behavior.

## Probe Coverage

- Total probes: 29
- Areas: `{"basic_invocation": 1, "errors": 2, "file_io": 5, "flags": 7, "help_usage": 4, "stdin": 10}`
- Return codes: `{"0": 16, "1": 13}`

## Representative Evidence

| case | area | args | rc | stdout | stderr |
|---|---|---|---:|---|---|
| `no_args` | basic_invocation | `` | 0 |  |  |
| `help_long` | help_usage | `--help` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `help_short` | help_usage | `-h` | 0 | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |  |
| `version_long` | help_usage | `--version` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `version_short` | help_usage | `-V` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `invalid_long_flag` | errors | `--programbench-invalid-flag` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `invalid_short_flag` | errors | `-Z` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `missing_file` | file_io | `does-not-exist.txt` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `stdin_empty` | stdin | `` | 0 |  |  |
| `dash_stdin_empty` | stdin | `-` | 0 |  |  |
| `stdin_plain_text` | stdin | `` | 0 | "hello world"\n |  |
| `dash_stdin_plain_text` | stdin | `-` | 0 | "hello world"\n |  |
| `stdin_csv` | stdin | `` | 0 | "a,b,c 1,2,3 4,5,6"\n |  |
| `dash_stdin_csv` | stdin | `-` | 0 | "a,b,c 1,2,3 4,5,6"\n |  |
| `stdin_json` | stdin | `` | 0 | {"name":"programbench","value":7}\n |  |
| `dash_stdin_json` | stdin | `-` | 0 | {"name":"programbench","value":7}\n |  |
| `stdin_yamlish` | stdin | `` | 0 | {"name":"programbench","value":7}\n |  |
| `dash_stdin_yamlish` | stdin | `-` | 0 | {"name":"programbench","value":7}\n |  |
| `file_input_txt` | file_io | `input.txt` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `file_table_csv` | file_io | `table.csv` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `file_data_json` | file_io | `data.json` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `file_empty_txt` | file_io | `empty.txt` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `flag_x` | flags | `-x` | 1 |  | Usage: /workspace/executable [-][ytjcrneikhv]\n\nConvert between YAML, TOML, JSON, and HCL.\nPreserves map order.\n\n-x[x]  Convert using stdin. Valid options:\n          -yj, -y = |
| `flag_t` | flags | `-t` | 0 | {}\n |  |
| `flag_j` | flags | `-j` | 1 |  | Error parsing JSON: unexpected end of JSON input\n |
| `flag_r` | flags | `-r` | 1 |  | Error parsing JSON: unexpected end of JSON input\n |
| `flag_c` | flags | `-c` | 0 | {}\n |  |

## Agent Instruction

Use this as behavioral evidence, not as implementation source. First implement the high-priority CLI surface and exact process behavior: arguments, stdin/file handling, stdout/stderr placement, exit codes, and formatting.
