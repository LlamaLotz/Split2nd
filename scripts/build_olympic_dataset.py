"""Build the aligned Olympic boxing punch dataset for Adaption.

Source:
  Kaggle "Olympic Boxing Punch Classification Video Dataset" (Stefanski et al.)
  Clip metadata: NealBeans/BoxingDataset (2,278 clips, 8 labels, 17 videos)

Output:
  data/olympic/train.parquet   (~1,822 rows)
  data/olympic/val.parquet     (~456 rows, held-out source videos)
  data/olympic/montages/*.jpg  (3-frame 320x320 panels -> 960x320)
  data/olympic/class_counts.json
  data/olympic/dataset_card.md
  data/olympic/README_ADAPTION.md
  data/olympic/samples/*.jpg
  olympic_boxing_adaption.zip

Each row: image (JPEG bytes), prompt, label, task, source_video,
start_time, end_time, start_frame, end_frame, bbox, split, source_dataset.

The montage is three frames from the clip (start / middle / end), cropped
around the referee's punch bbox with a 3x margin, so the puncher's upper
body is in frame. Labels are the eight referee classes.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "olympic"
META_PATH = ROOT / "data" / "hf" / "boxing_metadata.csv"
ZIP_PATH = ROOT / "olympic_boxing_adaption.zip"

# Best 4-task holdout from a brute-force split search: exactly 20% of rows,
# all eight labels present in both train and val.
VAL_TASKS = {
    "task_kam2_gh078416",
    "task_kam2_gh128416",
    "task_kam2_gh158416",
    "task_kam4_gh079681",
}

LABELS = {
    "head_left_hand": "HEAD_LEFT",
    "head_right_hand": "HEAD_RIGHT",
    "body_left_hand": "BODY_LEFT",
    "body_right_hand": "BODY_RIGHT",
    "block_left_hand": "BLOCK_LEFT",
    "block_right_hand": "BLOCK_RIGHT",
    "miss_left_hand": "MISS_LEFT",
    "miss_right_hand": "MISS_RIGHT",
}

PROMPT = (
    "Boxing match action. Classify the action: "
    "HEAD_LEFT, HEAD_RIGHT, BODY_LEFT, BODY_RIGHT, "
    "BLOCK_LEFT, BLOCK_RIGHT, MISS_LEFT, MISS_RIGHT. "
    "Answer with one label."
)

PANEL = 320
QUALITY = 85


def log(msg: str) -> None:
    print(msg, flush=True)


def find_kaggle_root() -> Path:
    env = os.getenv("KAGGLE_BOXING_ROOT", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
        raise SystemExit(f"KAGGLE_BOXING_ROOT does not exist: {p}")

    base = Path.home() / ".cache/kagglehub/datasets/piotrstefaskiue/olympic-boxing-punch-classification-video-dataset"
    candidates = sorted(base.glob("versions/*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for c in candidates:
        if any(c.rglob("*.mp4")) or any(c.rglob("*.MP4")):
            return c
    # Some kagglehub versions extract straight into the dataset folder.
    if any(base.rglob("*.mp4")):
        return base
    raise SystemExit(
        "Could not find the Kaggle dataset. Download it with:\n"
        "  kagglehub.dataset_download('piotrstefaskiue/olympic-boxing-punch-classification-video-dataset')\n"
        "or set KAGGLE_BOXING_ROOT to the extracted folder."
    )


def index_videos(root: Path) -> dict[str, Path]:
    videos: dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".avi"):
            videos.setdefault(p.name.lower(), p)
    log(f"indexed {len(videos)} videos under {root}")
    return videos


def find_video(row: pd.Series, videos: dict[str, Path]) -> Path | None:
    name = str(row["source_video"]).lower()
    if name in videos:
        return videos[name]
    # Fall back to the task directory name.
    task = str(row["task"]).lower()
    hits = [p for k, p in videos.items() if task in str(p.parent).lower() or task in str(p).lower()]
    return hits[0] if hits else None


def parse_bbox(value) -> tuple[float, float, float, float] | None:
    try:
        b = ast.literal_eval(value) if isinstance(value, str) else value
        if isinstance(b, (list, tuple)) and len(b) == 4:
            return tuple(float(x) for x in b)
    except Exception:
        pass
    return None


def scale_bbox(bbox, w: int, h: int):
    x1, y1, x2, y2 = bbox
    # The referee annotations were made on 1920x1080 GoPro frames. If the
    # Kaggle encode is smaller, scale the box down.
    if x2 > w or y2 > h:
        x1 *= w / 1920.0
        x2 *= w / 1920.0
        y1 *= h / 1080.0
        y2 *= h / 1080.0
    return x1, y1, x2, y2


def crop_square(frame: np.ndarray, bbox, margin: float = 3.0) -> np.ndarray:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = scale_bbox(bbox, w, h)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    side = max(x2 - x1, y2 - y1) * margin
    side = max(side, 0.35 * min(w, h))
    side = min(side, min(w, h))
    left = cx - side / 2.0
    top = cy - side / 2.0
    if left < 0:
        left = 0.0
    if top < 0:
        top = 0.0
    if left + side > w:
        left = w - side
    if top + side > h:
        top = h - side
    x1i, y1i = int(left), int(top)
    x2i, y2i = int(left + side), int(top + side)
    crop = frame[y1i:y2i, x1i:x2i]
    if crop.size == 0:
        crop = frame
    return cv2.resize(crop, (PANEL, PANEL), interpolation=cv2.INTER_AREA)


def read_at(cap: cv2.VideoCapture, idx: int) -> np.ndarray | None:
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(idx)))
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    return frame


def build() -> pd.DataFrame:
    if not META_PATH.exists():
        raise SystemExit(f"missing metadata: {META_PATH}")
    meta = pd.read_csv(META_PATH)
    log(f"metadata: {len(meta)} rows, {meta['task'].nunique()} tasks, {meta['label'].nunique()} labels")

    root = find_kaggle_root()
    videos = index_videos(root)
    if not videos:
        raise SystemExit("no video files found in the Kaggle dataset")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    montage_dir = DATA_DIR / "montages"
    sample_dir = DATA_DIR / "samples"
    montage_dir.mkdir(exist_ok=True)
    sample_dir.mkdir(exist_ok=True)

    caps: dict[str, cv2.VideoCapture] = {}
    rows = []
    missing = []
    bad = []

    for i, row in tqdm(meta.iterrows(), total=len(meta), desc="clips"):
        label = LABELS.get(str(row["label"]))
        if label is None:
            bad.append((i, f"unknown label {row['label']}"))
            continue
        video = find_video(row, videos)
        if video is None:
            missing.append((i, row["task"], row["source_video"]))
            continue
        key = str(video)
        cap = caps.get(key)
        if cap is None:
            cap = cv2.VideoCapture(key)
            if not cap.isOpened():
                missing.append((i, row["task"], row["source_video"]))
                continue
            caps[key] = cap
        fps = cap.get(cv2.CAP_PROP_FPS) or 50.0
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        t0 = float(row["start_time"])
        t1 = float(row["end_time"])
        f0 = int(round(t0 * fps))
        f1 = int(round(t1 * fps))
        if n_frames:
            f0 = min(max(0, f0), n_frames - 1)
            f1 = min(max(f0 + 1, f1), n_frames - 1)
        fm = (f0 + f1) // 2

        frames = [read_at(cap, f) for f in (f0, fm, f1)]
        if any(f is None for f in frames):
            bad.append((i, f"could not read frames {f0}-{f1}"))
            continue
        bbox = parse_bbox(row["bbox"])
        if bbox is None:
            h, w = frames[0].shape[:2]
            bbox = (w * 0.25, h * 0.2, w * 0.75, h * 0.9)
        panels = [crop_square(f, bbox) for f in frames]
        montage = np.hstack(panels)
        ok, buf = cv2.imencode(".jpg", montage, [int(cv2.IMWRITE_JPEG_QUALITY), QUALITY])
        if not ok:
            bad.append((i, "jpeg encode failed"))
            continue
        jpeg = buf.tobytes()
        clip_id = Path(str(row["file_name"])).stem
        out_name = f"{clip_id}.jpg"
        (montage_dir / out_name).write_bytes(jpeg)
        if len([p for p in sample_dir.iterdir() if p.suffix == ".jpg"]) < 60:
            (sample_dir / out_name).write_bytes(jpeg)

        rows.append(
            {
                "image": jpeg,
                "prompt": PROMPT,
                "label": label,
                "task": row["task"],
                "source_video": row["source_video"],
                "start_time": t0,
                "end_time": t1,
                "start_frame": int(row["start_frame"]),
                "end_frame": int(row["end_frame"]),
                "bbox": json.dumps(list(bbox)),
                "clip_id": clip_id,
                "split": "val" if row["task"] in VAL_TASKS else "train",
                "source_dataset": "olympic_boxing",
            }
        )

    for cap in caps.values():
        cap.release()

    if missing:
        log(f"WARNING: {len(missing)} clips had no matching video; first: {missing[:3]}")
    if bad:
        log(f"WARNING: {len(bad)} clips were skipped; first: {bad[:3]}")

    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("no rows built")
    log(f"built {len(df)} rows")
    return df


def validate(df: pd.DataFrame) -> None:
    train = df[df["split"] == "train"]
    val = df[df["split"] == "val"]
    log(f"train {len(train)} rows, val {len(val)} rows")
    for name, part in (("train", train), ("val", val)):
        counts = part["label"].value_counts().sort_index()
        log(f"{name} labels:\n{counts.to_string()}")
        missing = set(LABELS.values()) - set(counts.index)
        if missing:
            log(f"WARNING: {name} is missing labels: {sorted(missing)}")
    # decode a few images
    for i in range(min(5, len(df))):
        arr = np.frombuffer(df.iloc[i]["image"], dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None or img.shape != (PANEL, PANEL * 3, 3):
            raise SystemExit(f"bad montage at row {i}: {None if img is None else img.shape}")
    hashes = {}
    dup = 0
    for b in df["image"]:
        h = hashlib.md5(b).hexdigest()
        if h in hashes:
            dup += 1
        hashes[h] = True
    log(f"duplicate montage hashes: {dup}")


def write_outputs(df: pd.DataFrame) -> None:
    train = df[df["split"] == "train"].reset_index(drop=True)
    val = df[df["split"] == "val"].reset_index(drop=True)
    train.to_parquet(DATA_DIR / "train.parquet", index=False)
    val.to_parquet(DATA_DIR / "val.parquet", index=False)

    counts = {
        "total": len(df),
        "train": len(train),
        "val": len(val),
        "labels": df["label"].value_counts().to_dict(),
        "train_labels": train["label"].value_counts().to_dict(),
        "val_labels": val["label"].value_counts().to_dict(),
        "tasks": int(df["task"].nunique()),
        "val_tasks": sorted(VAL_TASKS),
    }
    (DATA_DIR / "class_counts.json").write_text(json.dumps(counts, indent=2))

    card = f"""# Olympic Boxing Punch Dataset (Adaption build)

