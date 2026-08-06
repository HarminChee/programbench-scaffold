# Previous V3 12-Repository Comparison

This is the prior cross-language table used before the Generalization-8 rerun.
Coverage labels are line/branch for C/C++ and line/region for Rust; dashes mean
the signal was unavailable or not valid.

| Repository | Language | Native tests | V3 tests | PB tests | Native coverage | V3 coverage | PB coverage |
|---|---|---:|---:|---:|---:|---:|---:|
| abishekvashok/cmatrix | C | 0 | 127 | 769 | 0.0/0.0% | 66.3/49.8% | 88.2/84.8% |
| eradman/entr | C | 0 | 269 | 682 | 0.0/0.0% | 46.5/34.0% | 79.7/64.9% |
| ggreer/the_silver_searcher | C | 0 | 479 | 1,192 | 0.0/0.0% | 72.0/62.6% | 84.1/78.3% |
| tukaani-project/xz | C | 0 | 695 | 2,025 | 37.1/23.1% | 49.1/35.8% | 67.5/53.0% |
| OSGeo/GDAL | C++ | 607 | 0 | 1,319 | – | – | – |
| tstack/lnav | C++ | 112 | 210 | 1,172 | 47.3/26.1% | 22.6/10.7% | 42.3/21.8% |
| NikolaDucak/caps-log | C++ | 106 | 209 | 1,226 | 0.0/0.0% | 70.1/39.1% | 79.0/43.0% |
| OSGeo/PROJ | C++ | 1,490 | 526 | 7,160 | 0.0/0.0% | 7.3/2.1% | 21.2/5.0% |
| svenstaro/miniserve | Rust | 6 | 157 | 430 | – | 40.48/41.35% | 72.98/73.17% |
| incu6us/goimports-reviser | Go | 30 | 9 | 593 | 71.2/68.0% | 11.2/3.1% | 67.5/66.6% |
| FiloSottile/age | Go | 41 | 308 | 839 | 59.2/61.9% | 38.8/41.0% | 48.8/50.7% |
| ajeetdsouza/zoxide | Rust | 8 | 394 | 577 | 34.52/36.11% | 63.86/57.56% | 88.18/83.54% |
