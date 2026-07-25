"""IGP-specific primitives."""

import hashlib
from typing import NewType


# ── Opaque types ─────────────────────────────────────────────────────────────
sys_id_t = NewType("sys_id_t", int)


# ── Opaque renderers ─────────────────────────────────────────────────────────
OPAQUE_SYS_ROOT = 0


def to_sys_id(s: sys_id_t) -> str:
    """Deterministic ISO system identifier for a sys_id_t identifier, over the
    full address space."""
    if s == OPAQUE_SYS_ROOT:
        return "0000.0000.0000"
    h = hashlib.blake2b(str(s).encode(), digest_size=6).digest()
    chunks = [h[i : i + 2].hex() for i in range(0, len(h), 2)]
    return ".".join(chunks)
