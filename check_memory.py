"""Quick check: verify agent memory entries exist."""
from agents.db import execute_query

rows = execute_query("SELECT agent_name, step_type FROM agent_memory ORDER BY created_at DESC LIMIT 10")
if rows:
    print(f"Found {len(rows)} memory entries:")
    for r in rows:
        print(f"  {r['agent_name']} | {r['step_type']}")
else:
    print("No memory entries found. The workflow may not be writing to memory.")

print()
embeddings = execute_query("SELECT COUNT(*) AS cnt FROM incident_embeddings WHERE embedding_type = 'resolution'")
print(f"Resolution embeddings stored: {embeddings[0]['cnt']}")
