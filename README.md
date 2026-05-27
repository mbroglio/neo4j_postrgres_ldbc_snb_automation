# Reproducing the LDBC SNB dual-database Benchmarking Lab

This guide explains how to set up the Database Benchmarking Lab from scratch on a completely fresh Ubuntu 26.04 machine. It covers generating the synthetic dataset, setting up the required repositories, and automatically building isolated Neo4j and PostgreSQL databases fairly using the exact same source data.

## 1. Automated Lab Workflow (Using Makefile)

The entire lab is now automated via a `Makefile`. This standardizes the build process and guarantees a reproducible, clean-slate environment for every benchmark.

Navigate to the benchmark lab directory:
```bash
cd ~/benchmark-lab/infrastructure
```

### Install Prerequisites
Run this once on a fresh machine to install Python, Docker, and the necessary Postgres adapters:
```bash
make setup
```

### Generate Data
Generate the raw LDBC SNB dataset. The Scale Factor (SF) determines the size of the generated graph (e.g., SF0.1 = ~100MB, SF1 = ~1GB). 

```bash
# Generates SF0.1 by default
make generate-data

# To generate a different scale factor, e.g., SF1
make generate-data SCALE_FACTOR=1
```

### Build the Databases
This step patches CSV headers for Neo4j, merges chunks for Postgres, and builds both database storage engines simultaneously using the scale factor you specify:

```bash
# Builds SF0.1 databases by default
make build

# To build for a different scale factor
make build SCALE_FACTOR=1
```

### Start the Lab
Spin up the optimized, isolated Docker containers for querying:

```bash
# Starts the databases
make up

# Starts the databases for a specific scale factor
make up SCALE_FACTOR=1
```

You can now connect and run your benchmarks!

### Connecting via GUI (DBeaver)
You can easily connect to both databases using a database tool like **DBeaver** to inspect the data or execute test queries:

**To Benchmark Neo4j:**
- **URL/Host:** `localhost`
- **Port:** `7687`
- **Username:** `neo4j`
- **Password:** `password`
*(Note: You can also access the Neo4j Browser at http://localhost:7474)*

**To Benchmark PostgreSQL:**
- **JDBC URL:** `jdbc:postgresql://localhost:5432/ldbcsf01`
- **Database:** `ldbcsnb`
- **Username:** `postgres`
- **Password:** `mysecretpassword`

### Teardown & Clean (Usa e Getta)
Once you are done benchmarking, ensure you tear down the environment to guarantee a clean slate for the next test.

```bash
# Stops containers and DESTROYS the optimized database files (clean slate)
make clean

# Same as above, but for a specific scale factor
make clean SCALE_FACTOR=1
```

*(Note: `make clean` keeps your generated raw CSV data. If you want to delete everything, including the raw data, run `make deep-clean`).*

## Advanced: The Reset Command
If you want to completely destroy the current database state, rebuild them from the raw files, and start them up again in one single command (ideal for automated CI/CD benchmark pipelines), just run:

```bash
make reset SCALE_FACTOR=0.1
```