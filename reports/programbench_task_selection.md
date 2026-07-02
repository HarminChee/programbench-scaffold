# ProgramBench MVP Task Inspection

This report ranks tasks for a small Robin-style dev set: easy CLI programs, manageable tests, and enough oracle-test signal to expose specs to agents.

| rank | task | repo | lang | difficulty | active tests | branches | score | notes |
|---:|---|---|---|---|---:|---:|---:|---|
| 1 | `multiprocessio__dsq.c3ae0ba` | `multiprocessio/dsq` | go | easy | 542 | 10 | 115 | easy; requested candidate; simple build target: go; manageable oracle-test count |
| 2 | `rs__jplot.2a54bcc` | `rs/jplot` | go | easy | 583 | 8 | 115 | easy; requested candidate; simple build target: go; manageable oracle-test count |
| 3 | `sclevine__yj.8016400` | `sclevine/yj` | go | easy | 767 | 9 | 115 | easy; requested candidate; simple build target: go; manageable oracle-test count |
| 4 | `sirwart__ripsecrets.34c9e03` | `sirwart/ripsecrets` | rs | easy | 611 | 10 | 115 | easy; requested candidate; simple build target: rs; manageable oracle-test count |
| 5 | `cmatsuoka__figlet.202a0a8` | `cmatsuoka/figlet` | c | easy | 872 | 12 | 103 | easy; requested candidate; simple build target: c |
| 6 | `mgdm__htmlq.6e31bc8` | `mgdm/htmlq` | rs | easy | 1455 | 10 | 103 | easy; requested candidate; simple build target: rs |
| 7 | `clog-tool__clog-cli.7066cba` | `clog-tool/clog-cli` | rs | easy | 575 | 10 | 70 | easy; simple build target: rs; manageable oracle-test count |
| 8 | `cslarsen__jp2a.61d205f` | `cslarsen/jp2a` | c | easy | 631 | 11 | 70 | easy; simple build target: c; manageable oracle-test count |
| 9 | `drew-alleman__datasurgeon.d257cee` | `Drew-Alleman/DataSurgeon` | rs | easy | 502 | 8 | 70 | easy; simple build target: rs; manageable oracle-test count |
| 10 | `eliukblau__pixterm.1a93fd5` | `eliukblau/pixterm` | go | easy | 430 | 6 | 70 | easy; simple build target: go; manageable oracle-test count |
| 11 | `eradman__entr.8e2e8b4` | `eradman/entr` | c | easy | 586 | 11 | 70 | easy; simple build target: c; manageable oracle-test count |
| 12 | `kisielk__errcheck.dacab89` | `kisielk/errcheck` | go | easy | 341 | 10 | 70 | easy; simple build target: go; manageable oracle-test count |
| 13 | `mibk__dupl.1bf052b` | `mibk/dupl` | go | easy | 373 | 10 | 70 | easy; simple build target: go; manageable oracle-test count |
| 14 | `miserlou__loop.209927c` | `Miserlou/Loop` | rs | easy | 710 | 11 | 70 | easy; simple build target: rs; manageable oracle-test count |
| 15 | `nachoparker__dutree.44e877d` | `nachoparker/dutree` | rs | easy | 641 | 11 | 70 | easy; simple build target: rs; manageable oracle-test count |
| 16 | `psampaz__go-mod-outdated.bb79367` | `psampaz/go-mod-outdated` | go | easy | 285 | 9 | 70 | easy; simple build target: go; manageable oracle-test count |
| 17 | `rbakbashev__elfcat.52f8cc7` | `rbakbashev/elfcat` | rs | easy | 564 | 13 | 70 | easy; simple build target: rs; manageable oracle-test count |
| 18 | `rs__curlie.5dfcbb1` | `rs/curlie` | go | easy | 701 | 10 | 70 | easy; simple build target: go; manageable oracle-test count |
| 19 | `sheepla__pingu.926d475` | `sheepla/pingu` | go | easy | 383 | 8 | 70 | easy; simple build target: go; manageable oracle-test count |
| 20 | `wfxr__code-minimap.0ddeea5` | `wfxr/code-minimap` | rs | easy | 313 | 8 | 70 | easy; simple build target: rs; manageable oracle-test count |
| 21 | `wfxr__csview.8ac4de0` | `wfxr/csview` | rs | easy | 335 | 7 | 70 | easy; simple build target: rs; manageable oracle-test count |
| 22 | `wintermute-cell__ngrrram.8ea13c3` | `wintermute-cell/ngrrram` | rs | easy | 303 | 6 | 70 | easy; simple build target: rs; manageable oracle-test count |
| 23 | `anordal__shellharden.6a6ffd4` | `anordal/shellharden` | rs | easy | 1095 | 15 | 58 | easy; simple build target: rs |
| 24 | `astaxie__bat.17d1080` | `astaxie/bat` | go | easy | 1091 | 15 | 58 | easy; simple build target: go |
| 25 | `testorg__calculator.abc1234` | `testorg/calculator` | bash | easy | 3 | 1 | 54 | easy; single active branch |

