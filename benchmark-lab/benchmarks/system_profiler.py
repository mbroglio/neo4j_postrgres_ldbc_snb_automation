import os
import json
import time
import subprocess
import threading
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


class DockerRAMProfiler:
    """Monitor generico della RAM dei container Docker durante l'esecuzione di query.

    Uso tipico:
        profiler = DockerRAMProfiler(["postgres-benchmark", "neo4j-benchmark"], "./output")
        profiler.start()
        # ... esegui query ...
        profiler.mark_event("query_start")
        # ... query ...
        profiler.mark_event("query_end")
        profiler.stop()
        json_path = profiler.save()
        plot_ram_usage(json_path, "./output")
    """

    def __init__(self, containers, output_dir, poll_interval=0.3):
        self.containers = containers
        self.output_dir = output_dir
        self.poll_interval = poll_interval

        self.monitoring = False
        self.start_time = 0
        self.timestamps = []
        self.ram_data = {c: [] for c in containers}
        self.events = {}
        self.thread = None

    def _parse_mem(self, mem_str):
        """Interpreta le stringhe di memoria di Docker (es. '48.95MiB', '4.6GiB')."""
        mem_str = mem_str.strip()
        try:
            if "GiB" in mem_str:
                return float(mem_str.replace("GiB", "")) * 1024
            elif "MiB" in mem_str:
                return float(mem_str.replace("MiB", ""))
            elif "KiB" in mem_str:
                return float(mem_str.replace("KiB", "")) / 1024
            elif "kB" in mem_str:
                return float(mem_str.replace("kB", "")) / 1024
            elif "B" in mem_str:
                return float(mem_str.replace("B", "")) / (1024 * 1024)
            return 0.0
        except:
            return 0.0

    def _monitor(self):
        while self.monitoring:
            try:
                args = (
                    ["docker", "stats"]
                    + self.containers
                    + ["--no-stream", "--format", "{{.Name}}:{{.MemUsage}}"]
                )
                res = subprocess.run(args, capture_output=True, text=True, check=True)
                now = time.time() - self.start_time

                current_mem = {c: 0.0 for c in self.containers}
                for line in res.stdout.strip().split("\n"):
                    if not line or ":" not in line:
                        continue
                    name, usage = line.split(":", 1)
                    actual_usage = usage.split("/")[0].strip()
                    mem_mb = self._parse_mem(actual_usage)
                    for c in self.containers:
                        if c in name:
                            current_mem[c] = mem_mb

                self.timestamps.append(now)
                for c in self.containers:
                    self.ram_data[c].append(current_mem[c])
            except Exception:
                pass
            time.sleep(self.poll_interval)

    def start(self):
        print("[Profiler] Starting Docker RAM monitor...")
        self.monitoring = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()

    def mark_event(self, event_name):
        t = time.time() - self.start_time
        self.events[event_name] = t
        print(f"[Profiler] Event marked: {event_name} at {t:.2f}s")

    def stop(self):
        print("[Profiler] Stopping monitor...")
        self.monitoring = False
        if self.thread:
            self.thread.join(timeout=5)

    def save(self, filename="ram_results.json"):
        os.makedirs(self.output_dir, exist_ok=True)
        out_path = os.path.join(self.output_dir, filename)
        res = {
            "containers": self.containers,
            "timestamps": self.timestamps,
            "ram_data": self.ram_data,
            "events": self.events,
        }
        with open(out_path, "w") as f:
            json.dump(res, f, indent=2)
        print(f"[Profiler] Results saved to {out_path}")
        return out_path


