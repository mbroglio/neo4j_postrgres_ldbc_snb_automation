# Scenario 2 – Transazioni e Concorrenza (Stress Test)

> **Esecuzione:** 2026-06-13 — SF 0.1 e SF 1 — N_RUNS=30 — N_THREADS=8
> I dati grezzi completi si trovano in [`results.json`](./results.json).

---

## Ambiente di Esecuzione

| Componente         | Versione / Configurazione        |
|--------------------|----------------------------------|
| Neo4j              | 5.20.0-community (Docker)        |
| Heap Neo4j         | 6 GB init + max                  |
| Page Cache Neo4j   | 2 GB                             |
| CPU limit          | 4 vCPU per container             |
| RAM limit          | 8 GB per container               |
| Dataset            | LDBC SNB SF 0.1                  |
| Person             | ~1.700 nodi                      |
| Thread concorrenti | 8 (2.1), 10 (2.2), 20 (2.3)     |
| Isolamento testato | Read Committed (default Neo4j)   |

---

## 2.1 – Gestione del Livello Read Committed

### Configurazione

- **Thread:** 4 reader + 4 writer concorrenti sullo stesso nodo
- **Target:** proprietà `notification_count` di un nodo Person
- **Metodo:** ogni writer usa una **transazione esplicita lunga**:
  - Step 1: `SET notification_count = 42` (dirty value, non committato)
  - Step 2: sleep 50–100 ms (finestra di Dirty Read aperta)
  - Step 3: `SET notification_count = 99` → `COMMIT`
- **Verifica:** se il reader vede `42` → Dirty Read; valori leciti: `{0, 99}`

### Query Neo4j (Lettura)

```cypher
MATCH (p:Person {id: $pid})
RETURN p.notification_count AS val
```

### Query Neo4j (Scrittura – committed)

```cypher
MATCH (p:Person {id: $pid})
SET p.notification_count = $val
```

### Risultati

<!-- FILL FROM results.json dopo l'esecuzione -->

| Metrica                        | Neo4j                            |
|-------------------------------|----------------------------------|
| Dirty Read osservati           | **0** ✅                          |
| Isolamento corretto            | ✅ Sì                             |
| Letture totali effettuate      | 640 (20 run × 4 reader × 8 read) |
| Dirty value testato            | `42` (non committato, finestra 50–100ms) |
| Committed value                | `99`                             |
| Latenza media lettura (ms)     | **1.798**                        |
| Mediana lettura (ms)           | 1.431                            |
| P90 lettura (ms)               | 2.796                            |
| Latenza media scrittura (ms)   | **265.082** (include sleep 50–100ms) |
| Mediana scrittura (ms)         | 201.169                          |
| P90 scrittura (ms)             | 546.069                          |

### Analisi

Il livello di isolamento *Read Committed* di Neo4j garantisce che ogni query di lettura veda
esclusivamente dati già committati da altre transazioni. I **lock condivisi di lettura (S-lock)**
vengono acquisiti implicitamente da ogni reader e rilasciati al termine dell'operazione, senza
bloccare i writer. I **lock esclusivi di scrittura (X-lock)** vengono acquisiti dai writer
impedendo a qualsiasi reader di leggere il valore intermedio non ancora committato.

Il risultato atteso è **zero Dirty Read**: nessun thread di lettura può mai osservare un valore
parzialmente scritto in corso di commit. Questo valida il corretto funzionamento del Lock Manager
di Neo4j in scenari ad alta concorrenza mista.

---

## 2.2 – Simulazione del Lost Update

### Configurazione

- **Race condition:** `LOST_UPDATE_THREADS` thread leggono lo stesso valore e lo incrementano
- **Valore di partenza:** `notification_count = 0`
- **Incremento per thread:** 1
- **Valore atteso a fine corsa:** `n_threads × 1 = n_threads`

### Strategia Non Atomica (vulnerabile)

```cypher
// Step 1 – READ (valore stale)
MATCH (p:Person {id: $pid}) RETURN p.notification_count AS val

// [pausa deliberata → overlap con altri thread]

// Step 2 – WRITE (valore già obsoleto)
MATCH (p:Person {id: $pid}) SET p.notification_count = $old_val + 1
```

> Il livello *Read Committed* **non** protegge da questo pattern: non acquisisce
> un X-lock sulla sola lettura, perciò due thread possono leggere lo stesso valore
> stale e scrivere la stessa somma, cancellando un aggiornamento.

### Strategia Atomica (robusta)

```cypher
// Unico costrutto SET con dipendenza sul valore corrente
MATCH (p:Person {id: $pid})
SET p.notification_count = p.notification_count + $inc
```

> Il compilatore Cypher rileva la **dipendenza self-referenziale** e acquisisce
> preventivamente un **X-lock esclusivo sul nodo prima della lettura**, garantendo
> l'atomicità dell'operazione read-modify-write.

### Risultati

<!-- FILL FROM results.json dopo l'esecuzione -->

| Strategia                      | Media Lost Update/Trial | Trial con loss | Correctness     |
|-------------------------------|:-----------------------:|:--------------:|-----------------|
| Non Atomica (alias separato)   | **8.95**                | **20/20**      | ❌ Vulnerabile  |
| Atomica (SET diretto)          | **0.00**                | **0/20**       | ✅ Robusta      |

### Analisi

Il *Lost Update* è la più subdola delle anomalie di concorrenza a livello *Read Committed*:
non causa dati "sporchi" (la scrittura è sempre committed), ma produce dati **silenziosamente
errati** per contesa sull'ordine delle operazioni. La tabella dimostra empiricamente che:

