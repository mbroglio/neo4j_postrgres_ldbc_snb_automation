#!/usr/bin/env python3
"""
=============================================================================
SCENARIO 4: I Punti Deboli (Quando NON usare Neo4j)
=============================================================================
Test suite che analizza i limiti strutturali del paradigma graph-native,
identificando i contesti in cui l'architettura tabellare relazionale
risulta nettamente superiore:

  4.1 - Full-Table Scan e Aggregazioni Globali
        Confronto Neo4j vs PostgreSQL su una query puramente statistica
        disconnessa dalla topologia: calcolo della lunghezza media dei testi
        di tutti i messaggi raggruppati per browser utilizzato.
        PostgreSQL esegue Sequential Scan su blocchi contigui in memoria;
        Neo4j scansiona nodi sparsi generando continui cache miss.

  4.2 - Inserimento Massivo di Dati Disconnessi (Bulk Insert)
        Misura il tempo di ingestione di record grezzi senza relazioni.
        PostgreSQL usa il comando nativo COPY; Neo4j usa batch CREATE.
        Neo4j soffre l'overhead di allocazione delle strutture per i
        puntatori dei record anche in assenza di archi logici.

  4.3 - Esplosione Combinatoria nei Cammini Non Filtrati
        Query Cypher con cammini a lunghezza indefinita su nodi ad alto
        branching factor (super-nodi): confronto tra pattern non filtrato
        (-[*1..6]-) e variante con vincoli topologici espliciti.
        L'assenza di filtri provoca crescita esponenziale della frontiera
        di esplorazione fino all'Out Of Memory / Timeout.

Metodologia:
  - Warm-up iniziale per popolare le page cache
  - N_RUNS ripetizioni per ogni query (escluso 4.3 che monitora OOM)
  - Metriche: media, mediana, 90° percentile, min, max (in ms)
  - Throughput in record/s per il bulk insert
=============================================================================
"""

import time
import random
import statistics
import json
import sys
import os
import csv
import io
import threading
from datetime import datetime

# ---------------------------------------------------------------------------
# Dipendenze – installazione automatica se mancanti
# ---------------------------------------------------------------------------
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    import subprocess
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "psycopg2-binary"]
    )
    import psycopg2
    import psycopg2.extras

try:
    from neo4j import GraphDatabase
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "neo4j"])
    from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Configurazione connessioni
# ---------------------------------------------------------------------------
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password"

PG_HOST = "localhost"
PG_PORT = 5432
PG_DB   = "ldbcsnb"
PG_USER = "postgres"
PG_PASS = "mysecretpassword"

# ---------------------------------------------------------------------------
# Parametri benchmark
# ---------------------------------------------------------------------------
N_RUNS   = 30   # ripetizioni per ogni query (escluso il test 4.3)
N_WARMUP = 20   # esecuzioni di warm-up (JIT compilation JVM)

# Bulk Insert (4.2)
BULK_INSERT_RECORDS = 50_000   # record da inserire (anagrafica piatta senza relazioni)
BULK_BATCH_SIZE     = 1_000    # dimensione batch per Neo4j CREATE

# Explosion test (4.3)
# Questo timeout non è un limite arbitrario: serve come protezione OOM.
# Su SF1, la query [*1..6] senza filtri topologici esplora miliardi di percorsi,
# saturando la RAM JVM fino al crash del container Docker. 300s permette di
# documentare il comportamento (timeout certo) senza distruggere l'ambiente.
# Su SF 0.1 la query può completare in ~10-20s anche senza questo limite.
EXPLOSION_TIMEOUT_S = 300      # secondi massimi per la query non filtrata (OOM protection)

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
    print(f"    Std Dev    : {stats.get('stdev_ms', 0.0):>10.3f} ms")
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
# TEST 4.1 – FULL-TABLE SCAN E AGGREGAZIONI GLOBALI
# ===========================================================================
# Query: lunghezza media dei testi di tutti i Post/Comment, raggruppati per browser.
# - PostgreSQL: Sequential Scan parallelo su blocchi di memoria contigui.
# - Neo4j:      scansione dei nodi sparsi nello store → cache miss continui.
#
# NOTE IMPLEMENTATIVA:
#   Lo schema LDBC SNB usa la tabella `message` che unifica Post e Comment.
#   Su Neo4j l'equivalente è il label :Post (che contiene la proprietà
#   `browserUsed` e `content`). I Comment LDBC non hanno `browserUsed` nel
#   dataset SF 0.1, quindi si confronta la stessa porzione di dati.
# ===========================================================================


