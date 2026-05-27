#!/usr/bin/env python3
"""
=============================================================================
SCENARIO 2: Transazioni e Concorrenza (Stress Test)
=============================================================================
Test suite che mette sotto sforzo la robustezza transazionale di Neo4j
in ambienti multi-utente ad alta concorrenza:

  2.1 - Gestione del Livello Read Committed
        Verifica S-lock / X-lock e assenza di anomalie Dirty Read durante
        letture analitiche e scritture concorrenti sullo stesso sotto-grafo.

  2.2 - Simulazione del Lost Update
        Race condition controllata con due thread concorrenti sullo stesso
        nodo. Confronto tra strategia non-atomica (vulnerabile) e strategia
        atomica Cypher (robusta).

  2.3 - Deadlock Detection e Risoluzione
        Scritture incrociate simmetriche che forzano un'attesa circolare.
        Misurazione del tempo di reazione del Lock Manager di Neo4j
        (Wait-for Graph) e verifica del rollback automatico via
        TransientError.

Metodologia:
  - N_THREADS thread per la concorrenza
  - N_RUNS ripetizioni per la raccolta di statistiche
  - Warm-up iniziale per stabilizzare le connessioni
  - Metriche: dirty_reads, lost_updates, deadlock_time_ms, ...
=============================================================================
"""

import time
import random
import statistics
import json
import sys
import threading
import concurrent.futures
from datetime import datetime

# ---------------------------------------------------------------------------
# Dipendenze – installazione automatica se mancanti
# ---------------------------------------------------------------------------
try:
    from neo4j import GraphDatabase, exceptions as neo4j_exc
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "neo4j"])
    from neo4j import GraphDatabase, exceptions as neo4j_exc

# ---------------------------------------------------------------------------
# Configurazione connessioni
# ---------------------------------------------------------------------------
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password"

# ---------------------------------------------------------------------------
# Parametri benchmark
# ---------------------------------------------------------------------------
N_RUNS        = 20   # ripetizioni per raccogliere statistiche
N_WARMUP      = 3    # warm-up iniziale
N_THREADS     = 8    # thread concorrenti per i test di concorrenza
LOST_UPDATE_THREADS = 10  # thread per il test Lost Update
DEADLOCK_PAIRS      = 20  # coppie di thread per forzare deadlock

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
        "n":         n,
        "mean_ms":   round(statistics.mean(sorted_t), 3),
        "median_ms": round(statistics.median(sorted_t), 3),
        "p90_ms":    round(sorted_t[p90_idx], 3),
        "min_ms":    round(sorted_t[0], 3),
        "max_ms":    round(sorted_t[-1], 3),
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
# CONNESSIONE
# ===========================================================================

def get_neo4j_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ===========================================================================
# SETUP: crea nodi dedicati ai test di concorrenza
# ===========================================================================

CONCURRENCY_NODE_COUNT = 5   # nodi dedicati ai test 2.1 e 2.3
TEST_PROP_NAME         = "notification_count"

def setup_test_nodes(driver, person_ids: list[int]) -> list[int]:
    """
    Seleziona N nodi Person esistenti e li usa come target per i test di
    concorrenza. Inizializza la proprietà `notification_count` = 0.
    Restituisce la lista degli id scelti.
    """
    chosen = random.sample(person_ids, min(CONCURRENCY_NODE_COUNT, len(person_ids)))
    with driver.session() as s:
        s.run(
            "UNWIND $ids AS pid "
            "MATCH (p:Person {id: pid}) "
            "SET p.notification_count = 0",
            ids=chosen
        )
    print(f"  [Setup] Nodi target per concorrenza: {chosen}")
    return chosen


def reset_notification_count(driver, person_ids: list[int], value: int = 0):
    """Reimposta notification_count al valore specificato su tutti i nodi target."""
    with driver.session() as s:
        s.run(
            "UNWIND $ids AS pid "
            "MATCH (p:Person {id: pid}) "
            "SET p.notification_count = $val",
            ids=person_ids, val=value
        )


# ===========================================================================
# TEST 2.1 – GESTIONE DEL LIVELLO READ COMMITTED
# ===========================================================================

