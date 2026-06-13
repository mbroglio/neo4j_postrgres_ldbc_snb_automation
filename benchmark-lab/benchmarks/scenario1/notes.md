# Scenario 1 – La Potenza del Grafo (Neo4j vs RDBMS)

> **Esecuzione:** 2026-06-13 — SF 0.1 e SF 1 — N_RUNS=50 — Warmup=20
> I dati grezzi completi si trovano in [`results.json`](./results.json).

---

## Ambiente di Esecuzione

| Componente | Versione / Configurazione |
|---|---|
| Neo4j | 5.20.0-community (Docker) |
| PostgreSQL | 14.4 (Docker) |
| Heap Neo4j | 6 GB init + max |
| Page Cache Neo4j | 2 GB |
| CPU limit | 4 vCPU per container |
| RAM limit | 8 GB per container |
| Dataset | LDBC SNB SF 0.1 |
| Person | 1.700 nodi |
| Relazioni KNOWS | ~9.067 (bidirezionali) |

---

## 1.1 – Query Multi-Hop (Analisi di Profondità)

**Query Neo4j (Cypher):**
```cypher
MATCH (p:Person {id: $pid})-[:KNOWS*1..N]-(friend:Person)
RETURN count(DISTINCT friend) AS cnt
```

**Query PostgreSQL (CTE ricorsiva):**
```sql
WITH RECURSIVE friends(person_id, depth) AS (
    SELECT k_person2id, 1 FROM knows WHERE k_person1id = $pid
    UNION SELECT k_person1id, 1 FROM knows WHERE k_person2id = $pid
    UNION ALL
    SELECT <vicino>, f.depth + 1
    FROM knows k JOIN friends f ON ...
    WHERE f.depth < N
)
SELECT COUNT(DISTINCT person_id) FROM friends WHERE person_id != $pid
```

### Risultati

| Hop | Person ID | Neo4j Media (ms) | Neo4j P90 (ms) | PG Media (ms) | PG P90 (ms) | Speedup Neo4j vs PG |
|-----|-----------|:---------------:|:--------------:|:-------------:|:-----------:|:-------------------:|
| 1   | 28587302323389 | 0.758 | 1.177 | **0.325** | 0.577 | 0.43× (PG vince) |
| 2   | 4398046511577  | **1.139** | 2.164 | 1.483 | 1.918 | **1.30×** |
| 3   | 983            | **3.627** | 8.750 | 45.901 | 49.085 | **12.66×** ✅ |
| 4   | 35184372088910 | **3.383** | 4.649 | 604.881 | 608.097 | **178.80×** ✅ |

> **Nota sul Join Pain (hop 4):** La CTE ricorsiva SQL cresce combinatorialmente con la profondità. A 4 hop la query ha richiesto in media ~604 millisecondi su un grafo di soli 1.700 nodi. Questo dimostra empiricamente il *Join Pain* e l'esplosione combinatoria tipica del modello relazionale.

### Analisi

Il punto di inflessione critico avviene **tra 2 e 3 hop**:
- **1 hop**: PostgreSQL è più veloce (semplice lookup indicizzato in cache vs overhead Bolt di Neo4j)
- **2 hop**: vantaggio (~1.3× Neo4j) — il doppio join è ancora gestibile per PostgreSQL.
- **3 hop**: **12.6× a favore di Neo4j** — la CTE inizia a mostrare gravi segni di rallentamento rispetto all'esplorazione grafo.
- **4 hop**: **178.8× a favore di Neo4j** — l'esplosione combinatoria si manifesta: PG impiega ~604 ms, mentre Neo4j resta invariato a ~3.3 ms.

Questo conferma empiricamente il teorema del *Join Pain*: ogni hop aggiuntivo aumenta esponenzialmente il costo dell'auto-join relazionale, mentre Neo4j naviga la struttura in tempo lineare rispetto ai nodi scoperti.

---

## 1.2 – Pattern Matching e Ricerca di Cicli (Triangle Detection)

**Query Neo4j (non direzionata):**
```cypher
MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS]-(c:Person)-[:KNOWS]-(a)
WHERE a.id < b.id AND b.id < c.id
RETURN count(*) AS triangles
```

**Query PostgreSQL (triplo self-join):**
```sql
SELECT COUNT(*) FROM knows k1
JOIN knows k2 ON k1.k_person2id = k2.k_person1id
JOIN knows k3 ON k2.k_person2id = k3.k_person1id
           AND k3.k_person2id = k1.k_person1id
WHERE k1.k_person1id < k1.k_person2id
  AND k2.k_person1id < k2.k_person2id
  AND k1.k_person1id < k2.k_person1id
```

### 1.2a – Conteggio Globale (tutti i nodi)

| Metrica | Neo4j | PostgreSQL |
|---|---|---|
| Triangoli trovati | 33.380 | 33.380 ✅ |
| Media (ms) | 811.901 | **46.112** |
| Mediana (ms) | 789.102 | 46.104 |
| P90 (ms) | 900.514 | 46.739 |
| Min (ms) | 786.020 | 45.633 |
| Max (ms) | 900.514 | 46.739 |
| **Speedup Neo4j vs PG** | **0.06×** | PG ~17.6× più veloce |

### 1.2b – Triangoli per Persona Specifica

| Metrica | Neo4j | PostgreSQL |
|---|---|---|
| Person ID | 10995116279182 | 10995116279182 |
| **Risultato (triangoli)** | **18** | **18** |
| N. Esecuzioni | 10 | 10 |
| Media (ms) | 0.879 | 0.646 |
| Mediana (ms) | 0.817 | 0.602 |
| P90 (ms) | 1.299 | 0.937 |
| **Speedup Neo4j vs PG** | **0.73×** | PG ~1.3× più veloce |