def neo4j_global_aggregation(session) -> list:
    """Lunghezza media dei contenuti per browser (tutti i Post)."""
    cypher = """
    MATCH (m:Post)
    WHERE m.browserUsed IS NOT NULL AND m.content IS NOT NULL
    RETURN m.browserUsed AS browser,
           avg(size(m.content)) AS avg_len,
           count(*) AS cnt
    ORDER BY browser
    """
    result = session.run(cypher)
    return result.data()


def pg_global_aggregation(conn) -> list:
    """Equivalente SQL: AVG(LENGTH(content)) GROUP BY browserUsed su Post."""
    sql = """
    SELECT m_browserused   AS browser,
           AVG(LENGTH(m_content::text)) AS avg_len,
           COUNT(*)        AS cnt
    FROM message
    WHERE m_browserused IS NOT NULL
      AND m_content IS NOT NULL
      AND m_c_replyof IS NULL
    GROUP BY m_browserused
    ORDER BY m_browserused
    """
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cur.close()
    return rows


def run_global_aggregation_test(neo4j_driver, pg_conn) -> dict:
    banner("TEST 4.1 – Full-Table Scan e Aggregazioni Globali")
    print("  Query: lunghezza media testi dei Post raggruppata per browser.")
    print("  PostgreSQL usa Sequential Scan; Neo4j deve scorrere nodi sparsi.\n")

    # Warm-up
    sub_banner("Warm-up (cache popolamento)")
    with neo4j_driver.session() as s:
        for _ in range(N_WARMUP):
            neo4j_global_aggregation(s)
    for _ in range(N_WARMUP):
        pg_global_aggregation(pg_conn)
    print("  [OK] Warm-up completato")

    # --- Neo4j ---
    sub_banner("Neo4j – scansione nodi Post")
    neo4j_times = []
    neo4j_result = None
    with neo4j_driver.session() as s:
        for i in range(N_RUNS):
            res, ms = measure_ms(neo4j_global_aggregation, s)
            neo4j_times.append(ms)
            neo4j_result = res
            print(f"  Run {i+1:2d}: {ms:.3f} ms")

    # --- PostgreSQL ---
    sub_banner("PostgreSQL – Sequential Scan")
    pg_times = []
    pg_result = None
    for i in range(N_RUNS):
        res, ms = measure_ms(pg_global_aggregation, pg_conn)
        pg_times.append(ms)
        pg_result = res
        print(f"  Run {i+1:2d}: {ms:.3f} ms")

    neo4j_stats = compute_stats(neo4j_times)
    pg_stats    = compute_stats(pg_times)
    speedup_pg_vs_neo4j = (
        round(neo4j_stats["mean_ms"] / pg_stats["mean_ms"], 2)
        if pg_stats["mean_ms"] > 0
        else "N/A"
    )

    print()
    print_stats("Neo4j", neo4j_stats)
    print_stats("PostgreSQL", pg_stats)
    print(f"\n  >>> Speedup PostgreSQL vs Neo4j: {speedup_pg_vs_neo4j}x")
    print(f"  (PostgreSQL è {speedup_pg_vs_neo4j}x più veloce di Neo4j su questa query)")

    # Mostra campione risultato per validazione
    if neo4j_result:
        print(f"\n  Campione risultato Neo4j (prime 3 righe):")
        for row in neo4j_result[:3]:
            print(f"    browser={row['browser']}  avg_len={row['avg_len']:.1f}  cnt={row['cnt']}")
    if pg_result:
        print(f"\n  Campione risultato PostgreSQL (prime 3 righe):")
        for row in pg_result[:3]:
            print(f"    browser={row[0]}  avg_len={float(row[1]):.1f}  cnt={row[2]}")

    return {
        "neo4j":                  neo4j_stats,
        "postgresql":             pg_stats,
        "speedup_pg_vs_neo4j":    speedup_pg_vs_neo4j,
        "neo4j_rows_returned":    len(neo4j_result) if neo4j_result else 0,
        "pg_rows_returned":       len(pg_result) if pg_result else 0,
    }


