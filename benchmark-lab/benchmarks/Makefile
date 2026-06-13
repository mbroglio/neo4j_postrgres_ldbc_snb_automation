# =============================================================================
# benchmarks/Makefile – Esecuzione degli scenari di benchmark
# =============================================================================
#
# PREREQUISITO: i container Neo4j e PostgreSQL devono essere già in esecuzione
# con il dataset desiderato (gestito dall'infrastructure/Makefile).
#
# UTILIZZO (dalla cartella benchmarks/):
#
#   make scenario1   – Esegue Scenario 1 sull'SF attualmente caricato
#   make scenario2   – Esegue Scenario 2 sull'SF attualmente caricato
#   make scenario3   – Avvia il cluster Raft ed esegue Scenario 3
#   make scenario4   – Esegue Scenario 4 sull'SF attualmente caricato
#
# L'SF viene rilevato automaticamente dagli script Python (conteggio Person).
# I risultati vengono archiviati in scenarioX/archive_SF<valore>/ in modo
# che eseguzioni su SF diversi non si sovrascrivano.
#
# FLUSSO TIPICO per doppio run SF1 + SF0.1:
#
#   # Dalla cartella infrastructure/:
#   make reset SF=1          # avvia container con dataset SF 1
#
#   # Dalla cartella benchmarks/:
#   make scenario1           # esegue su SF1, salva in archive_SF1/
#   make scenario2           # esegue su SF1, salva in archive_SF1/
#   make scenario4           # esegue su SF1, salva in archive_SF1/
#   make scenario3           # cluster Raft (indipendente dall'SF dei DB single-inst.)
#
#   # Dalla cartella infrastructure/:
#   make reset SF=0.1        # riavvia container con dataset SF 0.1
#
#   # Dalla cartella benchmarks/:
#   make scenario1           # esegue su SF0.1, salva in archive_SF0.1/
#   make scenario2           # esegue su SF0.1, salva in archive_SF0.1/
#   make scenario4           # esegue su SF0.1, salva in archive_SF0.1/
#
# =============================================================================

.PHONY: help scenario1 scenario2 scenario3 scenario4 query-plans wait-db scenario3-teardown

help:
	@echo ""
	@echo "============================================================"
	@echo "  benchmarks/Makefile – Esecuzione scenari"
	@echo "============================================================"
	@echo ""
	@echo "  make scenario1   Scenario 1: La Potenza del Grafo"
	@echo "                   (Multi-hop, Triangle Detection, Shortest Path)"
	@echo "                   NOTA: la query a 4 hop su SF1 può durare ore."
	@echo ""
	@echo "  make scenario2   Scenario 2: Transazioni e Concorrenza"
	@echo "                   (Read Committed, Lost Update, Deadlock)"
	@echo ""
	@echo "  make scenario3   Scenario 3: Sistemi Distribuiti / Teorema CAP"
	@echo "                   (Cluster Raft 5 nodi, Fault Tolerance, Causal Consistency)"
	@echo "                   Lo script avvia/ferma automaticamente il cluster."
	@echo ""
	@echo "  make scenario4   Scenario 4: I Punti Deboli di Neo4j"
	@echo "                   (Aggregazioni globali, Bulk Insert, Esplosione combinatoria)"
	@echo ""
	@echo "  make query-plans      Genera appendice con EXPLAIN/PROFILE"
	@echo ""
	@echo "  make wait-db          Attende che Neo4j e PostgreSQL single-instance"
	@echo "                        siano pronti (utile dopo scenario3)."
	@echo ""
	@echo "  make scenario3-teardown  Abbatte il cluster Raft e riavvia i DB"
	@echo "                           single-instance (recovery manuale)."
	@echo ""
	@echo "  I risultati vengono archiviati in scenarioX/archive_SF<valore>/"
	@echo "  L'SF viene rilevato automaticamente dal conteggio dei nodi Person."
	@echo ""
	@echo "  Per cambiare SF: cd ../infrastructure && make reset SF=<valore>"
	@echo ""

# ---------------------------------------------------------------------------
# Scenario 1 – La Potenza del Grafo
# Testa: navigazione multi-hop (1-4), triangle detection, shortest path.
# L'SF viene auto-rilevato. La query PG a 4 hop su SF1 richiede ore:
# lasciare girare senza timeout è la scelta metodologicamente corretta.
# ---------------------------------------------------------------------------
scenario1:
	@echo "========================================================"
	@echo "  Scenario 1: La Potenza del Grafo"
	@echo "========================================================"
	python3 scenario1/scenario1_benchmark.py
	python3 scenario1/plot_scenario1.py
	./archive_results.sh scenario1

# ---------------------------------------------------------------------------
# Scenario 2 – Transazioni e Concorrenza (ACID stress test)
# Testa: Read Committed, Lost Update, Deadlock detection.
# Durata stimata: ~2-3 minuti.
# ---------------------------------------------------------------------------
scenario2: wait-db
	@echo "========================================================"
	@echo "  Scenario 2: Transazioni e Concorrenza"
	@echo "========================================================"
	python3 scenario2/scenario2_benchmark.py
	./archive_results.sh scenario2

