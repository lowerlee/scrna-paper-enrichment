#!/usr/bin/env python3
"""
Validate the classifier against the labeled test set in test-data/ground_truth.json.

Usage:
    python validate.py [--ground-truth test-data/ground_truth.json]

Outputs:
    - Overall precision and recall
    - Per-confidence-bucket precision
    - Per-category recall (for positives)
    - Per-difficulty recall (for positives)
    - Failure log: papers where verdict != label
"""
import argparse
import json
import os
from collections import defaultdict

from pipeline import classifier
from pipeline.pdf import extract_abstract


def load_ground_truth(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    return data["papers"]


def run_validation(papers: list[dict], base_dir: str) -> list[dict]:
    results = []
    for i, p in enumerate(papers, 1):
        pdf_path = os.path.join(base_dir, p["source_path"])
        print(f"[{i}/{len(papers)}] {p['title'][:60]}…", end=" ", flush=True)
        try:
            abstract = extract_abstract(pdf_path)
            result = classifier.classify(p["title"], abstract)
            correct = result["verdict"] == p["label"]
            print(f"{'OK' if correct else 'FAIL'} ({result['verdict']}, {result['confidence']})")
        except Exception as exc:
            print(f"ERROR: {exc}")
            result = {"verdict": "ERROR", "confidence": "N/A", "reason": str(exc), "prompt_version": "?"}
            correct = False

        results.append({
            **p,
            "predicted": result["verdict"],
            "predicted_confidence": result["confidence"],
            "predicted_reason": result.get("reason", ""),
            "correct": correct,
        })
    return results


def compute_metrics(results: list[dict]) -> None:
    positives = [r for r in results if r["label"] == "RELEVANT"]
    negatives = [r for r in results if r["label"] == "NOT_RELEVANT"]

    tp = sum(1 for r in positives if r["predicted"] == "RELEVANT")
    fp = sum(1 for r in negatives if r["predicted"] == "RELEVANT")
    fn = sum(1 for r in positives if r["predicted"] != "RELEVANT")
    tn = sum(1 for r in negatives if r["predicted"] == "NOT_RELEVANT")

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    print("\n=== Overall ===")
    print(f"  Precision: {precision:.1%}  ({tp} TP, {fp} FP)")
    print(f"  Recall:    {recall:.1%}  ({tp} TP, {fn} FN)")
    print(f"  F1:        {f1:.1%}")
    print(f"  Specificity (TNR): {tn/(tn+fp):.1%}  ({tn} TN, {fp} FP)" if (tn + fp) else "")

    # Per-confidence-bucket precision
    print("\n=== Precision by confidence ===")
    buckets = defaultdict(lambda: {"total": 0})
    for r in results:
        if r["predicted"] not in ("RELEVANT", "NOT_RELEVANT"):
            continue
        conf = r["predicted_confidence"]
        buckets[conf]["total"] += 1
    for conf in ("HIGH", "MEDIUM", "LOW"):
        b = buckets.get(conf)
        if not b or b["total"] == 0:
            continue
        relevant_predicted = sum(
            1 for r in results if r["predicted_confidence"] == conf and r["predicted"] == "RELEVANT"
        )
        tp_c = sum(
            1 for r in results if r["predicted_confidence"] == conf
            and r["predicted"] == "RELEVANT" and r["label"] == "RELEVANT"
        )
        prec = tp_c / relevant_predicted if relevant_predicted else 0.0
        print(f"  {conf}: {prec:.1%}  ({tp_c}/{relevant_predicted} RELEVANT predictions correct)")

    # Per-category recall (positives only)
    print("\n=== Recall by category (positives) ===")
    by_cat = defaultdict(list)
    for r in positives:
        by_cat[r.get("category", "unknown")].append(r)
    for cat, group in sorted(by_cat.items()):
        cat_tp = sum(1 for r in group if r["predicted"] == "RELEVANT")
        print(f"  {cat}: {cat_tp}/{len(group)} = {cat_tp/len(group):.1%}")

    # Per-difficulty recall
    print("\n=== Recall by difficulty (positives) ===")
    for diff in ("easy", "hard"):
        group = [r for r in positives if r.get("difficulty") == diff]
        if not group:
            continue
        d_tp = sum(1 for r in group if r["predicted"] == "RELEVANT")
        print(f"  {diff}: {d_tp}/{len(group)} = {d_tp/len(group):.1%}")

    # Failures
    failures = [r for r in results if not r["correct"]]
    if failures:
        print(f"\n=== Failures ({len(failures)}) ===")
        for r in failures:
            print(f"  [{r['label']} → {r['predicted']} {r['predicted_confidence']}] {r['title'][:70]}")
            print(f"    Reason: {r['predicted_reason']}")

    # Gate check
    print()
    if recall >= 0.95:
        print(f"PASS — recall {recall:.1%} >= 95%. Daily timer can be enabled.")
    else:
        print(f"FAIL — recall {recall:.1%} < 95%. Do not enable daily timer.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", default="test-data/ground_truth.json")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(args.ground_truth))
    papers = load_ground_truth(args.ground_truth)
    print(f"Loaded {len(papers)} labeled papers from {args.ground_truth}")

    results = run_validation(papers, base_dir)
    compute_metrics(results)


if __name__ == "__main__":
    main()
