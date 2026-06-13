#!/usr/bin/env python3
"""
=============================================================================
APPENDICE – Query Plan (EXPLAIN / QUERY PROFILE)
=============================================================================
Questo script raccoglie i piani di esecuzione (query plan) sia da PostgreSQL
(EXPLAIN ANALYZE) che da Neo4j (EXPLAIN / PROFILE) per le query chiave del
benchmark, in particolare la query Multi-Hop che è al centro del confronto
di performance.

L'obiettivo è dimostrare al lettore che:
  1. PostgreSQL usa effettivamente gli indici (k_person1id) e NON fa full
     table scan per errore.
  2. Neo4j esegue una vera traversal BFS sfruttando i relationship records
     linkati direttamente nei node records (index-free adjacency).
  3. I piani cambiano all'aumentare della profondità (hop), con PostgreSQL
     che esplode in nested loop joins.

Output:
  - query_plans/pg_explain_*.txt      – piani PostgreSQL (EXPLAIN ANALYZE)
  - query_plans/neo4j_explain_*.txt   – piani Neo4j (PROFILE)
  - query_plans/appendice_explain.md  – markdown formattato per la tesi

Utilizzo:
  python3 collect_query_plans.py
=============================================================================
"""

import os
import sys
import json
from datetime import datetime

try:
    import psycopg2
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                          "--quiet", "psycopg2-binary"])
    import psycopg2

try:
    from neo4j import GraphDatabase
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                          "--quiet", "neo4j"])
    from neo4j import GraphDatabase


# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password"

PG_HOST = "localhost"
PG_PORT = 5432
PG_DB   = "ldbcsnb"
PG_USER = "postgres"
PG_PASS = "mysecretpassword"

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "query_plans")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def banner(s):
    print(f"\n{'='*60}\n  {s}\n{'='*60}")


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------------------
# PostgreSQL EXPLAIN ANALYZE
# ---------------------------------------------------------------------------

def pg_explain(conn, sql: str, params: dict = None) -> str:
    """Esegue EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) e restituisce il piano."""
    explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT, VERBOSE) {sql}"
    cur = conn.cursor()
    try:
        cur.execute(explain_sql, params)
        rows = cur.fetchall()
        conn.rollback()  # rollback per non persistere nulla
        return "\n".join(r[0] for r in rows)
    except Exception as e:
        conn.rollback()
        return f"[ERRORE EXPLAIN] {e}"
    finally:
        cur.close()


def pg_show_index_usage(conn, table: str) -> str:
    """Mostra gli indici disponibili su una tabella."""
    sql = """
    SELECT
        indexname,
        indexdef,
        pg_relation_size(indexrelid) AS size_bytes
    FROM pg_indexes
    JOIN pg_class ON pg_class.relname = pg_indexes.indexname
    JOIN pg_index ON pg_index.indexrelid = pg_class.oid
    WHERE tablename = %s
    ORDER BY indexname;
    """
    cur = conn.cursor()
    try:
        cur.execute(sql, (table,))
        rows = cur.fetchall()
        if not rows:
            return f"Nessun indice trovato su {table}."
        lines = [f"Indici su tabella '{table}':"]
        for name, defn, sz in rows:
            lines.append(f"  [{name}] ({sz:,} bytes)\n    {defn}")
        return "\n".join(lines)
    except Exception as e:
        return f"[ERRORE index query] {e}"
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Neo4j PROFILE / EXPLAIN
# ---------------------------------------------------------------------------

def neo4j_profile(driver, cypher: str, params: dict = None) -> str:
    """Esegue PROFILE su Cypher e restituisce il piano come stringa."""
    profile_cypher = "PROFILE " + cypher
    with driver.session() as s:
        try:
            result = s.run(profile_cypher, **(params or {}))
            # Consuma il risultato per ottenere il profilo
            summary = result.consume()
            plan = summary.profile
            if plan:
                return format_neo4j_plan(plan, indent=0)
            return "[Nessun piano disponibile]"
        except Exception as e:
            return f"[ERRORE PROFILE] {e}"


def neo4j_explain(driver, cypher: str, params: dict = None) -> str:
    """Esegue EXPLAIN su Cypher (senza eseguire la query) e restituisce il piano."""
    explain_cypher = "EXPLAIN " + cypher
    with driver.session() as s:
        try:
            result = s.run(explain_cypher, **(params or {}))
            summary = result.consume()
            plan = summary.plan
            if plan:
                return format_neo4j_plan(plan, indent=0)
            return "[Nessun piano disponibile]"
        except Exception as e:
            return f"[ERRORE EXPLAIN] {e}"


