#!/usr/bin/env python3
"""
=============================================================================
SCENARIO 1: La Potenza del Grafo (Neo4j vs RDBMS)
=============================================================================
Test suite che confronta Neo4j e PostgreSQL su tre classi di query grafiche:
  1.1 - Query Multi-Hop (profondità di navigazione crescente: 1, 2, 3, 4 hop)
  1.2 - Pattern Matching e Ricerca di Cicli (triangle detection)
  1.3 - Pathfinding (Cammini Minimi / Shortest Path)

Metodologia:
  - Warm-up iniziale per popolare le page cache
  - N_RUNS ripetizioni per ogni query
  - Metriche: media, mediana, 90° percentile, min, max (in ms)
  - Throughput in QPS durante lo stress test
=============================================================================
"""

import time
import random
import statistics
import json
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Dipendenze – installazione se mancanti
# ---------------------------------------------------------------------------
try:
    import psycopg2
except ImportError:
    import subprocess

    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "psycopg2-binary"]
    )
    import psycopg2

try:
    from neo4j import GraphDatabase
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "neo4j"])
    from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Configurazione connessioni
# ---------------------------------------------------------------------------
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password"

PG_HOST = "localhost"
PG_PORT = 5432
PG_DB = "ldbcsnb"
PG_USER = "postgres"
PG_PASS = "mysecretpassword"

