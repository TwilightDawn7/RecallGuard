"""Script to verify FalkorDB connection, API methods, and data persistence across container restart."""

import os
import subprocess
import time
import falkordb
import redis

def verify_full_persistence():
    host = os.getenv("FALKORDB_HOST", "localhost")
    port = int(os.getenv("FALKORDB_PORT", "6379"))
    graph_name = "persistence_check"
    container_name = "recallguard-falkordb"

    print("=================================================================")
    print("STEP 1: Connecting to FalkorDB and checking API")
    print("=================================================================")
    db = falkordb.FalkorDB(host=host, port=port)
    print(f"Connected to FalkorDB at {host}:{port}")
    print(f"Current graphs: {db.list_graphs()}")

    # Clean existing test graph
    graph = db.select_graph(graph_name)
    try:
        graph.delete()
        print(f"Cleaned previous graph '{graph_name}'")
    except Exception:
        pass

    test_id = f"claim-persist-{int(time.time())}"
    print(f"\n=================================================================")
    print(f"STEP 2: Writing test data to graph '{graph_name}'")
    print(f"=================================================================")
    create_cypher = """
    CREATE (c:Claim {
        id: $id,
        subject: $subject,
        predicate: $predicate,
        object: $object,
        status: $status,
        created_at: $created_at
    })
    RETURN c.id AS id, c.subject AS subject, c.predicate AS predicate, c.object AS object, c.status AS status
    """
    params = {
        "id": test_id,
        "subject": "system.persistence",
        "predicate": "status",
        "object": "verified",
        "status": "active",
        "created_at": "2026-10-16T05:30:00Z"
    }

    result = graph.query(create_cypher, params)
    print(f"Write successful! Created node: {result.result_set}")

    # NO MANUAL SAVE CALLED HERE. Relying entirely on container's AOF / RDB persistence on restart!
    print("NO manual SAVE called. Relying strictly on container persistence (--appendonly yes, graceful SIGTERM save).")

    print(f"\n=================================================================")
    print(f"STEP 3: Restarting Docker container '{container_name}'")
    print(f"=================================================================")
    restart_res = subprocess.run(["docker", "restart", container_name], capture_output=True, text=True)
    if restart_res.returncode != 0:
        raise RuntimeError(f"Failed to restart container: {restart_res.stderr}")
    print(f"Container restarted: {restart_res.stdout.strip()}")

    # Wait for FalkorDB to come back up
    print("Waiting for FalkorDB to accept connections...")
    reconnected = False
    for attempt in range(15):
        try:
            time.sleep(1)
            test_db = falkordb.FalkorDB(host=host, port=port)
            test_db.list_graphs()
            reconnected = True
            print(f"Reconnected after {attempt + 1}s!")
            break
        except Exception:
            pass

    if not reconnected:
        raise RuntimeError("FalkorDB failed to become ready after restart!")

    print(f"\n=================================================================")
    print(f"STEP 4: Querying graph '{graph_name}' after container restart")
    print(f"=================================================================")
    db_after = falkordb.FalkorDB(host=host, port=port)
    graphs_after = db_after.list_graphs()
    print(f"Graphs available after restart: {graphs_after}")
    assert graph_name in graphs_after, f"Expected graph '{graph_name}' to exist after restart, but found: {graphs_after}"

    graph_after = db_after.select_graph(graph_name)
    read_cypher = """
    MATCH (c:Claim {id: $id})
    RETURN c.id AS id, c.subject AS subject, c.predicate AS predicate, c.object AS object, c.status AS status
    """
    read_result = graph_after.query(read_cypher, {"id": test_id})
    print(f"Read query result: {read_result.result_set}")

    assert len(read_result.result_set) == 1, f"Expected 1 record, got {len(read_result.result_set)}"
    row = read_result.result_set[0]
    assert row[0] == test_id, f"ID mismatch: expected {test_id}, got {row[0]}"
    assert row[1] == "system.persistence", f"Subject mismatch: {row[1]}"
    assert row[2] == "status", f"Predicate mismatch: {row[2]}"
    assert row[3] == "verified", f"Object mismatch: {row[3]}"
    assert row[4] == "active", f"Status mismatch: {row[4]}"

    print("\n=================================================================")
    print("[SUCCESS] Data persistence verified across container restart!")
    print(f"Graph: {graph_name} | Node ID: {test_id} | Object: {row[3]}")
    print("=================================================================")

if __name__ == "__main__":
    verify_full_persistence()