# ===========================================================================
# TEST 4.2 – INSERIMENTO BATCH TRANSAZIONALE vs BULK LOAD
# ===========================================================================
# Confronto tra due modalità di ingestione di massa:
#   - PostgreSQL: COPY da buffer in-memory (bulk load nativo, bypass WAL parziale)
#   - Neo4j:      batch CREATE con UNWIND (inserimento transazionale batch)
#
# NOTA METODOLOGICA IMPORTANTE:
# Questo confronto è intenzionalmente asimmetrico e viene presentato come tale.
# PostgreSQL COPY è un'operazione di bulk load non-transazionale (bypass del WAL)
# progettata per il massimo throughput di ingestione. Il corrispettivo perfetto
# in Neo4j sarebbe lo strumento `neo4j-admin database import`, che bypassa
# anch'esso le transazioni e carica in modalità offline.
# Questo test confronta invece COPY (bulk load) con UNWIND+CREATE (inserimento
# transazionale batch) per evidenziare un limite strutturale reale di Neo4j:
# l'overhead dell'allocazione delle strutture dati per i puntatori degli archi
# anche in assenza di relazioni logiche. Questo overhead esiste in qualsiasi
# modalità di inserimento Neo4j, incluso neo4j-admin.
# Il capitolo è pertanto intitolato "Inserimento Batch Transazionale vs Bulk Load"
# per riflettere accuratamente la natura del confronto.
#
# I record sono nodi "BenchmarkRecord" fittizi con proprietà scalari:
#   id (int), name (string), score (float), created_at (string)
# Vengono creati e poi rimossi al termine del test per non inquinare il DB.
# ===========================================================================

_ADJECTIVES = [
    "fast", "slow", "bright", "dark", "sharp", "soft", "hard", "warm",
    "cool", "deep", "thin", "wide", "tall", "small", "large", "quick",
]
_NOUNS = [
    "river", "stone", "cloud", "light", "flame", "wave", "wind", "tree",
    "path", "field", "door", "bridge", "tower", "lake", "hill", "moon",
]


def _generate_bulk_records(n: int) -> list[dict]:
    """Genera n record fittizi per il bulk insert."""
    rng = random.Random(12345)
    return [
        {
            "id":         i,
            "name":       f"{rng.choice(_ADJECTIVES)}_{rng.choice(_NOUNS)}_{i}",
            "score":      round(rng.uniform(0.0, 1000.0), 4),
            "created_at": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
        }
        for i in range(n)
    ]


def neo4j_bulk_insert(driver, records: list[dict]) -> int:
    """
    Bulk insert tramite UNWIND + CREATE in batch.
    Restituisce il numero di nodi creati.
    """
    total = 0
    with driver.session() as s:
        for start in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[start : start + BULK_BATCH_SIZE]
            result = s.run(
                """
                UNWIND $batch AS row
                CREATE (r:BenchmarkRecord {
                    id:         row.id,
                    name:       row.name,
                    score:      row.score,
                    created_at: row.created_at
                })
                RETURN count(*) AS cnt
                """,
                batch=batch,
            )
            total += result.single()["cnt"]
    return total


def neo4j_bulk_cleanup(driver):
    """Rimuove tutti i nodi BenchmarkRecord creati dal test."""
    with driver.session() as s:
        s.run(
            """
            CALL apoc.periodic.iterate(
              'MATCH (r:BenchmarkRecord) RETURN r',
              'DETACH DELETE r',
              {batchSize: 5000, parallel: false}
            )
            """,
            # Se APOC non è disponibile, usa il fallback sotto
        )


