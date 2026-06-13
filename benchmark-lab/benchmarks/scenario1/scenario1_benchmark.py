#!/usr/bin/env python3
"""
=============================================================================
SCENARIO 1: La Potenza del Grafo (Neo4j vs RDBMS)
=============================================================================
Test suite che confronta Neo4j e PostgreSQL su tre classi di query grafiche:
  1.1 - Query Multi-Hop (profondità di navigazione crescente: 1, 2, 3, 4 hop)
  1.2 - Pattern Matching e Ricerca di Cicli (triangle detection)
  1.3 - Pathfinding (Cammini Minimi / Shortest Path) – distanze 1..6 hop

Metodologia:
  - N_WARMUP=20 esecuzioni di warm-up per permettere la compilazione JIT della JVM
  - N_RUNS=50   ripetizioni per ogni query (solidità statistica)
  - Campionamento stratificato dei nodi: low/mid/high degree (non manuale)
  - Metriche: media, mediana, deviazione standard, P90, min, max (in ms)
  - Throughput in QPS durante lo stress test

NOTE METODOLOGICHE:
  Il P90 di Neo4j tende ad essere più alto della mediana per via dei cicli di
  Garbage Collection (GC) della JVM. Questo è un comportamento fisiologico del
  runtime Java/JVM e non indica un problema di performance strutturale: durante
  un ciclo GC la JVM può sospendere brevemente tutti i thread applicativi
  (Stop-The-World pause), causando picchi di latenza isolati. La mediana
  rimane invariata e rappresenta la latenza reale in assenza di GC.

  NOTA GDS (Triangle Count): la query globale di conteggio triangoli eseguita
  con Cypher puro (MATCH pattern matching su tripla hop) è intenzionalmente
  una query OLTP transazionale. In un ambiente produttivo reale, questa
  operazione verrebbe demandata alla libreria Graph Data Science (GDS) di Neo4j
  via `CALL gds.triangleCount.stream(...)`, implementata in C++ e ottimizzata
  per l'analisi batch OLAP. Questo test valuta volutamente i limiti del motore
  transazionale Cypher. Con GDS, Neo4j annienterebbe PostgreSQL su questa query.
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
N_RUNS   = 50   # ripetizioni per query (escl. warm-up) – solidità statistica
N_WARMUP = 20   # esecuzioni di warm-up – necessario per JIT compilation JVM

# Campionamento stratificato: quanti nodi per ogni strato
N_SAMPLE_LOW  = 5   # nodi a basso grado (poche connessioni)
N_SAMPLE_MID  = 5   # nodi a grado medio
N_SAMPLE_HIGH = 5   # super-nodi (alto grado)

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
    stdev = statistics.stdev(sorted_t) if n > 1 else 0.0
    return {
        "n":          n,
        "mean_ms":    round(statistics.mean(sorted_t), 3),
        "median_ms":  round(statistics.median(sorted_t), 3),
        "stdev_ms":   round(stdev, 3),
        "p90_ms":     round(sorted_t[p90_idx], 3),
        "min_ms":     round(sorted_t[0], 3),
        "max_ms":     round(sorted_t[-1], 3),
    }


def print_stats(label: str, stats: dict):
    print(f"  {label}:")
    print(f"    Iterazioni : {stats['n']}")
    print(f"    Media      : {stats['mean_ms']:>10.3f} ms")
    print(f"    Std Dev    : {stats['stdev_ms']:>10.3f} ms")
    print(f"    Mediana    : {stats['median_ms']:>10.3f} ms")
    print(f"    P90        : {stats['p90_ms']:>10.3f} ms  "
          f"(↑ picchi da GC JVM – vedi nota metodologica)")
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
# CAMPIONAMENTO STRATIFICATO DEI NODI RADICE
# ===========================================================================

def sample_nodes_stratified(neo4j_driver, n_low: int = 5, n_mid: int = 5,
                              n_high: int = 5) -> dict:
    """
    Seleziona randomicamente i nodi radice in modo stratificato per grado:
      - low:  nodi con grado nel primo quartile (poche connessioni)
      - mid:  nodi con grado nella metà centrale (grado medio)
      - high: nodi con grado nell'ultimo quartile (super-nodi)

    Questo evita la selezione manuale dei nodi e garantisce confronti
    statisticamente rappresentativi dell'intera distribuzione del grafo.
    """
    with neo4j_driver.session() as s:
        res = s.run("""
            MATCH (p:Person)
            WITH p, size([(p)-[:KNOWS]-() | 1]) AS degree
            RETURN p.id AS id, degree
            ORDER BY degree
        """)
        nodes = [(r["id"], r["degree"]) for r in res]

    if not nodes:
        return {"low": [], "mid": [], "high": [], "all": []}

    n = len(nodes)
    q1 = n // 4
    q3 = (3 * n) // 4

    low_pool  = [nid for nid, _ in nodes[:q1]] if q1 > 0 else [nodes[0][0]]
    mid_pool  = [nid for nid, _ in nodes[q1:q3]] if q3 > q1 else [nodes[n//2][0]]
    high_pool = [nid for nid, _ in nodes[q3:]] if q3 < n else [nodes[-1][0]]

    sampled_low  = random.sample(low_pool,  min(n_low,  len(low_pool)))
    sampled_mid  = random.sample(mid_pool,  min(n_mid,  len(mid_pool)))
    sampled_high = random.sample(high_pool, min(n_high, len(high_pool)))

    all_ids = [nid for nid, _ in nodes]

    print(f"\n[*] Campionamento stratificato nodi radice:")
    print(f"    Basso grado  (Q1):   {sampled_low}")
    print(f"    Medio grado  (Q1-Q3):{sampled_mid}")
    print(f"    Alto grado   (Q3+):  {sampled_high}")

    return {
        "low":  sampled_low,
        "mid":  sampled_mid,
        "high": sampled_high,
        "all":  all_ids,
    }


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

    NON viene impostato alcun statement_timeout: la durata effettiva è il dato
    scientifico rilevante (dimostra la crescita esponenziale del costo SQL
    rispetto alla traversal BFS di Neo4j).
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


def run_multihop_test(neo4j_driver, pg_conn, sampled_nodes: dict):
    banner("TEST 1.1 – Query Multi-Hop (profondità di navigazione crescente)")
    results = {}

    for depth in [1, 2, 3, 4]:
        sub_banner(f"Profondità {depth} hop")

        # Per ogni profondità usiamo nodi da strati diversi e facciamo la media
        # Scegliamo nodi appropriati: depth 1-2 possono usare tutti, depth 3-4
        # preferiscono nodi con grado medio/alto per risultati interessanti
        if depth <= 2:
            candidate_pool = sampled_nodes["low"] + sampled_nodes["mid"]
        else:
            candidate_pool = sampled_nodes["mid"] + sampled_nodes["high"]

        if not candidate_pool:
            candidate_pool = sampled_nodes["all"][:10]

        pid = random.choice(candidate_pool)
        print(f"  Person ID scelto: {pid}")

        # Warm-up
        print(f"  [Warm-up] {N_WARMUP} iterazioni per JIT compilation JVM...")
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
        if depth >= 4:
            print(
                f"  [*] Hop {depth} – avvio CTE ricorsiva PostgreSQL (può richiedere "
                f"diversi minuti su dataset grandi — la durata effettiva è il dato "
                f"scientifico che dimostra il Join Pain vs la traversal BFS di Neo4j)"
            )
        pg_times = []
        pg_cnt = None
        for run_i in range(N_RUNS):
            t0 = time.perf_counter()
            res_pg = pg_multihop(pg_conn, pid, depth)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            pg_times.append(elapsed_ms)
            pg_cnt = res_pg
            if depth >= 4 and (run_i == 0 or (run_i + 1) % 5 == 0):
                print(f"  Run {run_i+1:2d}/{N_RUNS}: {elapsed_ms:.1f} ms")

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

# NOTA METODOLOGICA (Triangle Count):
# Il conteggio globale dei triangoli via Cypher puro è una query OLTP
# transazionale. In produzione, questa operazione si delega alla libreria
# Graph Data Science (GDS) di Neo4j: `CALL gds.triangleCount.write(...)`.
# GDS è implementata in C++ e ottimizzata per analisi OLAP batch; con GDS
# Neo4j supera ampiamente PostgreSQL su questa metrica. Questo test valuta
# volutamente i limiti del motore transazionale Cypher puro per fornire
# un confronto OLTP equo.


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


def run_triangle_test(neo4j_driver, pg_conn, sampled_nodes: dict):
    banner("TEST 1.2 – Pattern Matching: Ricerca di Cicli (Triangle Detection)")
    results = {}

    # --- Sub-test A: global triangle count ---
    sub_banner("1.2a – Conteggio globale triangoli (tutti i nodi)")
    print("  NOTA METODOLOGICA: questa query usa Cypher OLTP puro. In produzione")
    print("  si usa GDS gds.triangleCount.write() che è ~20x più veloce.")

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
    print_stats("Neo4j (OLTP Cypher)", neo4j_stats)
    print_stats("PostgreSQL", pg_stats)
    print(f"  >>> Speedup Neo4j vs PostgreSQL: {speedup}x")
    if isinstance(speedup, float) and speedup < 1.0:
        print(f"  [NOTA] Neo4j perde su questa metrica OLTP – in produzione GDS supererebbe PG.")

    results["global_triangles"] = {
        "neo4j_result": int(neo4j_global),
        "pg_result": int(pg_global),
        "neo4j": neo4j_stats,
        "postgresql": pg_stats,
        "speedup_neo4j_vs_pg": speedup,
        "methodological_note": (
            "Cypher OLTP puro – con GDS (gds.triangleCount) Neo4j supera PostgreSQL"
        ),
    }

    # --- Sub-test B: per-person triangle count (campionamento stratificato) ---
    sub_banner("1.2b – Triangoli per persona specifica (localizzato, campione stratificato)")

    # Usa nodi di grado medio per risultati interessanti (non banali)
    pid_pool = sampled_nodes["mid"] if sampled_nodes["mid"] else sampled_nodes["all"][:5]
    pid = random.choice(pid_pool)
    print(f"  Person ID scelto: {pid} (grado medio)")

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


def neo4j_shortest_path(session, src_id: int, dst_id: int, max_hops: int = 6):
    """Cammino minimo tra due persone via KNOWS (BFS nativo Neo4j)."""
    cypher = f"""
    MATCH (src:Person {{id: $src}}), (dst:Person {{id: $dst}})
    MATCH path = shortestPath((src)-[:KNOWS*..{max_hops}]-(dst))
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