def plot_ram_usage(
    json_path,
    output_dir,
    title="Allocazione Dinamica RAM",
    filename="ram_usage_plot.svg",
):
    """Genera un grafico a doppio pannello della variazione di RAM dei container Docker.

    Pannello Superiore: PostgreSQL (variazione dinamica durante CTE multi-hop).
    Pannello Inferiore: Neo4j (baseline alto per heap JVM pre-allocato, variazione minima).

    NOTA METODOLOGICA: i valori assoluti dei due pannelli NON sono confrontabili
    perché le configurazioni di memoria sono radicalmente diverse (PostgreSQL ~2 GB
    shared_buffers vs Neo4j 6 GB heap + 2 GB page cache pre-allocati). La metrica
    rilevante è la sola variazione netta (Δ) durante l'esecuzione della query.
    """
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return

    with open(json_path, "r") as f:
        data = json.load(f)

    ts = data["timestamps"]
    ram_data = data["ram_data"]
    events = data["events"]
    containers = data["containers"]

    # Calcola statistiche per ogni container
    stats = {}
    for c in containers:
        lst = ram_data[c]
        if not lst:
            continue
        baseline = lst[0]
        peak = max(lst)
        delta = peak - baseline
        stats[c] = {"baseline": baseline, "peak": peak, "delta": delta, "raw": lst}

    # Identifica le chiavi dei container
    pg_key    = next((c for c in containers if "postgres" in c.lower()), None)
    neo4j_key = next((c for c in containers if "neo4j" in c.lower()), None)

    if not pg_key or not neo4j_key:
        print("Errore: container non trovati nei dati.")
        return

    # ---------------------------------------------------------------------------
    # Stile dark-mode coerente con scenari 1, 2, 3, 4
    # ---------------------------------------------------------------------------
    DARK_STYLE = {
        "figure.facecolor":  "#1a1a2e",
        "axes.facecolor":    "#16213e",
        "axes.edgecolor":    "#4a4e69",
        "axes.labelcolor":   "#e0e0e0",
        "xtick.color":       "#b0b0b0",
        "ytick.color":       "#b0b0b0",
        "text.color":        "#e0e0e0",
        "grid.color":        "#2a2a4a",
        "grid.linestyle":    "--",
        "grid.linewidth":    0.6,
        "legend.facecolor":  "#16213e",
        "legend.edgecolor":  "#4a4e69",
        "font.family":       "DejaVu Sans",
    }

    COLOR_PG    = "#e74c3c"   # rosso  – PostgreSQL RAM
    COLOR_N4J   = "#2ecc71"   # verde  – Neo4j RAM
    COLOR_ANNOT = "#f39c12"   # arancio – annotazioni delta

    with plt.rc_context(DARK_STYLE):
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

        # ---- PANNELLO 1: PostgreSQL ----
        pg_raw  = stats[pg_key]["raw"]
        pg_base = stats[pg_key]["baseline"]
        pg_peak = stats[pg_key]["peak"]

        ax1.plot(ts, pg_raw, color=COLOR_PG, linewidth=2.5,
                 label="PostgreSQL RAM (MB)", zorder=3)
        ax1.fill_between(ts, pg_base * 0.99, pg_raw,
                         color=COLOR_PG, alpha=0.15, zorder=2)

        if "PG_start" in events and "PG_end" in events:
            ax1.axvspan(events["PG_start"], events["PG_end"],
                        color=COLOR_PG, alpha=0.1, label="Esecuzione CTE (PostgreSQL)")
            ax1.axvline(events["PG_start"], color=COLOR_PG, ls="--", alpha=0.6, lw=1)
            ax1.axvline(events["PG_end"],   color=COLOR_PG, ls="--", alpha=0.6, lw=1)

        # Annotazione delta
        delta_pg = stats[pg_key]["delta"]
        mid_t = (events.get("PG_start", ts[0]) + events.get("PG_end", ts[-1])) / 2
        ax1.annotate(
            f"Δ = +{delta_pg:.2f} MB\n(allocazione dinamica CTE)",
            xy=(mid_t, pg_peak),
            xytext=(mid_t + (ts[-1] - ts[0]) * 0.05, pg_peak * 1.002),
            color=COLOR_ANNOT, fontsize=9, fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=COLOR_ANNOT, lw=1.2)
        )

        ax1.set_ylabel("RAM Allocata (MB)", fontsize=11)
        ax1.set_title("PostgreSQL – Variazione RAM durante query 4-hop",
                      fontsize=12, fontweight="bold")
        ax1.legend(loc="upper left", fontsize=10)
        ax1.grid(True, which="both", alpha=0.3)
        ax1.set_ylim(bottom=max(0, pg_base - 5), top=pg_peak + 8)

        # ---- PANNELLO 2: Neo4j ----
        n4j_raw  = stats[neo4j_key]["raw"]
        n4j_base = stats[neo4j_key]["baseline"]
        n4j_peak = stats[neo4j_key]["peak"]

        ax2.plot(ts, n4j_raw, color=COLOR_N4J, linewidth=2.5,
                 label="Neo4j RAM (MB)", zorder=3)
        ax2.fill_between(ts, n4j_base * 0.9999, n4j_raw,
                         color=COLOR_N4J, alpha=0.15, zorder=2)

        if "Neo4j_start" in events and "Neo4j_end" in events:
            ax2.axvspan(events["Neo4j_start"], events["Neo4j_end"],
                        color=COLOR_N4J, alpha=0.15, label="Esecuzione Cypher (Neo4j)")
            ax2.axvline(events["Neo4j_start"], color=COLOR_N4J, ls="--", alpha=0.6, lw=1)
            ax2.axvline(events["Neo4j_end"],   color=COLOR_N4J, ls="--", alpha=0.6, lw=1)

        # Annotazione delta
        delta_n4j = stats[neo4j_key]["delta"]
        mid_t2 = (events.get("Neo4j_start", ts[0]) + events.get("Neo4j_end", ts[-1])) / 2
        ax2.annotate(
            f"Δ = +{delta_n4j:.2f} MB\n(GC JVM – fisiologico)",
            xy=(mid_t2, n4j_peak),
            xytext=(mid_t2 + (ts[-1] - ts[0]) * 0.05, n4j_peak + 5),
            color=COLOR_ANNOT, fontsize=9, fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=COLOR_ANNOT, lw=1.2)
        )

        ax2.set_xlabel("Tempo (secondi)", fontsize=12)
        ax2.set_ylabel("RAM Allocata (MB)", fontsize=11)
        ax2.set_title(
            "Neo4j – Variazione RAM durante query 4-hop\n"
            "(baseline ~5 GB = Heap JVM + Page Cache pre-allocati per configurazione)",
            fontsize=12, fontweight="bold"
        )
        ax2.legend(loc="upper left", fontsize=10)
        ax2.grid(True, which="both", alpha=0.3)
        ax2.set_ylim(bottom=n4j_base - 30, top=n4j_peak + 50)

        # Footer nota metodologica
        fig.text(
            0.02, -0.01,
            "Nota: i valori assoluti dei due pannelli NON sono confrontabili "
            "(configurazioni di memoria eterogenee). La metrica rilevante è la sola variazione netta Δ.",
            fontsize=8, color="#888888", ha="left"
        )

        fig.suptitle(title, fontsize=13, fontweight="bold", y=1.01)
        plt.tight_layout()

        out_img = os.path.join(output_dir, filename)
        plt.savefig(out_img, format="svg", dpi=300, bbox_inches="tight")
        plt.close()

    # Stampa statistiche per aggiornamento LaTeX
    print("\nSTATISTICHE RAM PER TABELLA LATEX:")
    for c in containers:
        if c not in stats:
            continue
        s = stats[c]
        print(f"--- {c} ---")
        print(f"  Baseline: {s['baseline']:.2f} MB")
        print(f"  Peak:     {s['peak']:.2f} MB")
        print(f"  Delta:    {s['delta']:.2f} MB")
    print(f"\nGrafico salvato in {out_img}")