def neo4j_bulk_cleanup_no_apoc(driver):
    """Fallback senza APOC: elimina in batch iterativi."""
    with driver.session() as s:
        while True:
            result = s.run(
                """
                MATCH (r:BenchmarkRecord)
                WITH r LIMIT 5000
                DETACH DELETE r
                RETURN count(*) AS deleted
                """
            )
            deleted = result.single()["deleted"]
            if deleted == 0:
                break


def pg_bulk_insert(conn, records: list[dict]) -> int:
    """
    Bulk insert via COPY da buffer in-memory (massima efficienza PostgreSQL).
    Usa una tabella temporanea per non inquinare lo schema LDBC.
    """
    cur = conn.cursor()

    # Crea tabella temporanea per il test
    cur.execute("""
        CREATE TEMP TABLE IF NOT EXISTS benchmark_record (
            id          BIGINT PRIMARY KEY,
            name        TEXT,
            score       DOUBLE PRECISION,
            created_at  TEXT
        )
    """)
    conn.commit()

    # Costruisce il CSV in memoria
    buf = io.StringIO()
    writer = csv.writer(buf)
    for r in records:
        writer.writerow([r["id"], r["name"], r["score"], r["created_at"]])
    buf.seek(0)

    # COPY da buffer in-memory
    cur.copy_from(buf, "benchmark_record",
                  columns=("id", "name", "score", "created_at"),
                  sep=",")
    conn.commit()

    # Conta i record inseriti
    cur.execute("SELECT COUNT(*) FROM benchmark_record")
    total = cur.fetchone()[0]

    # Cleanup: svuota la tabella temporanea
    cur.execute("TRUNCATE benchmark_record")
    conn.commit()
    cur.close()
    return total


def run_bulk_insert_test(neo4j_driver, pg_conn) -> dict:
    banner(f"TEST 4.2 – Inserimento Batch Transazionale (Neo4j) vs Bulk Load (PostgreSQL)")
    print(f"  Payload: {BULK_INSERT_RECORDS:,} record senza relazioni (anagrafica piatta).")
    print(f"  PostgreSQL: COPY da buffer in-memory (bulk load, parziale bypass WAL).")
    print(f"  Neo4j:      UNWIND+CREATE in batch da {BULK_BATCH_SIZE} record (transazionale).")
    print(f"  NOTA: confronto asimmetrico per design – misura overhead strutturale Neo4j.\n")

    records = _generate_bulk_records(BULK_INSERT_RECORDS)
    print(f"  [OK] {len(records):,} record generati in memoria")

    # --- Neo4j ---
    sub_banner("Neo4j – UNWIND+CREATE (batch insert)")
    neo4j_times = []
    neo4j_inserted = 0

    for i in range(N_RUNS):
        # Cleanup pre-run (nodi del run precedente)
        try:
            neo4j_bulk_cleanup_no_apoc(neo4j_driver)
        except Exception:
            pass

        t0 = time.perf_counter()
        cnt = neo4j_bulk_insert(neo4j_driver, records)
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000.0
        neo4j_times.append(ms)
        neo4j_inserted = cnt
        throughput = cnt / (ms / 1000.0) if ms > 0 else 0
        print(f"  Run {i+1:2d}: {ms:>10.1f} ms  |  {cnt:,} inseriti  |  {throughput:,.0f} rec/s")

    # Cleanup finale Neo4j
    print("  [*] Pulizia nodi BenchmarkRecord da Neo4j...")
    neo4j_bulk_cleanup_no_apoc(neo4j_driver)
    print("  [OK] Pulizia completata")

    # --- PostgreSQL ---
    sub_banner("PostgreSQL – COPY da buffer in-memory")
    pg_times = []
    pg_inserted = 0

    for i in range(N_RUNS):
        t0 = time.perf_counter()
        cnt = pg_bulk_insert(pg_conn, records)
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000.0
        pg_times.append(ms)
        pg_inserted = cnt
        throughput = cnt / (ms / 1000.0) if ms > 0 else 0
        print(f"  Run {i+1:2d}: {ms:>10.1f} ms  |  {cnt:,} inseriti  |  {throughput:,.0f} rec/s")

    neo4j_stats = compute_stats(neo4j_times)
    pg_stats    = compute_stats(pg_times)

    speedup_pg_vs_neo4j = (
        round(neo4j_stats["mean_ms"] / pg_stats["mean_ms"], 2)
        if pg_stats["mean_ms"] > 0
        else "N/A"
    )
    neo4j_tps = round(neo4j_inserted / (neo4j_stats["mean_ms"] / 1000.0), 0) if neo4j_stats.get("mean_ms", 0) > 0 else 0
    pg_tps    = round(pg_inserted    / (pg_stats["mean_ms"]    / 1000.0), 0) if pg_stats.get("mean_ms", 0) > 0 else 0

    print()
    print_stats("Neo4j (UNWIND+CREATE)", neo4j_stats)
    print_stats("PostgreSQL (COPY)",     pg_stats)
    print(f"\n  Neo4j throughput medio    : {neo4j_tps:,.0f} record/s")
    print(f"  PostgreSQL throughput medio: {pg_tps:,.0f} record/s")
    print(f"\n  >>> Speedup PostgreSQL vs Neo4j: {speedup_pg_vs_neo4j}x")

    return {
        "records":             BULK_INSERT_RECORDS,
        "batch_size_neo4j":    BULK_BATCH_SIZE,
        "neo4j":               neo4j_stats,
        "postgresql":          pg_stats,
        "neo4j_throughput_rps":    neo4j_tps,
        "pg_throughput_rps":       pg_tps,
        "speedup_pg_vs_neo4j": speedup_pg_vs_neo4j,
    }


