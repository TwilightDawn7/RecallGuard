"""Named Cypher query constants for RecallGuard (Rule 9 compliance)."""

# --- Schema & Indexes ---
Q_INIT_INDEX_CLAIM_ID = "CREATE INDEX FOR (c:Claim) ON (c.id)"
Q_INIT_INDEX_CLAIM_SUBJECT = "CREATE INDEX FOR (c:Claim) ON (c.subject)"
Q_INIT_INDEX_CLAIM_KEY = "CREATE INDEX FOR (c:Claim) ON (c.source_claim_key)"
Q_INIT_INDEX_SOURCE_ID = "CREATE INDEX FOR (s:Source) ON (s.id)"
Q_INIT_INDEX_DECISION_ID = "CREATE INDEX FOR (d:Decision) ON (d.id)"

# --- Claim Ingestion & Normalization ---
Q_INGEST_CLAIM = """
MERGE (src:Source {id: $source_id})
ON CREATE SET src.name = $source_name, src.source_type = $source_type, src.trust_tier = $trust_tier
MERGE (sess:Session {id: $session_id})
ON CREATE SET sess.agent_name = $agent_name, sess.created_at = $created_at
MERGE (c:Claim {source_claim_key: $source_claim_key})
ON CREATE SET c.id = $id, c.subject = $subject, c.predicate = $predicate, c.object = $object,
              c.status = $status, c.created_at = $created_at, c.demo = $demo
MERGE (src)-[:ASSERTED]->(c)
MERGE (sess)-[:PRODUCED]->(c)
RETURN c.id AS id, c.subject AS subject, c.predicate AS predicate, c.object AS object,
       c.status AS status, c.created_at AS created_at, c.source_claim_key AS source_claim_key
"""

Q_GET_CLAIM_BY_ID = """
MATCH (c:Claim {id: $id})
OPTIONAL MATCH (src:Source)-[:ASSERTED]->(c)
OPTIONAL MATCH (sess:Session)-[:PRODUCED]->(c)
RETURN c.id AS id, c.subject AS subject, c.predicate AS predicate, c.object AS object,
       c.status AS status, c.created_at AS created_at, c.source_claim_key AS source_claim_key,
       src.id AS source_id, src.name AS source_name, src.source_type AS source_type, src.trust_tier AS trust_tier,
       sess.id AS session_id
"""

Q_LIST_CLAIMS = """
MATCH (c:Claim)
OPTIONAL MATCH (src:Source)-[:ASSERTED]->(c)
OPTIONAL MATCH (sess:Session)-[:PRODUCED]->(c)
RETURN c.id AS id, c.subject AS subject, c.predicate AS predicate, c.object AS object,
       c.status AS status, c.created_at AS created_at, c.source_claim_key AS source_claim_key,
       src.id AS source_id, src.name AS source_name, src.source_type AS source_type, src.trust_tier AS trust_tier,
       sess.id AS session_id
ORDER BY c.created_at DESC
"""

Q_GET_CLAIMS_FOR_SUBJECT_PREDICATE = """
MATCH (c:Claim {subject: $subject, predicate: $predicate})
OPTIONAL MATCH (src:Source)-[:ASSERTED]->(c)
RETURN c.id AS id, c.subject AS subject, c.predicate AS predicate, c.object AS object,
       c.status AS status, c.created_at AS created_at, c.source_claim_key AS source_claim_key,
       src.id AS source_id, src.name AS source_name, src.source_type AS source_type, src.trust_tier AS trust_tier
"""

# --- Edges & Graph Relationships ---
Q_CREATE_DERIVED_FROM = """
MATCH (child:Claim {id: $child_id}), (parent:Claim {id: $parent_id})
MERGE (child)-[:DERIVED_FROM]->(parent)
"""

Q_CREATE_SUPERSEDES = """
MATCH (new:Claim {id: $new_id}), (old:Claim {id: $old_id})
SET old.status = 'superseded'
MERGE (new)-[r:SUPERSEDES]->(old)
SET r.at = $at, r.by_source = $by_source
"""

