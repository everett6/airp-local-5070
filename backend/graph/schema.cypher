// Uniqueness constraints — one node per (label, node_id).
CREATE CONSTRAINT company_id IF NOT EXISTS FOR (n:Company) REQUIRE n.node_id IS UNIQUE;
CREATE CONSTRAINT person_id IF NOT EXISTS FOR (n:Person) REQUIRE n.node_id IS UNIQUE;
CREATE CONSTRAINT fund_id IF NOT EXISTS FOR (n:Fund) REQUIRE n.node_id IS UNIQUE;
CREATE CONSTRAINT industry_id IF NOT EXISTS FOR (n:Industry) REQUIRE n.node_id IS UNIQUE;
CREATE CONSTRAINT sector_id IF NOT EXISTS FOR (n:Sector) REQUIRE n.node_id IS UNIQUE;
CREATE CONSTRAINT ticker_id IF NOT EXISTS FOR (n:Ticker) REQUIRE n.node_id IS UNIQUE;
CREATE CONSTRAINT metric_id IF NOT EXISTS FOR (n:Metric) REQUIRE n.node_id IS UNIQUE;
CREATE CONSTRAINT econ_indicator_id IF NOT EXISTS FOR (n:EconomicIndicator) REQUIRE n.node_id IS UNIQUE;
CREATE CONSTRAINT filing_id IF NOT EXISTS FOR (n:Filing) REQUIRE n.node_id IS UNIQUE;
CREATE CONSTRAINT event_id IF NOT EXISTS FOR (n:Event) REQUIRE n.node_id IS UNIQUE;

// Indexes for common lookups.
CREATE INDEX company_symbol IF NOT EXISTS FOR (n:Company) ON (n.symbol);
CREATE INDEX filing_type IF NOT EXISTS FOR (n:Filing) ON (n.filing_type);
CREATE INDEX filing_date IF NOT EXISTS FOR (n:Filing) ON (n.filed_at);

// Every relationship type used by app/knowledge_graph/schema.py, documented
// here for anyone reading the graph directly in the Neo4j browser:
//   (Fund|Person)-[:OWNS]->(Company)
//   (Company)-[:SUPPLIES]->(Company)
//   (Company)-[:COMPETES_WITH]->(Company)
//   (Company|Person)-[:MENTIONED_IN]->(Filing)
//   (EconomicIndicator)-[:AFFECTS]->(Sector|Company)
//   (Company)-[:DEPENDS_ON]->(Company)
//   (Metric)-[:REPORTED_BY]->(Company)
//   (Person)-[:EMPLOYED_BY]->(Company)
//   (Company)-[:MEMBER_OF_SECTOR]->(Sector)
//   (Company)-[:MEMBER_OF_INDUSTRY]->(Industry)
//   (Company)-[:HAS_TICKER]->(Ticker)