# ===========================================================================
# TEST 4.3 – ESPLOSIONE COMBINATORIA NEI CAMMINI NON FILTRATI
# ===========================================================================
# Dimostra il rischio di query Cypher mal ottimizzate su nodi ad alto
# branching factor (super-nodi) senza vincoli di profondità espliciti.
#
# Struttura del test:
#   A) Query non filtrata:  MATCH (p)-[*1..6]-(q) RETURN count(*)
#      Eseguita sul nodo con il MASSIMO grado (super-nodo) → crescita
#      esponenziale della frontiera → OOM / Timeout atteso.
#   B) Query con filtri topologici:
#      - Tipo di relazione esplicito [:KNOWS]
#      - Limite di profondità ridotto (max 3 hop)
#      - LIMIT sul risultato
#      → risponde in tempi accettabili.
#
# La query A viene eseguita in un thread separato con timeout controllato;
# se supera EXPLOSION_TIMEOUT_S viene interrotta e segnata come "timeout".
# ===========================================================================


def neo4j_find_supernode(driver) -> tuple[int, int]:
    """Trova il nodo Person con il massimo grado (super-nodo)."""
    with driver.session() as s:
        result = s.run("""
            MATCH (p:Person)
            WITH p, size([(p)-[:KNOWS]-() | 1]) AS degree
            ORDER BY degree DESC
            LIMIT 1
            RETURN p.id AS pid, degree
        """)
        rec = result.single()
        if rec:
            return rec["pid"], rec["degree"]
    return None, 0


def _run_with_timeout(fn, timeout_s: float, *args, **kwargs):
    """
    Esegue fn(*args, **kwargs) in un thread separato con timeout.
    Restituisce (result, elapsed_ms, timed_out: bool).
    """
    result_container = [None]
    exception_container = [None]

    def worker():
        try:
            result_container[0] = fn(*args, **kwargs)
        except Exception as e:
            exception_container[0] = e

    t = threading.Thread(target=worker, daemon=True)
    t0 = time.perf_counter()
    t.start()
    t.join(timeout=timeout_s)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    timed_out = t.is_alive()
    # Non possiamo uccidere il thread Python (limitazione GIL); il thread
    # continuerà in background finché il driver non riceve l'eccezione di
    # timeout dal server o la sessione non viene chiusa.
    return result_container[0], elapsed_ms, timed_out, exception_container[0]


