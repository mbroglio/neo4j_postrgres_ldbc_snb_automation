import json
import matplotlib.pyplot as plt
import numpy as np
import os


def generate_plots():
    dir_path = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(dir_path, "results.json")

    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return

    with open(json_path, "r") as f:
        data = json.load(f)

    # 1. Multi-hop Plot (Line Chart with Log Scale)
    hops = [1, 2, 3, 4]
    neo4j_ms = []
    pg_ms = []

    mh_data = data["test_1_1_multihop"]
    for h in hops:
        neo4j_ms.append(mh_data[f"hop_{h}"]["neo4j"]["mean_ms"])
        pg_ms.append(mh_data[f"hop_{h}"]["postgresql"]["mean_ms"])

    plt.figure(figsize=(8, 5))
    plt.plot(
        hops,
        neo4j_ms,
        marker="o",
        label="Neo4j",
        color="#2ca02c",
        linewidth=2.5,
        markersize=8,
    )
    plt.plot(
        hops,
        pg_ms,
        marker="s",
        label="PostgreSQL",
        color="#1f77b4",
        linewidth=2.5,
        markersize=8,
    )
    plt.yscale("log")
    plt.xticks(hops, [f"{h} Hop" for h in hops])
    plt.xlabel("Profondità", fontsize=11)
    plt.ylabel("Latenza Media (ms)", fontsize=11)
    plt.title("Neo4j vs PostgreSQL", fontsize=13, pad=15)

    plt.legend(fontsize=11)
    plt.grid(True, which="both", ls="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(os.path.join(dir_path, "multihop_plot.svg"), dpi=300)
    plt.close()

    # 2. Triangle Plot (Bar Chart)
    tri_data = data["test_1_2_triangle"]["global_triangles"]
    labels = ["Conteggio Globale Triangoli"]
    neo4j_tri = [tri_data["neo4j"]["mean_ms"]]
    pg_tri = [tri_data["postgresql"]["mean_ms"]]

    x = np.arange(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(6, 5))
    rects1 = ax.bar(x - width / 2, neo4j_tri, width, label="Neo4j", color="#2ca02c")
    rects2 = ax.bar(x + width / 2, pg_tri, width, label="PostgreSQL", color="#1f77b4")

    ax.set_ylabel("Tempo Medio di Esecuzione (ms)", fontsize=11)
    ax.set_title("Efficienza Aggregazione Globale", fontsize=13, pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.legend(fontsize=11)

    plt.tight_layout()
    plt.savefig(os.path.join(dir_path, "triangle_plot.svg"), dpi=300)
    plt.close()

    print("Grafici generati con successo in", dir_path)


if __name__ == "__main__":
    generate_plots()
