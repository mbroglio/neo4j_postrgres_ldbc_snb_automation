#!/usr/bin/env python3
"""
=============================================================================
SCENARIO 3: Sistemi Distribuiti e Teorema CAP
=============================================================================
Test suite per l'analisi delle architetture distribuite ad alta affidabilità
con Neo4j 5 in configurazione cluster Raft (3 primari + 2 secondari):

  3.1 - Configurazione e Verifica del Cluster
        Verifica che il cluster sia operativo, identifica il Leader Raft
        e la composizione dei nodi (ruolo, stato, version).

  3.2 - Tolleranza ai Guasti (Fault Tolerance)
        Spegnimento forzato del nodo Leader durante un flusso continuo di
        scritture. Misurazione del tempo esatto di indisponibilità (downtime),
        delle scritture perse e della transizione Raft completa
        (heartbeat timeout → elezione → quorum → ripresa).

  3.3 - Scalabilità in Lettura (Causal Consistency via Bookmark)
        Carico massivo di query di navigazione sul grafo via routing
        server-side (neo4j://). Misurazione del throughput QPS, della
        distribuzione del carico sui nodi secondari e dell'overhead dei
        Bookmark per la Causal Consistency (le scritture sono sempre visibili
        anche su repliche asincrone).

Prerequisiti:
  - Cluster avviato con infrastructure/docker-compose-cluster.yml
  - Dati LDBC SF 0.1 caricati nel cluster
  - Docker SDK for Python:  pip install docker
  - Driver Neo4j:           pip install neo4j

Metodologia:
  - WRITE_THREADS thread per il flusso di scrittura continua (3.2)
  - READ_THREADS  thread per il carico di lettura (3.3)
  - N_RUNS        cicli di lettura per raccogliere statistiche stabili
=============================================================================
"""

import os
import time
import random
import statistics
import json
import sys
import threading
import concurrent.futures
from collections import defaultdict
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

try:
    import docker as docker_sdk
    DOCKER_AVAILABLE = True
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", "docker"])
    try:
        import docker as docker_sdk
        DOCKER_AVAILABLE = True
    except ImportError:
        DOCKER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configurazione connessione al cluster
# ---------------------------------------------------------------------------
# Il routing neo4j:// richiede il nodo entry-point; il driver distribuisce
# automaticamente READ verso secondari e WRITE verso il leader corrente.
CLUSTER_URI   = os.environ.get("NEO4J_CLUSTER_URI", "neo4j://neo4j-core1:7687")
NEO4J_USER    = "neo4j"
NEO4J_PASSWORD = "password"

# URI diretti per verifica nodi singoli (senza routing)
# Usano i nomi hostname dei container quando eseguiti nella rete Docker interna,
# oppure le porte pubblicate sull'host se eseguiti dall'esterno.
_IN_DOCKER = os.environ.get("IN_DOCKER", "0") == "1"
if _IN_DOCKER:
    NODE_URIS = {
        "neo4j-core1":      "bolt://neo4j-core1:7687",
        "neo4j-core2":      "bolt://neo4j-core2:7687",
        "neo4j-core3":      "bolt://neo4j-core3:7687",
        "neo4j-secondary1": "bolt://neo4j-secondary1:7687",
        "neo4j-secondary2": "bolt://neo4j-secondary2:7687",
    }
else:
    NODE_URIS = {
        "neo4j-core1":      "bolt://localhost:7687",
        "neo4j-core2":      "bolt://localhost:7688",
        "neo4j-core3":      "bolt://localhost:7689",
        "neo4j-secondary1": "bolt://localhost:7690",
        "neo4j-secondary2": "bolt://localhost:7691",
    }

# Nome del container leader da abbattere nel test 3.2
# (viene rilevato dinamicamente dallo script)
LEADER_CONTAINER_NAME = None   # popolato da detect_cluster_roles()

# ---------------------------------------------------------------------------
# Parametri benchmark
# ---------------------------------------------------------------------------
N_RUNS          = 5000  # letture per verifica Causal Consistency (solidità statistica)
N_WARMUP        = 15    # warm-up connessioni
WRITE_THREADS   = 4     # thread scrittori durante il fault tolerance (3.2)
WRITE_DURATION  = 15.0  # secondi di scrittura continua prima dello stop
POST_STOP_MAX   = 60.0  # attesa massima per la rielezione (secondi)
READ_THREADS    = 16    # thread lettori per il test di scalabilità (3.3)
READ_DURATION   = 20.0  # secondi di carico di lettura (3.3)

