#!/bin/bash
# archive_results.sh – Archivia i risultati di uno scenario nella cartella
# corretta in base allo Scale Factor rilevato automaticamente.
#
# Uso: ./archive_results.sh <scenario_dir>
#
# SF detection:
#   - Prima tenta di interrogare il container single-instance 'neo4j-benchmark'
#   - Se non disponibile (es. scenario3 che usa il cluster), interroga neo4j-core1
#   - Se nessuno risponde, usa SF0.1 come default (con warning)

SCENARIO=$1
if [ -z "$SCENARIO" ]; then
    echo "Uso: $0 <scenario_dir>"
    exit 1
fi

# Detect scale factor – prova prima il container single-instance, poi il cluster
COUNT=""
if docker inspect neo4j-benchmark > /dev/null 2>&1; then
    COUNT=$(docker exec neo4j-benchmark cypher-shell -u neo4j -p password \
        "MATCH (p:Person) RETURN count(p);" 2>/dev/null \
        | grep -E '^[0-9]+$' | head -1)
fi

if [ -z "$COUNT" ] || [ "$COUNT" = "0" ]; then
    # Prova il cluster (scenario3)
    COUNT=$(docker exec neo4j-core1 cypher-shell -u neo4j -p password \
        "MATCH (p:Person) RETURN count(p);" 2>/dev/null \
        | grep -E '^[0-9]+$' | head -1)
fi

if [ -z "$COUNT" ]; then
    echo "[WARN] archive_results.sh: nessun database raggiungibile – uso SF0.1 come default"
    COUNT=0
fi

if [ "$COUNT" -gt 5000 ]; then
    SCALE="SF1"
else
    SCALE="SF0.1"
fi

echo "Archiving results for $SCENARIO as $SCALE (${COUNT} Person)..."
ARCHIVE_DIR="$SCENARIO/archive_$SCALE"
mkdir -p "$ARCHIVE_DIR"

if [ -f "$SCENARIO/results.json" ]; then
    mv "$SCENARIO/results.json" "$ARCHIVE_DIR/results.json"
    echo "  Spostato: results.json"
fi

for svg in "$SCENARIO"/*.svg; do
    if [ -f "$svg" ]; then
        mv "$svg" "$ARCHIVE_DIR/"
        echo "  Spostato: $(basename "$svg")"
    fi
done

for txt in "$SCENARIO"/*.txt; do
    if [ -f "$txt" ]; then
        mv "$txt" "$ARCHIVE_DIR/"
        echo "  Spostato: $(basename "$txt")"
    fi
done

echo "Saved and cleaned up $SCENARIO -> $ARCHIVE_DIR"