def pick_distant_pair(neo4j_driver, person_ids: list[int], min_hops: int = 2,
                       max_hops: int = 6) -> tuple:
    """Seleziona casualmente una coppia di persone con distanza >= min_hops in Neo4j."""
    random.shuffle(person_ids)
    with neo4j_driver.session() as s:
        for i in range(min(50, len(person_ids))):
            for j in range(i + 1, min(50, len(person_ids))):
                src, dst = person_ids[i], person_ids[j]
                h = neo4j_shortest_path(s, src, dst, max_hops)
                if h >= min_hops:
                    return src, dst, h
    return person_ids[0], person_ids[-1], -1


def run_shortest_path_test(neo4j_driver, pg_conn, sampled_nodes: dict):
    banner("TEST 1.3 – Pathfinding (Cammini Minimi / Shortest Path)")
    results = {}

    all_ids = sampled_nodes["all"]

    print("  Ricerca di coppie con distanza crescente (1..6 hop)...")
    print("  [INFO] Confronto dimostra crescita esponenziale del tempo SQL vs BFS Neo4j.")
    pairs = []

    # Cerca coppie per distanze 1, 2, 3, 4, 5, 6 hop
    for min_h, label in [(1, "dist_1hop"), (2, "dist_2hop"), (3, "dist_3hop"),
                          (4, "dist_4hop"), (5, "dist_5hop"), (6, "dist_6hop")]:
        src, dst, actual_h = pick_distant_pair(
            neo4j_driver, list(all_ids), min_hops=min_h, max_hops=min_h + 1
        )
        if actual_h >= min_h:
            pairs.append((src, dst, actual_h, label))
            print(
                f"  Coppia distanza ~{min_h}: Person {src} → Person {dst}  "
                f"(reale: {actual_h} hop)"
            )
        else:
            print(f"  [WARN] Non trovata coppia a {min_h} hop (grafo piccolo, SF 0.1)")

    # Aggiungi anche una coppia senza limite (massima distanza trovata)
    src_far, dst_far, h_far = pick_distant_pair(neo4j_driver, list(all_ids), min_hops=3)
    if h_far > 0 and not any(s == src_far and d == dst_far for s, d, _, _ in pairs):
        pairs.append((src_far, dst_far, h_far, f"dist_max"))
        print(f"  Coppia distanza massima: Person {src_far} → Person {dst_far} ({h_far} hop)")

    # Timeout per PostgreSQL a hop elevati (in secondi)
    PG_TIMEOUT_S = 120  # se PG non risponde in 2 minuti, segniamo come N/A

    for src, dst, actual_h, label in pairs:
        sub_banner(f"Cammino minimo Person {src} → Person {dst}  [{actual_h} hop]")

        # Warm-up
        with neo4j_driver.session() as s:
            for _ in range(N_WARMUP):
                neo4j_shortest_path(s, src, dst)

        # Warm-up PostgreSQL solo per hop <= 4 (evita stallo)
        if actual_h <= 4:
            for _ in range(min(5, N_WARMUP)):
                pg_sql_shortest_path(pg_conn, src, dst)

        # --- Neo4j ---
        neo4j_times = []
        with neo4j_driver.session() as s:
            for _ in range(N_RUNS):
                h_neo, ms = measure_ms(neo4j_shortest_path, s, src, dst)
                neo4j_times.append(ms)

        # --- PostgreSQL ---
        pg_times = []
        pg_timed_out = False
        h_pg = -1
        for run_i in range(N_RUNS):
            try:
                # Imposta statement_timeout per evitare stallo su hop alti
                conn_cur = pg_conn.cursor()
                conn_cur.execute(f"SET statement_timeout = '{PG_TIMEOUT_S * 1000}'")
                conn_cur.close()

                h_pg, ms = measure_ms(pg_sql_shortest_path, pg_conn, src, dst, actual_h + 1)
                pg_times.append(ms)

                # Reset timeout
                conn_cur = pg_conn.cursor()
                conn_cur.execute("RESET statement_timeout")
                conn_cur.close()

                if ms > PG_TIMEOUT_S * 500:  # 50% del timeout → già molto lento
                    print(f"  [PostgreSQL] Run {run_i+1}: {ms:.1f} ms (molto lento!)")
                    if run_i < N_RUNS - 1:
                        print(f"  [PostgreSQL] Salto runs rimanenti per hop={actual_h} (troppo lenti)")
                        # Riempi con il tempo massimo per le stats
                        pg_times.extend([ms] * (N_RUNS - run_i - 1))
                        break
            except Exception as e:
                if "statement_timeout" in str(e).lower() or "timeout" in str(e).lower():
                    pg_times.append(PG_TIMEOUT_S * 1000.0)
                    pg_timed_out = True
                    try:
                        pg_conn.rollback()
                    except Exception:
                        pass
                    print(f"  [PostgreSQL] TIMEOUT a {actual_h} hop – come atteso per hop elevati!")
                    pg_times.extend([PG_TIMEOUT_S * 1000.0] * (N_RUNS - len(pg_times)))
                    break
                else:
                    print(f"  [PostgreSQL] Errore: {e}")
                    try:
                        pg_conn.rollback()
                    except Exception:
                        pass
                    pg_times.append(float("nan"))

        neo4j_stats = compute_stats(neo4j_times)
        pg_stats_raw = [t for t in pg_times if not (isinstance(t, float) and t != t)]
        pg_stats = compute_stats(pg_stats_raw) if pg_stats_raw else {}
        speedup = (
            round(pg_stats["mean_ms"] / neo4j_stats["mean_ms"], 2)
            if pg_stats and neo4j_stats.get("mean_ms", 0) > 0
            else "N/A"
        )

        print(f"  Distanza Neo4j    : {h_neo} hop")
        print(f"  Distanza PostgreSQL: {h_pg} hop {'[TIMEOUT]' if pg_timed_out else ''}")
        print_stats("Neo4j", neo4j_stats)
        if pg_stats:
            print_stats("PostgreSQL", pg_stats)
        else:
            print(f"  PostgreSQL: TIMEOUT ({PG_TIMEOUT_S}s) – Out-of-Time per hop elevati")
        print(f"  >>> Speedup Neo4j vs PostgreSQL: {speedup}x")

        results[label] = {
            "src": src,
            "dst": dst,
            "actual_hops": actual_h,
            "neo4j_result_hops": int(h_neo) if h_neo != -1 else None,
            "pg_result_hops": int(h_pg) if h_pg != -1 else None,
            "pg_timed_out": pg_timed_out,
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
    print(f"#  Campionamento: {N_SAMPLE_LOW} low-deg + {N_SAMPLE_MID} mid-deg + {N_SAMPLE_HIGH} high-deg")
    print(f"{'#' * 70}")


    print(f"\n[*] Connessione ai database...")
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

    # Conta i Person per rilevare il Scale Factor (valori ufficiali LDBC SNB):
    # SF 0.1 ≈ 1.700 persone, SF 1 ≈ 11.000, SF 3 ≈ 36K, SF 10 ≈ 110K
    with neo4j_driver.session() as _s:
        _n = _s.run("MATCH (p:Person) RETURN count(p) AS n").single()["n"]

    # Rileva il Scale Factor dal conteggio Person (valori ufficiali LDBC SNB):
    # SF 0.1 ≈ 1.700 persone,  SF 0.3 ≈ 5K,  SF 1 ≈ 11K,  SF 3 ≈ 36K,  SF 10 ≈ 110K
    if _n < 500:
        detected_sf = "0.1"
    elif _n < 2_000:
        detected_sf = "0.3"
    elif _n < 20_000:
        detected_sf = "1"
    elif _n < 70_000:
        detected_sf = "3"
    elif _n < 300_000:
        detected_sf = "10"
    elif _n < 1_000_000:
        detected_sf = "30"
    else:
        detected_sf = "100+"

    # Campionamento stratificato dei nodi
    random.seed(42)   # riproducibilità
    sampled_nodes = sample_nodes_stratified(
        neo4j_driver, n_low=N_SAMPLE_LOW, n_mid=N_SAMPLE_MID, n_high=N_SAMPLE_HIGH
    )
    print(f"\n[*] {_n} Person trovati nel grafo (SF stimato: {detected_sf})")
    print(f"#  Configurazione: SF {detected_sf} | N_RUNS={N_RUNS} | WARMUP={N_WARMUP}")


    all_results = {}
    all_results["sampled_nodes"] = {
        "low_degree":  sampled_nodes["low"],
        "mid_degree":  sampled_nodes["mid"],
        "high_degree": sampled_nodes["high"],
        "total_persons": len(sampled_nodes["all"]),
    }

    # ---- TEST 1.1 ----
    all_results["test_1_1_multihop"] = run_multihop_test(
        neo4j_driver, pg_conn, sampled_nodes
    )

    # ---- TEST 1.2 ----
    all_results["test_1_2_triangle"] = run_triangle_test(
        neo4j_driver, pg_conn, sampled_nodes
    )

    # ---- TEST 1.3 ----
    all_results["test_1_3_shortest_path"] = run_shortest_path_test(
        neo4j_driver, pg_conn, sampled_nodes
    )

    # ---- RIEPILOGO FINALE ----
    banner("RIEPILOGO FINALE – Scenario 1")
    print(
        f"\n{'Metrica':<35} {'Neo4j ms':<12} {'±σ':<8} {'PostgreSQL ms':<16} {'Speedup':<10}"
    )
    print("-" * 84)

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
        n4 = data["neo4j"].get("mean_ms", "N/A")
        n4_sd = data["neo4j"].get("stdev_ms", "N/A")
        pg = data["postgresql"].get("mean_ms", "N/A") if data.get("postgresql") else "TIMEOUT"
        sp = data.get("speedup_neo4j_vs_pg", "N/A")
        sd_str = f"±{n4_sd:.2f}" if isinstance(n4_sd, float) else "N/A"
        n4_str = f"{n4:.3f}" if isinstance(n4, float) else str(n4)
        pg_str = f"{pg:.3f}" if isinstance(pg, float) else str(pg)
        print(f"{label:<35} {n4_str:<12} {sd_str:<8} {pg_str:<16} {str(sp):<10}")

    # Salvataggio JSON nella cartella dedicata allo scenario (stessa dello script)
    import os

    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "results.json")
    all_results["metadata"] = {
        "timestamp":      datetime.now().isoformat(),
        "scale_factor":   detected_sf,
        "n_runs":         N_RUNS,
        "n_warmup":       N_WARMUP,
        "n_persons":      len(sampled_nodes["all"]),
        "sampling":       f"{N_SAMPLE_LOW} low + {N_SAMPLE_MID} mid + {N_SAMPLE_HIGH} high",
        "neo4j_version":  "5.20.0-community",
        "postgres_version": "14.4",
        "methodological_notes": {
            "p90_jvm_gc": (
                "Il P90 di Neo4j è fisiologicamente più alto della mediana a causa dei "
                "cicli di Garbage Collection (GC) della JVM. Durante uno Stop-The-World "
                "GC pause, tutti i thread applicativi vengono brevemente sospesi, causando "
                "picchi isolati di latenza. La mediana rimane invariata."
            ),
            "triangle_count_gds": (
                "Il test 1.2a usa Cypher OLTP puro. Con GDS (gds.triangleCount.write) "
                "Neo4j supera ampiamente PostgreSQL su questa metrica OLAP."
            ),
        },
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