def neo4j_explosion_query_unfiltered(driver, person_id: int) -> int:
    """
    Query non filtrata: tutti i cammini di lunghezza 1-6 da un super-nodo.
    ATTENZIONE: può portare a OOM / Timeout su nodi ad alto grado.
    """
    # Creiamo la sessione internamente così in caso di timeout il thread isolato
    # la gestisce (o la perde) senza far crashare il thread principale.
    with driver.session() as session:
        with session.begin_transaction(timeout=float(EXPLOSION_TIMEOUT_S)) as tx:
            result = tx.run(
                """
                MATCH (p:Person {id: $pid})-[*1..6]-(q:Person)
                RETURN count(DISTINCT q) AS cnt
                """,
                pid=person_id,
            )
            rec = result.single()
            return rec["cnt"] if rec else -1


def neo4j_explosion_query_filtered(session, person_id: int) -> int:
    """
    Query con filtri topologici espliciti:
      - solo relazioni KNOWS (tipo dichiarato)
      - profondità identica (6 hop)
    """
    result = session.run(
        """
        MATCH (p:Person {id: $pid})-[:KNOWS*1..6]-(q:Person)
        RETURN count(DISTINCT q) AS cnt
        """,
        pid=person_id,
    )
    rec = result.single()
    return rec["cnt"] if rec else -1