def reader_task(driver, target_id: int, n_reads: int) -> dict:
    """
    Thread di LETTURA analitica locale: legge più volte la proprietà
    `notification_count` e verifica che non legga mai un valore
    parzialmente scritto (Dirty Read).
    Restituisce dizionario con latenze e valori letti.
    """
    latencies = []
    values_read = []

    with driver.session() as s:
        for _ in range(n_reads):
            t0 = time.perf_counter()
            result = s.run(
                "MATCH (p:Person {id: $pid}) RETURN p.notification_count AS val",
                pid=target_id
            )
            rec = result.single()
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000.0)
            if rec:
                values_read.append(rec["val"])

    return {"latencies": latencies, "values_read": values_read}


def writer_task_explicit_tx(driver, target_id: int, n_writes: int,
                             committed_value: int = 99,
                             dirty_value: int = 42) -> dict:
    """
    Thread di SCRITTURA con transazione esplicita lunga.
    Struttura di ogni iterazione:
      1. BEGIN TX
      2. SET notification_count = dirty_value   ← dato NON committato
      3. sleep(50-100ms)                         ← finestra di Dirty Read
      4. SET notification_count = committed_value
      5. COMMIT

    Se Neo4j rispettasse Read Committed, nessun reader vedrebbe `dirty_value`
    durante il passo 3. Un sistema senza isolamento mostrerebbe dirty_value.
    """
    latencies = []
    errors = 0

    for _ in range(n_writes):
        t0 = time.perf_counter()
        try:
            with driver.session() as s:
                tx = s.begin_transaction()
                try:
                    # Scrive il valore "sporco" (non committato)
                    tx.run(
                        "MATCH (p:Person {id: $pid}) SET p.notification_count = $val",
                        pid=target_id, val=dirty_value
                    ).consume()

                    # Finestra deliberata: reader possono girare in questo slot
                    time.sleep(random.uniform(0.050, 0.100))

                    # Aggiorna al valore finale committato
                    tx.run(
                        "MATCH (p:Person {id: $pid}) SET p.notification_count = $val",
                        pid=target_id, val=committed_value
                    ).consume()
                    tx.commit()
                except Exception:
                    try:
                        tx.rollback()
                    except Exception:
                        pass
                    raise
        except Exception:
            errors += 1
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    return {"latencies": latencies, "errors": errors}


def run_read_committed_test(driver, target_ids: list[int]) -> dict:
    banner("TEST 2.1 – Gestione del Livello Read Committed")
    results = {}

    target_id = target_ids[0]
    # Valori usati nel test: 42 = dirty (non committato), 99 = committed
    DIRTY_VALUE     = 42
    COMMITTED_VALUE = 99
    VALID_VALUES    = {0, COMMITTED_VALUE, None}  # valori leciti per un reader

    sub_banner(f"Read Committed – target Person ID: {target_id}")
    print(f"  [Info] Dirty value (uncommitted): {DIRTY_VALUE}")
    print(f"  [Info] Committed value          : {COMMITTED_VALUE}")
    print(f"  [Info] Valori leciti per reader : {{0, {COMMITTED_VALUE}}}")

    reset_notification_count(driver, [target_id], value=0)

    read_latencies_all   = []
    write_latencies_all  = []
    dirty_reads_detected = 0
    total_reads = 0

    for run_idx in range(N_RUNS):
        reset_notification_count(driver, [target_id], value=0)

        read_results  = []
        write_results = []

        # N_THREADS/2 reader e N_THREADS/2 writer concorrenti
        with concurrent.futures.ThreadPoolExecutor(max_workers=N_THREADS) as pool:
            n_readers = N_THREADS // 2
            n_writers = N_THREADS - n_readers

            reader_futures = [
                pool.submit(reader_task, driver, target_id, 8)
                for _ in range(n_readers)
            ]
            writer_futures = [
                pool.submit(writer_task_explicit_tx, driver, target_id,
                            n_writers,         # n_writes
                            COMMITTED_VALUE,   # committed_value
                            DIRTY_VALUE)       # dirty_value
                for _ in range(n_writers)
            ]

            for f in concurrent.futures.as_completed(reader_futures):
                read_results.append(f.result())
            for f in concurrent.futures.as_completed(writer_futures):
                write_results.append(f.result())

        # Raccolta latenze e verifica Dirty Read
        for r in read_results:
            read_latencies_all.extend(r["latencies"])
            total_reads += len(r["values_read"])
            for v in r["values_read"]:
                # Un Dirty Read si verifica se il reader vede DIRTY_VALUE
                # (42), che non è mai stato committato
                if v == DIRTY_VALUE:
                    dirty_reads_detected += 1

        for w in write_results:
            write_latencies_all.extend(w["latencies"])

    read_stats  = compute_stats(read_latencies_all)
    write_stats = compute_stats(write_latencies_all)

    print(f"\n  Letture totali effettuate : {total_reads}")
    print(f"  Dirty Read rilevati       : {dirty_reads_detected}  "
          f"{'✅ Nessuno (corretto)' if dirty_reads_detected == 0 else '❌ ANOMALIA!'}")
    print_stats("Latenza Lettura", read_stats)
    print_stats("Latenza Scrittura (tx esplicita)", write_stats)

    results["read_committed"] = {
        "target_id":            target_id,
        "dirty_value":          DIRTY_VALUE,
        "committed_value":      COMMITTED_VALUE,
        "total_reads":          total_reads,
        "dirty_reads_detected": dirty_reads_detected,
        "isolation_ok":         dirty_reads_detected == 0,
        "read_latency":         read_stats,
        "write_latency":        write_stats,
    }
    return results


