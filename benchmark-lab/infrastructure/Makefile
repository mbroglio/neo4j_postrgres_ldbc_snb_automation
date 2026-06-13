# =============================================================================
# infrastructure/Makefile – Gestione dataset e container
# =============================================================================
#
# UTILIZZO (dalla cartella infrastructure/):
#
#   Generazione dati grezzi (CSV):
#     make generate SF=0.1      – genera dataset SF 0.1 (~30s, ~500MB)
#     make generate SF=1        – genera dataset SF 1   (~30min, ~3GB RAM)
#
#   Build database (importa CSV in Neo4j e PostgreSQL):
#     make build SF=0.1         – carica SF 0.1 nei DB (prerequisito: generate)
#     make build SF=1           – carica SF 1 nei DB   (prerequisito: generate)
#
#   Avvio/stop container:
#     make up SF=0.1            – avvia i container con il volume SF 0.1
#     make up SF=1              – avvia i container con il volume SF 1
#     make down                 – ferma e rimuove i container (mantiene i dati)
#
#   Scorciatoie:
#     make reset SF=0.1         – down + build + up per SF 0.1  (ferma prima i container)
#     make reset SF=1           – down + build + up per SF 1   (ferma prima i container)
#
#   Prima installazione:
#     make setup                – installa dipendenze di sistema
#
# NOTA: generate genera i CSV grezzi (una volta sola per SF).
#       build li importa nei volumi Docker (deve essere ripetuto se si cambia SF
#       o se si eseguono deep-clean). up riavvia i container senza re-importare.
# =============================================================================

.PHONY: help setup generate build up down clean deep-clean reset

# ---------------------------------------------------------------------------
# Variabili – override con SF=<valore> da riga di comando
# ---------------------------------------------------------------------------
SF           ?= 0.1
DATA_DIR      = out-sf$(SF)
LAB_DATA_DIR  = data

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
help:
	@echo ""
	@echo "============================================================"
	@echo "  infrastructure/Makefile – Gestione dataset e container"
	@echo "============================================================"
	@echo ""
	@echo "  make generate SF=0.1   Genera CSV grezzi per SF 0.1 (~500MB)"
	@echo "  make generate SF=1     Genera CSV grezzi per SF 1   (~3GB, ~30min)"
	@echo ""
	@echo "  make build SF=0.1      Importa SF 0.1 in Neo4j e PostgreSQL"
	@echo "  make build SF=1        Importa SF 1 in Neo4j e PostgreSQL"
	@echo ""
	@echo "  make up SF=0.1         Avvia container con volume SF 0.1"
	@echo "  make up SF=1           Avvia container con volume SF 1"
	@echo "  make down              Ferma i container (mantiene i dati)"
	@echo ""
	@echo "  make reset SF=0.1      Equivale a: down + build + up per SF 0.1"
	@echo "  make reset SF=1        Equivale a: down + build + up per SF 1"
	@echo ""
	@echo "  make setup             Installa dipendenze di sistema"
	@echo ""
	@echo "  Flusso prima esecuzione:"
	@echo "    make setup"
	@echo "    make generate SF=0.1"
	@echo "    make generate SF=1      (opzionale, richiede ~30min)"
	@echo "    make reset SF=1         (build + up con SF 1)"
	@echo "    # → esegui scenari dal benchmarks/Makefile"
	@echo "    make reset SF=0.1       (build + up con SF 0.1)"
	@echo "    # → esegui di nuovo gli scenari"
	@echo ""

# ---------------------------------------------------------------------------
# Setup – installazione dipendenze di sistema (una volta sola)
# ---------------------------------------------------------------------------
setup:
	@echo "Installing system dependencies..."
	sudo apt update && sudo apt install -y docker.io docker-compose-v2 git python3 python3-pip
	sudo apt install -y python3-pandas
	pip3 install --user psycopg || pip3 install --user psycopg --break-system-packages
	sudo systemctl enable --now docker
	sudo usermod -aG docker $$USER
	@if [ ! -d "ldbc_snb_interactive_impls" ]; then \
		echo "Cloning ldbc_snb_interactive_impls..."; \
		git clone https://github.com/ldbc/ldbc_snb_interactive_impls.git; \
	fi

