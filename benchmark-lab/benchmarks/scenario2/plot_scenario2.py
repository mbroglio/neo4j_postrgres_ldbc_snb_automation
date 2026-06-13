#!/usr/bin/env python3
"""
=============================================================================
SCENARIO 2 – Generazione Grafici
=============================================================================
Legge results.json (da archive_SF0.1/ e archive_SF1/) e produce i grafici
SVG in dark-mode coerente con gli scenari 1, 3, 4:

  1. read_committed_plot.svg
     Latenza lettura vs scrittura netta su SF 0.1 e SF 1.

  2. lost_update_plot.svg
     Lost update medi (non-atomica vs atomica) + distribuzione per trial.

  3. deadlock_plot.svg
     Tempo di rilevazione deadlock (media ±σ, P90, Max).

Utilizzo:
  python3 plot_scenario2.py [--results-sf01 path] [--results-sf1 path] [--out dir]
=============================================================================
"""

import json
import os
import sys
import argparse

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
# Stile dark-mode identico agli altri scenari
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

COLOR_READ   = "#4a9eff"
COLOR_WRITE  = "#e74c3c"
COLOR_ATOMIC = "#2ecc71"
COLOR_NON_AT = "#e74c3c"
COLOR_DEADL  = "#9b59b6"
COLOR_OK     = "#2ecc71"