# ===========================================================================
# TEST 2.2 – SIMULAZIONE DEL LOST UPDATE
# ===========================================================================

# ---------- Strategia NON ATOMICA (vulnerabile) ----------

def lost_update_non_atomic(driver, target_id: int, increment: int, barrier: threading.Barrier) -> dict:
    """
    Thread che esegue un incremento NON atomico:
      1. READ: legge notification_count
      2. Pausa artificiale (simula processing)
      3. WRITE: scrive old_value + increment  ← Lost Update possibile qui
    """
    barrier.wait()   # sincronizza tutti i thread prima di partire

    try:
        with driver.session() as s:
            # READ
            res = s.run(
                "MATCH (p:Person {id: $pid}) RETURN p.notification_count AS val",
                pid=target_id
            )
            rec = res.single()
            old_val = rec["val"] if rec else 0

            # Pausa deliberata per aumentare la probabilità di overlap
            time.sleep(random.uniform(0.005, 0.020))

            # WRITE (con valore stale)
            s.run(
                "MATCH (p:Person {id: $pid}) SET p.notification_count = $val",
                pid=target_id, val=old_val + increment
            )
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------- Strategia ATOMICA (robusta) ----------

def lost_update_atomic(driver, target_id: int, increment: int, barrier: threading.Barrier) -> dict:
    """
    Thread che esegue un incremento ATOMICO con lock esclusivo automatico:
      SET p.notification_count = p.notification_count + increment
    Il compilatore Cypher rileva la dipendenza e acquisisce un X-lock prima
    della lettura, garantendo la correttezza.
    """
    barrier.wait()

    try:
        with driver.session() as s:
            s.run(
                "MATCH (p:Person {id: $pid}) "
                "SET p.notification_count = p.notification_count + $inc",
                pid=target_id, inc=increment
            )
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_lost_update_test(driver, target_ids: list[int]) -> dict:
    banner("TEST 2.2 – Simulazione del Lost Update (Race Condition)")
    results = {}

    target_id  = target_ids[1] if len(target_ids) > 1 else target_ids[0]
    n_threads  = LOST_UPDATE_THREADS
    increment  = 1
    expected   = n_threads * increment   # valore corretto se non ci sono lost update

    sub_banner(f"Lost Update – target Person ID: {target_id} | {n_threads} thread | incremento={increment}")

    # ---- Fase A: Strategia NON ATOMICA ----
    sub_banner("2.2a – Strategia NON ATOMICA (alias separato: READ → SET)")
    reset_notification_count(driver, [target_id], value=0)

    non_atomic_lost_updates = []
    for trial in range(N_RUNS):
        reset_notification_count(driver, [target_id], value=0)
        barrier = threading.Barrier(n_threads)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [
                pool.submit(lost_update_non_atomic, driver, target_id, increment, barrier)
                for _ in range(n_threads)
            ]
            concurrent.futures.wait(futures)

        # Leggi il valore finale
        with driver.session() as s:
            res = s.run(
                "MATCH (p:Person {id: $pid}) RETURN p.notification_count AS val",
                pid=target_id
            )
            final_val = res.single()["val"]

        lost = expected - final_val
        non_atomic_lost_updates.append(lost)
        print(f"  Trial {trial+1:2d}: atteso={expected}  finale={final_val}  "
              f"lost_updates={lost}  {'❌' if lost > 0 else '✅'}")

    non_atomic_summary = {
        "expected_value":       expected,
        "trials":               N_RUNS,
        "lost_updates_per_trial": non_atomic_lost_updates,
        "mean_lost":            round(statistics.mean(non_atomic_lost_updates), 2),
        "trials_with_loss":     sum(1 for x in non_atomic_lost_updates if x > 0),
    }
    print(f"\n  [Non Atomica] Media Lost Updates per trial : {non_atomic_summary['mean_lost']:.2f}")
    print(f"  [Non Atomica] Trial con almeno 1 lost update: "
          f"{non_atomic_summary['trials_with_loss']}/{N_RUNS}")

    # ---- Fase B: Strategia ATOMICA ----
    sub_banner("2.2b – Strategia ATOMICA (SET p.prop = p.prop + N in unico costrutto)")
    reset_notification_count(driver, [target_id], value=0)

    atomic_lost_updates = []
    for trial in range(N_RUNS):
        reset_notification_count(driver, [target_id], value=0)
        barrier = threading.Barrier(n_threads)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [
                pool.submit(lost_update_atomic, driver, target_id, increment, barrier)
                for _ in range(n_threads)
            ]
            concurrent.futures.wait(futures)

        with driver.session() as s:
            res = s.run(
                "MATCH (p:Person {id: $pid}) RETURN p.notification_count AS val",
                pid=target_id
            )
            final_val = res.single()["val"]

        lost = expected - final_val
        atomic_lost_updates.append(lost)
        print(f"  Trial {trial+1:2d}: atteso={expected}  finale={final_val}  "
              f"lost_updates={lost}  {'❌' if lost > 0 else '✅'}")

    atomic_summary = {
        "expected_value":       expected,
        "trials":               N_RUNS,
        "lost_updates_per_trial": atomic_lost_updates,
        "mean_lost":            round(statistics.mean(atomic_lost_updates), 2),
        "trials_with_loss":     sum(1 for x in atomic_lost_updates if x > 0),
    }
    print(f"\n  [Atomica]     Media Lost Updates per trial : {atomic_summary['mean_lost']:.2f}")
    print(f"  [Atomica]     Trial con almeno 1 lost update: "
          f"{atomic_summary['trials_with_loss']}/{N_RUNS}")

    results["lost_update"] = {
        "target_id":   target_id,
        "n_threads":   n_threads,
        "increment":   increment,
        "non_atomic":  non_atomic_summary,
        "atomic":      atomic_summary,
    }
    return results