def format_neo4j_plan(plan, indent: int = 0) -> str:
    """Formatta ricorsivamente il piano Neo4j in testo leggibile."""
    prefix = "  " * indent
    op = getattr(plan, "operator_type", "unknown")
    args = getattr(plan, "arguments", {})
    identifiers = getattr(plan, "identifiers", [])
    children = getattr(plan, "children", [])

    # Estrai metriche chiave dagli argomenti
    details = []
    for key in ["EstimatedRows", "Rows", "DbHits", "Memory", "Details"]:
        if key in args:
            details.append(f"{key}={args[key]}")

    ids_str = f"  [{', '.join(str(i) for i in identifiers[:4])}]" if identifiers else ""
    detail_str = f"  {{{', '.join(details)}}}" if details else ""

    lines = [f"{prefix}+-- {op}{ids_str}{detail_str}"]
    for child in children:
        lines.append(format_neo4j_plan(child, indent + 1))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Query chiave da documentare
# ---------------------------------------------------------------------------

QUERIES = {
    # ---- Multi-Hop ----
    "multihop_1hop_neo4j": {
        "db": "neo4j",
        "type": "profile",
        "label": "Multi-Hop 1 hop (Neo4j – BFS tramite index-free adjacency)",
        "cypher": """
        MATCH (p:Person {id: $pid})-[:KNOWS*1..1]-(friend:Person)
        RETURN count(DISTINCT friend) AS cnt
        """,
        "params": {"pid": 983},
    },
    "multihop_3hop_neo4j": {
        "db": "neo4j",
        "type": "profile",
        "label": "Multi-Hop 3 hop (Neo4j – crescita lineare nella traversal)",
        "cypher": """
        MATCH (p:Person {id: $pid})-[:KNOWS*1..3]-(friend:Person)
        RETURN count(DISTINCT friend) AS cnt
        """,
        "params": {"pid": 983},
    },
    "multihop_1hop_pg": {
        "db": "postgres",
        "type": "explain",
        "label": "Multi-Hop 1 hop (PostgreSQL – uso indice k_person1id)",
        "sql": """
        WITH RECURSIVE friends(person_id, depth) AS (
            SELECT k_person2id, 1
            FROM knows
            WHERE k_person1id = %(pid)s
            UNION ALL
            SELECT k.k_person2id, f.depth + 1
            FROM knows k
            JOIN friends f ON k.k_person1id = f.person_id
            WHERE f.depth < 1
        )
        SELECT COUNT(DISTINCT person_id) AS cnt
        FROM friends
        WHERE person_id != %(pid)s
        """,
        "params": {"pid": 983},
    },
    "multihop_3hop_pg": {
        "db": "postgres",
        "type": "explain",
        "label": "Multi-Hop 3 hop (PostgreSQL – Nested Loop Join che esplode)",
        "sql": """
        WITH RECURSIVE friends(person_id, depth) AS (
            SELECT k_person2id, 1
            FROM knows
            WHERE k_person1id = %(pid)s
            UNION ALL
            SELECT k.k_person2id, f.depth + 1
            FROM knows k
            JOIN friends f ON k.k_person1id = f.person_id
            WHERE f.depth < 3
        )
        SELECT COUNT(DISTINCT person_id) AS cnt
        FROM friends
        WHERE person_id != %(pid)s
        """,
        "params": {"pid": 983},
    },
    "multihop_4hop_pg": {
        "db": "postgres",
        "type": "explain",
        "label": "Multi-Hop 4 hop (PostgreSQL – esplosione combinatoria, ~600ms)",
        "sql": """
        WITH RECURSIVE friends(person_id, depth) AS (
            SELECT k_person2id, 1
            FROM knows
            WHERE k_person1id = %(pid)s
            UNION ALL
            SELECT k.k_person2id, f.depth + 1
            FROM knows k
            JOIN friends f ON k.k_person1id = f.person_id
            WHERE f.depth < 4
        )
        SELECT COUNT(DISTINCT person_id) AS cnt
        FROM friends
        WHERE person_id != %(pid)s
        """,
        "params": {"pid": 983},
    },
    # ---- Shortest Path ----
    "shortestpath_neo4j": {
        "db": "neo4j",
        "type": "explain",
        "label": "Shortest Path (Neo4j – shortestPath BFS bidirezionale nativo)",
        "cypher": """
        MATCH (src:Person {id: $src}), (dst:Person {id: $dst})
        MATCH path = shortestPath((src)-[:KNOWS*..6]-(dst))
        RETURN length(path) AS hops
        """,
        "params": {"src": 983, "dst": 28587302323389},
    },
    # ---- Aggregazione globale ----
    "aggregation_pg": {
        "db": "postgres",
        "type": "explain",
        "label": "Aggregazione Globale (PostgreSQL – Sequential Scan su message)",
        "sql": """
        SELECT m_browserused AS browser,
               AVG(LENGTH(m_content::text)) AS avg_len,
               COUNT(*) AS cnt
        FROM message
        WHERE m_browserused IS NOT NULL
          AND m_content IS NOT NULL
          AND m_c_replyof IS NULL
        GROUP BY m_browserused
        ORDER BY m_browserused
        """,
        "params": {},
    },
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    banner("APPENDICE – Raccolta Query Plan (EXPLAIN/PROFILE)")
    ensure_dir(OUTPUT_DIR)

    # Connessioni
    print("\n[*] Connessione ai database...")
    try:
        neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        neo4j_driver.verify_connectivity()
        print("  [OK] Neo4j")
    except Exception as e:
        print(f"  [ERR] Neo4j: {e}")
        neo4j_driver = None

    try:
        pg_conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
        )
        pg_conn.autocommit = False
        print("  [OK] PostgreSQL")
    except Exception as e:
        print(f"  [ERR] PostgreSQL: {e}")
        pg_conn = None

    # Mostra indici PostgreSQL
    md_sections = [
        "# Appendice: Query Plan (EXPLAIN / PROFILE)\n",
        f"_Generato: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_\n",
        "\n## A. Indici PostgreSQL sulla tabella `knows`\n",
    ]

    if pg_conn:
        idx_info = pg_show_index_usage(pg_conn, "knows")
        print(f"\n{idx_info}")
        md_sections.append(f"```\n{idx_info}\n```\n")

        # Indici anche su `message` e `person`
        for tbl in ["message", "person"]:
            idx_info2 = pg_show_index_usage(pg_conn, tbl)
            md_sections.append(f"\n### Indici su `{tbl}`\n```\n{idx_info2}\n```\n")

    md_sections.append("\n## B. Piani di Esecuzione Query Chiave\n")

    # Raccolta piani
    all_plans = {}
    for key, q in QUERIES.items():
        label = q["label"]
        banner(f"Query: {label}")

        plan_text = ""
        if q["db"] == "neo4j" and neo4j_driver:
            if q["type"] == "profile":
                plan_text = neo4j_profile(neo4j_driver, q["cypher"], q.get("params"))
            else:
                plan_text = neo4j_explain(neo4j_driver, q["cypher"], q.get("params"))
        elif q["db"] == "postgres" and pg_conn:
            plan_text = pg_explain(pg_conn, q["sql"], q.get("params"))
        else:
            plan_text = "[SKIP – database non disponibile]"

        print(plan_text[:2000])  # stampa primi 2000 caratteri

        # Salva file individuale
        fname = f"{key}.txt"
        fpath = os.path.join(OUTPUT_DIR, fname)
        with open(fpath, "w") as f:
            f.write(f"Query: {label}\n{'='*60}\n")
            f.write(plan_text)
        print(f"  [OK] Salvato: {fpath}")

        # Aggiungi alla sezione Markdown
        db_tag = q["db"].upper()
        qtype = q["type"].upper()
        query_text = q.get("cypher") or q.get("sql", "")
        md_sections.append(
            f"\n### {label}\n\n"
            f"**Database:** {db_tag}  |  **Tipo analisi:** {qtype}\n\n"
            f"**Query:**\n```{'cypher' if q['db'] == 'neo4j' else 'sql'}\n{query_text.strip()}\n```\n\n"
            f"**Piano di esecuzione:**\n```\n{plan_text}\n```\n"
        )
        all_plans[key] = {"label": label, "plan": plan_text}

    # Salva Markdown appendice
    md_path = os.path.join(OUTPUT_DIR, "appendice_explain.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md_sections))
    print(f"\n[OK] Appendice Markdown salvata in: {md_path}")

    # Salva JSON
    json_path = os.path.join(OUTPUT_DIR, "query_plans.json")
    with open(json_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "plans": all_plans
        }, f, indent=2, ensure_ascii=False)
    print(f"[OK] JSON salvato in: {json_path}")

    if neo4j_driver:
        neo4j_driver.close()
    if pg_conn:
        pg_conn.close()

    print("\n[*] Raccolta query plan completata.")
    print(f"    Output: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
