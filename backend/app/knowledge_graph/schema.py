"""
Typed entity/relationship schema for the knowledge graph. Agents reference
graph node/edge ids in their output instead of embedding facts as free text
(Layer 2 requirement). This module is the single source of truth for what
node labels and relationship types are valid — `neo4j_client.py` rejects
writes that don't match it, so the graph can't silently drift into an
untyped mess.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class NodeLabel(str, Enum):
    COMPANY = "Company"
    PERSON = "Person"
    FUND = "Fund"
    INDUSTRY = "Industry"
    SECTOR = "Sector"
    TICKER = "Ticker"
    METRIC = "Metric"
    ECONOMIC_INDICATOR = "EconomicIndicator"
    FILING = "Filing"
    EVENT = "Event"


class RelationshipType(str, Enum):
    OWNS = "OWNS"
    SUPPLIES = "SUPPLIES"
    COMPETES_WITH = "COMPETES_WITH"
    MENTIONED_IN = "MENTIONED_IN"
    AFFECTS = "AFFECTS"
    DEPENDS_ON = "DEPENDS_ON"
    REPORTED_BY = "REPORTED_BY"
    EMPLOYED_BY = "EMPLOYED_BY"
    MEMBER_OF_SECTOR = "MEMBER_OF_SECTOR"
    MEMBER_OF_INDUSTRY = "MEMBER_OF_INDUSTRY"
    HAS_TICKER = "HAS_TICKER"


# Constrains which (source_label, rel_type, target_label) triples are valid,
# enforced by neo4j_client.upsert_relationship before any Cypher write runs.
VALID_TRIPLES: set[tuple[NodeLabel, RelationshipType, NodeLabel]] = {
    (NodeLabel.FUND, RelationshipType.OWNS, NodeLabel.COMPANY),
    (NodeLabel.PERSON, RelationshipType.OWNS, NodeLabel.COMPANY),
    (NodeLabel.COMPANY, RelationshipType.SUPPLIES, NodeLabel.COMPANY),
    (NodeLabel.COMPANY, RelationshipType.COMPETES_WITH, NodeLabel.COMPANY),
    (NodeLabel.COMPANY, RelationshipType.MENTIONED_IN, NodeLabel.FILING),
    (NodeLabel.PERSON, RelationshipType.MENTIONED_IN, NodeLabel.FILING),
    (NodeLabel.ECONOMIC_INDICATOR, RelationshipType.AFFECTS, NodeLabel.SECTOR),
    (NodeLabel.ECONOMIC_INDICATOR, RelationshipType.AFFECTS, NodeLabel.COMPANY),
    (NodeLabel.COMPANY, RelationshipType.DEPENDS_ON, NodeLabel.COMPANY),
    (NodeLabel.METRIC, RelationshipType.REPORTED_BY, NodeLabel.COMPANY),
    (NodeLabel.PERSON, RelationshipType.EMPLOYED_BY, NodeLabel.COMPANY),
    (NodeLabel.COMPANY, RelationshipType.MEMBER_OF_SECTOR, NodeLabel.SECTOR),
    (NodeLabel.COMPANY, RelationshipType.MEMBER_OF_INDUSTRY, NodeLabel.INDUSTRY),
    (NodeLabel.COMPANY, RelationshipType.HAS_TICKER, NodeLabel.TICKER),
}


class GraphNode(BaseModel):
    node_id: str
    label: NodeLabel
    properties: dict[str, Any]


class GraphRelationship(BaseModel):
    source_id: str
    source_label: NodeLabel
    rel_type: RelationshipType
    target_id: str
    target_label: NodeLabel
    properties: dict[str, Any] = {}
    evidence_id: str | None = None  # ties the edge back to evidence/models.EvidenceRef

    def is_valid(self) -> bool:
        return (self.source_label, self.rel_type, self.target_label) in VALID_TRIPLES
