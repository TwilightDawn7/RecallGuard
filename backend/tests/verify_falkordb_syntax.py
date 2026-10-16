"""Verify FalkorDB index and constraint syntax."""

import falkordb

def test_syntax():
    db = falkordb.FalkorDB()
    g = db.select_graph("syntax_check")
    try:
        g.delete()
    except Exception:
        pass

    print("--> Creating index for (c:Claim) ON (c.id)...")
    idx_res = g.query("CREATE INDEX FOR (c:Claim) ON (c.id)")
    print("--> Index created:", idx_res)

    print("--> Creating unique constraint for Claim.id...")
    constraint_res = db.connection.execute_command(
        "GRAPH.CONSTRAINT", "CREATE", "syntax_check", "UNIQUE", "NODE", "Claim", "PROPERTIES", "1", "id"
    )
    print("--> Constraint result:", constraint_res)

    print("--> Testing unique constraint enforcement...")
    g.query("CREATE (:Claim {id: 'c1', subject: 'test', predicate: 'p', object: 'v1'})")
    try:
        g.query("CREATE (:Claim {id: 'c1', subject: 'test', predicate: 'p', object: 'v2'})")
        print("--> Error: duplicate ID was allowed!")
    except Exception as e:
        print(f"--> [SUCCESS] Constraint enforced! FalkorDB blocked duplicate: {e}")

    g.delete()
    print("--> Cleanup completed.")

if __name__ == "__main__":
    test_syntax()
