"""Language-neutral public names for Rust/C/C++ AFL++ QEMU evidence.

The legacy ``rust_afl_qemu`` module remains import-compatible with existing
campaign checkpoints. New adapters should import from this module.
"""

from .rust_afl_qemu import (  # noqa: F401
    CHECKPOINT_SCHEMA,
    EDGE_METRIC,
    PATH_METRIC,
    RustAflQemuError as NativeAflQemuError,
    build_rust_qemu_checkpoint as build_native_qemu_checkpoint,
    build_rust_qemu_metrics as build_native_qemu_metrics,
    build_v3_rust_qemu_command as build_v3_native_qemu_command,
    persist_rust_qemu_checkpoint as persist_native_qemu_checkpoint,
    rust_qemu_path_marginal_signal as native_qemu_path_marginal_signal,
    validate_rust_qemu_scope as validate_native_qemu_scope,
)