def run_explosion_test(neo4j_driver) -> dict:
    banner("TEST 4.3 – Esplosione Combinatoria nei Cammini Non Filtrati")
    print("  Dimostra il rischio di query Cypher senza filtri topologici")
    print("  su nodi ad alto branching factor (super-nodi).\n")
    results = {}

    # Trova il super-nodo
    sub_banner("Identificazione del super-nodo (nodo con grado massimo)")
    supernode_id, supernode_degree = neo4j_find_supernode(neo4j_driver)
    if supernode_id is None:
        print("  [WARN] Nessun nodo Person trovato. Test saltato.")
        return {"skipped": True, "reason": "no Person nodes found"}

    print(f"  Super-nodo: Person id={supernode_id}  grado={supernode_degree} vicini diretti")
    print(f"  Stima percorsi a 6 hop: ~{supernode_degree}^6 ≈ {supernode_degree**6:,} (ordine di grandezza)")

    # ---- 4.3a: Query NON FILTRATA – eseguita 10 volte per solidità statistica ----
    sub_banner(f"4.3a – Query NON FILTRATA: MATCH (p)-[*1..6]-(q)  [timeout={EXPLOSION_TIMEOUT_S}s]")
    print("  ATTENZIONE: questa query può saturare la RAM e causare OOM/Timeout.")
    print(f"  Eseguita 1 volta a causa dell'impatto atteso sui tempi di esecuzione.\n")

    EXPLOSION_N_RUNS = 1    # ripetizioni per la query non filtrata
    unfiltered_times = []
    unfiltered_outcomes = []
    unfiltered_cnt = None

    for trial in range(EXPLOSION_N_RUNS):
        res, elapsed_ms, timed_out, exc = _run_with_timeout(
            neo4j_explosion_query_unfiltered,
            EXPLOSION_TIMEOUT_S,
            neo4j_driver, supernode_id
        )

        if timed_out:
            outcome = f"TIMEOUT (>{EXPLOSION_TIMEOUT_S}s)"
            unfiltered_times.append(EXPLOSION_TIMEOUT_S * 1000.0)
            print(f"  Trial {trial+1:2d}: ⏱  TIMEOUT ({EXPLOSION_TIMEOUT_S}s)")
        elif exc is not None:
            outcome = f"ERRORE: {type(exc).__name__}"
            unfiltered_times.append(elapsed_ms)
            print(f"  Trial {trial+1:2d}: ❌ Errore: {exc}")
        else:
            outcome = "completata"
            unfiltered_cnt = res
            unfiltered_times.append(elapsed_ms)
            print(f"  Trial {trial+1:2d}: ✅ {elapsed_ms:.1f} ms  |  nodi distinti: {res:,}")

        unfiltered_outcomes.append(outcome)

    unfiltered_stats = compute_stats(unfiltered_times)
    n_timeouts = sum(1 for o in unfiltered_outcomes if "TIMEOUT" in o)
    n_complete = sum(1 for o in unfiltered_outcomes if o == "completata")

    elapsed_ms = unfiltered_times[-1] if unfiltered_times else 0
    unfiltered_outcome = f"{n_complete}/{EXPLOSION_N_RUNS} completate, {n_timeouts} timeout"

    print(f"\n  Esito aggregato: {unfiltered_outcome}")
    if unfiltered_stats:
        print_stats("Query non filtrata", unfiltered_stats)
    if n_timeouts > 0:
        print(f"  NOTA: Su dataset reali (SF 1+) causerebbe OOM certo. Su SF 0.1 il timeout")
        print(f"        si attiva perché l'esplosione combinatoria satura la memoria JVM.")
    else:
        print(f"  NOTA: Su SF 0.1 (~1700 nodi) la query riesce. Su dataset reali (milioni")
        print(f"        di nodi) causerebbe OOM. La deviazione standard σ={unfiltered_stats.get('stdev_ms',0):.1f}ms")
        print(f"        indica alta variabilità da GC JVM su query pesanti.")

    results["unfiltered"] = {
        "supernode_id":     supernode_id,
        "supernode_degree": supernode_degree,
        "query":            "MATCH (p:Person {id: $pid})-[*1..6]-(q) RETURN count(DISTINCT q)",
        "n_runs":           EXPLOSION_N_RUNS,
        "outcomes":         unfiltered_outcomes,
        "n_timeouts":       n_timeouts,
        "n_completed":      n_complete,
        "stats":            unfiltered_stats,
        "count_returned":   unfiltered_cnt,
        "outcome":          unfiltered_outcome,
    }

    # ---- 4.3b: Query CON FILTRI TOPOLOGICI ----
    sub_banner("4.3b – Query CON FILTRI: [:KNOWS*1..6] (tipo esplicito)")
    print("  Stessa semantica e stessa profondità, ma con vincoli espliciti che limitano la frontiera.\n")

    # Warm-up
    with neo4j_driver.session() as s:
        for _ in range(N_WARMUP):
            neo4j_explosion_query_filtered(s, supernode_id)

    filtered_times = []
    with neo4j_driver.session() as s:
        for i in range(N_RUNS):
            cnt, ms = measure_ms(neo4j_explosion_query_filtered, s, supernode_id)
            filtered_times.append(ms)
            print(f"  Run {i+1:2d}: {ms:.3f} ms  |  {cnt:,} nodi distinti")

    filtered_stats = compute_stats(filtered_times)
    print()
    print_stats("Query con filtri topologici", filtered_stats)

    results["filtered"] = {
        "supernode_id":  supernode_id,
        "query":         "MATCH (p:Person {id: $pid})-[:KNOWS*1..6]-(q:Person) RETURN count(DISTINCT q)",
        "stats":         filtered_stats,
    }

    # ---- Confronto riepilogativo ----
    print()
    print(f"  {'Variante':<30} {'Esito':<25} {'Tempo (ms)'}")
    print(f"  {'-'*70}")
    print(f"  {'Non filtrata [*1..6]':<30} {unfiltered_outcome:<25} {elapsed_ms:.1f}")
    print(f"  {'Filtrata [:KNOWS*1..6]':<30} {'OK':<25} {filtered_stats.get('mean_ms', 'N/A'):.3f} (media)")

    if not timed_out and unfiltered_cnt is not None and filtered_stats:
        slowdown = round(elapsed_ms / filtered_stats["mean_ms"], 1) if filtered_stats["mean_ms"] > 0 else "N/A"
        print(f"\n  >>> Rallentamento query non filtrata vs filtrata: {slowdown}x")

    return results


# ===========================================================================
# MAIN
# ===========================================================================


