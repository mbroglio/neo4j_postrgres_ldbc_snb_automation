#!/bin/bash

# ==============================================================================
# Script di Automazione - Scenario 4 (I Punti Deboli di Neo4j)
# ==============================================================================
# Questo script automatizza l'intera esecuzione dello Scenario 4:
#   1. Verifica che i container Neo4j e PostgreSQL standalone siano attivi
#   2. Esecuzione del benchmark Python (Test 4.1, 4.2, 4.3)
#   3. Generazione dei grafici SVG (4.1 e 4.2)
#
# Prerequisiti:
#   - docker compose up -d  (docker-compose.yml con neo4j-benchmark e postgres)
#   - Dataset LDBC SF 0.1 già caricato in entrambi i DB
#   - Python 3.10+ con psycopg2-binary e neo4j installati
#     (lo script li installa automaticamente se mancanti)
#
# Utilizzo:
#   bash benchmarks/scenario4/run_scenario4.sh [--skip-plots]
#   oppure dalla cartella dello scenario:
#   bash run_scenario4.sh [--skip-plots]
# ==============================================================================

set -e

# Posizionati sempre nella root del progetto (benchmark-lab)
cd "$(dirname "$0")/../../"

# ---------------------------------------------------------------------------
# Colori
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Argomenti
# ---------------------------------------------------------------------------
SKIP_PLOTS=false
for arg in "$@"; do
    case $arg in
        --skip-plots) SKIP_PLOTS=true ;;
    esac
done

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo -e "${GREEN}======================================================================"
echo -e "  SCENARIO 4 – I Punti Deboli (Quando NON usare Neo4j)"
echo -e "======================================================================${NC}"
echo -e "  Test 4.1 – Full-Table Scan e Aggregazioni Globali"
echo -e "  Test 4.2 – Inserimento Massivo di Dati Disconnessi (Bulk Insert)"
echo -e "  Test 4.3 – Esplosione Combinatoria nei Cammini Non Filtrati"
echo -e "${GREEN}======================================================================${NC}\n"

# ---------------------------------------------------------------------------
# [1/3] Verifica container attivi
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[1/3] Verifica disponibilità dei database...${NC}"

NEO4J_CONTAINER="neo4j-benchmark"
POSTGRES_CONTAINER="postgres-benchmark"

if ! docker ps --format '{{.Names}}' | grep -q "^${NEO4J_CONTAINER}$"; then
    echo -e "${RED}[ERR] Container '${NEO4J_CONTAINER}' non trovato o non in esecuzione.${NC}"
    echo -e "      Avvia l'ambiente con:  docker compose up -d"
    exit 1
fi
echo -e "  ${GREEN}[OK]${NC} Neo4j  → container '${NEO4J_CONTAINER}' attivo"

if ! docker ps --format '{{.Names}}' | grep -q "^${POSTGRES_CONTAINER}$"; then
    echo -e "${RED}[ERR] Container '${POSTGRES_CONTAINER}' non trovato o non in esecuzione.${NC}"
    echo -e "      Avvia l'ambiente con:  docker compose up -d"
    exit 1
fi
echo -e "  ${GREEN}[OK]${NC} PostgreSQL → container '${POSTGRES_CONTAINER}' attivo"

# Attesa che Neo4j sia pronto a rispondere (bolt)
echo -e "\n  Attesa che Neo4j sia pronto..."
MAX_RETRIES=20
RETRY_COUNT=0
until docker exec "${NEO4J_CONTAINER}" cypher-shell -u neo4j -p password "RETURN 1" >/dev/null 2>&1; do
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo -e "\n${RED}[ERR] Neo4j non risponde dopo $((MAX_RETRIES * 3))s.${NC}"
        exit 1
    fi
    printf "."
    sleep 3
    RETRY_COUNT=$((RETRY_COUNT + 1))
done
echo -e "\n  ${GREEN}[OK]${NC} Neo4j risponde e accetta connessioni Bolt"