Q_CREATE_CONTRADICTS = """
MATCH (from_c:Claim {id: $from_id}), (to_c:Claim {id: $to_id})
MERGE (from_c)-[:CONTRADICTS]->(to_c)
"""

Q_SET_CLAIM_STATUS = """
MATCH (c:Claim {id: $id})
SET c.status = $status
"""

# --- Provenance, Taint, and Multi-Hop Chain Head ---
Q_CHECK_CLAIM_TAINT = """
MATCH path = (c:Claim {id: $id})-[:DERIVED_FROM*1..5]->(ancestor:Claim)
WHERE ancestor.status = 'superseded'
RETURN ancestor.id AS ancestor_id, ancestor.subject AS subject, ancestor.object AS object, length(path) AS depth
"""

Q_FIND_CHAIN_HEAD = """
MATCH (head:Claim)-[:SUPERSEDES*1..5]->(old:Claim {id: $old_id})
WHERE head.status = 'active'
OPTIONAL MATCH (src:Source)-[:ASSERTED]->(head)
RETURN head.id AS id, head.subject AS subject, head.predicate AS predicate,
       head.object AS object, head.status AS status, src.trust_tier AS trust_tier
LIMIT 1
"""

# --- Blast Radius ---
Q_BLAST_RADIUS_DOWNSTREAM = """
MATCH path = (target:Claim {id: $id})<-[:DERIVED_FROM*1..5]-(downstream:Claim)
RETURN DISTINCT downstream.id AS id, downstream.subject AS subject, downstream.predicate AS predicate,
       downstream.object AS object, downstream.status AS status, length(path) AS depth
ORDER BY depth ASC
"""

Q_BLAST_RADIUS_DECISIONS = """
MATCH (d:Decision)-[:RELIED_ON]->(c:Claim)
WHERE (c.id = $id OR c.id IN $downstream_ids) AND d.decision = 'ALLOW'
RETURN DISTINCT d.id AS id, d.action_type AS action_type, d.subject AS subject,
       d.predicate AS predicate, d.requested_value AS requested_value, d.decision AS decision,
       d.created_at AS created_at
ORDER BY d.created_at DESC
"""

# --- Decision Node & RELIED_ON Edges ---
Q_RECORD_DECISION = """
CREATE (d:Decision {
    id: $id,
    action_type: $action_type,
    subject: $subject,
    predicate: $predicate,
    requested_value: $requested_value,
    decision: $decision,
    reason_code: $reason_code,
    policy_version: $policy_version,
    created_at: $created_at,
    demo: $demo
})
RETURN d.id AS id
"""

Q_LINK_DECISION_EVIDENCE = """
MATCH (d:Decision {id: $decision_id}), (c:Claim {id: $claim_id})
MERGE (d)-[r:RELIED_ON]->(c)
SET r.role = $role
"""

Q_GET_DECISIONS = """
MATCH (d:Decision)
RETURN d.id AS id, d.action_type AS action_type, d.subject AS subject,
       d.predicate AS predicate, d.requested_value AS requested_value,
       d.decision AS decision, d.reason_code AS reason_code,
       d.policy_version AS policy_version, d.created_at AS created_at
ORDER BY d.created_at DESC
"""

Q_GET_CLAIM_RELATIONSHIPS = """
MATCH (c:Claim {id: $id})-[r]-(other:Claim)
RETURN type(r) AS type, startNode(r).id AS from_id, endNode(r).id AS to_id
"""

# --- Stats Aggregation ---
Q_GET_STATS_TOTAL_CLAIMS = "MATCH (c:Claim) RETURN count(c) AS count"
Q_GET_STATS_SUPERSEDED_CLAIMS = "MATCH (c:Claim {status: 'superseded'}) RETURN count(c) AS count"
Q_GET_STATS_DISPUTED_CLAIMS = "MATCH (c:Claim {status: 'disputed'}) RETURN count(c) AS count"
Q_GET_STATS_DECISIONS_BY_TYPE = "MATCH (d:Decision) RETURN d.decision AS decision_type, count(d) AS count"

# --- Demo Reset ---
Q_DEMO_RESET = "MATCH (n) WHERE n.demo = true DETACH DELETE n"