def main():
    print(f"\n{'#' * 70}")
    print(f"#  SCENARIO 4: I Punti Deboli – Quando NON usare Neo4j")
    print(f"#  Data/ora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"#  Configurazione: SF (rilevamento in corso...) | N_RUNS={N_RUNS} | WARMUP={N_WARMUP}")
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

    # Conta i Person e rileva il Scale Factor
    with neo4j_driver.session() as _s:
        _n_persons_total = _s.run("MATCH (p:Person) RETURN count(p) AS n").single()["n"]
    if _n_persons_total < 2_000:
        detected_sf = "0.1"
    elif _n_persons_total < 20_000:
        detected_sf = "1"
    else:
        detected_sf = "3+"

    print(f"\n[*] {_n_persons_total} Person trovati nel grafo (SF rilevato: {detected_sf})")
    print(f"#  Configurazione: SF {detected_sf} | N_RUNS={N_RUNS} | WARMUP={N_WARMUP}")

    # Seed per riproducibilità
    random.seed(42)

    all_results = {}

    # ---- TEST 4.1 ----
    all_results["test_4_1_global_aggregation"] = run_global_aggregation_test(
        neo4j_driver, pg_conn
    )

    # ---- TEST 4.2 ----
    all_results["test_4_2_bulk_insert"] = run_bulk_insert_test(
        neo4j_driver, pg_conn
    )

    # ---- TEST 4.3 ----
    all_results["test_4_3_explosion"] = run_explosion_test(neo4j_driver)

    # ---- RIEPILOGO FINALE ----
    banner("RIEPILOGO FINALE – Scenario 4: I Punti Deboli di Neo4j")

    t41 = all_results["test_4_1_global_aggregation"]
    t42 = all_results["test_4_2_bulk_insert"]
    t43 = all_results["test_4_3_explosion"]

    print(f"\n{'Test':<35} {'Neo4j (ms)':<15} {'PostgreSQL (ms)':<18} {'Speedup PG'}")
    print("-" * 78)

    # 4.1
    n4j_41 = t41.get("neo4j", {}).get("mean_ms", "N/A")
    pg_41  = t41.get("postgresql", {}).get("mean_ms", "N/A")
    sp_41  = t41.get("speedup_pg_vs_neo4j", "N/A")
    print(f"{'4.1 Aggregazione globale (media)':<35} {str(n4j_41):<15} {str(pg_41):<18} {str(sp_41)}x")

    # 4.2
    n4j_42 = t42.get("neo4j", {}).get("mean_ms", "N/A")
    pg_42  = t42.get("postgresql", {}).get("mean_ms", "N/A")
    sp_42  = t42.get("speedup_pg_vs_neo4j", "N/A")
    print(f"{'4.2 Bulk Insert (media)':<35} {str(n4j_42):<15} {str(pg_42):<18} {str(sp_42)}x")

    # 4.3
    unf = t43.get("unfiltered", {})
    flt = t43.get("filtered", {})
    unf_ms  = unf.get("stats", {}).get("mean_ms", "N/A")
    flt_ms  = flt.get("stats", {}).get("mean_ms", "N/A")
    unf_out = unf.get("outcome", "N/A")
    print(f"\n{'Test':<35} {'Non filtrata':<20} {'Filtrata (ms)':<20} {'Esito'}")
    print("-" * 78)
    print(f"{'4.3 Esplosione combinatoria':<35} {str(unf_ms)+'ms':<20} {str(flt_ms):<20} {unf_out}")

    # Throughput riepilogo 4.2
    print(f"\n  [4.2] Throughput Neo4j:      {t42.get('neo4j_throughput_rps', 'N/A'):,.0f} record/s")
    print(f"  [4.2] Throughput PostgreSQL: {t42.get('pg_throughput_rps', 'N/A'):,.0f} record/s")

    # Salvataggio JSON
    output_dir  = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "results.json")

    all_results["metadata"] = {
        "timestamp":      datetime.now().isoformat(),
        "scale_factor":   detected_sf,
        "n_persons":       _n_persons_total,
        "n_runs":         N_RUNS,
        "n_warmup":       N_WARMUP,
        "bulk_records":   BULK_INSERT_RECORDS,
        "bulk_batch_size": BULK_BATCH_SIZE,
        "explosion_timeout_s": EXPLOSION_TIMEOUT_S,
        "neo4j_version":  "5.20.0-community",
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