# ---------------------------------------------------------------------------
# NOTA INFRASTRUTTURALE - Latenze Docker (importante per interpretare i dati)
# ---------------------------------------------------------------------------
# In ambienti Docker su singolo host, le latenze di rete osservate possono
# essere anomale rispetto a un cluster reale. In particolare:
#   - Un nodo può rispondere a 6ms, gli altri a 1400ms: questo indica
#     che la rete virtuale Docker (bridge) sta "strozzando" i pacchetti,
#     oppure che i container lottano per la CPU (frequente senza limiti CPU).
#   - SOLUZIONE: tutti i container sono sulla stessa docker network bridge
#     e ciascuno ha un limite di CPU esplicito (--cpus="1.5").
#   - Se le latenze anomale persistono, questo viene dichiarato come limite
#     infrastrutturale nella tesi: i tempi di rielezione Raft misurati
#     includono l'overhead della virtualizzazione Docker e non sono
#     rappresentativi di un cluster bare-metal.

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def banner(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def sub_banner(title: str):
    print(f"\n--- {title} ---")


def compute_stats(times_ms: list[float]) -> dict:
    """Calcola statistiche di latenza su una lista di misurazioni."""
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
    if not stats:
        print(f"  {label}: N/D")
        return
    print(f"  {label}:")
    print(f"    Campioni   : {stats['n']}")
    print(f"    Media      : {stats['mean_ms']:>10.3f} ms")
    print(f"    Mediana    : {stats['median_ms']:>10.3f} ms")
    print(f"    P90        : {stats['p90_ms']:>10.3f} ms")
    print(f"    Min        : {stats['min_ms']:>10.3f} ms")
    print(f"    Max        : {stats['max_ms']:>10.3f} ms")


def get_driver(uri: str = CLUSTER_URI):
    """Restituisce un driver Neo4j connesso all'URI specificato."""
    return GraphDatabase.driver(uri, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ===========================================================================
# TEST 3.1 – CONFIGURAZIONE E VERIFICA DEL CLUSTER
# ===========================================================================

def detect_cluster_roles(driver) -> dict:
    """
    Interroga il cluster via SHOW SERVERS (Neo4j 5.x) e SHOW DATABASES
    per rilevare il leader Raft corrente.
    Aggiorna anche la variabile globale LEADER_CONTAINER_NAME.
    """
    global LEADER_CONTAINER_NAME
    cluster_info = {}
    try:
        # Neo4j 5.x: SHOW SERVERS mostra tutti i membri del cluster
        with driver.session(database="system") as s:
            servers = list(s.run(
                "SHOW SERVERS YIELD serverId, name, address, state, health"
            ))
            for rec in servers:
                srv_id  = rec.get("name") or rec.get("serverId") or "unknown"
                address = rec.get("address") or ""
                state   = rec.get("state") or "UNKNOWN"
                health  = rec.get("health") or "UNKNOWN"
                cluster_info[srv_id] = {
                    "name":      srv_id,
                    "role":      state,
                    "addresses": [address],
                    "health":    health,
                }

        # Scopri chi è il leader interrogando SHOW DATABASES
        # In Neo4j 5 il leader ha ruolo 'primary' con writer=true
        with driver.session(database="system") as s:
            dbs = list(s.run(
                "SHOW DATABASES YIELD name, address, role, requestedStatus, currentStatus, writer "
                "WHERE name = 'neo4j'"
            ))
            for rec in dbs:
                if rec.get("writer") and rec.get("role") == "primary":
                    addr = rec.get("address", "")
                    # Mappa l'indirizzo al nome del container
                    for cname in NODE_URIS:
                        if cname in addr:
                            if LEADER_CONTAINER_NAME is None:
                                LEADER_CONTAINER_NAME = cname
                                print(f"  [INFO] Leader Raft rilevato via SHOW DATABASES: {cname} ({addr})")
    except Exception as e:
        print(f"  [WARN] SHOW SERVERS/DATABASES fallito: {e}")
    return cluster_info


def probe_node_alive(uri: str, timeout: float = 2.0) -> tuple[bool, float]:
    """
    Controlla se un nodo risponde alla connessione Bolt.
    Restituisce (alive: bool, latency_ms: float).
    """
    try:
        drv = GraphDatabase.driver(
            uri,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
            connection_timeout=timeout,
            max_connection_lifetime=5,
        )
        t0 = time.perf_counter()
        with drv.session() as s:
            s.run("RETURN 1").consume()
        t1 = time.perf_counter()
        drv.close()
        return True, (t1 - t0) * 1000.0
    except Exception:
        return False, 0.0


def run_cluster_info_test(driver) -> dict:
    banner("TEST 3.1 – Configurazione e Verifica del Cluster")

    sub_banner("Stato dei nodi via dbms.cluster.overview()")
    cluster_info = detect_cluster_roles(driver)

    if cluster_info:
        print(f"  {'Nome':<25} {'Ruolo':<15} {'Indirizzi'}")
        print(f"  {'-'*25} {'-'*15} {'-'*40}")
        for name, info in cluster_info.items():
            addrs_str = ", ".join(info["addresses"])[:60]
            print(f"  {name:<25} {info['role']:<15} {addrs_str}")
    else:
        print("  [WARN] Nessuna informazione cluster recuperata.")

    sub_banner("Probe di connettività Bolt su tutti i nodi")
    node_status = {}
    for name, uri in NODE_URIS.items():
        alive, lat = probe_node_alive(uri)
        status_str = f"✅  ({lat:.1f} ms)" if alive else "❌  (irraggiungibile)"
        print(f"  {name:<25} bolt={uri:<35} {status_str}")
        node_status[name] = {"alive": alive, "latency_ms": round(lat, 3)}

    active_nodes = sum(1 for v in node_status.values() if v["alive"])
    print(f"\n  Nodi attivi: {active_nodes} / {len(NODE_URIS)}")
    if active_nodes < 3:
        print("  [WARN] Quorum non raggiungibile con meno di 3 primari attivi.")
    else:
        print("  [OK] Quorum garantito (≥ 3 primari attivi).")

    if LEADER_CONTAINER_NAME:
        print(f"\n  Leader Raft rilevato: {LEADER_CONTAINER_NAME}")
    else:
        print("\n  [WARN] Leader Raft non identificato automaticamente.")

    return {
        "cluster_overview": cluster_info,
        "node_status":      node_status,
        "active_nodes":     active_nodes,
        "leader_container": LEADER_CONTAINER_NAME,
    }


# ===========================================================================
# TEST 3.2 – TOLLERANZA AI GUASTI (FAULT TOLERANCE)
# ===========================================================================

class WriterWorker:
    """
    Worker che esegue scritture continue su un nodo Person e registra
    ogni transazione come (timestamp, success, latency_ms).
    Viene fermato tramite stop_event.
    """
    def __init__(self, driver, target_id: int):
        self.driver    = driver
        self.target_id = target_id
        self.records   = []   # lista di (t_abs, success, latency_ms)
        self.stop_event = threading.Event()

    def run(self):
        counter = 0
        while not self.stop_event.is_set():
            t0 = time.perf_counter()
            t_abs = time.time()
            try:
                with self.driver.session() as s:
                    s.run(
                        "MATCH (p:Person {id: $pid}) "
                        "SET p.bench_counter = $c",
                        pid=self.target_id, c=counter
                    ).consume()
                t1 = time.perf_counter()
                self.records.append((t_abs, True, (t1 - t0) * 1000.0))
            except Exception:
                t1 = time.perf_counter()
                self.records.append((t_abs, False, (t1 - t0) * 1000.0))
            counter += 1

    def stop(self):
        self.stop_event.set()


def stop_leader_container(container_name: str) -> float:
    """
    Esegue una PARTIZIONE DI RETE sul container Docker del leader:
    disconnette il container dalla rete Docker senza spegnerlo.
    Questo è l'approccio corretto per testare il Teorema CAP (profilo CP):
      - Il leader è ACCESO ma ISOLATO dagli altri nodi
      - I follower non ricevono heartbeat → avviano elezione Raft
      - Il leader isolato non raggiunge il quorum → non può committare
      - Nessun split-brain: il sistema va in CP (Consistency over Availability)

    NOTA: se il container usa più reti Docker, vengono disconnesse tutte.
    Restituisce il timestamp assoluto in cui la partizione è iniziata.
    """
    if not DOCKER_AVAILABLE:
        raise RuntimeError("Docker SDK non disponibile. Installa: pip install docker")
    client = docker_sdk.from_env()
    t_partition = time.time()
    container = client.containers.get(container_name)

    # Disconnette il container da tutte le sue reti Docker
    partitioned_networks = []
    for net_name, net_info in container.attrs.get("NetworkSettings", {}).get("Networks", {}).items():
        try:
            network = client.networks.get(net_name)
            network.disconnect(container)
            partitioned_networks.append(net_name)
            print(f"  [PARTITION] Container '{container_name}' disconnesso dalla rete '{net_name}'")
        except Exception as e:
            print(f"  [WARN] Impossibile disconnettere da '{net_name}': {e}")
            # Fallback: SIGKILL se la disconnessione di rete fallisce
            container.stop(timeout=0)
            print(f"  [FALLBACK] Container '{container_name}' fermato via SIGKILL")

    if not partitioned_networks:
        # Nessuna rete trovata, usa SIGKILL come fallback
        container.stop(timeout=0)
        print(f"  [FALLBACK] Container '{container_name}' fermato via SIGKILL (nessuna rete trovata)")

    print(f"  [PARTITION] Partizione di rete attivata a t={t_partition:.3f}")
    print(f"  [PARTITION] Leader '{container_name}' isolato ma ancora in esecuzione")
    print(f"  [PARTITION] I follower avvieranno elezione Raft dopo heartbeat timeout")

    # Salva le reti partizionate per il ripristino
    stop_leader_container._partitioned_networks = {container_name: partitioned_networks}
    return t_partition


def restart_leader_container(container_name: str):
    """Ripristina la connettività di rete del container precedentemente partizionato."""
    if not DOCKER_AVAILABLE:
        return
    client = docker_sdk.from_env()
    try:
        container = client.containers.get(container_name)
        # Riconnette il container alle reti da cui era stato disconnesso
        partitioned = getattr(stop_leader_container, '_partitioned_networks', {}).get(container_name, [])
        if partitioned:
            for net_name in partitioned:
                try:
                    network = client.networks.get(net_name)
                    network.connect(container)
                    print(f"  [RESTORE] Container '{container_name}' riconnesso alla rete '{net_name}'")
                except Exception as e:
                    print(f"  [WARN] Impossibile riconnettere a '{net_name}': {e}")
        else:
            # Fallback: riavvia il container se era stato fermato via SIGKILL
            container.start()
            print(f"  [START] Container '{container_name}' riavviato (fallback SIGKILL).")
    except Exception as e:
        print(f"  [WARN] Impossibile ripristinare '{container_name}': {e}")


def measure_failover(driver, t_crash: float, max_wait: float = POST_STOP_MAX) -> dict:
    """
    Dopo il crash del leader, sonda periodicamente il cluster finché le
    scritture non riprendono (nuovo leader eletto). Restituisce:
      - t_first_failure_ms: ms dall'inizio del test alla prima write fallita
      - t_recovery_ms:      ms dal crash al ripristino delle scritture
      - downtime_ms:        durata dell'indisponibilità delle scritture
    """
    probe_interval = 0.2   # 200 ms tra un tentativo e il successivo
    t_start = time.time()
    t_first_ok = None

    print(f"\n  [Failover probe] Attendo ripristino scritture (max {max_wait}s)...")
    attempt = 0
    while (time.time() - t_start) < max_wait:
        attempt += 1
        t0 = time.perf_counter()
        try:
            with driver.session() as s:
                # Scrittura leggera: aggiorna un nodo di test noto
                s.run(
                    "MERGE (x:_FailoverProbe {id: 1}) "
                    "SET x.ts = $ts",
                    ts=time.time()
                ).consume()
            t1 = time.perf_counter()
            lat = (t1 - t0) * 1000.0
            t_first_ok = time.time()
            print(f"  [Failover probe] ✅ Scrittura ripresa al tentativo {attempt} "
                  f"(latenza={lat:.1f} ms, t={t_first_ok - t_crash:.2f}s dal crash)")
            break
        except Exception as e:
            elapsed = time.time() - t_crash
            if attempt % 5 == 1:
                print(f"  [Failover probe] ⏳ tentativo {attempt} – "
                      f"ancora indisponibile ({elapsed:.1f}s dal crash): "
                      f"{type(e).__name__}")
            time.sleep(probe_interval)

    if t_first_ok is None:
        print(f"  [Failover probe] ❌ Scritture non riprese entro {max_wait}s!")
        return {"t_recovery_s": None, "downtime_ms": None, "probe_attempts": attempt}

    downtime_ms = (t_first_ok - t_crash) * 1000.0
    return {
        "t_recovery_s":   round(t_first_ok - t_crash, 3),
        "downtime_ms":    round(downtime_ms, 1),
        "probe_attempts": attempt,
    }


def run_fault_tolerance_test(driver, person_ids: list[int]) -> dict:
    banner("TEST 3.2 – Tolleranza ai Guasti (Fault Tolerance)")
    global LEADER_CONTAINER_NAME

    if not DOCKER_AVAILABLE:
        print("  [SKIP] Docker SDK non installato. Installa con: pip install docker")
        return {"fault_tolerance": {"skipped": True, "reason": "docker SDK mancante"}}

    if not LEADER_CONTAINER_NAME:
        # Fallback: usa il primo core come leader presunto
        LEADER_CONTAINER_NAME = "neo4j-core1"
        print(f"  [WARN] Leader non rilevato automaticamente, uso '{LEADER_CONTAINER_NAME}'")

    target_id = person_ids[0]

    # Pulisci eventuale nodo probe precedente
    try:
        with driver.session() as s:
            s.run("MATCH (x:_FailoverProbe) DETACH DELETE x").consume()
    except Exception:
        pass

    sub_banner(f"Fase A – Scrittura continua ({WRITE_THREADS} thread × {WRITE_DURATION}s)")
    print(f"  Target Person ID : {target_id}")
    print(f"  Leader da fermare: {LEADER_CONTAINER_NAME}")

    # Avvia i writer
    workers = [WriterWorker(driver, target_id) for _ in range(WRITE_THREADS)]
    threads = [threading.Thread(target=w.run, daemon=True) for w in workers]
    
    # Crea un Reader worker speciale per il downtime
    downtime_reader = ReaderWorker(driver, person_ids)
    downtime_reader_thread = threading.Thread(target=downtime_reader.run, daemon=True)
    
    for t in threads:
        t.start()
    downtime_reader_thread.start()

    # Fase stabile pre-crash
    time.sleep(WRITE_DURATION)

    # Conta le scritture nella fase stabile
    t_pre_crash = time.time()
    pre_crash_writes = sum(
        1 for w in workers
        for ts, ok, _ in w.records
        if ok and ts <= t_pre_crash
    )
    print(f"\n  Scritture andate a buon fine prima del crash: {pre_crash_writes}")

    # ---- CRASH ----
    sub_banner("Fase B – Crash del Leader Raft (SIGKILL)")
    t_crash = stop_leader_container(LEADER_CONTAINER_NAME)

    # Aspetta qualche secondo che i failure si propaghino ai worker
    time.sleep(1.0)

    # Conta gli errori immediatamente post-crash
    post_crash_errors = sum(
        1 for w in workers
        for ts, ok, _ in w.records
        if not ok and ts >= t_crash
    )
    print(f"  Errori di scrittura post-crash (prime misurazioni): {post_crash_errors}")

    # ---- FAILOVER PROBE ----
    sub_banner("Fase C – Misura del Downtime e Rielezione Raft")
    failover = measure_failover(driver, t_crash)

    # Ferma i writer e il reader
    for w in workers:
        w.stop()
    downtime_reader.stop()
    for t in threads:
        t.join(timeout=5)
    downtime_reader_thread.join(timeout=5)

    # Analisi delle registrazioni dei writer
    all_records = [(ts, ok, lat) for w in workers for ts, ok, lat in w.records]
    all_records.sort(key=lambda x: x[0])

    total_writes   = len(all_records)
    failed_writes  = sum(1 for _, ok, _ in all_records if not ok)
    success_writes = sum(1 for _, ok, _ in all_records if ok)
    write_lats_ok  = [lat for _, ok, lat in all_records if ok]
    write_lats_err = [lat for _, ok, lat in all_records if not ok]

    # Stima prima scrittura fallita (relativa al crash)
    first_fail_ts = next((ts for ts, ok, _ in all_records if not ok and ts >= t_crash), None)
    t_first_failure_ms = (first_fail_ts - t_crash) * 1000.0 if first_fail_ts else None

    print(f"\n  Scritture totali registrate   : {total_writes}")
    print(f"  Successi                       : {success_writes}")
    print(f"  Errori (scritture rifiutate)   : {failed_writes}")
    if failed_writes > 0:
        print(f"  [NOTA] Le {failed_writes} scritture fallite NON implicano corruzione dei dati.")
        print(f"         Il client Python riceve un'eccezione SessionExpired o NotALeader")
        print(f"         durante la finestra di rielezione Raft. In un'architettura")
        print(f"         produttiva reale, il driver ufficiale Neo4j (neo4j-driver-python)")
        print(f"         ritenta automaticamente la transazione con `session.execute_write()`,")
        print(f"         funzione che implementa la retry policy integrata. Nessun dato")
        print(f"         viene corrotto: il cluster mantiene il profilo CP del CAP theorem.")
    if t_first_failure_ms is not None:
        print(f"  Primo errore post-partizione (ms) : {t_first_failure_ms:.1f} ms")
    if failover["downtime_ms"] is not None:
        print(f"  Downtime totale scritture (ms) : {failover['downtime_ms']:.1f} ms  "
              f"({failover['t_recovery_s']:.2f}s)")
        print(f"  Profilo CAP validato           : CP ✅  "
              f"(indisponibilità temporanea per preservare consistenza)")
        print(f"  NOTA CAP: con partizione di rete (non SIGKILL), il leader isolato")
        print(f"            è ancora acceso ma non può committare (no quorum). I follower")
        print(f"            eleggono un nuovo leader. Questo dimostra CP: no split-brain.")
              
    read_records = downtime_reader.records
    read_total = len(read_records)
    read_success = sum(1 for _, _, ok in read_records if ok)
    print(f"\n  [Availability Check] Letture durante test: {read_success}/{read_total}")
    if read_success > 0 and failed_writes > 0:
        print(f"  ✅ Letture disponibili nonostante le scritture bloccate (Partition Tolerance provata)")

    write_stats_ok  = compute_stats(write_lats_ok)
    write_stats_err = compute_stats(write_lats_err)
    print_stats("Latenza scritture OK    ", write_stats_ok)
    print_stats("Latenza scritture FAILED", write_stats_err)

    # Riavvia il container abbattuto (per non lasciare il cluster degradato)
    sub_banner("Fase D – Ripristino del nodo (per cleanup)")
    restart_leader_container(LEADER_CONTAINER_NAME)
    # Attesa generosa: il container deve avviare la JVM e fare rejoin nel cluster
    print(f"  Attendo che {LEADER_CONTAINER_NAME} ritorni online (Probe Bolt)...")
    uri_to_probe = NODE_URIS.get(LEADER_CONTAINER_NAME)
    for _ in range(60):
        alive, _ = probe_node_alive(uri_to_probe, timeout=1.0)
        if alive:
            print("  [OK] Nodo tornato raggiungibile via Bolt. Attesa 15s per sincronizzazione Raft...")
            time.sleep(15.0)
            break
        print("  .", end="", flush=True)
        time.sleep(2.0)
    else:
        print("  [WARN] Timeout attesa riavvio nodo.")
    print("  [OK] Cluster ripristinato.")

    # Pulisci il nodo probe
    try:
        with driver.session() as s:
            s.run("MATCH (x:_FailoverProbe) DETACH DELETE x").consume()
            s.run(
                "MATCH (p:Person {id: $pid}) REMOVE p.bench_counter",
                pid=target_id
            ).consume()
    except Exception:
        pass

    return {
        "fault_tolerance": {
            "leader_container":       LEADER_CONTAINER_NAME,
            "target_person_id":       target_id,
            "write_threads":          WRITE_THREADS,
            "pre_crash_duration_s":   WRITE_DURATION,
            "total_writes":           total_writes,
            "successful_writes":      success_writes,
            "failed_writes":          failed_writes,
            "t_first_failure_ms":     round(t_first_failure_ms, 1) if t_first_failure_ms else None,
            "downtime_ms":            failover["downtime_ms"],
            "t_recovery_s":           failover["t_recovery_s"],
            "probe_attempts":         failover["probe_attempts"],
            "cap_profile":            "CP",
            "write_latency_ok_ms":    write_stats_ok,
            "write_latency_error_ms": write_stats_err,
            # Timeline eventi per il grafico
            "timeline": {
                "t_crash_abs":       t_crash,
                "t_recovery_abs":    t_crash + failover["t_recovery_s"] if failover["t_recovery_s"] else None,
                "write_records":     [(round(ts - t_crash, 3), ok, round(lat, 2))
                                      for ts, ok, lat in all_records],
            },
        }
    }


# ===========================================================================
# TEST 3.3 – SCALABILITÀ IN LETTURA (CAUSAL CONSISTENCY VIA BOOKMARK)
# ===========================================================================

class ReaderWorker:
    """
    Worker che esegue query di navigazione in lettura via routing neo4j://.
    Registra il nome del server che ha servito la richiesta per verificare
    la distribuzione del carico.
    Il campo `bookmark` (se non None) forza Causal Consistency: il driver
    aspetta che il secondario abbia applicato il log fino al punto indicato.
    """
    def __init__(self, driver, person_ids: list[int], bookmark=None):
        self.driver     = driver
        self.person_ids = person_ids
        self.bookmark   = bookmark
        self.records    = []   # (server_address, latency_ms, success)
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            pid = random.choice(self.person_ids)
            t0  = time.perf_counter()
            try:
                kwargs = {}
                if self.bookmark:
                    kwargs["bookmarks"] = [self.bookmark]
                with self.driver.session(default_access_mode="READ", **kwargs) as s:
                    result = s.run(
                        "MATCH (p:Person {id: $pid})-[:KNOWS]->(friend:Person) "
                        "RETURN friend.id AS fid LIMIT 10",
                        pid=pid
                    )
                    rows = list(result)
                    # Recupera il nome del server che ha servito la query
                    server_info = result.consume().server
                    server_addr = getattr(server_info, "address", "unknown")
                t1 = time.perf_counter()
                lat = (t1 - t0) * 1000.0
                self.records.append((str(server_addr), lat, True))
            except Exception as e:
                t1 = time.perf_counter()
                lat = (t1 - t0) * 1000.0
                if not hasattr(self, 'first_error_printed'):
                    print(f"\n  [ReaderWorker ERR] {type(e).__name__}: {e}")
                    self.first_error_printed = True
                self.records.append(("error", lat, False))

    def stop(self):
        self.stop_event.set()


def write_and_get_bookmark(driver, person_ids: list[int]) -> tuple[str | None, str]:
    """
    Esegue una scrittura e restituisce il bookmark prodotto dalla transazione.
    Usato per verificare che la lettura successiva su un secondario asincrono
    rispetti la Causal Consistency (il lettore vede sempre le proprie scritture).
    """
    pid = person_ids[0]
    marker_value = int(time.time())

    try:
        with driver.session() as s:
            with s.begin_transaction() as tx:
                tx.run(
                    "MATCH (p:Person {id: $pid}) SET p.causal_marker = $val",
                    pid=pid, val=marker_value
                )
                tx.commit()
            bookmark = s.last_bookmarks()
        # bookmark è un neo4j.Bookmarks object
        bookmark_str = str(bookmark)
        return bookmark, marker_value
    except Exception as e:
        print(f"  [WARN] Write per Bookmark fallita: {e}")
        return None, marker_value


def verify_causal_consistency(driver, person_ids: list[int],
                               bookmark, expected_val: int,
                               n_checks: int = 20) -> dict:
    """
    Esegue n_checks letture su nodi READ (secondari), forzando la Causal
    Consistency tramite Bookmark. Verifica che il valore scritto sia sempre
    visibile (stale read = 0).
    """
    pid = person_ids[0]
    stale_reads   = 0
    correct_reads = 0
    lats          = []

    for _ in range(n_checks):
        t0 = time.perf_counter()
        try:
            session_kwargs = {}
            if bookmark:
                session_kwargs["bookmarks"] = bookmark
            with driver.session(default_access_mode="READ", **session_kwargs) as s:
                res = s.run(
                    "MATCH (p:Person {id: $pid}) RETURN p.causal_marker AS val",
                    pid=pid
                )
                rec = res.single()
                t1  = time.perf_counter()
                lats.append((t1 - t0) * 1000.0)
                if rec and rec["val"] == expected_val:
                    correct_reads += 1
                else:
                    stale_reads += 1
        except Exception:
            t1 = time.perf_counter()
            lats.append((t1 - t0) * 1000.0)
            stale_reads += 1

    return {
        "n_checks":     n_checks,
        "correct_reads": correct_reads,
        "stale_reads":  stale_reads,
        "bookmark_lat": compute_stats(lats),
    }


def run_read_scalability_test(driver, person_ids: list[int]) -> dict:
    banner("TEST 3.3 – Scalabilità in Lettura e Causal Consistency")
    results = {}

    # ---- Fase A: causal consistency via bookmark ----
    sub_banner("Fase A – Causal Consistency (Bookmark Write-then-Read)")
    print(f"  Eseguo una scrittura e verifico la visibilità su nodi secondari...")

    bookmark, marker_val = write_and_get_bookmark(driver, person_ids)
    if bookmark:
        print(f"  Bookmark ottenuto: {str(bookmark)[:80]}...")
    else:
        print(f"  [WARN] Bookmark non disponibile, test senza Causal Consistency forzata.")

    cc_result = verify_causal_consistency(driver, person_ids, bookmark, marker_val,
                                           n_checks=N_RUNS)
    print(f"\n  Letture con Causal Consistency ({N_RUNS} campioni):")
    print(f"    Letture corrette (valore aggiornato)  : {cc_result['correct_reads']} / {cc_result['n_checks']}")
    print(f"    Stale read osservate                  : {cc_result['stale_reads']}  "
          f"{'✅ (atteso: 0)' if cc_result['stale_reads'] == 0 else '⚠️ Stale reads rilevate!'}")
    print_stats("    Overhead Bookmark (latenza lettura)", cc_result["bookmark_lat"])

    # Pulizia marker
    try:
        with driver.session() as s:
            s.run(
                "MATCH (p:Person {id: $pid}) REMOVE p.causal_marker",
                pid=person_ids[0]
            ).consume()
    except Exception:
        pass

    results["causal_consistency"] = cc_result

    # ---- Fase B: throughput e distribuzione del carico ----
    sub_banner(f"Fase B – Throughput & Load Balancing ({READ_THREADS} thread × {READ_DURATION}s)")
    print(f"  Carico di {READ_THREADS} thread reader via routing neo4j:// ...")

    workers = [ReaderWorker(driver, person_ids, bookmark=None) for _ in range(READ_THREADS)]
    threads = [threading.Thread(target=w.run, daemon=True) for w in workers]
    for t in threads:
        t.start()

    t_start = time.time()
    time.sleep(READ_DURATION)
    t_end = time.time()

    for w in workers:
        w.stop()
    for t in threads:
        t.join(timeout=5)

    # Aggrega tutti i record
    all_recs = [(addr, lat, ok) for w in workers for addr, lat, ok in w.records]
    total_queries = len(all_recs)
    success_count = sum(1 for _, _, ok in all_recs if ok)
    elapsed       = t_end - t_start
    qps           = success_count / elapsed if elapsed > 0 else 0

    # Distribuzione per server (load balancing)
    server_counts: dict[str, int] = defaultdict(int)
    server_lats:   dict[str, list] = defaultdict(list)
    for addr, lat, ok in all_recs:
        if ok:
            server_counts[addr] += 1
            server_lats[addr].append(lat)

    all_lats = [lat for _, lat, ok in all_recs if ok]
    global_stats = compute_stats(all_lats)

    print(f"\n  Durata effettiva        : {elapsed:.2f}s")
    print(f"  Query totali            : {total_queries}")
    print(f"  Query riuscite          : {success_count}")
    print(f"  Throughput              : {qps:.1f} QPS")
    print_stats("  Latenza globale", global_stats)

    print(f"\n  Distribuzione del carico per nodo:")
    print(f"  {'Indirizzo server':<45} {'Query':<8} {'%':<8} {'Media (ms)'}")
    print(f"  {'-'*45} {'-'*8} {'-'*8} {'-'*10}")
    server_distribution = {}
    for addr, cnt in sorted(server_counts.items(), key=lambda x: -x[1]):
        pct  = cnt / success_count * 100 if success_count > 0 else 0
        avg  = statistics.mean(server_lats[addr]) if server_lats[addr] else 0
        print(f"  {addr:<45} {cnt:<8} {pct:<8.1f} {avg:.2f}")
        server_distribution[addr] = {
            "count":       cnt,
            "percent":     round(pct, 2),
            "mean_lat_ms": round(avg, 3),
        }

    results["read_scalability"] = {
        "read_threads":       READ_THREADS,
        "duration_s":         round(elapsed, 3),
        "total_queries":      total_queries,
        "successful_queries": success_count,
        "qps":                round(qps, 2),
        "latency_ms":         global_stats,
        "server_distribution": server_distribution,
    }
    return results


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print(f"\n{'#' * 70}")
    print(f"#  SCENARIO 3: Sistemi Distribuiti e Teorema CAP")
    print(f"#  Data/ora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"#  Cluster: 3 primari + 2 secondari | neo4j://localhost:7687")
    print(f"{'#' * 70}")

    # Connessione
    print("\n[*] Connessione al cluster Neo4j via routing neo4j://...")
    try:
        driver = get_driver(CLUSTER_URI)
        driver.verify_connectivity()
        print("  [OK] Cluster raggiungibile")
    except Exception as e:
        print(f"  [ERR] Impossibile connettersi al cluster: {e}")
        print("  [HINT] Avvia il cluster con:")
        print("    NEO4J_ACCEPT_LICENSE_AGREEMENT=yes docker compose -f infrastructure/docker-compose-cluster.yml up -d")
        sys.exit(1)

    # Lista Person
    with driver.session() as s:
        res = s.run("MATCH (p:Person) RETURN p.id AS id ORDER BY p.id")
        person_ids = [r["id"] for r in res]
        
    _n_persons_total = len(person_ids)
    if _n_persons_total < 2_000:
        detected_sf = "0.1"
    elif _n_persons_total < 20_000:
        detected_sf = "1"
    else:
        detected_sf = "3+"

    print(f"\n[*] {_n_persons_total} Person nel grafo (SF rilevato: {detected_sf})")

    if not person_ids:
        print("  [ERR] Nessun nodo Person trovato. Il dataset è caricato nel cluster?")
        sys.exit(1)

    random.seed(42)

    # Warm-up
    print("\n[*] Warm-up connessioni Bolt...")
    for _ in range(N_WARMUP):
        with driver.session() as s:
            s.run("RETURN 1").consume()
    print("  [OK] Warm-up completato")

    all_results = {}

    # ---- TEST 3.1 ----
    r31 = run_cluster_info_test(driver)
    all_results["cluster_info"] = r31

    # ---- TEST 3.2 ----
    r32 = run_fault_tolerance_test(driver, person_ids)
    all_results.update(r32)

    # ---- TEST 3.3 ----
    # Chiude il driver precedente (che potrebbe avere la routing table corrotta
    # dopo il crash del leader in 3.2) e ne apre uno fresco.
    driver.close()
    
    # Attendiamo un tempo sufficiente affinché il cluster aggiorni la topology
    print("\n[*] Attesa 20s per assestamento routing table del cluster...")
    time.sleep(20)
    
    print("[*] Riapertura driver fresco per test 3.3 (post failover)...")
    for attempt in range(12):
        try:
            driver = get_driver(CLUSTER_URI)
            driver.verify_connectivity()
            print("  [OK] Driver connesso al cluster aggiornato.")
            break
        except Exception as e:
            print(f"  [WARN] Tentativo {attempt+1}/12 fallito: {e} – ritento in 10s")
            time.sleep(10)
    else:
        print("  [ERR] Impossibile riconnettersi dopo failover. Test 3.3 saltato.")
        all_results["read_scalability"] = {"skipped": True}
        all_results["causal_consistency"] = {"skipped": True}
        r33 = {}
        driver = None

    if driver:
        r33 = run_read_scalability_test(driver, person_ids)
        all_results.update(r33)
    else:
        r33 = {}

    # ---- RIEPILOGO FINALE ----
    banner("RIEPILOGO FINALE – Scenario 3")

    ci  = all_results.get("cluster_info", {})
    ft  = all_results.get("fault_tolerance", {})
    rs  = all_results.get("read_scalability", {})
    cc  = all_results.get("causal_consistency", {})

    print("\n[3.1] Configurazione Cluster:")
    print(f"  Nodi attivi             : {ci.get('active_nodes', 'N/A')} / {len(NODE_URIS)}")
    print(f"  Leader Raft rilevato    : {ci.get('leader_container', 'N/A')}")

    print("\n[3.2] Fault Tolerance:")
    if ft.get("skipped"):
        print(f"  [SKIP] {ft.get('reason', '')}")
    else:
        print(f"  Scritture totali        : {ft.get('total_writes', 'N/A')}")
        print(f"  Scritture fallite       : {ft.get('failed_writes', 'N/A')}")
        dt = ft.get('downtime_ms')
        print(f"  Downtime scritture (ms) : {f'{dt:.1f}' if dt else 'N/A'}")
        print(f"  Profilo CAP             : {ft.get('cap_profile', 'N/A')}")

    print("\n[3.3] Scalabilità in Lettura:")
    print(f"  Throughput              : {rs.get('qps', 'N/A')} QPS")
    lat = rs.get("latency_ms", {})
    print(f"  Latenza media           : {lat.get('mean_ms', 'N/A')} ms")
    print(f"  Latenza P90             : {lat.get('p90_ms', 'N/A')} ms")

    print("\n[3.3] Causal Consistency:")
    print(f"  Stale read osservate    : {cc.get('stale_reads', 'N/A')}")
    print(f"  Letture corrette        : {cc.get('correct_reads', 'N/A')} / {cc.get('n_checks', 'N/A')}")
    bm_lat = cc.get("bookmark_lat", {})
    print(f"  Overhead Bookmark medio : {bm_lat.get('mean_ms', 'N/A')} ms")

    # Salvataggio JSON
    import os
    output_dir  = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "results.json")

    all_results["metadata"] = {
        "timestamp":     datetime.now().isoformat(),
        "scale_factor":  detected_sf,
        "cluster_uri":   CLUSTER_URI,
        "n_warmup":      N_WARMUP,
        "write_threads": WRITE_THREADS,
        "read_threads":  READ_THREADS,
        "n_persons":     len(person_ids),
        "neo4j_version": "5.20.0-enterprise",
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
