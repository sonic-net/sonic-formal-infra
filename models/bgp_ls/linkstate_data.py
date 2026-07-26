from bgp_ls.edge_mgmt.igp_primitives import sys_id_t, to_sys_id
from dataclasses import dataclass
from enum import IntEnum
from modeling_primitives import (
    clamp_u32,
    opaque_addr_t,
    to_ipv6_opaque_address,
    uint8_t,
    uint32_t,
)
from typing import NewType

# ── Domain types ─────────────────────────────────────────────────────────────
# Abstract types that capture key fields relevant to bgpd's handling of link
# state edge updates.


# ── BGP-LS data structures ───────────────────────────────────────────────────
# BGP-LS-specific types; adheres to RFC and implemented similarly within FRR.


@dataclass
class BgpLsNode:
    """Node descriptor: identifies a router/node in the topology."""

    asn: uint32_t
    igp_router_id: sys_id_t

    def to_dict(self) -> dict:
        return {
            "asn": clamp_u32(self.asn),
            "igp_router_id": to_sys_id(self.igp_router_id),
        }


@dataclass
class BgpLsLink:
    """Link descriptor: identifies a link between two nodes; does not include
    IPv4 addresses. Note that the FRR's link descriptor also specifies
    identifier fields for nodes that do not have IPv4 or IPv6 addresses
    associated with their interfaces."""

    interface: opaque_addr_t  # IPv6 address
    neighbor: opaque_addr_t  # IPv6 address
    remote_asn: uint32_t

    def to_dict(self) -> dict:
        return {
            "interface": to_ipv6_opaque_address(self.interface),
            "neighbor": to_ipv6_opaque_address(self.neighbor),
            "remote_asn": clamp_u32(self.remote_asn),
        }


@dataclass
class BgpLsLinkNlri:
    """Link NLRI: Identifies a link between two nodes.

    Compliant with RFC 9552, which requires that Link NLRI specifically specify
    source and destination node descriptors as well as a link descriptor."""

    source: BgpLsNode
    destination: BgpLsNode
    link: BgpLsLink

    def to_dict(self) -> dict:
        return {
            "source": self.source.to_dict(),
            "destination": self.destination.to_dict(),
            "link": self.link.to_dict(),
        }


# ── Link-state data structures ───────────────────────────────────────────────
# FRR-specific Link-State implementation types. SONiC, running FRR as its
# network stack, relies on these data types to generalize information from both
# IS-IS and OSPF.


@dataclass(frozen=True)
class LinkStateNodeId:
    """Link-state node identifier: ISO system ID + IS-IS level.

    Note that this is IS-IS specific. In case we test OSPF in the future, we'll
    need to be flexible towards other node IDs.

    For reference:
    .. code-block:: c
      enum ls_origin { UNKNOWN = 0, ISIS_L1, ISIS_L2, OSPFv2, DIRECT, STATIC };

      struct ls_node_id {
        enum ls_origin origin;  /* Origin of the LS information */
        union {
          struct {
            struct in_addr addr;    /* OSPF Router IS */
            struct in_addr area_id; /* OSPF Area ID */
          } ip;
          struct {
            uint8_t sys_id[ISO_SYS_ID_LEN]; /* ISIS System ID */
            uint8_t level;                  /* ISIS Level */
            uint8_t padding;
            } iso;
          } id;
        };
      };
    """

    iso_sys_id: sys_id_t
    level: uint8_t

    def to_dict(self) -> dict:
        return {"iso_sys_id": to_sys_id(self.iso_sys_id), "level": self.level}


@dataclass
class LinkStateEdge:
    """Link-state edge: Represents a unidirectional link between two nodes in
    the topology, typically tracked in the TED; operational status field not
    represented for now."""

    asn: uint32_t
    source_node: LinkStateNodeId
    dest_node: LinkStateNodeId
    source: opaque_addr_t
    destination: opaque_addr_t

    def to_dict(self) -> dict:
        return {
            "asn": clamp_u32(self.asn),
            "source": to_ipv6_opaque_address(self.source),
            "destination": to_ipv6_opaque_address(self.destination),
            "source_node": self.source_node.to_dict(),
            "destination_node": self.dest_node.to_dict(),
        }


@dataclass
class LinkStateAttributes:
    """Link-state attributes: payload carried by zebra.

    For reference: see
    https://github.com/FRRouting/frr/blob/master/lib/link_state.h#L190C1-L258C3
    """

    adv: LinkStateNodeId  # advertising router
    name: str | None
    local: opaque_addr_t  # local IPv6 address
    remote: opaque_addr_t  # remote IPv6 address

    def to_dict(self) -> dict:
        return {
            "adv": self.adv.to_dict(),
            "local": to_ipv6_opaque_address(self.local),
            "remote": to_ipv6_opaque_address(self.remote),
        }


# ── API surface ──────────────────────────────────────────────────────────────
# The action under test takes a BApiLinkStateUpdate (what zebra sends) and
# returns 0 after successfully originating the link; -1 otherwise.


# ── Zebra types ──────────────────────────────────────────────────────────────
# Types used in the Zebra API, such as Opaque messages.


class BEvent(IntEnum):
    """Link-state event conveyed by zebra."""

    UNDEF = 0
    SYNC = 1
    ADD = 2
    UPDATE = 3
    DELETE = 4


@dataclass
class BApiLinkStateUpdate:
    """Input payload: zebra's link-state message notification. Type info
    omitted; implied to be link in this API.

    For reference:
    .. code-block:: c
      struct ls_message {
        uint8_t event;		/* Message Event: Sync, Add, Update, Delete */
        uint8_t type;		/* Message Data Type: Node, Attribute, Prefix */
        struct ls_node_id remote_id;	/* Remote Link State Node ID */

        union {
          struct ls_node *node;         /* Link State Node */
          struct ls_attributes *attr;   /* Link State Attributes */
          struct ls_prefix *prefix;     /* Link State Prefix */
        } data;
      };
    """

    event: BEvent
    remote: LinkStateNodeId  # this field is only used if using the attr field
    data: LinkStateAttributes

    def to_dict(self) -> dict:
        return {
            "event": self.event,
            "remote": self.remote.to_dict(),
            "data": self.data.to_dict(),
        }


BApiLinkStateDelete = NewType("BApiLinkStateDelete", BApiLinkStateUpdate)

# ── BGP data structures ──────────────────────────────────────────────────────
# BGP-specific types; adheres to RFC and implemented similarly in FRR>


@dataclass
class BgpAttributes:
    """BGP attributes: as defined by the RFC, core attributes sent from a BGP
    message describing a path.

    For now, the BGP-LS attribute will be set as a flag indicating whether to
    advertise or withdraw a path."""

    mp_reach_nlri: BgpLsLinkNlri | None = None
    mp_unreach_nlri: BgpLsLinkNlri | None = None

    def to_dict(self) -> dict:
        attr_dict = {}
        if self.mp_reach_nlri:
            attr_dict["mp_reach_nlri"] = self.mp_reach_nlri.to_dict()
        if self.mp_unreach_nlri:
            attr_dict["mp_unreach_nlri"] = self.mp_unreach_nlri.to_dict()
        return attr_dict
