#!/usr/bin/env python3
"""
=============================================================================
SCENARIO 4 – Generazione Grafici
=============================================================================
Legge results.json e produce due grafici SVG:

  1. aggregation_plot.svg
     Confronto latenze Neo4j vs PostgreSQL su query di aggregazione globale
     (Full-Table Scan):
     - Boxplot affiancati (distribuzione completa delle misurazioni)
     - Annotazione dello speedup di PostgreSQL
     Questo grafico ha valore visivo: evidenzia la differenza strutturale
     tra scansione sequenziale (PostgreSQL) e accesso casuale ai nodi
     sparsi nello store (Neo4j).

  2. bulk_insert_plot.svg
     Confronto throughput (record/s) e latenza media per il Bulk Insert:
     - Barre affiancate: throughput e latenza
     Evidenzia la superiorità del comando COPY di PostgreSQL rispetto
     all'overhead del motore a grafo per dati disconnessi.

NOTE: Il test 4.3 (Esplosione Combinatoria) non produce un grafico perché
      il risultato è binario (timeout vs OK) e non beneficia di una
      visualizzazione rispetto alla tabella testuale.

Utilizzo:
  python3 plot_scenario4.py [--results path/to/results.json]
=============================================================================
"""

import json
import sys
import os
import argparse

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.ticker as ticker
except ImportError:
    import subprocess
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--break-system-packages", "matplotlib"]
    )
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.ticker as ticker


# ---------------------------------------------------------------------------
# Stile comune (coerente con scenari 1–3)
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

COLOR_NEO4J = "#f39c12"    # arancio – Neo4j
COLOR_PG    = "#4a9eff"    # blu – PostgreSQL
COLOR_GRID  = "#2a2a4a"


