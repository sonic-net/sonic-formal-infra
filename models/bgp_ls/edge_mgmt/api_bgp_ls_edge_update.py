"""Executable model of bgpd's link state edge-processing callback
(api_bgp_ls_edge_update).

The four MBT artifacts a tester uses:

* State        — BgpLsLinkState. What bgpd remembers between calls. Based off
                 the Routing Information Base (RIB). Read from and written to
                 by each call.
* Transition   — api_bgp_ls_edge_update(api). The action under test. Returns
                 0 after successfully originating the link; -1 otherwise. Also
                 updates the state.
* Precondition — api_bgp_ls_edge_update(api). Guards the action. Random/
                 symbolic test generators use it to skip or shrink infeasible
                 inputs.
* Invariant    — Must hold at every state the model visits. Checked after each
                 transition.

Scope: api_bgp_ls_edge_update only. Link withdraw and unexpected messages are
out of scope.

"""

from bgp_ls.linkstate_data import *
from dataclasses import dataclass, field
from modeling_primitives import (
    clamp_u32,
    no_dup,
    opaque_addr_t,
    uint32_t,
)


# ── Model: state, contract, transition ───────────────────────────────────────


@dataclass
class BgpLsLinkState:
    """
    The model's overall state. Represents bgpd's memory between API calls.
    Tracks the RIB and BGP-LS TED.
    """

    asn: uint32_t = field(default=uint32_t(100))  # arbitrary ASN for now
    ted: list[LinkStateEdge] = field(default_factory=list)
    rib: list[BgpLsLinkNlri] = field(default_factory=list)

    # ── Invariant ────────────────────────────────────────────────────────────
    def invariant(self) -> bool:
        """
        Groups of constraints:
          1. Keys are unique within each table.
          2. RIB entries are well-formed (NLRI includes node and link
             descriptors).
          3. The LSDB/TED and the RIB agree: every edge described in the NLRI
             corresponds to a link entry within the LSDB/TED; however, the
             LSDB/TED may still have entries that the RIB NLRI does not
             contain.

        Future Modeling Notes:
        * Nexthop deduplication: Nexthops should be duplicated if using multi-
          domain (IS-IS and OSPF). Since the current scope is only IS-IS,
          deduplication is pending.
        """

        # ── Uniqueness ───────────────────────────────────────────────────────
        ted_unique = no_dup(edge for edge in self.ted)
        ted_nonloop = all(edge.source != edge.destination for edge in self.ted)
        ted_one_sys_many_ip = self._sys_id_matches_ip(self.ted)
        ted_nonzero_ids = all(
            edge.source_node.iso_sys_id != 0
            and edge.dest_node.iso_sys_id != 0
            and edge.source != 0
            and edge.destination != 0
            for edge in self.ted
        )

        # ── Format ───────────────────────────────────────────────────────────
        nlri_nonnull = all(nlri is not None for nlri in self.rib)
        nlri_nodes_nonnull = all(
            nlri.source is not None and nlri.destination is not None
            for nlri in self.rib
        )
        nlri_link_nonnull = all(nlri.link is not None for nlri in self.rib)
        nlri_nonloop = all(
            nlri.source.igp_router_id != nlri.destination.igp_router_id
            for nlri in self.rib
        )

        # ── LSDB/TED ─────────────────────────────────────────────────────────
        ted_is_referenced = all(
            any(
                nlri.link.interface == edge.source
                and nlri.link.neighbor == edge.destination
                for edge in self.ted
            )
            for nlri in self.rib
        )

        return (
            ted_unique
            and ted_nonloop
            and ted_one_sys_many_ip
            and ted_nonzero_ids
            and nlri_nonnull
            and nlri_nodes_nonnull
            and nlri_link_nonnull
            and nlri_nonloop
            and ted_is_referenced
        )

    # ── Precondition ─────────────────────────────────────────────────────────
    def api_bgp_ls_edge_update_precondition(
        self, api: BApiLinkStateUpdate
    ) -> bool:
        """Action guard. The harness must only call api_bgp_ls_edge_update when
        this returns True."""

        return self.invariant()

    # ── Transition ───────────────────────────────────────────────────────────
    def api_bgp_ls_edge_update(
        self, api: BApiLinkStateUpdate
    ) -> BgpAttributes | None:
        """Process one zebra link-state update."""

        # find and link reverse edge only if sync, add, or update event
        if api.event == BEvent.UNDEF:
            return None

        if api.event == BEvent.DELETE:
            return self._delete_edge(BApiLinkStateDelete(api))

        if api.data.adv == api.remote or api.data.local == api.data.remote:
            # disallow loopbacks to be appended as NLRI
            return None

        # two-way connectivity check - in the FRR implementation, both forward
        # and reverse direction are checked
        edge = self._find_edge(api.data.local, api.data.remote)

        if not edge:
            self.ted.append(
                LinkStateEdge(
                    self.asn,
                    api.data.adv,
                    api.remote,
                    api.data.local,
                    api.data.remote,
                )
            )

        reverse = self._find_edge(api.data.remote, api.data.local)

        if not reverse:
            self.ted.append(
                LinkStateEdge(
                    self.asn,
                    api.remote,
                    api.data.adv,
                    api.data.remote,
                    api.data.local,
                )
            )

        ls_nlri = BgpLsLinkNlri(
            BgpLsNode(self.asn, api.data.adv.iso_sys_id),
            BgpLsNode(self.asn, api.remote.iso_sys_id),
            BgpLsLink(api.data.local, api.data.remote, self.asn),
        )

        # check if existing entry exists before updating
        if ls_nlri not in self.rib:
            self.rib.append(ls_nlri)

        return BgpAttributes(mp_reach_nlri=ls_nlri)

    def to_dict(self) -> dict:
        return {
            "asn": clamp_u32(self.asn),
            "linkstate_ted": [e.to_dict() for e in self.ted],
            "rib_nlri": [nlri.to_dict() for nlri in self.rib],
        }

    def _delete_edge(self, api: BApiLinkStateDelete) -> BgpAttributes | None:
        """Treat the zebra link-state update as a delete message."""

        edge = self._find_edge(api.data.local, api.data.remote)

        if not edge:
            return None

        # build search NLRI
        nlri: BgpLsLinkNlri = BgpLsLinkNlri(
            BgpLsNode(self.asn, api.data.adv.iso_sys_id),
            BgpLsNode(self.asn, api.remote.iso_sys_id),
            BgpLsLink(
                interface=api.data.local,
                neighbor=api.data.remote,
                remote_asn=self.asn,
            ),
        )

        if nlri in self.rib:
            self.rib.remove(nlri)

        # withdraw
        self.ted.remove(edge)

        return BgpAttributes(mp_unreach_nlri=nlri)

    def _find_edge(
        self, local: opaque_addr_t, remote: opaque_addr_t
    ) -> LinkStateEdge | None:
        """Searches for an existing LinkStateEdge corresponding to the `local`
        and `remote` nodes."""
        for e in self.ted:
            if e.source == local and e.destination == remote:
                return e
        return None

    def _sys_id_matches_ip(self, edges: list[LinkStateEdge]) -> bool:
        """Verifies a strict 1:N mapping between system IDs and IPs."""
        sys_m = {}

        for e in edges:
            # fmt: off
            pairs = (
                (e.source_node, e.source,),
                (e.dest_node, e.destination),
            )
            # fmt: on
            for node, ip in pairs:
                if any(
                    sys_id != node and ip in ips
                    for sys_id, ips in sys_m.items()
                ):
                    return False

                sys_m.setdefault(node, set()).add(ip)

        return True
