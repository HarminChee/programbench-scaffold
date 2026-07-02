# Mined Black-Box Behavior Spec: `rs__jplot.2a54bcc`

This scaffold was generated without official tests or source code. It only uses docs already in the cleanroom image plus black-box executions of `/workspace/executable`.

## Prioritized Requirements

- Use the bundled documentation as the primary feature inventory, then use probes to pin down process-level behavior.
- Preserve help/version behavior; help probes produced stdout and define the visible CLI surface.
- Preserve invalid-argument behavior, including nonzero exit codes and stderr/stdout placement.
- Stdin behavior is observable; do not assume the program is file-only.
- File-path behavior is observable; handle existing, empty, and missing files carefully.
- Help-derived option probes found 5 candidate flags; implement common flags before edge-only behavior.

## Bundled Documentation Signals

### `./assets/help_combined.bin`

```text
\x1b[cUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:

OPTIONS:
  -interval duration
    	When url is provided, defines the interval between fetches. Note that counter fields are computed based on this interval. (default 1s)
  -rows int
    	Limits the height of the graph output.
  -steps int
    	Number of values to plot. (default 100)
  -url string
    	URL to fetch every second. Read JSON objects from stdin if not specified.

FIELD_SPEC: [<option>[,<option>...]:]path
  option:
    - counter: Computes the difference with the last value. The value must increase monotonically.
    - marker: When the value is none-zero, a vertical line is drawn.
  path:
    JSON field path (eg: field.sub-field).
```

### `./README.md`

```text
# jplot

Jplot tracks expvar-like (JSON) metrics and plot their evolution over time right into your iTerm2, Kitty terminal, or terminals with DRCS Sixel Graphics support.

![](doc/demo.gif)

Above capture is jplot monitoring a Go service's expvar:

'''
jplot --url http://:8080/debug/vars \
    memstats.HeapSys+memstats.HeapAlloc+memstats.HeapIdle+marker,counter:memstats.NumGC \
    counter:memstats.TotalAlloc \
    memstats.HeapObjects \
    memstats.StackSys+memstats.StackInuse
'''

By default, jplot uses the full size of the terminal, but it is possible to limit the render to a few rows:

![](doc/rows.gif)

## Install

Using homebrew:

'''
brew install rs/tap/jplot
'''

This tool works with [iTerm2](https://www.iterm2.com), [Kitty](https://sw.kovidgoyal.net/kitty/), [Warp](https://www.warp.dev/), or terminals that support DRCS Sixel Graphics.

## Usage

Given the following JSON output:

'''
{
    "mem": {
        "Heap": 1234,
        "Sys": 4321,
        "Stack": 203
    },
    "cpu
...[truncated 1500 chars]
```


## Probe Coverage

- Total probes: 29
- Areas: `{"basic_invocation": 1, "errors": 2, "file_io": 5, "flags": 5, "help_usage": 4, "stdin": 10, "terminal": 2}`
- Return codes: `{"0": 2, "1": 18, "2": 9}`

## Representative Evidence

| case | area | args/env | rc | stdout | stderr |
|---|---|---|---:|---|---|
| `no_args` | basic_invocation | `` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `help_long` | help_usage | `--help` | 0 | \x1b[c | Usage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval between fetches. Note that counter fields are  |
| `help_short` | help_usage | `-h` | 0 | \x1b[c | Usage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval between fetches. Note that counter fields are  |
| `version_long` | help_usage | `--version` | 2 | \x1b[c | flag provided but not defined: -version\nUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval betwe |
| `version_short` | help_usage | `-V` | 2 | \x1b[c | flag provided but not defined: -V\nUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval between fet |
| `invalid_long_flag` | errors | `--programbench-invalid-flag` | 2 | \x1b[c | flag provided but not defined: -programbench-invalid-flag\nUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines  |
| `invalid_short_flag` | errors | `-Z` | 2 | \x1b[c | flag provided but not defined: -Z\nUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval between fet |
| `missing_file` | file_io | `does-not-exist.txt` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `terminal_kitty_no_args` | terminal | `COLORTERM=truecolor TERM=xterm-kitty` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `terminal_sixel_no_args` | terminal | `COLORTERM=truecolor TERM=xterm-256color` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `stdin_empty` | stdin | `` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `dash_stdin_empty` | stdin | `-` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `stdin_plain_text` | stdin | `` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `dash_stdin_plain_text` | stdin | `-` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `stdin_csv` | stdin | `` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `dash_stdin_csv` | stdin | `-` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `stdin_json` | stdin | `` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `dash_stdin_json` | stdin | `-` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `stdin_yamlish` | stdin | `` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `dash_stdin_yamlish` | stdin | `-` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `file_input_txt` | file_io | `input.txt` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `file_table_csv` | file_io | `table.csv` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `file_data_json` | file_io | `data.json` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `file_empty_txt` | file_io | `empty.txt` | 1 | \x1b[cjplot:  iTerm2, Kitty, or DRCS Sixel graphics required\n |  |
| `flag_interval` | flags | `-interval` | 2 | \x1b[c | flag needs an argument: -interval\nUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval between fet |
| `flag_rows` | flags | `-rows` | 2 | \x1b[c | flag needs an argument: -rows\nUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval between fetches |
| `flag_steps` | flags | `-steps` | 2 | \x1b[c | flag needs an argument: -steps\nUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval between fetche |
| `flag_url` | flags | `-url` | 2 | \x1b[c | flag needs an argument: -url\nUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval between fetches. |
| `flag_version` | flags | `-version` | 2 | \x1b[c | flag provided but not defined: -version\nUsage: jplot [OPTIONS] FIELD_SPEC [FIELD_SPEC...]:\n\nOPTIONS:\n  -interval duration\n    	When url is provided, defines the interval betwe |

## Agent Instruction

Use this as behavioral evidence, not as implementation source. First implement the high-priority CLI surface and exact process behavior: arguments, stdin/file handling, stdout/stderr placement, exit codes, and formatting.