## What this is

A real, aligned RGB video dataset for boxing punch/action classification,
derived from the **Olympic Boxing Punch Classification Video Dataset** by
Piotr Stefanski et al. (2021 youth boxing league, Szczyrk, Poland), using the
clip metadata published as `NealBeans/BoxingDataset`.

- **{len(df)} clips**, {df['task'].nunique()} source videos, 50 fps
- **8 referee labels** (left/right hand x head/body/hit/block/miss)
- **3-frame montage** per clip: start / middle / end of the punch
- Cropped around the referee's punch bbox with a 3x margin
- **Group split by source video**: {len(VAL_TASKS)} held-out videos for val
- No augmentation; the split is by video, not by clip

## Files

| File | Meaning |
|---|---|
| `train.parquet` | {len(train)} rows, upload this to Adaption |
| `val.parquet` | {len(val)} rows, keep local for evaluation |
| `montages/` | 960x320 JPEG montages |
| `class_counts.json` | counts per split |
| `README_ADAPTION.md` | upload/train commands |

## Parquet schema

| Column | Value |
|---|---|
| `image` | JPEG bytes, 960x320 |
| `prompt` | fixed classification prompt |
| `label` | `HEAD_LEFT`, `HEAD_RIGHT`, `BODY_LEFT`, `BODY_RIGHT`, `BLOCK_LEFT`, `BLOCK_RIGHT`, `MISS_LEFT`, `MISS_RIGHT` |
| `task` | source recording task |
| `source_video` | source MP4 |
| `start_time`, `end_time` | clip bounds (seconds) |
| `start_frame`, `end_frame` | referee punch bounds (50 fps) |
| `bbox` | punch bounding box in source-frame coordinates |
| `split` | train / val |
| `source_dataset` | olympic_boxing |