> **Discrepanza 1.2b:** Risolta. Dopo l'applicazione della condizione `k2.k_person1id < k3.k_person1id` in PostgreSQL, i risultati sono perfettamente coerenti (18 = 18).

### Analisi

Il conteggio globale dei triangoli favorisce **nettamente PostgreSQL (~18×)**. La ragione è strutturale:
- PostgreSQL usa un **hash join** altamente ottimizzato con statistiche di colonna
- Neo4j deve materializzare il matching su tutto il grafo senza filtri push-down efficaci per pattern ciclici globali
- Questo scenario è un esempio classico di **query statistica disconnessa dalla topologia**: esattamente il caso d'uso dove l'RDBMS supera il grafo

---

## 1.3 – Pathfinding (Cammini Minimi / Shortest Path)

**Query Neo4j (`shortestPath` nativo — BFS ottimizzato):**
```cypher
MATCH (src:Person {id: $src}), (dst:Person {id: $dst})
MATCH path = shortestPath((src)-[:KNOWS*]-(dst))
RETURN length(path) AS hops
```

**PostgreSQL (`pg_sql_shortest_path`):** CTE ricorsiva pura che traccia il percorso usando un array per il rilevamento di cicli (`k.k_person2id = ANY(sg.path)`).

### Risultati

| Coppia (Distanza Hop) | Neo4j Media (ms) | Neo4j P90 (ms) | PG CTE Media (ms) | PG P90 (ms) | Speedup Neo4j vs PG |
|--------|:---------------:|:--------------:|:-----------------:|:-----------------:|---------|
| A (3 Hop) | **0.884** | 1.879 | 34.932 | 38.428 | **39.52×** |
| B (2 Hop) | **0.855** | 1.708 | 36.313 | 38.563 | **42.47×** |
| C (3 Hop) | **0.911** | 1.799 | 30.384 | 31.758 | **33.35×** |

### Analisi Metodologica

Questo confronto è ora al **100% onesto e intra-engine**. In passato usavamo una BFS Python-side in memoria che aggirava i limiti del database. Usando il motore SQL puro (CTE ricorsiva con cycle detection), il reale costo computazionale dell'esplorazione dei path nel modello relazionale viene alla luce: Neo4j risponde in meno di 1 millisecondo grazie al pathfinding ottimizzato e algoritmico nativo, mentre PostgreSQL impiega oltre 30 millisecondi (rallentamento >30×), saturando la CTE per ricostruire l'albero di esplorazione.

---

## Riepilogo Comparativo

| Profondità | Neo4j (Media) | PostgreSQL (Media) | Speedup Neo4j vs PG |
| --- | --- | --- | --- |
| 1 Hop | 0.76 ms | 0.33 ms | PG vince (~0.43×) |
| 2 Hop | 1.14 ms | 1.48 ms | **1.3×** |
| 3 Hop | 3.63 ms | 45.90 ms | **12.6×** |
| 4 Hop | 3.38 ms | 604.88 ms | **178.8×** |
| Triangoli globali | 811.90 ms | 46.11 ms | 0.06× |
| Triangoli per persona | 0.88 ms | 0.65 ms | 0.73× |
| Shortest path (A - 3 hop) | 0.88 ms | 34.93 ms | **39.5×** |
| Shortest path (B - 2 hop) | 0.86 ms | 36.31 ms | **42.5×** |
| Shortest path (C - 3 hop) | 0.91 ms | 30.38 ms | **33.3×** |

## Profilazione RAM (Query Multi-Hop 4)
I dati estratti da `ram_results_4hop.json` documentano l'allocazione spaziale della CTE vs Grafo durante la query a 4 hop:
- **PostgreSQL:** Incremento di **+8.70 MB** (da ~34 MB a ~43 MB) dovuto alla materializzazione dinamica dei self-join.
- **Neo4j:** Incremento di **+2.05 MB** all'interno dello heap preallocato (~5060 MB), riconducibile alla fisiologica Garbage Collection e non alla duplicazione cartesiana di dati intermedi.

---

## Conclusioni

Il test empirico ha evidenziato un comportamento **strettamente dipendente dalla classe di query**:

1. **Multi-Hop Traversal (il vero vantaggio grafo-nativo):** Neo4j dimostra la sua superiorità strutturale al crescere della profondità. L'accelerazione passa da un ritardo iniziale a 1 hop a **12.6× (3 hop)** fino a quasi **180× a 4 hop**, dove l'esplosione combinatoria allunga i tempi relazionali a oltre 600ms (mentre Neo4j resta stabile sui ~3ms).

2. **Triangle Detection (global pattern matching):** PostgreSQL supera Neo4j (~17×) nelle query di pattern matching ciclico globale. Il motore SQL sfrutta hash join e statistiche di costo su tutta la tabella `knows`. Questo scenario è un esempio di quando non conviene usare un grafo puro senza indici appropriati per query di BI globali.

3. **Shortest Path:** Attraverso il calcolo pathfinding nativo via CTE (PostgreSQL) vs Algoritmo nativo (Neo4j), il database a grafo si impone brutalmente. L'algoritmo di shortest path e l'inversione di relazioni intrinseche permettono a Neo4j di calcolare i cammini con speedup sistematici tra il 30× e il 40× rispetto alla CTE ricorsiva relazionale.

---

## File di Riferimento

- **Dati grezzi:** [`results.json`](./results.json)
- **Script:** [`./scenario1_benchmark.py`](./scenario1_benchmark.py)
- **Dataset:** LDBC SNB SF 0.1 — 1.700 Person, ~9.067 relazioni KNOWS
