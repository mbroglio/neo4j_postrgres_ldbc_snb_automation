MATCH (p:Person) WITH p ORDER BY size((p)-[:KNOWS]-()) DESC LIMIT 1
MATCH (p)-[*1..3]-(q:Person) RETURN count(DISTINCT q);

MATCH (p:Person) WITH p ORDER BY size((p)-[:KNOWS]-()) DESC LIMIT 1
MATCH (p)-[:KNOWS*1..3]-(q:Person) RETURN count(DISTINCT q);
