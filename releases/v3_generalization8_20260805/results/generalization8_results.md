# V3 Generalization-8 Results

Coverage labels are line/branch for C/C++ and line/region for Rust. Counts are
behavioral pytest functions (one retained case per function). “Selected” is
the suite retained by the generic non-regression promotion rule.

| Repository | Language | Prior tests / coverage | Candidate tests / coverage | Selected | PB tests / coverage |
|---|---|---:|---:|---|---:|
| abishekvashok/cmatrix | C | 127 / 66.3/49.8% | 141 / 66.3/52.1% | candidate | 769 / 88.2/84.8% |
| eradman/entr | C | 269 / 46.5/34.0% | 332 / 46.3/33.3% | prior | 682 / 79.7/64.9% |
| tukaani-project/xz | C | 695 / 49.1/35.8% | 791 / 62.9/46.8% | candidate | 2,025 / 67.5/53.0% |
| OSGeo/GDAL | C++ | 0 / – | 4 / 0.9/0.2% | candidate, parity-limited | 1,319 / – |
| tstack/lnav | C++ | 210 / 22.6/10.7% | 34 / 19.5/8.8% | prior | 1,172 / 42.3/21.8% |
| OSGeo/PROJ | C++ | 526 / 7.3/2.1% | 647 / 7.8/2.1% | candidate | 7,160 / 21.2/5.0% |
| svenstaro/miniserve | Rust | 157 / 40.48/41.35% | 207 / 41.14/41.90% | candidate | 430 / 72.98/73.17% |
| ajeetdsouza/zoxide | Rust | 394 / 63.86/57.56% | 347 / 86.36/80.79% | candidate | 577 / 88.18/83.54% |

All candidate suites had passing deterministic execution and three-binary
consistency where the coverage artifact is marked valid. GDAL's four-case
result is a valid measurement, but the source build lacks the cleanroom's full
driver/data parity. The machine-readable provenance is in
`results_audit_20260805.json`.
