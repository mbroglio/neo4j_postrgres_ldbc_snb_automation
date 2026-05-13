#!/bin/bash
set -e

echo "============================================="
echo "🚀 STARTING MASTER DATABASE BUILDER (SF0.1) 🚀"
echo "============================================="

# Define paths
LAB_DIR=$(pwd)
SCALE_FACTOR="${SCALE_FACTOR:-0.1}"
export RAW_DATA_DIR="$LAB_DIR/out-sf${SCALE_FACTOR}/graphs/csv/raw/composite-projected-fk"
NEO4J_TARGET_DIR="$LAB_DIR/data/neo4j-sf${SCALE_FACTOR}"
POSTGRES_TARGET_DIR="$LAB_DIR/data/postgres-sf${SCALE_FACTOR}"
LDBC_PG_SCRIPTS="$LAB_DIR/ldbc_snb_interactive_impls/postgres/scripts"

# Create target directories
mkdir -p "$NEO4J_TARGET_DIR"
mkdir -p "$POSTGRES_TARGET_DIR"

echo "---------------------------------------------"
echo "🔧 PHASE 1: PREPPING CSV HEADERS FOR NEO4J 🔧"
echo "---------------------------------------------"
python3 -c "
import os, glob, subprocess
raw_dir = '$RAW_DATA_DIR'
for filepath in glob.glob(raw_dir + '/**/*.csv', recursive=True):
    with open(filepath, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()
    
    if not first_line: continue
    
    headers = first_line.split('|')
    folder_name = os.path.basename(os.path.dirname(filepath))
    is_edge = '_' in folder_name
    
    start_found = False
    for i, h in enumerate(headers):
        if not is_edge and h.lower() == 'id':
            headers[i] = ':ID'
        elif is_edge and h.endswith('Id'):
            if not start_found:
                headers[i] = ':START_ID'
                start_found = True
            else:
                headers[i] = ':END_ID'
                
    new_header = '|'.join(headers)
    if first_line != new_header:
        # Uses Linux native tools to replace the header with zero memory overhead
        subprocess.run(['sed', '-i', f'1s/.*/{new_header}/', filepath])
"
echo "Headers patched successfully!"

echo "---------------------------------------------"
echo "🟩 PHASE 2: BUILDING NEO4J GRAPH STORE 🟩"
echo "---------------------------------------------"
docker run --rm \
  -v "$NEO4J_TARGET_DIR":/data \
  -v "$RAW_DATA_DIR":/import \
  -e NEO4J_server_memory_heap_max__size=1G \
  neo4j:5.20.0-community \
  neo4j-admin database import full neo4j \
  --delimiter='|' \
  --array-delimiter=';' \
  --multiline-fields=true \
  --overwrite-destination=true \
  --skip-duplicate-nodes=true \
  --skip-bad-relationships=true \
  --bad-tolerance=10000000 \
  --nodes=Person="/import/dynamic/Person/.*\.csv" \
  --nodes=Post="/import/dynamic/Post/.*\.csv" \
  --nodes=Comment="/import/dynamic/Comment/.*\.csv" \
  --nodes=Forum="/import/dynamic/Forum/.*\.csv" \
  --nodes=Tag="/import/static/Tag/.*\.csv" \
  --nodes=TagClass="/import/static/TagClass/.*\.csv" \
  --nodes=Organisation="/import/static/Organisation/.*\.csv" \
  --nodes=Place="/import/static/Place/.*\.csv" \
  --relationships=KNOWS="/import/dynamic/Person_knows_Person/.*\.csv" \
  --relationships=LIKES="/import/dynamic/Person_likes_Post/.*\.csv" \
  --relationships=HAS_CREATOR="/import/dynamic/Post_hasCreator_Person/.*\.csv" \
  --relationships=HAS_TAG="/import/dynamic/Post_hasTag_Tag/.*\.csv" \
  --relationships=IS_LOCATED_IN="/import/dynamic/Person_isLocatedIn_City/.*\.csv"

echo "---------------------------------------------"
echo "🐘 PHASE 3: BUILDING POSTGRESQL STORE 🐘"
echo "---------------------------------------------"
PG_CSV_DIR="$LAB_DIR/data/postgres-csv-formatted"
mkdir -p "$PG_CSV_DIR"

echo "Merging CSVs, mapping foreign keys, and matching PostgreSQL expected formats using pandas..."
python3 postgres_prep.py
echo "PostgreSQL CSVs successfully filtered and formatted!"

cd "$LDBC_PG_SCRIPTS"

# Reset vars.sh to its original state just in case
git checkout -- vars.sh

# Forcefully append our NEW merged directory path
echo "export POSTGRES_CSV_DIR=\"$PG_CSV_DIR/\"" >> vars.sh
echo "export POSTGRES_DATA_DIR=\"$POSTGRES_TARGET_DIR\"" >> vars.sh

# Run the official LDBC tools
./start.sh
./load-in-one-step.sh
./stop.sh

echo "============================================="
echo "✅ BUILD COMPLETE! YOUR LAB IS READY. ✅"
echo "============================================="
cd "$LAB_DIR"