1. La strategia non atomica produce una perdita di aggiornamenti proporzionale al numero di
   thread e alla finestra di overlap.
2. La strategia atomica Cypher azzera completamente l'anomalia grazie all'acquisizione
   automatica dell'X-lock prima della lettura del valore self-referenziale.

---

## 2.3 – Deadlock Detection e Risoluzione

### Configurazione

- **Pattern:** scritture incrociate simmetriche
  - Thread A: acquisisce lock su `nodo_A` → tenta lock su `nodo_B`
  - Thread B: acquisisce lock su `nodo_B` → tenta lock su `nodo_A`
- **Attesa circolare:** garantita artificialmente tramite `threading.Barrier`
- **Meccanismo di rilevazione:** *Wait-for Graph* del Lock Manager di Neo4j

### Query Neo4j (acquisizione lock a catena)

```cypher
// Thread A – lock 1
MATCH (p:Person {id: $nodeA}) SET p.notification_count = p.notification_count + 1

// [sync barrier – overlap garantito]

// Thread A – lock 2 → possibile deadlock se Thread B ha già nodeB
MATCH (p:Person {id: $nodeB}) SET p.notification_count = p.notification_count + 1
```

### Risultati

<!-- FILL FROM results.json dopo l'esecuzione -->

| Metrica                        | Valore                                   |
|-------------------------------|------------------------------------------|
| Deadlock rilevati (totale)     | **20 / 20 run** ✅                       |
| Rollback automatici confermati | **20 / 20** ✅                           |
| Tempo rilevazione medio (ms)   | **35.53** \*                             |
| Tempo rilevazione P90 (ms)     | **71.49** \*                             |
| Meccanismo                     | Wait-for Graph (TransientError)          |
| Eccezione lanciata             | `Neo.TransientError.Transaction.DeadlockDetected` |
| Rollback automatico            | ✅ Confermato (100%)                     |

> \* Tempo misurato dall'istante in cui il secondo lock viene richiesto fino all'arrivo della `TransientError`. Esclude il delay artificiale di sincronizzazione (50–150ms). Misura l'overhead puro del Wait-for Graph.

### Analisi

Neo4j mantiene in memoria un **grafo delle attese (Wait-for Graph)** che viene analizzato
ad ogni acquisizione di lock. Non appena viene rilevato un **ciclo** nel grafo, il sistema:

1. **Identifica** la transazione causale del ciclo.
2. **Interrompe** quella transazione lanciando una `TransientError` (categoria `Neo.TransientError.Transaction.DeadlockDetected`).
3. **Esegue il rollback automatico** di tutte le operazioni non committate, liberando i lock
   e permettendo alle altre transazioni nel ciclo di procedere.

Il tempo di rilevazione misura la latenza tra l'insorgenza del ciclo e l'invio dell'eccezione
all'applicazione. Valori tipici nell'ordine di pochi decine di millisecondi confermano
l'efficienza del meccanismo.

---

## Riepilogo Comparativo

| Test                   | Metrica                      | Risultato      | Correttezza      |
|------------------------|------------------------------|:--------------:|------------------|
| 2.1 Read Committed     | Dirty Read rilevati          | **0**          | ✅ Isolamento OK |
| 2.1 Read Committed     | Latenza lettura media (ms)   | **1.798**      | —                |
| 2.2 Non Atomica        | Media Lost Update/trial      | **9.00**       | ❌ Vulnerabile   |
| 2.2 Non Atomica        | Trial con almeno 1 loss      | **20/20**      | ❌ Vulnerabile   |
| 2.2 Atomica            | Media Lost Update/trial      | **0.00**       | ✅ Robusta       |
| 2.2 Atomica            | Trial con almeno 1 loss      | **0/20**       | ✅ Robusta       |
| 2.3 Deadlock rilevati  | Su 20 run                    | **20/20**      | ✅ Deterministic |
| 2.3 Rollback automatici| Su 20 deadlock               | **20/20**      | ✅ Confermato    |
| 2.3 Deadlock           | Tempo rilevazione medio (ms) | **35.53** \*   | ✅ Auto-rollback |
| 2.3 Deadlock           | Tempo rilevazione P90 (ms)   | **71.49** \*   | ✅ Auto-rollback |

---

## Conclusioni

I tre test dello Scenario 2 documentano il modello transazionale di Neo4j in ambienti
multi-utente ad alta concorrenza:

1. **Read Committed funziona correttamente:** zero Dirty Read in qualsiasi condizione di
   concorrenza mista, grazie alla gestione atomica degli S-lock e X-lock.

2. **Il livello Read Committed non è sufficiente per prevenire i Lost Update:** la strategia
   non atomica (READ → modifica → WRITE come operazioni separate) è vulnerabile. La soluzione
   nativa di Cypher (`SET p.prop = p.prop + N`) sfrutta il lock esclusivo automatico acquisito
   in fase di lettura e azzera l'anomalia.

3. **Il sistema di deadlock detection è robusto e trasparente:** il Wait-for Graph intercetta
   l'attesa circolare in pochi millisecondi, il rollback automatico libera le risorse bloccate
   e l'applicazione riceve una `TransientError` gestibile, senza necessità di timeout esterni.

---

## File di Riferimento

- **Dati grezzi:** [`results.json`](./results.json)
- **Script benchmark:** [`scenario2_benchmark.py`](./scenario2_benchmark.py)
- **Script plot:** [`plot_scenario2.py`](./plot_scenario2.py)
- **Dataset:** LDBC SNB SF 0.1 — ~1.700 Person
