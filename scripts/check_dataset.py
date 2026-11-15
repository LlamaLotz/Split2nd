"""Inspect a built dataset parquet and render a QA contact sheet.

Usage:
  python scripts/check_dataset.py data/olympic/train.parquet
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    path = Path(args.parquet)
    df = pd.read_parquet(path)
    print(f"{path}: {len(df)} rows, {len(df.columns)} columns")
    print("columns:", list(df.columns))
    if "label" in df.columns:
        print("\nlabels:\n" + df["label"].value_counts().to_string())
    if "split" in df.columns:
        print("\nsplit:\n" + df["split"].value_counts().to_string())
    if "task" in df.columns:
        print(f"\ntasks: {df['task'].nunique()}")
    if "prompt" in df.columns:
        print(f"unique prompts: {df['prompt'].nunique()}")
    if "image" not in df.columns:
        return

    sizes = []
    bad = 0
    for b in df["image"].head(200):
        arr = np.frombuffer(b, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            bad += 1
        else:
            sizes.append(img.shape)
    print(f"\nimages checked: {min(200, len(df))}, bad: {bad}")
    if sizes:
        print("shape examples:", sizes[:5])
    print(f"mean image bytes: {int(df['image'].map(len).mean())}")

    sample = df.sample(min(12, len(df)), random_state=0).reset_index(drop=True)
    panels = []
    for _, row in sample.iterrows():
        img = cv2.imdecode(np.frombuffer(row["image"], dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        panel = cv2.resize(img, (480, 160))
        label = str(row.get("label", ""))
        cv2.rectangle(panel, (0, 0), (300, 24), (0, 0, 0), -1)
        cv2.putText(panel, label, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        panels.append(panel)
    if panels:
        rows = []
        for i in range(0, len(panels), 2):
            pair = panels[i:i + 2]
            if len(pair) == 1:
                pair.append(np.zeros_like(panels[0]))
            rows.append(np.hstack(pair))
        sheet = np.vstack(rows)
        out = Path(args.out) if args.out else path.with_name("qa_contact.jpg")
        cv2.imwrite(str(out), sheet)
        print("wrote", out)


if __name__ == "__main__":
    main()