# Verifica minima dataset (almeno qualche nodo Person)
PERSON_COUNT=$(docker exec "${NEO4J_CONTAINER}" \
    cypher-shell -u neo4j -p password "MATCH (p:Person) RETURN count(p) AS n" \
    --format plain 2>/dev/null | tail -1)

if [ -z "$PERSON_COUNT" ] || [ "$PERSON_COUNT" -eq 0 ] 2>/dev/null; then
    echo -e "${RED}[ERR] Nessun nodo Person trovato in Neo4j.${NC}"
    echo -e "      Il dataset LDBC SF 0.1 non risulta caricato."
    echo -e "      Esegui prima:  make build && make up"
    exit 1
fi
echo -e "  ${GREEN}[OK]${NC} Dataset Neo4j: ${PERSON_COUNT} nodi Person trovati"

# ---------------------------------------------------------------------------
# [2/3] Esecuzione benchmark
# ---------------------------------------------------------------------------
echo -e "\n${YELLOW}[2/3] Avvio benchmark Scenario 4...${NC}"
echo -e "  ${CYAN}Output testuale: benchmarks/scenario4/benchmark_output.txt${NC}\n"

BENCHMARK_SCRIPT="benchmarks/scenario4/scenario4_benchmark.py"
OUTPUT_FILE="benchmarks/scenario4/benchmark_output.txt"

python3 "${BENCHMARK_SCRIPT}" 2>&1 | tee "${OUTPUT_FILE}"
BENCHMARK_EXIT=${PIPESTATUS[0]}

if [ $BENCHMARK_EXIT -ne 0 ]; then
    echo -e "\n${RED}[ERR] Il benchmark è terminato con errore (exit code ${BENCHMARK_EXIT}).${NC}"
    echo -e "      Controlla il log: ${OUTPUT_FILE}"
    exit $BENCHMARK_EXIT
fi

echo -e "\n${GREEN}[OK] Benchmark completato. Risultati in: benchmarks/scenario4/results.json${NC}"

# ---------------------------------------------------------------------------
# [3/3] Generazione grafici
# ---------------------------------------------------------------------------
if [ "$SKIP_PLOTS" = true ]; then
    echo -e "\n${YELLOW}[3/3] Generazione grafici saltata (--skip-plots).${NC}"
else
    echo -e "\n${YELLOW}[3/3] Generazione grafici SVG...${NC}"
    PLOT_SCRIPT="benchmarks/scenario4/plot_scenario4.py"

    python3 "${PLOT_SCRIPT}" 2>&1 | tee benchmarks/scenario4/plot_output.txt
    PLOT_EXIT=${PIPESTATUS[0]}

    if [ $PLOT_EXIT -ne 0 ]; then
        echo -e "\n${YELLOW}[WARN] Generazione grafici fallita (exit code ${PLOT_EXIT}).${NC}"
        echo -e "       Il benchmark è comunque completato. Riesegui plot_scenario4.py manualmente."
    else
        echo -e "${GREEN}[OK] Grafici generati.${NC}"
    fi
fi

# ---------------------------------------------------------------------------
# Riepilogo finale
# ---------------------------------------------------------------------------
echo -e "\n${GREEN}======================================================================"
echo -e "  SCENARIO 4 COMPLETATO"
echo -e "======================================================================${NC}"
echo -e "  I risultati sono disponibili in:"
echo -e "    ${CYAN}benchmarks/scenario4/results.json${NC}           ← dati grezzi (JSON)"
echo -e "    ${CYAN}benchmarks/scenario4/benchmark_output.txt${NC}   ← log testuale console"
if [ "$SKIP_PLOTS" = false ]; then
    echo -e "    ${CYAN}benchmarks/scenario4/aggregation_plot.svg${NC}   ← grafico 4.1 (aggregazione)"
    echo -e "    ${CYAN}benchmarks/scenario4/bulk_insert_plot.svg${NC}   ← grafico 4.2 (bulk insert)"
fi
echo -e "\n  Per rieseguire solo i grafici:"
echo -e "    ${YELLOW}python3 benchmarks/scenario4/plot_scenario4.py${NC}\n"
