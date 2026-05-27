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
RAW_DATA_DIR="$RAW_DATA_DIR" python3 patch_headers.py
echo "Headers patched successfully!"

echo "---------------------------------------------"
echo "🟩 PHASE 2: BUILDING NEO4J GRAPH STORE 🟩"
echo "---------------------------------------------"
# Header path on host
HEADER_DIR_HOST="$(dirname "$RAW_DATA_DIR")/headers"

# Build arguments for neo4j-admin import
NODE_ARGS=""
REL_ARGS=""

# Use python to generate the arguments string to avoid shell escaping hell
ARGS=$(python3 -c "
import os
raw_dir = '$RAW_DATA_DIR'
header_dir = '/headers'
import_dir = '/import'

nodes = []
rels = []

for sub in ['dynamic', 'static']:
    d = os.path.join(raw_dir, sub)
    if not os.path.exists(d): continue
    for name in os.listdir(d):
        if '_' in name:
            label = name.split('_')[1].upper() # Simplified label
            if 'knows' in name.lower(): label = 'KNOWS'
            elif 'likes' in name.lower(): label = 'LIKES'
            elif 'hascreator' in name.lower(): label = 'HAS_CREATOR'
            elif 'hastag' in name.lower(): label = 'HAS_TAG'
            elif 'islocatedin' in name.lower(): label = 'IS_LOCATED_IN'
            elif 'containerof' in name.lower(): label = 'CONTAINER_OF'
            elif 'hasmember' in name.lower(): label = 'HAS_MEMBER'
            elif 'hasmoderator' in name.lower(): label = 'HAS_MODERATOR'
            elif 'hasinterest' in name.lower(): label = 'HAS_INTEREST'
            elif 'studyat' in name.lower(): label = 'STUDY_AT'
            elif 'workat' in name.lower(): label = 'WORK_AT'
            elif 'replyof' in name.lower(): label = 'REPLY_OF'
            elif 'ispartof' in name.lower(): label = 'IS_PART_OF'
            elif 'hastype' in name.lower(): label = 'HAS_TYPE'
            elif 'issubclassof' in name.lower(): label = 'IS_SUBCLASS_OF'
            
            rels.append(f'--relationships={label}={header_dir}/{name}-header.csv,{import_dir}/{sub}/{name}/.*\.csv')
        else:
            label = name
            nodes.append(f'--nodes={label}={header_dir}/{name}-header.csv,{import_dir}/{sub}/{name}/.*\.csv')

print(' '.join(nodes + rels))
")

docker run --rm \
  -v "$NEO4J_TARGET_DIR":/data \
  -v "$RAW_DATA_DIR":/import \
  -v "$HEADER_DIR_HOST":/headers \
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
  $ARGS

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