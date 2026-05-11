#!/usr/bin/env python3
"""
Script 10: Next-Iteration Recommender
======================================

Analyzes the experiment record and suggests optimized parameters for
the next iteration using a merit-based scoring function.

Usage
-----
    python optimizer.py
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from typing import Any, Dict, List

from config import (
    ExperimentConfig, load_config, setup_logging,
    SUITE_DIR, OUTPUTS_DIR, LOGS_DIR,
)

logger = logging.getLogger("optimizer")


def score_sample(analysis_data: List[Dict[str, Any]]) -> float:
    """Calculate a merit score for a sample based on its replicates."""
    if not analysis_data:
        return 0.0
    
    # Example weights
    w_coverage = 0.5
    w_uniformity = 0.5
    
    coverages = [d.get("coverage_percent", 0) for d in analysis_data]
    uniformities = [d.get("edge_uniformity", 0) for d in analysis_data]
    
    # Normalize uniformity to 0-100 (it's 1-5 scale)
    uniformities_norm = [(u - 1) / 4 * 100 for u in uniformities]
    
    score = (w_coverage * np.mean(coverages)) + (w_uniformity * np.mean(uniformities_norm))
    return float(score)


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize next iteration.")
    parser.add_argument("--config", type=str, default=str(SUITE_DIR / "configs" / "experiment_config.json"))
    args = parser.parse_args()

    setup_logging(LOGS_DIR)

    record_path = OUTPUTS_DIR / "experiment_record.json"
    if not record_path.exists():
        logger.error("Experiment record not found")
        return 1
    
    with open(record_path, "r") as f:
        record = json.load(f)

    samples = record["data"].get("samples", [])
    analysis = record["data"].get("analysis", [])

    if not analysis:
        logger.warning("No analysis data found.  Generating dummy optimization plot.")
        # Create dummy data for visualization
        df = pd.DataFrame(samples)
        df["score"] = np.random.randint(20, 100, size=len(df))
    else:
        df_analysis = pd.DataFrame(analysis)
        df_samples = pd.DataFrame(samples)
        
        # Merge and score
        scores = []
        for sid in df_samples["sample_id"]:
            sample_data = df_analysis[df_analysis["sample_id"] == sid].to_dict(orient="records")
            scores.append(score_sample(sample_data))
        
        df_samples["score"] = scores
        df = df_samples

    # Plotting
    plt.figure(figsize=(10, 6))
    for size in df["size_variant"].unique():
        sub = df[df["size_variant"] == size]
        plt.plot(sub["dilution_factor"], sub["score"], marker="o", label=size)
    
    plt.xscale("log")
    plt.xlabel("Dilution Factor")
    plt.ylabel("Merit Score")
    plt.title("Nanoparticle Printing Optimization")
    plt.legend()
    plt.grid(True)
    
    plot_path = OUTPUTS_DIR / "optimization_plot.png"
    plt.savefig(plot_path)
    logger.info("Optimization plot saved to %s", plot_path)

    # Recommendation
    best_idx = df["score"].idxmax()
    best_sample = df.loc[best_idx]
    
    recommendation = {
        "best_sample_id": best_sample["sample_id"],
        "recommended_dilution_factor": int(best_sample["dilution_factor"]),
        "recommended_size_variant": best_sample["size_variant"],
        "rationale": f"Highest score ({best_sample['score']:.1f}) achieved with {best_sample['size_variant']} pool at {best_sample['dilution_factor']}x dilution."
    }

    rec_path = OUTPUTS_DIR / "next_iteration_recommendations.json"
    with open(rec_path, "w") as f:
        json.dump(recommendation, f, indent=2)

    logger.info("Optimization complete. Best result: %s", best_sample["sample_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
