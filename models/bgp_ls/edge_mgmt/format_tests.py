"""Format script for CrossHair arg_dictionary test inputs.

Reads tests/nh_update_argdict.txt (or any arg_dictionary-format file from
`crosshair cover`), canonicalizes and dedupes inputs, re-executes the
model on each canonical input, and writes a JSON file of fully-populated
test cases that can compare against actual bgpd execution.

Run from anywhere:
    python3 path/to/format_tests.py [INPUT] [-o OUTPUT]
"""

import argparse
import copy
import json
import os
import sys
from dataclasses import dataclass
from typing import Iterator

_HERE = os.path.dirname(
    os.path.abspath(__file__)
)  # <project-root>/models/bgp_ls/edge_mgmt/
_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))
)  # root project directory
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "mbt"))
sys.path.insert(0, os.path.join(_ROOT, "models"))


print(f"_ROOT: {_ROOT}")

from mbt.modeling_primitives import clamp_u32, to_ipv6_opaque_address, uint8_t  # noqa: E402
from bgp_ls.edge_mgmt.api_bgp_ls_edge_update import (  # noqa: E402
    BApiLinkStateUpdate,
    BgpLsLinkState,
)

# Reaching for the underscore-prefixed helpers from crosshair_target.
# They're the canonical "normalize + build" trio; reusing them here keeps
# the postprocessor exactly in sync with the symbolic entry point.
from bgp_ls.edge_mgmt.crosshair_target import (  # noqa: E402
    _dedup_ted_seed,
    _build_state_and_api,
)


DEFAULT_INPUT = os.path.join(_HERE, "tests", "edge_update_argdict.txt")
DEFAULT_OUTPUT = os.path.join(_HERE, "tests", "edge_update_formatted.json")


OPAQUE_SYS_ID = [0, 0, 0, 0, 0, 0]
BROAD_SYS_ID = [255, 255, 255, 255, 255, 255]
NUM_BYTES_SYS_ID = 6


def to_iso_sys_id(a: list[uint8_t]) -> str:
    """Deterministic ISO System ID for a six-byte list of uint8_t. Keeps 4
    hextets (64 bits) of entropy and zeroes the other 4.
    Suitable for nexthop / endpoint / resolved fields.
    OPAQUE_ROOT (id 0) maps to '::', the IPv6 unspecified address."""
    if len(a) < NUM_BYTES_SYS_ID or a == OPAQUE_SYS_ID:
        return "0000.0000.0000"
    if a == BROAD_SYS_ID:
        return "FFFF.FFFF.FFFF"
    h = ""
    for i in range(NUM_BYTES_SYS_ID):
        if i % 2 == 0 and i > 0:
            h += "."
        h += f"{a[i]:02X}"
    return h


@dataclass
class TestCase:
    """One row from a CrossHair arg_dictionary file, plus slots that the
    processing step fills in.

    After read_and_dedup_argdict, only test_id and inputs are populated.
    After format_test_case (which calls evaluate), all six are populated."""

    test_id: int  # sequential, starts at 1
    inputs: dict  # canonicalized kwargs
    initial_state: BgpLsLinkState | None = None
    api_param: BApiLinkStateUpdate | None = None
    final_state: BgpLsLinkState | None = None
    response: int | None = None


def read_argdict(path: str) -> Iterator[TestCase]:
    """Stream TestCases from a CrossHair arg_dictionary output file.

    Each non-blank line is one Python expression (a dict of kwargs).
    Only the input side of TestCase is populated; computed fields stay None."""
    with open(path) as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            # eval, not ast.literal_eval: CrossHair emits walrus aliases
            # (e.g., `v1:=(0, [])`) which ast rejects. Restricted __builtins__
            # keeps this safe for our trusted-but-walrus-containing input.
            inputs = eval(line, {"__builtins__": {}}, {})
            yield TestCase(test_id=line_no, inputs=inputs)


def canonicalize(inputs: dict) -> dict:
    """Run the same seed coercions the symbolic wrapper applies.

    After this pass, two raw inputs that map to the same model behavior
    become byte-identical, which lets us dedupe."""
    ted_seed = _dedup_ted_seed(inputs["ted_seed"])
    return {
        "ted_seed": ted_seed,
        "api_asn": inputs["api_asn"],
        "api_metric": inputs["api_metric"],
        "api_src_sys_id": inputs["api_src_sys_id"],
        "api_dest_sys_id": inputs["api_dest_sys_id"],
        "api_level": inputs["api_level"],
        "api_local": inputs["api_local"],
        "api_remote": inputs["api_remote"],
    }


def _hash_key(canonical: dict) -> tuple:
    """Hashable fingerprint of canonical inputs (lists become tuples)."""
    return (
        # list[tuple[int, list[int], list[int], int, int]]
        tuple(
            (asn, tuple(src_sys_id), tuple(dest_sys_id), src, dest)
            for asn, src_sys_id, dest_sys_id, src, dest in canonical["ted_seed"]
        ),
        canonical["api_asn"],
        canonical["api_metric"],
        tuple(canonical["api_src_sys_id"]),
        tuple(canonical["api_dest_sys_id"]),
        canonical["api_level"],
        canonical["api_local"],
        canonical["api_remote"],
    )


