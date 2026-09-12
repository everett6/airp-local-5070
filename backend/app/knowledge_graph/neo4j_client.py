from __future__ import annotations

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.knowledge_graph.schema import GraphNode, GraphRelationship


class SchemaViolation(ValueError):
    pass


class KnowledgeGraphClient:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self) -> None:
        await self._driver.close()

    async def upsert_node(self, node: GraphNode) -> None:
        query = (
            f"MERGE (n:{node.label.value} {{node_id: $node_id}}) "
            "SET n += $properties"
        )
        async with self._driver.session() as session:
            await session.run(query, node_id=node.node_id, properties=node.properties)

    async def upsert_relationship(self, rel: GraphRelationship) -> None:
        if not rel.is_valid():
            raise SchemaViolation(
                f"({rel.source_label}, {rel.rel_type}, {rel.target_label}) is not a "
                "registered triple in knowledge_graph.schema.VALID_TRIPLES — add it "
                "there deliberately before writing this edge."
            )
        query = (
            f"MATCH (a:{rel.source_label.value} {{node_id: $source_id}}) "
            f"MATCH (b:{rel.target_label.value} {{node_id: $target_id}}) "
            f"MERGE (a)-[r:{rel.rel_type.value}]->(b) "
            "SET r += $properties, r.evidence_id = $evidence_id"
        )
        async with self._driver.session() as session:
            await session.run(
                query,
                source_id=rel.source_id, target_id=rel.target_id,
                properties=rel.properties, evidence_id=rel.evidence_id,
            )

    async def neighbors(self, node_id: str, rel_type: str | None = None, depth: int = 1) -> list[dict]:
        rel_clause = f":{rel_type}" if rel_type else ""
        query = (
            f"MATCH (n {{node_id: $node_id}})-[r{rel_clause}*1..{depth}]-(m) "
            "RETURN DISTINCT m.node_id AS node_id, labels(m) AS labels, m AS properties"
        )
        async with self._driver.session() as session:
            result = await session.run(query, node_id=node_id)
            return [dict(record) async for record in result]