def load_results(path):
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# GRAFICO 1 – Read Committed: latenza lettura vs scrittura netta
# ---------------------------------------------------------------------------
def plot_read_committed(results_sf01, results_sf1, out_dir):
    labels_sf, read_means, read_stds, write_net, dirty = [], [], [], [], []

    for sf_label, rc_data in [("SF 0.1", results_sf01.get("read_committed", {})),
                               ("SF 1",   results_sf1.get("read_committed", {}))]:
        if not rc_data:
            continue
        labels_sf.append(sf_label)
        rl = rc_data.get("read_latency", {})
        read_means.append(rl.get("mean_ms", 0))
        read_stds.append(rl.get("stdev_ms", 0))
        write_net.append(rc_data.get("net_write_latency_ms", 0))
        dirty.append(rc_data.get("dirty_reads_detected", 0))

    if not labels_sf:
        print("[SKIP] Dati read_committed non disponibili.")
        return

    x     = np.arange(len(labels_sf))
    width = 0.32

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(9, 6))

        b_read  = ax.bar(x - width/2, read_means, width, label="Latenza Lettura (media)",
                         color=COLOR_READ,  alpha=0.85, edgecolor="#ffffff22", zorder=3)
        b_write = ax.bar(x + width/2, write_net,  width,
                         label="Latenza Scrittura netta (escluso sleep 75ms)",
                         color=COLOR_WRITE, alpha=0.85, edgecolor="#ffffff22", zorder=3)

        ax.errorbar(x - width/2, read_means, yerr=read_stds,
                    fmt="none", color="#e0e0e0", capsize=6, capthick=1.5,
                    linewidth=1.5, zorder=5)

        for bar, val in zip(b_read, read_means):
            ax.text(bar.get_x() + bar.get_width()/2, val + 0.3,
                    f"{val:.2f} ms", ha="center", va="bottom",
                    fontsize=9, fontweight="bold", color="#ffffff")

        for bar, val in zip(b_write, write_net):
            ax.text(bar.get_x() + bar.get_width()/2, val + 0.3,
                    f"{val:.1f} ms", ha="center", va="bottom",
                    fontsize=9, fontweight="bold", color="#ffffff")

        top = max(read_means + write_net) if (read_means or write_net) else 10
        for xi, dr in zip(x, dirty):
            msg = f"Dirty Read: {dr}\nIsolamento OK" if dr == 0 else f"{dr} Dirty Read!"
            ax.annotate(msg, xy=(xi, 0), xytext=(xi, top * 0.45),
                        ha="center", fontsize=9, fontweight="bold",
                        color=COLOR_OK if dr == 0 else "#e74c3c")

        ax.set_xticks(x)
        ax.set_xticklabels(labels_sf, fontsize=12)
        ax.set_ylabel("Latenza (ms)", fontsize=11)
        ax.set_title(
            "Test 2.1 – Read Committed: Latenza Lettura vs Scrittura Netta\n"
            "Barre di errore = ±1σ  |  Scrittura netta = write_mean − 75ms (sleep deliberato)",
            fontsize=11, fontweight="bold", color="#ffffff", pad=14
        )
        ax.legend(fontsize=10)
        ax.grid(axis="y", alpha=0.3, zorder=0)
        ax.set_axisbelow(True)

        fig.text(0.02, 0.01,
            "Nota: lo sleep deliberato (50-100ms) serve a creare la finestra di Dirty Read – "
            "non è parte del costo operativo del motore.",
            fontsize=7, color="#888888", ha="left")

        fig.tight_layout()
        out_path = os.path.join(out_dir, "read_committed_plot.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# GRAFICO 2 – Lost Update: distribuzione per trial + medie
# ---------------------------------------------------------------------------
def plot_lost_update(results_sf01, results_sf1, out_dir):
    lu01 = results_sf01.get("lost_update", {})
    lu1  = results_sf1.get("lost_update", {})
    lu_main     = lu01 if lu01 else lu1
    sf_main_lbl = "SF 0.1" if lu01 else "SF 1"

    if not lu_main:
        print("[SKIP] Dati lost_update non disponibili.")
        return

    na       = lu_main.get("non_atomic", {})
    at       = lu_main.get("atomic", {})
    na_trials = na.get("lost_updates_per_trial", [])
    at_trials = at.get("lost_updates_per_trial", [])
    expected  = na.get("expected_value", 10)

    with plt.rc_context(STYLE):
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 6))

        # ---- Subplot A: medie ----
        labels = ["Non Atomica\n(vulnerabile)", "Atomica\n(robusta)"]
        means  = [na.get("mean_lost", 0), at.get("mean_lost", 0)]
        colors = [COLOR_NON_AT, COLOR_ATOMIC]

        bars = ax1.bar(labels, means, width=0.45, color=colors,
                       alpha=0.85, edgecolor="#ffffff22", zorder=3)
        ax1.axhline(y=0, color=COLOR_OK, linewidth=1.5, linestyle="--",
                    label="Obiettivo: 0 lost update", zorder=2)

        for bar, val in zip(bars, means):
            ax1.text(bar.get_x() + bar.get_width()/2, val + 0.1,
                     f"{val:.2f}\nlost/trial", ha="center", va="bottom",
                     fontsize=11, fontweight="bold", color="#ffffff")

        ax1.set_ylabel("Lost Update medi per trial", fontsize=11)
        ax1.set_title("Media Lost Update per Strategia", fontsize=11,
                      fontweight="bold", pad=10)
        ax1.legend(fontsize=9)
        ax1.grid(axis="y", alpha=0.3, zorder=0)
        ax1.set_axisbelow(True)

        # ---- Subplot B: distribuzione per trial ----
        n = max(len(na_trials), len(at_trials))
        tx = list(range(1, n+1))

        if na_trials:
            ax2.scatter(tx[:len(na_trials)], na_trials,
                        color=COLOR_NON_AT, alpha=0.75, s=60, zorder=4,
                        label=f"Non Atomica (μ={na.get('mean_lost',0):.2f})")
            ax2.axhline(na.get("mean_lost", 0), color=COLOR_NON_AT,
                        linewidth=1.5, linestyle="--", alpha=0.6)

        if at_trials:
            ax2.scatter(tx[:len(at_trials)], at_trials,
                        color=COLOR_ATOMIC, alpha=0.75, s=60, zorder=4,
                        marker="^", label=f"Atomica (μ={at.get('mean_lost',0):.2f})")
            ax2.axhline(at.get("mean_lost", 0), color=COLOR_ATOMIC,
                        linewidth=1.5, linestyle="--", alpha=0.6)

        ax2.set_xlabel("Trial (#)", fontsize=11)
        ax2.set_ylabel("Lost Update rilevati", fontsize=11)
        ax2.set_title(
            f"Distribuzione per Trial ({sf_main_lbl})\n"
            f"{expected} thread per trial, incremento=1 ciascuno",
            fontsize=11, fontweight="bold", pad=10
        )
        ax2.legend(fontsize=10)
        ax2.grid(alpha=0.3, zorder=0)
        ax2.set_axisbelow(True)

        fig.suptitle("Test 2.2 – Simulazione Lost Update: Non Atomica vs Atomica",
                     fontsize=13, fontweight="bold", color="#ffffff")
        fig.tight_layout()
        out_path = os.path.join(out_dir, "lost_update_plot.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# GRAFICO 3 – Deadlock Detection: tempo rilevazione
# ---------------------------------------------------------------------------
def plot_deadlock(results_sf01, results_sf1, out_dir):
    labels_sf, means, stds, p90s, maxs_ = [], [], [], [], []

    for sf_label, dl in [("SF 0.1", results_sf01.get("deadlock", {})),
                          ("SF 1",   results_sf1.get("deadlock", {}))]:
        det = dl.get("detection_time_ms", {})
        if not det or not det.get("mean_ms"):
            continue
        labels_sf.append(sf_label)
        means.append(det["mean_ms"])
        stds.append(det.get("stdev_ms", 0))
        p90s.append(det.get("p90_ms", 0))
        maxs_.append(det.get("max_ms", 0))

    if not labels_sf:
        print("[SKIP] Dati deadlock detection_time_ms non disponibili.")
        return

    x = np.arange(len(labels_sf))
    w = 0.45

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(9, 6))

        bars = ax.bar(x, means, w, color=COLOR_DEADL, alpha=0.85,
                      edgecolor="#ffffff22", zorder=3, label="Media rilevazione (ms)")

        ax.errorbar(x, means, yerr=stds, fmt="none", color="#e0e0e0",
                    capsize=8, capthick=2, linewidth=2, zorder=5, label="±1σ")

        for xi, p90 in zip(x, p90s):
            ax.plot([xi-w/2+0.02, xi+w/2-0.02], [p90, p90],
                    color="#e74c3c", linewidth=2, linestyle="--", zorder=6,
                    label="P90" if xi == x[0] else "")

        for xi, mx in zip(x, maxs_):
            ax.plot([xi-w/2+0.04, xi+w/2-0.04], [mx, mx],
                    color="#f39c12", linewidth=1.5, linestyle=":", zorder=6,
                    alpha=0.7, label="Max" if xi == x[0] else "")

        for bar, val, p90, mx in zip(bars, means, p90s, maxs_):
            ax.text(bar.get_x() + bar.get_width()/2,
                    bar.get_height() + 0.5,
                    f"μ={val:.1f}ms\nP90={p90:.1f}ms\nMax={mx:.1f}ms",
                    ha="center", va="bottom", fontsize=9,
                    fontweight="bold", color="#ffffff")

        ax.set_xticks(x)
        ax.set_xticklabels(labels_sf, fontsize=12)
        ax.set_ylabel("Tempo rilevazione deadlock (ms)", fontsize=11)
        ax.set_title(
            "Test 2.3 – Deadlock Detection (Wait-for Graph)\n"
            "Tempo dal 2° lock request alla TransientError  |  Linea tratteggiata = P90",
            fontsize=11, fontweight="bold", color="#ffffff", pad=14
        )
        ax.legend(fontsize=10)
        ax.grid(axis="y", alpha=0.3, zorder=0)
        ax.set_axisbelow(True)

        fig.text(0.02, 0.01,
            "Meccanismo: Wait-for Graph (TransientError) – rollback automatico garantito su entrambi gli SF.",
            fontsize=7, color="#888888", ha="left")

        fig.tight_layout()
        out_path = os.path.join(out_dir, "deadlock_plot.svg")
        fig.savefig(out_path, format="svg", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] Salvato: {out_path}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    base = os.path.dirname(__file__)
    parser = argparse.ArgumentParser(description="Genera i grafici dello Scenario 2")
    parser.add_argument("--results-sf01",
        default=os.path.join(base, "archive_SF0.1", "results.json"))
    parser.add_argument("--results-sf1",
        default=os.path.join(base, "archive_SF1", "results.json"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    res01, res1 = {}, {}

    if os.path.exists(args.results_sf01):
        res01 = load_results(args.results_sf01)
        print(f"[*] Caricato SF 0.1: {args.results_sf01}")
    else:
        print(f"[WARN] Non trovato: {args.results_sf01}")

    if os.path.exists(args.results_sf1):
        res1 = load_results(args.results_sf1)
        print(f"[*] Caricato SF 1: {args.results_sf1}")
    else:
        print(f"[WARN] Non trovato: {args.results_sf1}")

    if not res01 and not res1:
        print("[ERR] Nessun results.json trovato. Riesegui scenario2_benchmark.py.")
        sys.exit(1)

    out_dir = args.out or os.path.dirname(os.path.abspath(
        args.results_sf01 if res01 else args.results_sf1))
    os.makedirs(out_dir, exist_ok=True)
    print(f"[*] Output: {out_dir}\n")

    plot_read_committed(res01, res1, out_dir)
    plot_lost_update(res01, res1, out_dir)
    plot_deadlock(res01, res1, out_dir)

    print("\n[*] Grafici generati:")
    for name in ["read_committed_plot.svg", "lost_update_plot.svg", "deadlock_plot.svg"]:
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            print(f"    {name}  ({os.path.getsize(p)/1024:.1f} KB)")

if __name__ == "__main__":
    main()
