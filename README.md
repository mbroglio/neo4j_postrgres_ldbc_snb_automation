# Graph Databases & Neo4j Analysis (LDBC SNB)

Questo repository contiene un laboratorio completo per eseguire benchmark, testare e analizzare le differenze di performance tra un database a grafo nativo (**Neo4j**) e un database relazionale tradizionale (**PostgreSQL**). Le valutazioni vengono effettuate utilizzando i dataset e le query del celebre [LDBC Social Network Benchmark (SNB)](https://ldbcouncil.org/benchmarks/snb/).

## Obiettivo del Progetto

Lo scopo principale di questa analisi è misurare le prestazioni e l'efficienza nell'esecuzione di query complesse e traversal su strutture a grafo, confrontando:
- L'approccio *Index-Free Adjacency* e l'espressività del linguaggio **Cypher** di Neo4j.
- Le classiche join relazionali, le Common Table Expressions (CTE) e l'ottimizzazione tramite indici in **SQL** su PostgreSQL.

## Struttura del Repository

L'intero codice per l'infrastruttura e le analisi si trova all'interno della cartella `benchmark-lab`, suddivisa nelle seguenti sezioni:

- 📊 **`benchmark-lab/benchmarks/`**: Contiene il core dell'analisi. Troverai gli script Python per eseguire la suite di test (es. `system_profiler.py`, `collect_query_plans.py`), organizzati in vari scenari di carico, oltre ai Makefile necessari per automatizzare l'estrazione delle metriche (tempi di esecuzione, utilizzo RAM/CPU, execution plan).
- 🏗️ **`benchmark-lab/infrastructure/`**: Include l'Infrastruttura as Code (Docker Compose), gli script per sistemare i CSV e automatizzare il setup, la generazione dei dati grezzi e l'importazione nei due database per garantire un ambiente sempre pulito e riproducibile.
- ⚙️ **`benchmark-lab (No Makefile)/`**: Fornisce una versione puramente bash (senza l'orchestratore `make`) degli script di importazione e configurazione dei container.

> 💡 **Nota**: Gli script automatizzati per il setup e la generazione dell'infrastruttura (con e senza Makefile) sono stati estrapolati anche in un **repository separato standalone**, pensato esclusivamente per l'automazione dei database: [neo4j_postgresql_ldbc_snb_automation](https://github.com/mbroglio/neo4j_postgresql_ldbc_snb_automation).

## Come riprodurre i Benchmark

Il flusso di test è stato completamente automatizzato per standardizzare le procedure su qualsiasi macchina Ubuntu.

### 1. Inizializzazione dell'ambiente
Spostati nella cartella infrastrutturale, installa le dipendenze, genera il dataset e inizializza i database. (Il parametro `SF` indica lo Scale Factor, es. 0.1, 1, 10).
```bash
cd benchmark-lab/infrastructure
make setup
make generate SF=0.1
make build SF=0.1
make up SF=0.1
```

### 2. Avvio dei Test e Analisi
Una volta che i container di Neo4j e PostgreSQL sono pronti, recati nella cartella dei benchmark per lanciare la profilazione delle query e raccogliere i risultati.
```bash
cd ../benchmarks

# Esegui la profilazione del sistema e delle query
python3 system_profiler.py
# (Oppure utilizza i comandi previsti nel Makefile interno)
```

### 3. Teardown
Dopo i test, per garantire un ambiente "pulito" (clean-slate) per la sessione successiva, puoi distruggere l'ambiente. I CSV originali verranno mantenuti.
```bash
cd ../infrastructure
make clean
```

## Connessione Manuale ai Database

Se desideri collegarti manualmente per esplorare lo schema o testare le query (tramite CLI o UI come DBeaver):
- **Neo4j**: 
  - **URL**: `localhost:7687` (Interfaccia web: `http://localhost:7474`)
  - **Credenziali**: `neo4j` / `password`
- **PostgreSQL**: 
  - **JDBC URL**: `jdbc:postgresql://localhost:5432/ldbcsf01`
  - **Database**: `ldbcsnb`
  - **Credenziali**: `postgres` / `mysecretpassword`