## Label mapping

| Label | Meaning |
|---|---|
| HEAD_LEFT | punch to the head with the left hand |
| HEAD_RIGHT | punch to the head with the right hand |
| BODY_LEFT | punch to the body with the left hand |
| BODY_RIGHT | punch to the body with the right hand |
| BLOCK_LEFT | left-hand punch blocked |
| BLOCK_RIGHT | right-hand punch blocked |
| MISS_LEFT | left-hand punch missed |
| MISS_RIGHT | right-hand punch missed |

## Important limits

- These are **match clips**, not front-facing shadowboxing. Expect a domain
  gap against a laptop-webcam drill.
- The labels are **not** jab/cross/hook/uppercut. They are hand x target x
  outcome. Do not claim six punch types from this dataset.
- Class imbalance is real (HEAD_LEFT is the majority). Report per-class
  recall, not just top-1.
- Faces are blurred in the source. License is non-commercial/academic use;
  cite Stefanski et al. (Entropy 26(8):617, 2024).
- `HEAD_LEFT` etc. are hand-based. Map to lead/rear only after you know the
  athlete's stance.

## Citation

Stefanski, P., Kozak, J., Jach, T. *Boxing Punch Detection with Single Static
Camera.* Entropy 26(8):617, 2024. Dataset: Kaggle
`piotrstefaskiue/olympic-boxing-punch-classification-video-dataset`.
"""
    (DATA_DIR / "dataset_card.md").write_text(card)

    readme = f"""# Adaption upload

