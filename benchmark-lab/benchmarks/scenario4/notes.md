# Scenario 4 – I Punti Deboli (Quando NON usare Neo4j)

> **Note:** Questo file contiene le note narrative e l'analisi dei risultati dello Scenario 4.
> I dati grezzi si trovano in `results.json` nella stessa cartella.

---

## Obiettivo

Analizzare i limiti strutturali del paradigma graph-native, identificando i contesti in cui
l'architettura tabellare relazionale risulta nettamente superiore. In ottica ingegneristica,
ogni tecnologia è ottimale per una classe specifica di problemi — e Neo4j non fa eccezione.

---

## 4.1 – Full-Table Scan e Aggregazioni Globali

### Query

Calcolo della lunghezza media dei testi di tutti i Post, raggruppati per browser utilizzato.
La query è **puramente statistica**, disconnessa dalla topologia del grafo.

**PostgreSQL:**
```sql
SELECT m_browserused AS browser,
       AVG(LENGTH(m_content::text)) AS avg_len,
       COUNT(*)                     AS cnt
FROM message
WHERE m_browserused IS NOT NULL
  AND m_content IS NOT NULL
  AND m_type = 'Post'
GROUP BY m_browserused
ORDER BY m_browserused;
```

**Neo4j (Cypher):**
```cypher
MATCH (m:Post)
WHERE m.browserUsed IS NOT NULL AND m.content IS NOT NULL
RETURN m.browserUsed AS browser,
       avg(size(m.content)) AS avg_len,
       count(*) AS cnt
ORDER BY browser
```

### Struttura del test

- **N_RUNS = 10** ripetizioni per raccogliere statistiche stabili.
- **N_WARMUP = 3** esecuzioni di warm-up per popolare le page cache di entrambi i sistemi.
- **Metriche:** media, mediana, P90, min, max (in ms).

### Analisi

PostgreSQL esegue un **Sequential Scan parallelo** sui blocchi di dati contigui sul disco,
completando l'aggregazione di massa in frazioni di secondo. Il motore SQL sfrutta:
- lettura sequenziale a blocchi da disco (prefetching efficiente)
- hash aggregation sui valori di `browserUsed`
- elaborazione vettoriale delle colonne `content`

Neo4j evidenzia tempi di risposta significativamente più alti. Non disponendo di un motore
colonnare o di strutture sequenziali piatte, il sistema deve scorrere i nodi **sparsi** nel
supporto di memorizzazione per estrarre le proprietà, generando continui fenomeni di
**cache miss**. Ogni nodo `:Post` occupa una posizione arbitraria nello store dei nodi; la
scansione sequenziale produce un pattern di accesso casuale ai blocchi di I/O che vanifica
la prefetch pipeline.

### Grafici

- `aggregation_plot.svg` – Boxplot affiancati Neo4j vs PostgreSQL (latenze con whisker
  min-max). Visivamente evidenzia la differenza strutturale senza ambiguità numerica.

---

## 4.2 – Inserimento Massivo di Dati Disconnessi (Bulk Insert)

### Configurazione

- **Payload:** 50.000 record sintetici (`BenchmarkRecord`) con 4 proprietà scalari
  (id, name, score, created_at) e **nessuna relazione** — anagrafica piatta.
- **PostgreSQL:** comando nativo `COPY` da buffer `io.StringIO` in-memory
  (bypass del parser SQL, massima efficienza di ingestione).
- **Neo4j:** `UNWIND $batch AS row CREATE (r:BenchmarkRecord {...})` con batch da
  1.000 record — nessuna creazione di archi.