# ---------------------------------------------------------------------------
# Scenario 3 – Sistemi Distribuiti e Teorema CAP
# Avvia cluster Raft 5 nodi, carica i dati con batch transactions,
# esegue Fault Tolerance (Chaos Engineering) e Causal Consistency.
# run_scenario3.sh gestisce tutto: non è necessario alcun prerequisito.
# Durata stimata: ~5-8 minuti.
# ---------------------------------------------------------------------------
scenario3:
	@echo "========================================================"
	@echo "  Scenario 3: Sistemi Distribuiti / Teorema CAP"
	@echo "========================================================"
	bash scenario3/run_scenario3.sh
	./archive_results.sh scenario3

# ---------------------------------------------------------------------------
# Scenario 4 – I Punti Deboli di Neo4j
# Testa: aggregazioni globali (Full Scan), Bulk Insert, esplosione combinatoria.
# Il timeout del test 4.3 è impostato a 300s come protezione OOM
# (la query senza filtri su SF1 non può terminare in tempi ragionevoli).
# Durata stimata: ~15-20 minuti (di cui 5min di timeout 4.3).
# ---------------------------------------------------------------------------
scenario4: wait-db
	@echo "========================================================"
	@echo "  Scenario 4: I Punti Deboli di Neo4j"
	@echo "========================================================"
	python3 scenario4/scenario4_benchmark.py
	python3 scenario4/plot_scenario4.py
	./archive_results.sh scenario4

# ---------------------------------------------------------------------------
# wait-db – Attende che i container single-instance siano pronti.
# Usato come prerequisito da scenario2 e scenario4 per essere eseguibili
# in qualsiasi ordine, anche subito dopo scenario3.
# ---------------------------------------------------------------------------
wait-db:
	@echo "[wait-db] Verifico che i DB single-instance siano in esecuzione..."
	@if ! docker inspect neo4j-benchmark >/dev/null 2>&1; then \
		echo "[wait-db] ERRORE: container neo4j-benchmark non trovato."; \
		echo "         Esegui prima: cd ../infrastructure && make reset SF=<valore>"; \
		exit 1; \
	fi
	@echo "[wait-db] Attendo Neo4j single-instance (max 120s)..."
	@n=0; \
	until docker exec neo4j-benchmark cypher-shell -u neo4j -p password "RETURN 1" >/dev/null 2>&1; do \
		if [ $$n -ge 60 ]; then \
			echo "[wait-db] TIMEOUT: Neo4j non risponde dopo 120s."; \
			exit 1; \
		fi; \
		printf "."; \
		sleep 2; \
		n=$$((n+1)); \
	done; \
	echo ""
	@echo "[wait-db] Attendo PostgreSQL single-instance (max 60s)..."
	@n=0; \
	until docker exec postgres-benchmark pg_isready -U postgres >/dev/null 2>&1; do \
		if [ $$n -ge 30 ]; then \
			echo "[wait-db] TIMEOUT: PostgreSQL non risponde dopo 60s."; \
			exit 1; \
		fi; \
		printf "."; \
		sleep 2; \
		n=$$((n+1)); \
	done; \
	echo ""
	@echo "[wait-db] OK: Neo4j e PostgreSQL pronti."

# ---------------------------------------------------------------------------
# scenario3-teardown – Recovery manuale: abbatte il cluster Raft (se rimasto
# attivo) e riavvia i DB single-instance con l'SF rilevato automaticamente.
# Utile se scenario3 è stato interrotto con Ctrl+C senza eseguire il cleanup.
# ---------------------------------------------------------------------------
scenario3-teardown:
	@echo "[teardown] Abbatto il cluster Raft (se attivo)..."
	@docker compose -f ../infrastructure/docker-compose-cluster.yml down -v 2>/dev/null || true
	@SF=$$(docker inspect neo4j-benchmark \
		--format '{{ range .Mounts }}{{ if eq .Destination "/data" }}{{ .Name }}{{ end }}{{ end }}' 2>/dev/null \
		| grep -o 'sf[0-9.]*' | head -1 | sed 's/sf//'); \
	SF=$${SF:-0.1}; \
	echo "[teardown] Riavvio single-instance con SF=$${SF}..."; \
	SCALE_FACTOR="$${SF}" docker compose -f ../infrastructure/docker-compose.yml up -d 2>/dev/null || true
	@$(MAKE) wait-db

# ---------------------------------------------------------------------------
# Query Plans – Genera appendice con piani di esecuzione EXPLAIN/PROFILE
# ---------------------------------------------------------------------------
query-plans:
	@echo "Collecting Query Plans (EXPLAIN / PROFILE)..."
	python3 collect_query_plans.py