# ---------------------------------------------------------------------------
# Parametri benchmark
# ---------------------------------------------------------------------------
N_RUNS = 10  # ripetizioni per query (escl. warm-up)
N_WARMUP = 3  # esecuzioni di warm-up

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def banner(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def sub_banner(title: str):
    print(f"\n--- {title} ---")


def measure_ms(fn, *args, **kwargs):
    """Esegue fn(*args, **kwargs) e restituisce (risultato, latenza_ms)."""
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    t1 = time.perf_counter()
    return result, (t1 - t0) * 1000.0


def compute_stats(times_ms: list[float]) -> dict:
    """Calcola le statistiche di latenza su una lista di misurazioni."""
    if not times_ms:
        return {}
    sorted_t = sorted(times_ms)
    n = len(sorted_t)
    p90_idx = min(int(n * 0.90), n - 1)
    return {
        "n": n,
        "mean_ms": round(statistics.mean(sorted_t), 3),
        "median_ms": round(statistics.median(sorted_t), 3),
        "p90_ms": round(sorted_t[p90_idx], 3),
        "min_ms": round(sorted_t[0], 3),
        "max_ms": round(sorted_t[-1], 3),
    }


def print_stats(label: str, stats: dict):
    print(f"  {label}:")
    print(f"    Iterazioni : {stats['n']}")
    print(f"    Media      : {stats['mean_ms']:>10.3f} ms")
    print(f"    Mediana    : {stats['median_ms']:>10.3f} ms")
    print(f"    P90        : {stats['p90_ms']:>10.3f} ms")
    print(f"    Min        : {stats['min_ms']:>10.3f} ms")
    print(f"    Max        : {stats['max_ms']:>10.3f} ms")


# ===========================================================================
# CONNESSIONI
# ===========================================================================


def get_neo4j_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


# ===========================================================================
# TEST 1.1 – QUERY MULTI-HOP
# ===========================================================================


def neo4j_multihop(session, person_id: int, depth: int):
    """Conta tutti i Person raggiungibili tramite KNOWS a 'depth' hop."""
    cypher = (
        f"MATCH (p:Person {{id: $pid}})-[:KNOWS*1..{depth}]-(friend:Person) "
        f"RETURN count(DISTINCT friend) AS cnt"
    )
    result = session.run(cypher, pid=person_id)
    return result.single()["cnt"]


def pg_multihop(conn, person_id: int, depth: int):
    """
    Equivalente SQL con CTE ricorsiva per navigare la rete KNOWS fino a 'depth' livelli.

    NOTA IMPORTANTE: la tabella `knows` è già bidirezionale (il loader LDBC
    inserisce sia (A,B) che (B,A) per ogni relazione). Pertanto è sufficiente
    navigare in una sola direzione (k_person1id → k_person2id) per raggiungere
    tutti i vicini, esattamente come fa Neo4j con il pattern non direzionato
    `-[:KNOWS]-`.
    """
    cur = conn.cursor()
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
    SELECT COUNT(DISTINCT person_id) AS cnt
    FROM friends
    WHERE person_id != %(pid)s
    """
    cur.execute(sql, {"pid": person_id, "depth": depth})
    return cur.fetchone()[0]


def run_multihop_test(neo4j_driver, pg_conn, person_ids: list[int]):
    banner("TEST 1.1 – Query Multi-Hop (profondità di navigazione crescente)")
    results = {}

    for depth in [1, 2, 3, 4]:
        sub_banner(f"Profondità {depth} hop")
        pid = random.choice(person_ids)
        print(f"  Person ID scelto: {pid}")

        # Warm-up
        with neo4j_driver.session() as s:
            for _ in range(N_WARMUP):
                neo4j_multihop(s, pid, depth)
        for _ in range(N_WARMUP):
            pg_multihop(pg_conn, pid, depth)

        # --- Neo4j ---
        neo4j_times = []
        with neo4j_driver.session() as s:
            for _ in range(N_RUNS):
                res, ms = measure_ms(neo4j_multihop, s, pid, depth)
                neo4j_times.append(ms)
        neo4j_cnt = res

        # --- PostgreSQL ---
        if depth == 4:
            print(
                f"  [*] Hop 4 – avvio CTE ricorsiva PostgreSQL (attesa variabile, "
                f"anche diversi minuti — dato fondamentale per il Join Pain)"
            )
        pg_times = []
        for _ in range(N_RUNS):
            res_pg, ms = measure_ms(pg_multihop, pg_conn, pid, depth)
            pg_times.append(ms)
        pg_cnt = res_pg

        neo4j_stats = compute_stats(neo4j_times)
        pg_stats = compute_stats(pg_times)
        speedup = (
            round(pg_stats["mean_ms"] / neo4j_stats["mean_ms"], 2)
            if neo4j_stats["mean_ms"] > 0
            else "N/A"
        )

        print(f"  Risultato Neo4j    : {neo4j_cnt} amici trovati")
        print(f"  Risultato PostgreSQL: {pg_cnt} amici trovati")
        print_stats("Neo4j", neo4j_stats)
        print_stats("PostgreSQL", pg_stats)
        print(f"  >>> Speedup Neo4j vs PostgreSQL: {speedup}x")

        results[f"hop_{depth}"] = {
            "person_id": pid,
            "neo4j_result": int(neo4j_cnt),
            "pg_result": int(pg_cnt),
            "neo4j": neo4j_stats,
            "postgresql": pg_stats,
            "speedup_neo4j_vs_pg": speedup,
        }

    return results


# ===========================================================================
# TEST 1.2 – PATTERN MATCHING E RICERCA DI CICLI (Triangle Detection)
# ===========================================================================


def neo4j_triangle_count(session):
    """Conta i triangoli (A-B-C-A) nel grafo delle amicizie KNOWS (non direzionato)."""
    cypher = """
    MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS]-(c:Person)-[:KNOWS]-(a)
    WHERE a.id < b.id AND b.id < c.id
    RETURN count(*) AS triangles
    """
    result = session.run(cypher)
    return result.single()["triangles"]


def neo4j_person_triangle_count(session, person_id: int):
    """Conta i triangoli che coinvolgono una specifica persona (non direzionato)."""
    cypher = """
    MATCH (p:Person {id: $pid})-[:KNOWS]-(b:Person)-[:KNOWS]-(c:Person)-[:KNOWS]-(p)
    WHERE b.id < c.id
    RETURN count(*) AS triangles
    """
    result = session.run(cypher, pid=person_id)
    return result.single()["triangles"]


def pg_triangle_count(conn):
    """Conta i triangoli nel grafo delle amicizie usando self-join triplo."""
    sql = """
    SELECT COUNT(*) AS triangles
    FROM knows k1
    JOIN knows k2 ON k1.k_person2id = k2.k_person1id
    JOIN knows k3 ON k2.k_person2id = k3.k_person1id
                  AND k3.k_person2id = k1.k_person1id
    WHERE k1.k_person1id < k1.k_person2id
      AND k2.k_person1id < k2.k_person2id
      AND k1.k_person1id < k2.k_person1id
    """
    cur = conn.cursor()
    cur.execute(sql)
    return cur.fetchone()[0]


def pg_person_triangle_count(conn, person_id: int):
    """Conta i triangoli che coinvolgono una persona specifica.

    La clausola k2.k_person1id < k3.k_person1id è l'equivalente SQL
    del `WHERE b.id < c.id` usato in Cypher per deduplicare i triangoli
    (evitando di contare sia (pid,b,c) che (pid,c,b)).
    """
    sql = """
    SELECT COUNT(*) AS triangles
    FROM knows k1
    JOIN knows k2 ON k1.k_person2id = k2.k_person1id
    JOIN knows k3 ON k2.k_person2id = k3.k_person1id
                  AND k3.k_person2id = k1.k_person1id
    WHERE k1.k_person1id = %(pid)s
      AND k2.k_person1id < k3.k_person1id
    """
    cur = conn.cursor()
    cur.execute(sql, {"pid": person_id})
    return cur.fetchone()[0]


def run_triangle_test(neo4j_driver, pg_conn, person_ids: list[int]):
    banner("TEST 1.2 – Pattern Matching: Ricerca di Cicli (Triangle Detection)")
    results = {}

    # --- Sub-test A: global triangle count ---
    sub_banner("1.2a – Conteggio globale triangoli (tutti i nodi)")

    # Warm-up
    with neo4j_driver.session() as s:
        for _ in range(N_WARMUP):
            neo4j_triangle_count(s)
    for _ in range(N_WARMUP):
        pg_triangle_count(pg_conn)

    neo4j_times = []
    with neo4j_driver.session() as s:
        for _ in range(N_RUNS):
            cnt, ms = measure_ms(neo4j_triangle_count, s)
            neo4j_times.append(ms)
    neo4j_global = cnt

    pg_times = []
    for _ in range(N_RUNS):
        cnt_pg, ms = measure_ms(pg_triangle_count, pg_conn)
        pg_times.append(ms)
    pg_global = cnt_pg

    neo4j_stats = compute_stats(neo4j_times)
    pg_stats = compute_stats(pg_times)
    speedup = (
        round(pg_stats["mean_ms"] / neo4j_stats["mean_ms"], 2)
        if neo4j_stats["mean_ms"] > 0
        else "N/A"
    )

    print(f"  Triangoli trovati (Neo4j)    : {neo4j_global}")
    print(f"  Triangoli trovati (PostgreSQL): {pg_global}")
    print_stats("Neo4j", neo4j_stats)
    print_stats("PostgreSQL", pg_stats)
    print(f"  >>> Speedup Neo4j vs PostgreSQL: {speedup}x")

    results["global_triangles"] = {
        "neo4j_result": int(neo4j_global),
        "pg_result": int(pg_global),
        "neo4j": neo4j_stats,
        "postgresql": pg_stats,
        "speedup_neo4j_vs_pg": speedup,
    }

    # --- Sub-test B: per-person triangle count ---
    sub_banner("1.2b – Triangoli per persona specifica (localizzato)")
    pid = random.choice(person_ids)
    print(f"  Person ID scelto: {pid}")

    # Warm-up
    with neo4j_driver.session() as s:
        for _ in range(N_WARMUP):
            neo4j_person_triangle_count(s, pid)
    for _ in range(N_WARMUP):
        pg_person_triangle_count(pg_conn, pid)

    neo4j_times = []
    with neo4j_driver.session() as s:
        for _ in range(N_RUNS):
            cnt, ms = measure_ms(neo4j_person_triangle_count, s, pid)
            neo4j_times.append(ms)
    neo4j_local = cnt

    pg_times = []
    for _ in range(N_RUNS):
        cnt_pg, ms = measure_ms(pg_person_triangle_count, pg_conn, pid)
        pg_times.append(ms)
    pg_local = cnt_pg

    neo4j_stats = compute_stats(neo4j_times)
    pg_stats = compute_stats(pg_times)
    speedup = (
        round(pg_stats["mean_ms"] / neo4j_stats["mean_ms"], 2)
        if neo4j_stats["mean_ms"] > 0
        else "N/A"
    )

    print(f"  Triangoli persona (Neo4j)    : {neo4j_local}")
    print(f"  Triangoli persona (PostgreSQL): {pg_local}")
    print_stats("Neo4j", neo4j_stats)
    print_stats("PostgreSQL", pg_stats)
    print(f"  >>> Speedup Neo4j vs PostgreSQL: {speedup}x")

    results["per_person_triangles"] = {
        "person_id": pid,
        "neo4j_result": int(neo4j_local),
        "pg_result": int(pg_local),
        "neo4j": neo4j_stats,
        "postgresql": pg_stats,
        "speedup_neo4j_vs_pg": speedup,
    }

    return results


# ===========================================================================
# TEST 1.3 – PATHFINDING (CAMMINI MINIMI)
# ===========================================================================


def neo4j_shortest_path(session, src_id: int, dst_id: int):
    """Cammino minimo tra due persone via KNOWS (BFS nativo Neo4j)."""
    cypher = """
    MATCH (src:Person {id: $src}), (dst:Person {id: $dst})
    MATCH path = shortestPath((src)-[:KNOWS*..6]-(dst))
    RETURN length(path) AS hops
    """
    result = session.run(cypher, src=src_id, dst=dst_id)
    record = result.single()
    return record["hops"] if record else -1




def pg_sql_shortest_path(conn, src_id: int, dst_id: int, max_depth: int = 6) -> int:
    """
    Cammino minimo calcolato interamente dal motore SQL usando una CTE ricorsiva.
    """
    if src_id == dst_id:
        return 0

    sql = """
    WITH RECURSIVE search_graph(node, depth) AS (
        -- Nodo di partenza
        SELECT %(src)s::bigint, 0
        
        UNION
        
        -- Navigazione BFS
        SELECT 
            k.k_person2id, 
            sg.depth + 1
        FROM knows k
        JOIN search_graph sg ON k.k_person1id = sg.node
        WHERE sg.depth < %(max_depth)s
    )
    SELECT min(depth) 
    FROM search_graph 
    WHERE node = %(dst)s;
    """
    cur = conn.cursor()
    cur.execute(sql, {"src": src_id, "dst": dst_id, "max_depth": max_depth})
    result = cur.fetchone()[0]

    return int(result) if result is not None else -1


def pick_distant_pair(neo4j_driver, person_ids: list[int], min_hops: int = 2) -> tuple:
    """Seleziona casualmente una coppia di persone con distanza >= min_hops in Neo4j."""
    random.shuffle(person_ids)
    with neo4j_driver.session() as s:
        for i in range(min(30, len(person_ids))):
            for j in range(i + 1, min(30, len(person_ids))):
                src, dst = person_ids[i], person_ids[j]
                h = neo4j_shortest_path(s, src, dst)
                if h >= min_hops:
                    return src, dst, h
    return person_ids[0], person_ids[-1], -1


def run_shortest_path_test(neo4j_driver, pg_conn, person_ids: list[int]):
    banner("TEST 1.3 – Pathfinding (Cammini Minimi / Shortest Path)")
    results = {}

    print("  Ricerca di coppie con distanza crescente...")
    pairs = []
    for min_h in [1, 2, 3]:
        src, dst, actual_h = pick_distant_pair(
            neo4j_driver, list(person_ids), min_hops=min_h
        )
        pairs.append((src, dst, actual_h, f"dist_{min_h}hop"))
        print(
            f"  Coppia distanza ~{min_h}: Person {src} → Person {dst}  (reale: {actual_h} hop)"
        )

    for src, dst, actual_h, label in pairs:
        sub_banner(f"Cammino minimo Person {src} → Person {dst}  [{actual_h} hop]")

        # Warm-up
        with neo4j_driver.session() as s:
            for _ in range(N_WARMUP):
                neo4j_shortest_path(s, src, dst)
        for _ in range(N_WARMUP):
            pg_sql_shortest_path(pg_conn, src, dst)

        # --- Neo4j ---
        neo4j_times = []
        with neo4j_driver.session() as s:
            for _ in range(N_RUNS):
                h_neo, ms = measure_ms(neo4j_shortest_path, s, src, dst)
                neo4j_times.append(ms)

        # --- PostgreSQL ---
        pg_times = []
        for _ in range(N_RUNS):
            h_pg, ms = measure_ms(pg_sql_shortest_path, pg_conn, src, dst)
            pg_times.append(ms)

        neo4j_stats = compute_stats(neo4j_times)
        pg_stats = compute_stats(pg_times)
        speedup = (
            round(pg_stats["mean_ms"] / neo4j_stats["mean_ms"], 2)
            if neo4j_stats["mean_ms"] > 0
            else "N/A"
        )

        print(f"  Distanza Neo4j    : {h_neo} hop")
        print(f"  Distanza PostgreSQL: {h_pg} hop")
        print_stats("Neo4j", neo4j_stats)
        print_stats("PostgreSQL", pg_stats)
        print(f"  >>> Speedup Neo4j vs PostgreSQL: {speedup}x")

        results[label] = {
            "src": src,
            "dst": dst,
            "actual_hops": actual_h,
            "neo4j_result_hops": int(h_neo) if h_neo != -1 else None,
            "pg_result_hops": int(h_pg) if h_pg != -1 else None,
            "neo4j": neo4j_stats,
            "postgresql": pg_stats,
            "speedup_neo4j_vs_pg": speedup,
        }

    return results


# ===========================================================================
# MAIN
# ===========================================================================


def main():
    print(f"\n{'#' * 70}")
    print(f"#  SCENARIO 1: La Potenza del Grafo – Neo4j vs PostgreSQL")
    print(f"#  Data/ora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"#  Configurazione: SF 0.1 | N_RUNS={N_RUNS} | WARMUP={N_WARMUP}")
    print(f"{'#' * 70}")

    # Connessioni
    print("\n[*] Connessione ai database...")
    try:
        neo4j_driver = get_neo4j_driver()
        neo4j_driver.verify_connectivity()
        print("  [OK] Neo4j connesso")
    except Exception as e:
        print(f"  [ERR] Neo4j: {e}")
        sys.exit(1)

    try:
        pg_conn = get_pg_conn()
        pg_conn.autocommit = True
        print("  [OK] PostgreSQL connesso")
    except Exception as e:
        print(f"  [ERR] PostgreSQL: {e}")
        sys.exit(1)

    # Lista person_id (Neo4j)
    with neo4j_driver.session() as s:
        res = s.run("MATCH (p:Person) RETURN p.id AS id ORDER BY p.id")
        person_ids = [r["id"] for r in res]
    print(f"\n[*] {len(person_ids)} Person trovati nel grafo (SF 0.1)")

    # Seed per riproducibilità
    random.seed(42)

    all_results = {}

    # ---- TEST 1.1 ----
    all_results["test_1_1_multihop"] = run_multihop_test(
        neo4j_driver, pg_conn, person_ids
    )

    # ---- TEST 1.2 ----
    all_results["test_1_2_triangle"] = run_triangle_test(
        neo4j_driver, pg_conn, person_ids
    )

    # ---- TEST 1.3 ----
    all_results["test_1_3_shortest_path"] = run_shortest_path_test(
        neo4j_driver, pg_conn, person_ids
    )

    # ---- RIEPILOGO FINALE ----
    banner("RIEPILOGO FINALE – Scenario 1")
    print(
        f"\n{'Metrica':<35} {'Neo4j (ms)':>12} {'PostgreSQL (ms)':>16} {'Speedup':>10}"
    )
    print("-" * 78)

    rows = [
        ("Multi-Hop 1 hop (media)", all_results["test_1_1_multihop"]["hop_1"]),
        ("Multi-Hop 2 hop (media)", all_results["test_1_1_multihop"]["hop_2"]),
        ("Multi-Hop 3 hop (media)", all_results["test_1_1_multihop"]["hop_3"]),
        ("Multi-Hop 4 hop (media)", all_results["test_1_1_multihop"]["hop_4"]),
        (
            "Triangoli globali (media)",
            all_results["test_1_2_triangle"]["global_triangles"],
        ),
        (
            "Triangoli per persona",
            all_results["test_1_2_triangle"]["per_person_triangles"],
        ),
    ]

    sp_keys = [k for k in all_results["test_1_3_shortest_path"]]
    for k in sp_keys:
        h = all_results["test_1_3_shortest_path"][k].get("actual_hops", "?")
        rows.append(
            (f"Shortest path {h} hop (media)", all_results["test_1_3_shortest_path"][k])
        )

    for label, data in rows:
        n4 = data["neo4j"]["mean_ms"]
        pg = data["postgresql"]["mean_ms"]
        sp = data.get("speedup_neo4j_vs_pg", "N/A")
        print(f"{label:<35} {n4:>12.3f} {pg:>16.3f} {str(sp):>10}")

    # Salvataggio JSON nella cartella dedicata allo scenario (stessa dello script)
    import os

    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "results.json")
    all_results["metadata"] = {
        "timestamp": datetime.now().isoformat(),
        "scale_factor": "0.1",
        "n_runs": N_RUNS,
        "n_warmup": N_WARMUP,
        "n_persons": len(person_ids),
        "neo4j_version": "5.20.0-community",
        "postgres_version": "14.4",
    }
    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        print(f"\n[*] Risultati salvati in: {output_path}")
    except Exception as e:
        print(f"\n[WARN] Impossibile salvare JSON: {e}")

    neo4j_driver.close()
    pg_conn.close()
    print("\n[*] Connessioni chiuse. Benchmark completato.\n")


if __name__ == "__main__":
    main()
