#!/usr/bin/env python3
"""
=============================================================================
SCENARIO 1 – Generazione Grafici
=============================================================================
Legge results.json e produce i seguenti grafici SVG con error bars (±σ):

  1. multihop_plot.svg
     Confronto Neo4j vs PostgreSQL su query Multi-Hop (1..4 hop).
     - Linee con marker e barre di errore (±1 deviazione standard)
     - Scala logaritmica sull'asse Y per rendere visibili tutti gli ordini
       di grandezza contemporaneamente.

  2. triangle_plot.svg
     Confronto latenza globale triangle detection.
     - Barre con error bars (±σ)
     - Annotazione della nota metodologica GDS

  3. multihop_speedup_plot.svg
     Speedup di Neo4j vs PostgreSQL al crescere dei hop.
     - Visualizza l'andamento asintotico dello speedup.

  4. shortest_path_plot.svg
     Confronto latenza pathfinding Neo4j vs PostgreSQL per hop crescenti.
     - Include gestione del caso TIMEOUT (PostgreSQL su hop elevati).

NOTE: Le error bars mostrano ±1 deviazione standard (σ), un intervallo che
      copre ~68% delle misurazioni, secondo la convenzione scientifica standard.
      Il P90 elevato di Neo4j rispetto alla mediana è fisiologico: è causato
      dai cicli di Garbage Collection (GC) della JVM (Stop-The-World pause).
=============================================================================
"""

import json
import os
import sys

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
except ImportError:
    import subprocess
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--break-system-packages", "matplotlib", "numpy"]
    )
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np