def read_and_dedup_argdict(path: str) -> Iterator[TestCase]:
    """Read, canonicalize, and dedupe.

    Yields the first occurrence of each canonical key, with `inputs` replaced
    by its canonical form. `test_id` is preserved from the source line so the
    survivor still points back to its original row in the input file."""
    seen: set[tuple] = set()
    for tc in read_argdict(path):
        canonical = canonicalize(tc.inputs)
        key = _hash_key(canonical)
        if key in seen:
            continue
        seen.add(key)
        yield TestCase(test_id=tc.test_id, inputs=canonical)


def evaluate(
    canonical: dict,
) -> tuple[BgpLsLinkState, BApiLinkStateUpdate, BgpLsLinkState, int]:
    """Re-execute the model on canonical inputs.

    Returns (initial_state, api_param, final_state, downstream_msgs).
    `initial_state` is the freshly-built pre-state; `final_state` is a
    deep copy mutated by the transition.

    Asserts the invariant before and after — before is a sanity check on
    canonicalize() (its job is to produce invariant-satisfying states);
    after is the model's claimed property. Each test case is thus also
    an invariant check."""
    print(f"{canonical!r}")
    initial_state, api = _build_state_and_api(**canonical)
    assert initial_state.invariant(), (
        f"canonicalized inputs produced a non-invariant state: {canonical!r}"
    )
    final_state = copy.deepcopy(initial_state)
    res = final_state.api_bgp_ls_edge_update(api)
    assert final_state.invariant(), (
        f"transition broke invariant; inputs={canonical!r}, res={res!r}"
    )
    return initial_state, api, final_state, res


def _state_to_dict(s: BgpLsLinkState) -> dict:
    return {
        "asn": clamp_u32(s.asn),
        "linkstate_ted": [
            {
                "asn": clamp_u32(e.asn),
                "source": to_ipv6_opaque_address(e.source),
                "destination": to_ipv6_opaque_address(e.destination),
                "source_node": {
                    "iso_sys_id": to_iso_sys_id(e.source_node.iso_sys_id),
                    "level": e.source_node.level,
                },
                "destination_node": {
                    "iso_sys_id": to_iso_sys_id(e.dest_node.iso_sys_id),
                    "level": e.dest_node.level,
                },
            }
            for e in s.ted
        ],
        "rib_nlri": [
            {
                "source": {
                    "asn": clamp_u32(e.ls_nlri.source.asn),
                    "igp_router_id": to_iso_sys_id(
                        e.ls_nlri.source.igp_router_id
                    ),
                },
                "destination": {
                    "asn": clamp_u32(e.ls_nlri.destination.asn),
                    "igp_router_id": to_iso_sys_id(
                        e.ls_nlri.destination.igp_router_id
                    ),
                },
                "link": {
                    "interface": to_ipv6_opaque_address(
                        e.ls_nlri.link.interface
                    ),
                    "neighbor": to_ipv6_opaque_address(e.ls_nlri.link.neighbor),
                    "remote_asn": clamp_u32(e.ls_nlri.link.remote_asn),
                },
            }
            for e in s.rib
        ],
    }


def _api_to_dict(a: BApiLinkStateUpdate) -> dict:
    return {
        "event": a.event,
        "remote": {
            "iso_sys_id": to_iso_sys_id(a.remote.iso_sys_id),
            "level": a.remote.level,
        },
        "data": {
            "adv": {
                "iso_sys_id": to_iso_sys_id(a.data.adv.iso_sys_id),
                "level": a.data.adv.level,
            },
            "name": a.data.name if a.data.name else "",
            "metric": a.data.metric,
            "local": to_ipv6_opaque_address(a.data.local),
            "remote": to_ipv6_opaque_address(a.data.remote),
            "valid": True,  # set to true for now
        },
    }


def format_test_case(tc: TestCase) -> dict:
    """Re-execute the model and produce the formatted output dict.

    Populates tc.initial_state / api_param / final_state / downstream_msgs
    in place so in-memory consumers see a fully-formed TestCase."""
    initial_state, api, final_state, res = evaluate(tc.inputs)
    tc.initial_state = initial_state
    tc.api_param = api
    tc.final_state = final_state
    tc.response = res
    return {
        "TestId": tc.test_id,
        "Op": "api_bgp_nh_update",
        "InitialState": _state_to_dict(initial_state),
        "ApiParam": _api_to_dict(api),
        "FinalState": _state_to_dict(final_state),
        "Response": res,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Read a CrossHair arg_dictionary file, canonicalize + dedupe inputs, "
            "re-execute the model, and emit JSON test cases."
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=DEFAULT_INPUT,
        help=f"Path to arg_dictionary file (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Path to output JSON file (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    raw = sum(1 for _ in read_argdict(args.input))
    cases = list(read_and_dedup_argdict(args.input))
    formatted = [format_test_case(tc) for tc in cases]

    with open(args.output, "w") as f:
        json.dump(formatted, f, indent=2)

    print(f"path:     {args.input}")
    print(f"raw:      {raw} inputs")
    print(f"deduped:  {len(cases)} test cases")
    print(f"output:   {args.output}")


if __name__ == "__main__":
    main()