# ===========================================================================
# TEST 2.3 – DEADLOCK DETECTION E RISOLUZIONE
# ===========================================================================

def deadlock_thread_v2(driver, first_id: int, second_id: int,
                       my_ready: threading.Event, other_ready: threading.Event,
                       result_out: list, idx: int):
    """
    Approccio canonico con handshake a due eventi:
      1. Acquisisce X-lock su `first_id` in una tx esplicita aperta
      2. Segnala `my_ready` (il lock è tenuto aperto nella tx non committata)
      3. Attende `other_ready` (l'altro thread ha il suo lock)
      4. Tenta di acquisire `second_id` → deadlock circolare → TransientError

    L'approccio a coppia singola per run garantisce che il ciclo si formi
    tra esattamente due transazioni aperte, rendendo il deadlock deterministico.
    """
    t_start = time.perf_counter()
    outcome = {"thread_idx": idx, "deadlock_detected": False,
               "detection_time_ms": None,   # tempo dal secondo lock request alla TransientError
               "error_type": None,
               "rollback_ok": False}

    with driver.session() as s:
        tx = s.begin_transaction()
        try:
            # Passo 1 – LOCK su first_id (tenuto aperto nella tx)
            tx.run(
                "MATCH (p:Person {id: $pid}) "
                "SET p.notification_count = p.notification_count + 1",
                pid=first_id
            ).consume()

            # Passo 2 – Segnalo che il mio lock è acquisito
            my_ready.set()

            # Passo 3 – Aspetto che l'altro thread abbia il suo lock
            if not other_ready.wait(timeout=5):
                raise TimeoutError("Timeout waiting for other thread's lock")

            # Piccola pausa per assicurare che entrambe le tx siano in attesa
            time.sleep(random.uniform(0.05, 0.15))

            # Passo 4 – LOCK su second_id → circolo → deadlock
            # t_lock2_start misura SOLO il tempo del Wait-for Graph,
            # escludendo il sleep artificiale precedente
            t_lock2_start = time.perf_counter()
            tx.run(
                "MATCH (p:Person {id: $pid}) "
                "SET p.notification_count = p.notification_count + 1",
                pid=second_id
            ).consume()

            tx.commit()

        except neo4j_exc.TransientError as e:
            t_detected = time.perf_counter()
            outcome["deadlock_detected"]   = True
            # detection_time_ms = tempo dal secondo lock request alla TransientError
            # (= overhead reale del Wait-for Graph, senza il sleep)
            outcome["detection_time_ms"]   = round((t_detected - t_lock2_start) * 1000.0, 3)
            outcome["error_type"]          = type(e).__name__
            outcome["rollback_ok"]         = True
            outcome["message"]             = str(e)[:200]
            try:
                tx.rollback()
            except Exception:
                pass
        except Exception as e:
            outcome["error_type"] = type(e).__name__
            outcome["message"]    = str(e)[:200]
            try:
                tx.rollback()
            except Exception:
                pass
        finally:
            # Sblocca sempre l'altro thread in caso di errore prematuro
            my_ready.set()

    result_out[idx] = outcome