# ---------------------------------------------------------------------------
# generate – generazione CSV grezzi LDBC SNB
# I file vengono salvati in out-sf$(SF)/ e NON vengono sovrascritti se
# la cartella esiste già. Cancella manualmente out-sf$(SF)/ per rigenerare.
# ---------------------------------------------------------------------------
generate:
	@echo "============================================================"
	@echo "  Generazione dataset SF $(SF)"
	@echo "============================================================"
	@if [ -d "$(DATA_DIR)/graphs" ]; then \
		echo "[SKIP] $(DATA_DIR)/ già presente. Cancellalo per rigenerare."; \
	else \
		echo "Avvio generazione (può richiedere fino a 30min per SF 1)..."; \
		MEMORY=8g; \
		if [ "$(SF)" = "1" ]; then MEMORY=12g; fi; \
		docker run --rm -v $(PWD)/$(DATA_DIR):/out ldbc/datagen-standalone:latest \
			--memory $$MEMORY -y -- \
			--scale-factor $(SF) \
			--mode raw \
			--format csv \
			--explode-edges \
			--output-dir /out; \
		docker run --rm -v $(PWD):/app ubuntu chown -R 1000:1000 /app/$(DATA_DIR); \
		echo "[OK] Dataset SF $(SF) generato in $(PWD)/$(DATA_DIR)"; \
	fi

# ---------------------------------------------------------------------------
# build – importa i CSV nei volumi Docker (Neo4j + PostgreSQL)
# Prerequisito: make generate SF=$(SF) già completato
# ---------------------------------------------------------------------------
build:
	@echo "============================================================"
	@echo "  Build database SF $(SF)"
	@echo "============================================================"
	@if [ ! -d "$(DATA_DIR)/graphs" ]; then \
		echo "[ERR] Dataset non trovato: $(DATA_DIR)/graphs"; \
		echo "      Esegui prima: make generate SF=$(SF)"; \
		exit 1; \
	fi
	chmod +x build-databases.sh
	SCALE_FACTOR=$(SF) bash ./build-databases.sh

# ---------------------------------------------------------------------------
# up – avvia container single-instance con il volume dell'SF scelto
# Ferma prima eventuali container già attivi (qualunque SF stessero usando).
# ---------------------------------------------------------------------------
up:
	@echo "============================================================"
	@echo "  Avvio container SF $(SF)"
	@echo "============================================================"
	@echo "[1/4] Fermo container attualmente attivi (se presenti)..."
	-docker compose -f docker-compose.yml down 2>/dev/null || true
	@echo "[2/4] Avvio container con volume SF $(SF)..."
	SCALE_FACTOR=$(SF) docker compose -f docker-compose.yml up -d
	@echo "[3/4] Attendo che Neo4j sia pronto..."
	@until docker exec neo4j-benchmark cypher-shell -u neo4j -p password "RETURN 1" > /dev/null 2>&1; do \
		printf "."; sleep 2; \
	done
	@echo ""
	@echo "[4/4] Applico constraint e indici Neo4j..."
	cat neo4j_setup.cypher | docker exec -i neo4j-benchmark cypher-shell -u neo4j -p password
	@echo "[OK] Container pronti. Verifica contenuto:"
	@docker exec neo4j-benchmark cypher-shell -u neo4j -p password \
		"MATCH (p:Person) RETURN count(p) AS n_persons;" 2>/dev/null

# ---------------------------------------------------------------------------
# down – ferma e rimuove i container (i dati nei volumi rimangono)
# ---------------------------------------------------------------------------
down:
	@echo "Stopping databases..."
	-SCALE_FACTOR=$(SF) docker compose -f docker-compose.yml down 2>/dev/null || true

# ---------------------------------------------------------------------------
# clean – down + rimozione dei volumi Docker (dati DB cancellati)
# I CSV grezzi in out-sf$(SF)/ vengono mantenuti.
# ---------------------------------------------------------------------------
clean: down
	@echo "Removing Docker volumes for SF $(SF)..."
	docker run --rm -v $(PWD):/app ubuntu rm -rf /app/$(LAB_DATA_DIR)
	@echo "Done. I CSV grezzi in $(DATA_DIR)/ sono stati mantenuti."

# ---------------------------------------------------------------------------
# deep-clean – clean + cancellazione CSV grezzi
# ---------------------------------------------------------------------------
deep-clean: clean
	@echo "Removing raw CSV data for SF $(SF)..."
	docker run --rm -v $(PWD):/app ubuntu rm -rf /app/$(DATA_DIR)

# ---------------------------------------------------------------------------
# reset – down + build + up (workflow completo per cambiare SF)
# Ferma prima tutti i container (libera la porta 5432), poi importa i CSV
# e riavvia. I CSV grezzi devono esistere già (fai prima make generate SF=$(SF)).
# ---------------------------------------------------------------------------
reset: down build up
	@echo ""
	@echo "============================================================"
	@echo "  [OK] Ambiente pronto per SF $(SF)"
	@echo "============================================================"
