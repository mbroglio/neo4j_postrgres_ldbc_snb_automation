import os
import sys
import time
import psycopg2
from neo4j import GraphDatabase

# Aggiunge la cartella temp al path per poter importare system_profiler
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from system_profiler import DockerRAMProfiler, plot_ram_usage

NEO4J_URI = "neo4j://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "password"
PG_DSN = "dbname=ldbcsnb user=postgres password=mysecretpassword host=localhost port=5432"

PID = 35184372088910

def run_queries():
    output_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("Connecting to DBs...")
    pg_conn = psycopg2.connect(PG_DSN)
    pg_conn.autocommit = True
    neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    
    for depth in [4]:
        print(f"\n=======================================================")
        print(f" Avvio profilazione per query a {depth} HOP")
        print(f"=======================================================\n")

        profiler = DockerRAMProfiler(
            containers=["postgres-benchmark", "neo4j-benchmark"],
            output_dir=output_dir,
            poll_interval=0.3
        )
        profiler.start()
        
        print("Baseline wait (3s)...")
        time.sleep(3)
        
        # ---------------------------------------------------------------
        # NOTA: La tabella `knows` è già bidirezionale (il loader LDBC
        # inserisce sia (A,B) che (B,A)). Si naviga solo k_person1id →
        # k_person2id per essere equivalenti al pattern Neo4j `-[:KNOWS]-`.
        # ---------------------------------------------------------------
        print(f"Running PostgreSQL {depth}-hop query for PID {PID}...")
        profiler.mark_event("PG_start")
        cur = pg_conn.cursor()
        sql = """
        WITH RECURSIVE friends(person_id, depth) AS (
            SELECT k_person2id, 1
            FROM knows
            WHERE k_person1id = %(pid)s
            UNION ALL
            SELECT k.k_person2id, f.depth + 1
            FROM knows k
            JOIN friends f ON k.k_person1id = f.person_id
            WHERE f.depth < %(depth)s
        )
        SELECT COUNT(DISTINCT person_id) FROM friends WHERE person_id != %(pid)s
        """
        cur.execute(sql, {"pid": PID, "depth": depth})
        pg_result = cur.fetchone()[0]
        profiler.mark_event("PG_end")
        print(f"PostgreSQL result: {pg_result} friends")
        
        print("Cooldown wait (3s)...")
        time.sleep(3)
        
        print(f"Running Neo4j {depth}-hop query for PID {PID}...")
        profiler.mark_event("Neo4j_start")
        with neo4j_driver.session() as s:
            neo4j_result = s.run(
                f"MATCH (p:Person {{id: $pid}})-[:KNOWS*1..{depth}]-(friend:Person) "
                "RETURN count(DISTINCT friend)",
                pid=PID
            ).single()[0]
        profiler.mark_event("Neo4j_end")
        print(f"Neo4j result: {neo4j_result} friends")
        
        # Verifica di coerenza dei risultati
        if pg_result != neo4j_result:
            print(f"⚠️  ATTENZIONE: risultati diversi! PG={pg_result}, Neo4j={neo4j_result}")
        else:
            print(f"✅ Risultati coerenti: {pg_result} amici trovati da entrambi i motori.")
        
        print("Cooldown wait (3s)...")
        time.sleep(3)
        
        profiler.stop()
        json_path = profiler.save(f"ram_results_{depth}hop.json")
        
        print("\nGenerazione del grafico...")
        plot_ram_usage(json_path, output_dir, title=f"Costo Spaziale (RAM) - Query {depth} Hop - Scenario 1", filename=f"ram_chart_{depth}hop.svg")

if __name__ == "__main__":
    run_queries()
