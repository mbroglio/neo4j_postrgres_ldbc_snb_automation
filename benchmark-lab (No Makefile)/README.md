# Reproducing the LDBC SNB dual-database Benchmarking Lab

This guide explains how to set up the Database Benchmarking Lab from scratch on a completely fresh Ubuntu 26.04 machine. It covers generating the synthetic dataset, setting up the required repositories, and automatically building isolated Neo4j and PostgreSQL databases fairly using the exact same source data.

## 1. Prerequisites (From Zero)

Ensure the Ubuntu machine has the following installed. Open a terminal and run:

```bash
# 1. Update system and install core tools
sudo apt update
sudo apt install docker.io git python3 python3-pip -y

# 2. Start and enable Docker
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
# (Note: Log out and log back in here so Docker can run without 'sudo')

# 3. Install the specific Postgres-Python adapter required by the LDBC scripts
pip3 install psycopg psycopg-binary --break-system-packages
```

## 2. Generate the Raw Dataset (Changing Scale Factors)

The Social Network Benchmark (SNB) dataset is generated using the official LDBC Datagen Docker image.

**To change the Graph Size (Scale Factor):**
The Scale Factor (SF) determines the size of the generated graph. Modify the `--scale-factor` flag in the command below.

* `SF0.1`: ~100 MB of raw CSVs (Good for testing)
* `SF1`: ~1 GB of raw CSVs (Requires at least 8GB memory flag)
* `SF10`: ~10 GB of raw CSVs
* `SF100`: ~100 GB of raw CSVs

Run this command to generate your data. *(Change `out-sf0.1` and `--scale-factor 0.1` to your desired size!)*

```bash
docker run --rm -v $(pwd)/out-sf0.1:/out ldbc/datagen-standalone:latest \
  --memory 8g -- \
  --scale-factor 0.1 \
  --mode raw \
  --format csv \
  --explode-edges \
  --output-dir /out

# Crucial: Give ownership of the generated files back to your local user
sudo chown -R $USER:$USER $(pwd)/out-sf0.1

```

## 3. Clone Required Repositories

We need the official LDBC PostgreSQL implementation scripts, as they contain the table schemas and bulk loading architecture.

```bash
cd ~
git clone [https://github.com/ldbc/ldbc_snb_interactive_impls.git](https://github.com/ldbc/ldbc_snb_interactive_impls.git)

```

## 4. Setup the Benchmarking Lab

1. Create a directory for your lab (e.g., `mkdir ~/benchmark-lab` and `cd ~/benchmark-lab`).
2. Ensure you have `docker-compose.yml`, `build-databases.sh` and `postgres_prep.py` files in this directory.

### Adjusting Paths for Different Scale Factors

The `build-databases.sh` script is entirely self-contained. If you generated an `SF1` dataset instead of `SF0.1`, open `build-databases.sh` in a text editor and simply change the variables at the very top:

```bash
# CHANGE THESE THREE LINES TO MATCH YOUR SCALE FACTOR FOLDER
RAW_DATA_DIR="$HOME/out-sf1/graphs/csv/raw/composite-projected-fk"
NEO4J_TARGET_DIR="$LAB_DIR/data/neo4j-sf1"
POSTGRES_TARGET_DIR="$LAB_DIR/data/postgres-sf1"

```

## 5. Build the Databases

Run the master automation script. This script handles everything:

1. **Neo4j Prep:** Dynamically patches the CSV headers for Neo4j's strict `:START_ID` / `:END_ID` requirements (zero-memory overhead).
2. **Neo4j Build:** Spins up an ephemeral container to ingest the graph using `neo4j-admin database import full`.
3. **Postgres Prep:** Merges the distributed Spark chunks (`part-*.csv`) into single files and filters out BI-specific columns (`deletionDate`, `explicitlyDeleted`) that crash the interactive schema.
4. **Postgres Build:** Spins up the LDBC container to strictly enforce the relational schema, pipe the formatted CSVs, and build the indexes.

```bash
cd ~/benchmark-lab
./build-databases.sh

```

## 6. Accessing the Databases

Once the script completes successfully (`✅ BUILD COMPLETE!`), your pre-built, fully optimized database files are stored safely inside the `~/benchmark-lab/data/` directory.

You can now start them via your `docker-compose.yml` file to begin executing your benchmark queries in a strictly constrained, containerized environment!

**To Benchmark Neo4j:**

```bash
docker compose up neo4j-sf01
```

**To Benchmark PostgreSQL:**

```bash
docker compose up postgres-sf01
```

Connect, via DBeaver, using the following credentials:
- JDBC URL: jdbc:postgresql://localhost:5432/ldbcsnb
- Username: postgres
- Password: password

## 7. Close the Lab

Delete data and stop containers when done:

```bash
docker compose down
rm -r ~/benchmark-lab/data
```