# ---------------------------------------------------------------------------
# Stile comune dark-mode
# ---------------------------------------------------------------------------
STYLE = {
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

COLOR_NEO4J = "#f39c12"   # arancio – Neo4j
COLOR_PG    = "#4a9eff"   # blu     – PostgreSQL
COLOR_SPEEDUP = "#2ecc71" # verde   – speedup


def load_results(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# GRAFICO 1 – Multi-Hop con error bars (±σ)
# ---------------------------------------------------------------------------

def plot_multihop(results: dict, out_dir: str):
    mh = results.get("test_1_1_multihop", {})
    if not mh:
        print("[SKIP] Dati test_1_1_multihop non disponibili.")
        return

    hops = [1, 2, 3, 4]
    neo4j_means, neo4j_errs = [], []
    pg_means, pg_errs = [], []

    for h in hops:
        nd = mh.get(f"hop_{h}", {})
        neo4j_means.append(nd.get("neo4j", {}).get("mean_ms", 0))
        neo4j_errs.append(nd.get("neo4j", {}).get("stdev_ms", 0))
        pg_means.append(nd.get("postgresql", {}).get("mean_ms", 0))
        pg_errs.append(nd.get("postgresql", {}).get("stdev_ms", 0))

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(9, 6))

        ax.errorbar(
            hops, neo4j_means, yerr=neo4j_errs,
            marker="o", label="Neo4j", color=COLOR_NEO4J,
            linewidth=2.5, markersize=9, capsize=6, capthick=2,
            elinewidth=1.5, ecolor="#f39c1288", zorder=4
        )
        ax.errorbar(
            hops, pg_means, yerr=pg_errs,
            marker="s", label="PostgreSQL (CTE ricorsiva)", color=COLOR_PG,
            linewidth=2.5, markersize=9, capsize=6, capthick=2,
            elinewidth=1.5, ecolor="#4a9eff88", zorder=4
        )

        # ---- Linea verticale per il punto di CROSSOVER (tra 2 e 3 hop) ----
        # A sinistra del crossover PostgreSQL è più veloce (lookup indicizzato),
        # a destra Neo4j domina (Index-Free Adjacency).
        crossover_x = 2.5
        ax.axvline(x=crossover_x, color="#e74c3c", linewidth=1.8,
                   linestyle=":", alpha=0.85, zorder=3)
        ax.annotate(
            "← PG più veloce  |  Neo4j domina →\n(punto di crossover)",
            xy=(crossover_x, 0),
            xytext=(crossover_x, 0),
            xycoords=("data", "axes fraction"),
            textcoords=("data", "axes fraction"),
            annotation_clip=False,
            fontsize=8, color="#e74c3c", ha="center", va="bottom",
            fontweight="bold",
        )

        # Annotazioni speedup sui punti 3 e 4 hop
        for h_idx, h in enumerate([3, 4]):
            nd = mh.get(f"hop_{h}", {})
            sp = nd.get("speedup_neo4j_vs_pg")
            if sp and isinstance(sp, (int, float)) and sp > 1:
                ax.annotate(
                    f"{sp}×",
                    xy=(h, neo4j_means[h_idx + 2]),
                    xytext=(h - 0.25, neo4j_means[h_idx + 2] * 3),
                    color=COLOR_SPEEDUP, fontsize=11, fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color=COLOR_SPEEDUP, lw=1.2)
                )

        ax.set_yscale("log")
        ax.set_xticks(hops)
        ax.set_xticklabels([f"{h} Hop" for h in hops], fontsize=11)
        ax.set_xlabel("Profondità di Navigazione", fontsize=12)
        ax.set_ylabel("Latenza Media (ms) – scala logaritmica", fontsize=12)
        ax.set_title(
            "Query Multi-Hop: Neo4j vs PostgreSQL\n"
            "Barre di errore = ±1σ (deviazione standard)",
            fontsize=13, fontweight="bold", color="#ffffff", pad=14
        )
        ax.legend(fontsize=11)
        ax.grid(True, which="both", alpha=0.3)

        # Nota metodologica P90/GC
        fig.text(
            0.02, 0.01,
            "Nota: Il P90 di Neo4j è più alto della mediana per via dei cicli GC della JVM (Stop-The-World pause) – fisiologico.",
            fontsize=7, color="#888888", ha="left"
        )

        fig.tight_layout()
        out_path = os.path.join(out_dir, "multihop_plot.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# GRAFICO 2 – Speedup Multi-Hop
# ---------------------------------------------------------------------------

def plot_multihop_speedup(results: dict, out_dir: str):
    mh = results.get("test_1_1_multihop", {})
    if not mh:
        return

    hops = [1, 2, 3, 4]
    speedups = []
    for h in hops:
        sp = mh.get(f"hop_{h}", {}).get("speedup_neo4j_vs_pg", 1)
        speedups.append(sp if isinstance(sp, (int, float)) else 1)

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(8, 5))

        bars = ax.bar(
            [f"{h} Hop" for h in hops], speedups,
            color=[COLOR_SPEEDUP if s >= 1 else "#e74c3c" for s in speedups],
            alpha=0.85, edgecolor="#ffffff22", width=0.55, zorder=3
        )

        # Linea di parità (speedup = 1)
        ax.axhline(y=1, color="#e0e0e0", linewidth=1.2, linestyle="--",
                   label="Parità (1×)", zorder=2)

        for bar, sp in zip(bars, speedups):
            label = f"{sp:.1f}×" if isinstance(sp, float) else f"{sp}×"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(speedups) * 0.02,
                label, ha="center", va="bottom", fontsize=11,
                fontweight="bold", color="#ffffff"
            )

        ax.set_ylabel("Speedup Neo4j vs PostgreSQL", fontsize=12)
        ax.set_title(
            "Speedup Neo4j vs PostgreSQL – Query Multi-Hop\n"
            "(valori > 1× = Neo4j più veloce; < 1× = PostgreSQL più veloce)",
            fontsize=12, fontweight="bold", color="#ffffff", pad=14
        )
        ax.legend(fontsize=10)
        ax.grid(axis="y", alpha=0.3, zorder=0)
        ax.set_axisbelow(True)

        fig.tight_layout()
        out_path = os.path.join(out_dir, "multihop_speedup_plot.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# GRAFICO 3 – Triangle Detection con error bars
# ---------------------------------------------------------------------------

def plot_triangles(results: dict, out_dir: str):
    tri = results.get("test_1_2_triangle", {})
    if not tri:
        print("[SKIP] Dati test_1_2_triangle non disponibili.")
        return

    gt = tri.get("global_triangles", {})
    pt = tri.get("per_person_triangles", {})

    if not gt:
        print("[SKIP] global_triangles non disponibili.")
        return

    labels = ["Globale (Cypher OLTP)", "Per Persona"]
    neo4j_means = [
        gt.get("neo4j", {}).get("mean_ms", 0),
        pt.get("neo4j", {}).get("mean_ms", 0) if pt else 0
    ]
    neo4j_errs = [
        gt.get("neo4j", {}).get("stdev_ms", 0),
        pt.get("neo4j", {}).get("stdev_ms", 0) if pt else 0
    ]
    pg_means = [
        gt.get("postgresql", {}).get("mean_ms", 0),
        pt.get("postgresql", {}).get("mean_ms", 0) if pt else 0
    ]
    pg_errs = [
        gt.get("postgresql", {}).get("stdev_ms", 0),
        pt.get("postgresql", {}).get("stdev_ms", 0) if pt else 0
    ]

    x = np.arange(len(labels))
    width = 0.35

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(9, 6))

        bars1 = ax.bar(x - width / 2, neo4j_means, width,
                       label="Neo4j (OLTP Cypher)", color=COLOR_NEO4J,
                       alpha=0.85, edgecolor="#ffffff22", zorder=3)
        bars2 = ax.bar(x + width / 2, pg_means, width,
                       label="PostgreSQL (Triple Self-Join)", color=COLOR_PG,
                       alpha=0.85, edgecolor="#ffffff22", zorder=3)

        # Error bars
        ax.errorbar(x - width / 2, neo4j_means, yerr=neo4j_errs,
                    fmt="none", color="#e0e0e0", capsize=6, capthick=1.5,
                    linewidth=1.5, zorder=5)
        ax.errorbar(x + width / 2, pg_means, yerr=pg_errs,
                    fmt="none", color="#e0e0e0", capsize=6, capthick=1.5,
                    linewidth=1.5, zorder=5)

        # Annotazioni valori
        for bar, mean in zip(bars1, neo4j_means):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(neo4j_means + pg_means) * 0.02,
                    f"{mean:.1f} ms", ha="center", va="bottom",
                    fontsize=9, fontweight="bold", color="#ffffff")
        for bar, mean in zip(bars2, pg_means):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(neo4j_means + pg_means) * 0.02,
                    f"{mean:.1f} ms", ha="center", va="bottom",
                    fontsize=9, fontweight="bold", color="#ffffff")

        ax.set_ylabel("Latenza Media (ms)", fontsize=12)
        ax.set_title(
            "Triangle Detection: Neo4j (OLTP) vs PostgreSQL\n"
            "Barre di errore = ±1σ",
            fontsize=13, fontweight="bold", color="#ffffff", pad=14
        )
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=11)
        ax.legend(fontsize=10)
        ax.grid(axis="y", alpha=0.3, zorder=0)
        ax.set_axisbelow(True)

        # Nota metodologica GDS
        fig.text(
            0.02, 0.01,
            "Nota: Neo4j perde sulla query globale OLTP – in produzione si usa GDS (gds.triangleCount) che supera PostgreSQL.",
            fontsize=7, color="#888888", ha="left"
        )

        fig.tight_layout()
        out_path = os.path.join(out_dir, "triangle_plot.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# GRAFICO 4 – Shortest Path (con timeout handling)
# ---------------------------------------------------------------------------

def plot_shortest_path(results: dict, out_dir: str):
    sp = results.get("test_1_3_shortest_path", {})
    if not sp:
        print("[SKIP] Dati test_1_3_shortest_path non disponibili.")
        return

    # Ordina per hop effettivi
    items = sorted(sp.items(), key=lambda x: x[1].get("actual_hops", 0))

    labels, neo4j_m, neo4j_e, pg_m, pg_e, pg_timeout = [], [], [], [], [], []
    for key, data in items:
        h = data.get("actual_hops", "?")
        labels.append(f"{h} hop")
        neo4j_m.append(data.get("neo4j", {}).get("mean_ms", 0))
        neo4j_e.append(data.get("neo4j", {}).get("stdev_ms", 0))
        if data.get("pg_timed_out"):
            pg_m.append(None)
            pg_e.append(0)
            pg_timeout.append(True)
        else:
            pg_m.append(data.get("postgresql", {}).get("mean_ms", 0))
            pg_e.append(data.get("postgresql", {}).get("stdev_ms", 0))
            pg_timeout.append(False)

    x = np.arange(len(labels))
    width = 0.35

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(10, 6))

        # Neo4j bars (always valid)
        bars1 = ax.bar(x - width / 2, neo4j_m, width,
                       label="Neo4j (BFS nativo)", color=COLOR_NEO4J,
                       alpha=0.85, edgecolor="#ffffff22", zorder=3)
        ax.errorbar(x - width / 2, neo4j_m, yerr=neo4j_e,
                    fmt="none", color="#e0e0e0", capsize=5, capthick=1.5,
                    linewidth=1.2, zorder=5)

        # PostgreSQL bars (con timeout handling)
        pg_valid_m = [m if m is not None else 0 for m in pg_m]
        bars2 = ax.bar(x + width / 2, pg_valid_m, width,
                       label="PostgreSQL (CTE ricorsiva)", color=COLOR_PG,
                       alpha=0.85, edgecolor="#ffffff22", zorder=3)
        # Error bars solo dove non timeout
        for i, (xi, m, e, to) in enumerate(zip(x, pg_m, pg_e, pg_timeout)):
            if not to and m is not None:
                ax.errorbar(xi + width / 2, m, yerr=e,
                            fmt="none", color="#e0e0e0", capsize=5, capthick=1.5,
                            linewidth=1.2, zorder=5)
            elif to:
                ax.text(xi + width / 2, max(neo4j_m) * 0.1,
                        "TIMEOUT", ha="center", va="bottom",
                        fontsize=8, color="#e74c3c", fontweight="bold",
                        rotation=90)

        # Annotazioni valori Neo4j
        for bar, m in zip(bars1, neo4j_m):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    m + max(neo4j_m) * 0.02,
                    f"{m:.1f}ms", ha="center", va="bottom",
                    fontsize=8, color="#ffffff")

        ax.set_ylabel("Latenza Media (ms)", fontsize=12)
        ax.set_title(
            "Pathfinding: Neo4j (shortestPath) vs PostgreSQL (CTE BFS)\n"
            "Barre di errore = ±1σ  |  TIMEOUT = PostgreSQL supera limite temporale",
            fontsize=12, fontweight="bold", color="#ffffff", pad=14
        )
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=11)
        ax.legend(fontsize=10)
        ax.grid(axis="y", alpha=0.3, zorder=0)
        ax.set_axisbelow(True)

        fig.tight_layout()
        out_path = os.path.join(out_dir, "shortest_path_plot.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Genera i grafici dello Scenario 1")
    parser.add_argument(
        "--results",
        default=os.path.join(os.path.dirname(__file__), "results.json"),
        help="Percorso al file results.json (default: ./results.json)"
    )
    args = parser.parse_args()

    if not os.path.exists(args.results):
        print(f"[ERR] File non trovato: {args.results}")
        print("      Esegui prima scenario1_benchmark.py per generare results.json")
        sys.exit(1)

    out_dir = os.path.dirname(os.path.abspath(args.results))
    results = load_results(args.results)

    print(f"[*] Generazione grafici da: {args.results}")
    print(f"[*] Output directory: {out_dir}\n")

    plot_multihop(results, out_dir)
    plot_multihop_speedup(results, out_dir)
    plot_triangles(results, out_dir)
    plot_shortest_path(results, out_dir)

    print("\n[*] Grafici generati:")
    for name in ["multihop_plot.svg", "multihop_speedup_plot.svg",
                 "triangle_plot.svg", "shortest_path_plot.svg"]:
        path = os.path.join(out_dir, name)
        if os.path.exists(path):
            size_kb = os.path.getsize(path) / 1024
            print(f"    {name}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