## Requested Candidate Snapshot

| task | active tests | top oracle-test groups | sample tests |
|---|---:|---|---|
| `multiprocessio__dsq.c3ae0ba` | 542 | test_harvest(48), test_formats(41), test_output(41), test_query(37), test_errors(31) | `eval.tests.test_advanced_features.test_no_sqlite_writer_env`<br>`eval.tests.test_advanced_features.test_no_sqlite_writer_flag`<br>`eval.tests.test_advanced_features.test_path_specification`<br>`eval.tests.test_basic_invocation.test_help_flag` |
| `rs__jplot.2a54bcc` | 583 | test_cli(47), test_spec_parsing(42), test_stdin_source(35), test_terminal_common(35), test_argparse_validation(30) | `eval.tests.test_basic_invocation.test_help_describes_counter_option`<br>`eval.tests.test_basic_invocation.test_help_describes_interval_option`<br>`eval.tests.test_basic_invocation.test_help_describes_marker_option`<br>`eval.tests.test_basic_invocation.test_help_describes_rows_option` |
| `sclevine__yj.8016400` | 767 | test_flags(49), test_errors(45), test_edge_cases(40), test_order(40), test_toml(39) | `eval.tests.test_additional_coverage.test_hcl_multiline_strings`<br>`eval.tests.test_additional_coverage.test_hcl_to_yaml_with_complex_structure`<br>`eval.tests.test_additional_coverage.test_hcl_with_comments`<br>`eval.tests.test_additional_coverage.test_json_array_with_nulls` |
| `sirwart__ripsecrets.34c9e03` | 611 | test_edge_cases(78), test_harvest(46), test_patterns(46), test_secret_detection(43), test_precommit(38) | `tests.test_basic.test_empty_directory`<br>`tests.test_basic.test_exit_code_no_secrets`<br>`tests.test_basic.test_exit_code_secrets_found`<br>`tests.test_basic.test_help_output` |
| `cmatsuoka__figlet.202a0a8` | 872 | test_layout(43), test_errors(40), test_encoding(37), test_info(36), test_input_edge_cases(33) | `eval.tests.test_additional_coverage.test_ampersand_and_asterisk`<br>`eval.tests.test_additional_coverage.test_at_symbol`<br>`eval.tests.test_additional_coverage.test_brackets_and_braces`<br>`eval.tests.test_additional_coverage.test_equals_and_underscore` |
| `mgdm__htmlq.6e31bc8` | 1455 | test_11_massive_generation(156), test_07_massive_parametrized(138), test_18_absolute_maximum(125), test_16_mega_parametrized(118), test_15_final_massive_push(96) | `eval.tests.test_01_basic.test_help_flag`<br>`eval.tests.test_01_basic.test_no_args_empty_stdin`<br>`eval.tests.test_01_basic.test_selector_attribute`<br>`eval.tests.test_01_basic.test_selector_basic` |
| `noborus__trdsql.d8c5ff6` | 1312 | test_help_output(68), test_output_formats(67), test_input_gaps(52), test_input_formats(50), test_sql_ops(46) | `eval.tests.test_advanced_features.test_between_operator`<br>`eval.tests.test_advanced_features.test_custom_delimiter_output`<br>`eval.tests.test_advanced_features.test_header_with_special_chars`<br>`eval.tests.test_advanced_features.test_in_operator` |

## Immediate Dev-Set Recommendation

Start with `sclevine__yj.8016400`, `multiprocessio__dsq.c3ae0ba`, and `rs__jplot.2a54bcc`. Keep `sirwart__ripsecrets.34c9e03`, `cmatsuoka__figlet.202a0a8`, and `mgdm__htmlq.6e31bc8` as alternates after the first smoke run.