- I record vengono **rimossi** al termine del test (no-pollution del dataset LDBC).
- **N_RUNS = 10** ripetizioni complete (insert + cleanup tra una run e l'altra).

### Analisi

PostgreSQL sfrutta l'efficienza del `COPY` nativo: il parser viene bypassato, i dati
vengono trasferiti direttamente nel motore di storage tramite il protocollo binario
interno. L'operazione è dominata dall'I/O sequenziale in append sulle page heap.

Neo4j soffre l'**overhead computazionale legato all'architettura a grafo**: anche in
assenza di archi logici attivi, il motore alloca le strutture di puntatori per ogni
nodo creato (slot nell'`NodeStore`, header del record, pointer verso il `PropertyStore`).
L'overhead non è banale: ogni nodo nel format di Neo4j occupa 15 byte fissi nello store
principale + proprietà separate, contro i soli byte di dati effettivi della tupla PostgreSQL.

### Grafici

- `bulk_insert_plot.svg` – Due subplot affiancati:
  1. **Throughput** (record/s): barre comparative con annotazione dello speedup.
  2. **Latenza media** con whisker min-max.
  Il grafico ha valore visivo: mostra l'ordine di grandezza dello svantaggio di Neo4j
  su un workload per cui non è progettato.

---

## 4.3 – Esplosione Combinatoria nei Cammini Non Filtrati

### Configurazione

Il test opera sul **super-nodo** (il nodo `:Person` con grado massimo nel grafo LDBC SF 0.1).

**Query non filtrata (pericolosa):**
```cypher
MATCH (p:Person {id: $pid})-[*1..6]-(q)
RETURN count(DISTINCT q) AS cnt
```
Eseguita con timeout Python-side di 30 secondi. Su dataset a scala reale causa OOM.

**Query con filtri topologici (sicura):**
```cypher
MATCH (p:Person {id: $pid})-[:KNOWS*1..3]-(q:Person)
RETURN count(DISTINCT q) AS cnt
```
Con tipo di relazione esplicito e profondità ridotta a 3 hop.

### Analisi

L'assenza di filtri topologici su nodi ad alto **branching factor** provoca la crescita
esponenziale della frontiera di esplorazione: il numero di percorsi da materializzare
cresce come `grado^profondità`. Un super-nodo con grado 100 genera:
- a 3 hop: ~1.000.000 percorsi
- a 6 hop: ~10^12 percorsi (impraticabile)

Neo4j deve mantenere in RAM tutti i percorsi intermedi per deduplicare i nodi distinti.
Sui dataset a scala reale (milioni di nodi), questo comportamento porta rapidamente alla
**saturazione della RAM del server** (Out Of Memory) o al **timeout del query engine**.

I filtri topologici espliciti limitano la frontiera in modo drastico:
- `:KNOWS` riduce l'espansione alle sole relazioni di tipo dichiarato (no wildcard).
- `*1..3` dimezza i livelli di profondità, abbattendo esponenzialmente i percorsi.

> **NOTA su SF 0.1:** Il grafo LDBC SF 0.1 contiene ~1.700 nodi Person e ~18.135 archi
> KNOWS. Su questo dataset piccolo la query non filtrata potrebbe completare entro il
> timeout. L'obiettivo del test è misurare il **tempo relativo** tra le due varianti
> e documentare il comportamento qualitativo su dataset reali.

### Grafici

Questo sotto-test **non produce grafici**: il risultato è binario (timeout/OOM vs OK) e
leggibile direttamente dalla tabella testuale nel riepilogo finale. Un grafico a barre
con "timeout vs ~Xms" non aggiungerebbe comprensione rispetto al testo.

---

## Conclusioni

Lo Scenario 4 evidenzia tre classi di problemi in cui Neo4j è strutturalmente svantaggiato:

| Scenario | Vantaggio PostgreSQL | Causa strutturale Neo4j |
|---|---|---|
| Aggregazioni globali | Sequential Scan vettorializzato | Cache miss su nodi sparsi |
| Bulk Insert flat | COPY bypass-parser | Overhead puntatori grafo per ogni nodo |
| Cammini non filtrati | N/A (problema Neo4j-only) | Esplosione combinatoria frontiera |

La scelta del paradigma va sempre calibrata sulla **classe di interrogazione dominante**:
PostgreSQL è nettamente superiore su operazioni analitiche tabellari e ingestion massiva;
Neo4j eccelle nelle navigazioni topologiche profonde e nel pattern matching strutturale.

---

## File di Riferimento

- **Script benchmark:** [`scenario4_benchmark.py`](./scenario4_benchmark.py)
- **Script grafici:** [`plot_scenario4.py`](./plot_scenario4.py)
- **Dati grezzi:** [`results.json`](./results.json)
- **Dataset:** LDBC SNB SF 0.1 (~1.700 nodi Person, ~18.135 archi KNOWS)
- **Grafici prodotti:**
  - `aggregation_plot.svg` – confronto latenze Full-Table Scan
  - `bulk_insert_plot.svg` – confronto throughput Bulk Insert

## Esecuzione

```bash
# Dalla root del progetto:
make scenario4           # esegue il benchmark e salva results.json
make plot-scenario4      # genera i grafici SVG da results.json

# Oppure direttamente:
cd temp/scenario4
python3 scenario4_benchmark.py
python3 plot_scenario4.py
```