```bash
pip install "adaption>=0.9.0"
export ADAPTION_API_KEY="pt_live_..."
```

```python
from adaption import Adaption
client = Adaption()

ds = client.datasets.upload_file("{DATA_DIR / 'train.parquet'}")
client.datasets.wait_for_completion(ds.dataset_id)

mapping = {{"prompt": "prompt", "completion": "label", "image": "image"}}
est = client.datasets.adapt(ds.dataset_id, column_mapping=mapping, estimate=True)
print(est.estimated_credits_consumed, est.estimated_minutes)
client.datasets.adapt(ds.dataset_id, column_mapping=mapping)
client.datasets.wait_for_completion(ds.dataset_id, timeout=3600)

run = client.autoscientist.create(
    dataset_id=ds.dataset_id,
    model="google/gemma-3-4b-it-VLM",
    training_method="instruction",
    target_win_rate=0.8,
)
run = client.autoscientist.wait_for_completion(run.id)
print(run.status, run.best_win_rate, run.download_available)
if run.download_available:
    with client.autoscientist.with_streaming_response.download(run.id) as r:
        r.stream_to_file("olympic-vlm.tgz")
```

Do **not** upload `val.parquet`; it is the held-out source-video split.
"""
    (DATA_DIR / "README_ADAPTION.md").write_text(readme)

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(DATA_DIR / "train.parquet", "train.parquet")
        z.write(DATA_DIR / "dataset_card.md", "dataset_card.md")
        z.write(DATA_DIR / "README_ADAPTION.md", "README_ADAPTION.md")
        z.write(DATA_DIR / "class_counts.json", "class_counts.json")
        for p in sorted((DATA_DIR / "samples").glob("*.jpg"))[:50]:
            z.write(p, f"samples/{p.name}")
    log(f"wrote {DATA_DIR / 'train.parquet'} and {DATA_DIR / 'val.parquet'}")
    log(f"wrote {ZIP_PATH}")


def main() -> None:
    df = build()
    validate(df)
    write_outputs(df)


if __name__ == "__main__":
    main()