def load_results(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# GRAFICO 1 – Full-Table Scan: Aggregazione Globale (Neo4j vs PostgreSQL)
# ---------------------------------------------------------------------------

def plot_aggregation(results: dict, out_dir: str):
    t41 = results.get("test_4_1_global_aggregation", {})
    if not t41:
        print("[SKIP] Dati test_4_1_global_aggregation non disponibili.")
        return

    neo4j_stats = t41.get("neo4j", {})
    pg_stats    = t41.get("postgresql", {})
    speedup     = t41.get("speedup_pg_vs_neo4j", "N/A")

    if not neo4j_stats or not pg_stats:
        print("[SKIP] Statistiche incomplete per il test 4.1.")
        return

    # Ricostruzione approssimata delle distribuzioni dai percentili
    # (media, mediana, p90, min, max)
    labels = ["Neo4j", "PostgreSQL"]
    means  = [neo4j_stats["mean_ms"], pg_stats["mean_ms"]]
    stdevs = [neo4j_stats.get("stdev_ms", 0), pg_stats.get("stdev_ms", 0)]
    p90s   = [neo4j_stats["p90_ms"],  pg_stats["p90_ms"]]
    mins_  = [neo4j_stats["min_ms"],  pg_stats["min_ms"]]
    maxs_  = [neo4j_stats["max_ms"],  pg_stats["max_ms"]]
    meds   = [neo4j_stats["median_ms"], pg_stats["median_ms"]]

    x = [0, 1]
    colors = [COLOR_NEO4J, COLOR_PG]

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(9, 6))

        # Barre per la media
        bars = ax.bar(x, means, width=0.45, color=colors, alpha=0.85,
                      edgecolor="#ffffff22", zorder=3)

        # Error bars ±1σ (deviazione standard) – indicatore statistico primario
        ax.errorbar(x, means, yerr=stdevs,
                    fmt="none", color="#ffffff", capsize=10, capthick=2,
                    linewidth=2, zorder=5, label="±1σ (deviazione standard)")

        # Linea per la mediana e P90 (indicatori secondari)
        for xi, mean, mn, mx, med, p90 in zip(x, means, mins_, maxs_, meds, p90s):
            # Linea per la mediana
            ax.plot([xi - 0.22, xi + 0.22], [med, med],
                    color="#ffffff", linewidth=2, zorder=6, linestyle="-")
            # Linea per P90
            ax.plot([xi - 0.15, xi + 0.15], [p90, p90],
                    color="#e74c3c", linewidth=1.5, zorder=6,
                    linestyle="--", alpha=0.8)

        # Annotazioni valore media
        for bar, mean in zip(bars, means):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                mean + max(maxs_) * 0.02,
                f"{mean:.1f} ms",
                ha="center", va="bottom", fontsize=11, fontweight="bold",
                color="#ffffff"
            )

        # Legenda
        legend_items = [
            mpatches.Patch(color=COLOR_NEO4J, label="Neo4j (MATCH su nodi sparsi)"),
            mpatches.Patch(color=COLOR_PG,    label="PostgreSQL (Sequential Scan)"),
            plt.Line2D([0], [0], color="#ffffff", linewidth=2.5,
                       solid_capstyle="round", label="±1σ (barre di errore)"),
            plt.Line2D([0], [0], color="#ffffff", linewidth=2, label="Mediana"),
            plt.Line2D([0], [0], color="#e74c3c", linewidth=1.5,
                       linestyle="--", label="P90"),
        ]
        ax.legend(handles=legend_items, fontsize=9, loc="upper right")

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=12)
        ax.set_ylabel("Latenza (ms)", fontsize=11)
        ax.set_title(
            f"Aggregazione Globale: Neo4j vs PostgreSQL\n"
            f"PostgreSQL {speedup}× più veloce  |  Barre di errore = ±1σ",
            fontsize=12, fontweight="bold", color="#ffffff", pad=14
        )
        ax.grid(axis="y", zorder=0)
        ax.set_axisbelow(True)

        # Disegna una freccia bidire che indica la differenza
        if means[0] > means[1]:
            ax.annotate(
                "", xy=(1, means[1]), xytext=(1, means[0]),
                arrowprops=dict(arrowstyle="<->", color="#f39c12",
                                lw=1.5, connectionstyle="arc3,rad=0")
            )

        fig.tight_layout()
        out_path = os.path.join(out_dir, "aggregation_plot.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# GRAFICO 2 – Bulk Insert: Throughput e Latenza
# ---------------------------------------------------------------------------

def plot_bulk_insert(results: dict, out_dir: str):
    t42 = results.get("test_4_2_bulk_insert", {})
    if not t42:
        print("[SKIP] Dati test_4_2_bulk_insert non disponibili.")
        return

    neo4j_stats = t42.get("neo4j", {})
    pg_stats    = t42.get("postgresql", {})
    neo4j_tps   = t42.get("neo4j_throughput_rps", 0)
    pg_tps      = t42.get("pg_throughput_rps", 0)
    speedup     = t42.get("speedup_pg_vs_neo4j", "N/A")
    n_records   = t42.get("records", 0)

    if not neo4j_stats or not pg_stats:
        print("[SKIP] Statistiche incomplete per il test 4.2.")
        return

    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(1, 2, figsize=(13, 6))

        # ---- Subplot A: Throughput (record/s) ----
        ax1 = axes[0]
        labels = ["Neo4j\n(UNWIND batch)", "PostgreSQL\n(COPY)"]
        tps_vals = [neo4j_tps, pg_tps]
        colors   = [COLOR_NEO4J, COLOR_PG]

        bars1 = ax1.bar(labels, tps_vals, width=0.5, color=colors,
                        alpha=0.85, edgecolor="#ffffff22", zorder=3)
        for bar, val in zip(bars1, tps_vals):
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                val + max(tps_vals) * 0.02,
                f"{val:,.0f}\nrec/s",
                ha="center", va="bottom", fontsize=10, fontweight="bold",
                color="#ffffff"
            )

        ax1.set_ylabel("Throughput (record/s)", fontsize=11)
        ax1.set_title(
            "Throughput Bulk Insert",
            fontsize=11, fontweight="bold", pad=10
        )
        ax1.grid(axis="y", zorder=0)
        ax1.set_axisbelow(True)
        ax1.yaxis.set_major_formatter(ticker.FuncFormatter(
            lambda x, _: f"{x:,.0f}"
        ))

        # ---- Subplot B: Latenza media ----
        ax2 = axes[1]
        lat_vals = [neo4j_stats["mean_ms"], pg_stats["mean_ms"]]
        bars2 = ax2.bar(labels, lat_vals, width=0.5, color=colors,
                        alpha=0.85, edgecolor="#ffffff22", zorder=3)

        # Whisker min-max
        for xi, (lbl, mean, mn, mx) in enumerate(zip(
            labels,
            lat_vals,
            [neo4j_stats["min_ms"], pg_stats["min_ms"]],
            [neo4j_stats["max_ms"], pg_stats["max_ms"]]
        )):
            ax2.errorbar(xi, mean, yerr=[[mean - mn], [mx - mean]],
                         fmt="none", color="#e0e0e0", capsize=8,
                         capthick=1.5, linewidth=1.5, zorder=4)

        for bar, val in zip(bars2, lat_vals):
            ax2.text(
                bar.get_x() + bar.get_width() / 2,
                val + max(lat_vals) * 0.02,
                f"{val:,.1f} ms",
                ha="center", va="bottom", fontsize=10, fontweight="bold",
                color="#ffffff"
            )

        ax2.set_ylabel("Latenza media (ms)", fontsize=11)
        ax2.set_title(
            "Latenza Media Bulk Insert",
            fontsize=11, fontweight="bold", pad=10
        )
        ax2.grid(axis="y", zorder=0)
        ax2.set_axisbelow(True)

        fig.suptitle(
            "Test 4.2 – Bulk Insert (Ingestione Online OLTP)",
            fontsize=13, fontweight="bold", y=1.02, color="#ffffff"
        )
        
        fig.text(
            0.02, -0.02,
            "Nota metodologica: questo test valuta l'ingestione transazionale online via driver. "
            "Per import batch offline, Neo4j dispone di strumenti dedicati (neo4j-admin import) molto più veloci di UNWIND.",
            fontsize=8, color="#888888", ha="left"
        )

        fig.tight_layout()
        out_path = os.path.join(out_dir, "bulk_insert_plot.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Genera i grafici dello Scenario 4")
    parser.add_argument(
        "--results",
        default=os.path.join(os.path.dirname(__file__), "results.json"),
        help="Percorso al file results.json (default: ./results.json)"
    )
    args = parser.parse_args()

    if not os.path.exists(args.results):
        print(f"[ERR] File non trovato: {args.results}")
        print("      Esegui prima scenario4_benchmark.py per generare results.json")
        sys.exit(1)

    out_dir = os.path.dirname(os.path.abspath(args.results))
    results = load_results(args.results)

    print(f"[*] Generazione grafici da: {args.results}")
    print(f"[*] Output directory: {out_dir}\n")

    plot_aggregation(results, out_dir)
    plot_bulk_insert(results, out_dir)

    print("\n[*] Grafici generati:")
    for name in ["aggregation_plot.svg", "bulk_insert_plot.svg"]:
        path = os.path.join(out_dir, name)
        if os.path.exists(path):
            size_kb = os.path.getsize(path) / 1024
            print(f"    {name}  ({size_kb:.1f} KB)")

    print("\n[NOTE] Il test 4.3 (Esplosione Combinatoria) non produce grafici:")
    print("       il risultato è binario (timeout/OOM vs OK) e leggibile")
    print("       dalla tabella testuale nel riepilogo finale.")


if __name__ == "__main__":
    main()
