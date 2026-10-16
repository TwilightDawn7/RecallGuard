"""FalkorDB connection and graph management for RecallGuard."""

import os
import logging
from typing import Optional, Any
import falkordb
from recallguard import queries

logger = logging.getLogger("recallguard.database")

class Database:
    def __init__(self):
        self.host = os.getenv("FALKORDB_HOST", "localhost")
        self.port = int(os.getenv("FALKORDB_PORT", "6379"))
        self.graph_name = os.getenv("FALKORDB_GRAPH", "recallguard")
        self.client: Optional[falkordb.FalkorDB] = None
        self._connected = False

    def connect(self) -> falkordb.FalkorDB:
        if self.client is None:
            logger.info(f"Connecting to FalkorDB at {self.host}:{self.port}...")
            self.client = falkordb.FalkorDB(host=self.host, port=self.port)
            self._connected = True
        return self.client

    def ping(self) -> bool:
        try:
            client = self.connect()
            client.list_graphs()
            return True
        except Exception as e:
            logger.error(f"FalkorDB ping failed: {e}")
            return False

    def get_graph(self, name: Optional[str] = None):
        client = self.connect()
        target_name = name or self.graph_name
        return client.select_graph(target_name)

    def init_schema(self, graph_name: Optional[str] = None):
        target = graph_name or self.graph_name
        graph = self.get_graph(target)
        client = self.connect()

        # Create indexes
        for idx_query in [
            queries.Q_INIT_INDEX_CLAIM_ID,
            queries.Q_INIT_INDEX_CLAIM_SUBJECT,
            queries.Q_INIT_INDEX_CLAIM_KEY,
            queries.Q_INIT_INDEX_SOURCE_ID,
            queries.Q_INIT_INDEX_DECISION_ID,
        ]:
            try:
                graph.query(idx_query)
            except Exception as e:
                # Index may already exist
                logger.debug(f"Index query info ({idx_query}): {e}")

        # Create unique constraints
        for prop in ["id", "source_claim_key"]:
            try:
                client.connection.execute_command(
                    "GRAPH.CONSTRAINT", "CREATE", target, "UNIQUE", "NODE", "Claim", "PROPERTIES", "1", prop
                )
            except Exception as e:
                # Constraint may already exist
                logger.debug(f"Constraint info ({prop}): {e}")

db = Database()