def run_deadlock_test(driver, target_ids: list[int]) -> dict:
    banner("TEST 2.3 – Deadlock Detection e Risoluzione (Wait-for Graph)")
    results = {}

    if len(target_ids) < 2:
        print("  [SKIP] Servono almeno 2 nodi target per il test deadlock.")
        return {"deadlock": {"skipped": True}}

    node_a = target_ids[0]
    node_b = target_ids[1]

    sub_banner(f"Deadlock – nodo A={node_a} | nodo B={node_b} | 1 coppia per run × {N_RUNS} run")

    total_deadlocks    = 0
    detection_times_ms = []
    rollback_confirmed = 0

    for run_idx in range(N_RUNS):
        reset_notification_count(driver, [node_a, node_b], value=0)

        # Due eventi per il handshake: ciascun thread segnala quando ha il lock
        ready_a = threading.Event()
        ready_b = threading.Event()
        result_out = [None, None]

        # Thread A: acquisisce nodeA poi cerca nodeB
        t_a = threading.Thread(
            target=deadlock_thread_v2,
            args=(driver, node_a, node_b, ready_a, ready_b, result_out, 0),
            daemon=True
        )
        # Thread B: acquisisce nodeB poi cerca nodeA → ciclo garantito
        t_b = threading.Thread(
            target=deadlock_thread_v2,
            args=(driver, node_b, node_a, ready_b, ready_a, result_out, 1),
            daemon=True
        )

        t_a.start()
        t_b.start()
        t_a.join(timeout=15)
        t_b.join(timeout=15)

        run_deadlocks = 0
        for outcome in result_out:
            if outcome and outcome["deadlock_detected"]:
                run_deadlocks += 1
                total_deadlocks += 1
                if outcome["detection_time_ms"] is not None:
                    detection_times_ms.append(outcome["detection_time_ms"])
                if outcome["rollback_ok"]:
                    rollback_confirmed += 1

        print(f"  Run {run_idx+1:2d}: deadlock rilevati={run_deadlocks}  "
              f"(totale cumulativo: {total_deadlocks})")

    detection_stats = compute_stats(detection_times_ms) if detection_times_ms else {}

    print(f"\n  Deadlock totali rilevati  : {total_deadlocks} / {N_RUNS} run")
    print(f"  Rollback automatici conf. : {rollback_confirmed}")
    if detection_stats:
        print(f"  Tempo rilevazione medio   : {detection_stats['mean_ms']:.3f} ms")
        print(f"  Tempo rilevazione P90     : {detection_stats['p90_ms']:.3f} ms")
        print(f"  Meccanismo                : Wait-for Graph (TransientError)")
    print(f"  Rollback automatico       : {'✅ Confermato' if rollback_confirmed > 0 else '⚠️ Non rilevato'}")

    results["deadlock"] = {
        "node_a":             node_a,
        "node_b":             node_b,
        "total_runs":         N_RUNS,
        "total_deadlocks":    total_deadlocks,
        "rollback_confirmed": rollback_confirmed,
        "detection_time_ms":  detection_stats,
    }
    return results


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print(f"\n{'#' * 70}")
    print(f"#  SCENARIO 2: Transazioni e Concorrenza – Stress Test Neo4j")
    print(f"#  Data/ora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"#  Configurazione: SF 0.1 | N_RUNS={N_RUNS} | N_THREADS={N_THREADS}")
    print(f"{'#' * 70}")

    # Connessione
    print("\n[*] Connessione a Neo4j...")
    try:
        driver = get_neo4j_driver()
        driver.verify_connectivity()
        print("  [OK] Neo4j connesso")
    except Exception as e:
        print(f"  [ERR] Neo4j: {e}")
        sys.exit(1)

    # Lista person_id
    with driver.session() as s:
        res = s.run("MATCH (p:Person) RETURN p.id AS id ORDER BY p.id")
        person_ids = [r["id"] for r in res]
    print(f"\n[*] {len(person_ids)} Person trovati nel grafo (SF 0.1)")

    # Seed per riproducibilità
    random.seed(42)

    # Warm-up: apre e chiude alcune sessioni per stabilizzare il pool Bolt
    print("\n[*] Warm-up connessioni Bolt...")
    for _ in range(N_WARMUP):
        with driver.session() as s:
            s.run("RETURN 1")
    print("  [OK] Warm-up completato")

    # Setup nodi target per concorrenza
    target_ids = setup_test_nodes(driver, person_ids)

    all_results = {}

    # ---- TEST 2.1 ----
    r21 = run_read_committed_test(driver, target_ids)
    all_results.update(r21)

    # ---- TEST 2.2 ----
    r22 = run_lost_update_test(driver, target_ids)
    all_results.update(r22)

    # ---- TEST 2.3 ----
    r23 = run_deadlock_test(driver, target_ids)
    all_results.update(r23)

    # ---- RIEPILOGO FINALE ----
    banner("RIEPILOGO FINALE – Scenario 2")

    rc  = all_results.get("read_committed", {})
    lu  = all_results.get("lost_update", {})
    dl  = all_results.get("deadlock", {})

    print("\n[2.1] Read Committed:")
    print(f"  Dirty Read rilevati     : {rc.get('dirty_reads_detected', 'N/A')}")
    print(f"  Isolamento corretto     : {'✅ Sì' if rc.get('isolation_ok') else '❌ No'}")
    if rc.get("read_latency"):
        print(f"  Lat. lettura media (ms) : {rc['read_latency']['mean_ms']:.3f}")
    if rc.get("write_latency"):
        print(f"  Lat. scrittura media(ms): {rc['write_latency']['mean_ms']:.3f}")

    print("\n[2.2] Lost Update:")
    if lu.get("non_atomic") and lu.get("atomic"):
        na = lu["non_atomic"]
        at = lu["atomic"]
        print(f"  {'Strategia':<30} {'Lost update medi':<20} {'Trial con loss'}")
        print(f"  {'Non Atomica (vulnerabile)':<30} {na['mean_lost']:<20.2f} "
              f"{na['trials_with_loss']}/{na['trials']}")
        print(f"  {'Atomica (robusta)':<30} {at['mean_lost']:<20.2f} "
              f"{at['trials_with_loss']}/{at['trials']}")

    print("\n[2.3] Deadlock:")
    print(f"  Deadlock rilevati       : {dl.get('total_deadlocks', 'N/A')}")
    print(f"  Rollback automatici     : {dl.get('rollback_confirmed', 'N/A')}")
    det = dl.get("detection_time_ms", {})
    if det:
        print(f"  Tempo rilevazione medio : {det.get('mean_ms', 'N/A')} ms")
        print(f"  Tempo rilevazione P90   : {det.get('p90_ms', 'N/A')} ms")

    # Pulizia: rimuovi notification_count dai nodi di test
    with driver.session() as s:
        s.run(
            "UNWIND $ids AS pid "
            "MATCH (p:Person {id: pid}) "
            "REMOVE p.notification_count",
            ids=target_ids
        )
    print("\n[*] Proprietà di test rimossa dai nodi.")

    # Salvataggio risultati JSON
    import os
    output_dir  = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "results.json")

    all_results["metadata"] = {
        "timestamp":       datetime.now().isoformat(),
        "scale_factor":    "0.1",
        "n_runs":          N_RUNS,
        "n_warmup":        N_WARMUP,
        "n_threads":       N_THREADS,
        "n_persons":       len(person_ids),
        "neo4j_version":   "5.20.0-community",
    }
    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        print(f"\n[*] Risultati salvati in: {output_path}")
    except Exception as e:
        print(f"\n[WARN] Impossibile salvare JSON: {e}")

    driver.close()
    print("\n[*] Connessione chiusa. Benchmark completato.\n")


if __name__ == "__main__":
    main()
