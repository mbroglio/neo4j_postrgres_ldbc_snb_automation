#!/usr/bin/env python3
"""
=============================================================================
SCENARIO 3 – Generazione Grafici
=============================================================================
Legge results.json e produce due grafici SVG:

  1. fault_tolerance_timeline.svg
     Timeline delle scritture durante il fault tolerance test:
     - asse x = tempo relativo al crash (secondi)
     - asse y = latenza di ogni transazione (ms)
     - colore verde = scrittura andata a buon fine
     - colore rosso = scrittura fallita (finestra di downtime)
     Il grafico evidenzia visivamente la "finestra nera" del downtime Raft
     e il momento preciso della rielezione del leader.

  2. load_balancing.svg
     Distribuzione del carico di lettura sui nodi del cluster:
     - grafico a barre orizzontali
     - ogni barra = un nodo server (primario o secondario)
     - larghezza proporzionale al numero di query servite
     Evidenzia se il routing server-side bilancia il carico sui secondari.

Utilizzo:
  python3 plot_scenario3.py [--results path/to/results.json]
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
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", "matplotlib"])
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches


# ---------------------------------------------------------------------------
# Stile comune coerente con i plot degli scenari 1 e 2
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

COLOR_OK    = "#2ecc71"   # verde – scrittura riuscita
COLOR_FAIL  = "#e74c3c"   # rosso – scrittura fallita
COLOR_SHADE = "#c0392b"   # sfondo finestra downtime
COLOR_BAR1  = "#4a9eff"   # blu – nodi primari
COLOR_BAR2  = "#f39c12"   # arancio – nodi secondari


def load_results(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# GRAFICO 1 – Timeline Fault Tolerance
# ---------------------------------------------------------------------------

def plot_fault_tolerance_timeline(results: dict, out_dir: str):
    ft = results.get("fault_tolerance", {})
    if not ft or ft.get("skipped"):
        print("[SKIP] Dati fault_tolerance non disponibili.")
        return

    timeline = ft.get("timeline", {})
    records  = timeline.get("write_records", [])   # [(t_rel_s, ok, lat_ms), ...]
    downtime = ft.get("downtime_ms")

    if not records:
        print("[SKIP] Nessun record di scrittura nella timeline.")
        return

    # Separa per esito
    t_ok    = [r[0] for r in records if r[1]]
    lat_ok  = [r[2] for r in records if r[1]]
    t_fail  = [r[0] for r in records if not r[1]]
    lat_fail= [r[2] for r in records if not r[1]]

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(12, 5))

        # Sfondo finestra di downtime
        if t_fail:
            t_fail_min = min(t_fail)
            t_fail_max = max(t_fail)
            ax.axvspan(t_fail_min, t_fail_max, alpha=0.15, color=COLOR_SHADE,
                       label=f"Finestra downtime ({downtime:.0f} ms)" if downtime else "Finestra downtime")

        # Linea verticale: momento del crash (t=0)
        ax.axvline(x=0, color="#e74c3c", linewidth=1.5, linestyle="--",
                   label="Crash Leader (t=0)")

        # Linea verticale: ripristino
        if ft.get("t_recovery_s") is not None:
            ax.axvline(x=ft["t_recovery_s"], color=COLOR_OK, linewidth=1.5, linestyle="--",
                       label=f"Nuovo leader operativo (+{ft['t_recovery_s']:.2f}s)")

        # Scatter scritture
        if t_ok:
            ax.scatter(t_ok, lat_ok, c=COLOR_OK, s=12, alpha=0.7,
                       label=f"Scrittura OK ({len(t_ok)})")
        if t_fail:
            ax.scatter(t_fail, lat_fail, c=COLOR_FAIL, s=18, alpha=0.9, marker="x",
                       label=f"Scrittura FALLITA ({len(t_fail)})")

        ax.set_xlabel("Tempo relativo al crash del leader (s)", fontsize=11)
        ax.set_ylabel("Latenza transazione (ms)", fontsize=11)
        ax.set_title(
            "Timeline Failover Raft",
            fontsize=13, fontweight="bold", color="#ffffff", pad=12
        )
        ax.legend(loc="upper right", fontsize=9)
        ax.grid(True)

        fig.tight_layout()
        out_path = os.path.join(out_dir, "fault_tolerance_timeline.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# GRAFICO 2 – Distribuzione del Carico (Load Balancing)
# ---------------------------------------------------------------------------

def plot_load_balancing(results: dict, out_dir: str):
    rs   = results.get("read_scalability", {})
    dist = rs.get("server_distribution", {})

    if not dist:
        print("[SKIP] Dati server_distribution non disponibili.")
        return

    # Ordina per numero di query (desc)
    items = sorted(dist.items(), key=lambda x: -x[1]["count"])
    labels = [addr for addr, _ in items]
    counts = [v["count"] for _, v in items]
    pcts   = [v["percent"] for _, v in items]
    lats   = [v["mean_lat_ms"] for _, v in items]

    # Colori: secondari tipicamente hanno porte 7690/7691
    colors = []
    for lbl in labels:
        is_secondary = any(p in lbl for p in ["7690", "7691", "secondary"])
        colors.append(COLOR_BAR2 if is_secondary else COLOR_BAR1)

    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(1, 2, figsize=(14, max(4, len(labels) * 0.8 + 2)))

        # -- Sottografico A: barre orizzontali (conteggio query) --
        ax1 = axes[0]
        bars = ax1.barh(labels, counts, color=colors, alpha=0.85, edgecolor="#ffffff22")
        for bar, pct in zip(bars, pcts):
            ax1.text(
                bar.get_width() + max(counts) * 0.01,
                bar.get_y() + bar.get_height() / 2,
                f"{pct:.1f}%",
                va="center", ha="left", fontsize=9, color="#e0e0e0"
            )
        ax1.set_xlabel("Query servite", fontsize=10)
        ax1.set_title("Load Balancing", fontsize=11,
                      fontweight="bold", pad=8)
        ax1.grid(axis="x")
        ax1.invert_yaxis()

        # Legenda colori
        p_patch = mpatches.Patch(color=COLOR_BAR1, label="Nodo Primario")
        s_patch = mpatches.Patch(color=COLOR_BAR2, label="Nodo Secondario")
        ax1.legend(handles=[p_patch, s_patch], fontsize=9, loc="lower right")

        # -- Sottografico B: latenza media per nodo --
        ax2 = axes[1]
        bars2 = ax2.barh(labels, lats, color=colors, alpha=0.85, edgecolor="#ffffff22")
        for bar, lat in zip(bars2, lats):
            ax2.text(
                bar.get_width() + max(lats) * 0.01,
                bar.get_y() + bar.get_height() / 2,
                f"{lat:.1f} ms",
                va="center", ha="left", fontsize=9, color="#e0e0e0"
            )
        ax2.set_xlabel("Latenza media (ms)", fontsize=10)
        ax2.set_title("Latenza di Lettura", fontsize=11,
                      fontweight="bold", pad=8)
        ax2.grid(axis="x")
        ax2.invert_yaxis()

        fig.tight_layout()
        out_path = os.path.join(out_dir, "load_balancing.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Genera i grafici dello Scenario 3")
    parser.add_argument(
        "--results",
        default=os.path.join(os.path.dirname(__file__), "results.json"),
        help="Percorso al file results.json (default: ./results.json)"
    )
    args = parser.parse_args()

    if not os.path.exists(args.results):
        print(f"[ERR] File non trovato: {args.results}")
        print("      Esegui prima scenario3_benchmark.py per generare results.json")
        sys.exit(1)

    out_dir = os.path.dirname(os.path.abspath(args.results))
    results = load_results(args.results)

    print(f"[*] Generazione grafici da: {args.results}")
    print(f"[*] Output directory: {out_dir}\n")

    plot_fault_tolerance_timeline(results, out_dir)
    plot_load_balancing(results, out_dir)

    print("\n[*] Grafici generati:")
    for name in ["fault_tolerance_timeline.svg", "load_balancing.svg"]:
        path = os.path.join(out_dir, name)
        if os.path.exists(path):
            size_kb = os.path.getsize(path) / 1024
            print(f"    {name}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
