"""Upload the Olympic boxing dataset to Adaption and optionally train.

Usage:
  pip install "adaption>=0.9.0"
  export ADAPTION_API_KEY="pt_live_..."
  python scripts/adaption_upload.py --estimate
  python scripts/adaption_upload.py              # upload + adapt, no training
  python scripts/adaption_upload.py --train      # upload + adapt + AutoScientist

The script never uploads val.parquet; that split is for local evaluation.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "data" / "olympic" / "train.parquet"
MAPPING = {"prompt": "prompt", "completion": "label", "image": "image"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    ap.add_argument("--estimate", action="store_true", help="upload + cost estimate only")
    ap.add_argument("--train", action="store_true", help="launch AutoScientist after adapting")
    ap.add_argument("--model", default="google/gemma-3-4b-it-VLM")
    ap.add_argument("--target-win-rate", type=float, default=0.8)
    args = ap.parse_args()

    try:
        from adaption import Adaption
    except ImportError:
        raise SystemExit('Install the SDK first: pip install "adaption>=0.9.0"')

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"missing dataset: {path}")

    client = Adaption()
    print(f"uploading {path} ({path.stat().st_size / 1e6:.1f} MB)")
    ds = client.datasets.upload_file(str(path))
    print("dataset_id", ds.dataset_id)

    status = client.datasets.wait_for_completion(ds.dataset_id, timeout=3600)
    print("ingested", status.status, "rows", getattr(status, "row_count", None))

    estimate = client.datasets.adapt(
        ds.dataset_id,
        column_mapping=MAPPING,
        estimate=True,
    )
    print(
        "estimated credits",
        estimate.estimated_credits_consumed,
        "minutes",
        estimate.estimated_minutes,
        "multimodal",
        estimate.multimodal_pricing_applied,
    )
    if args.estimate:
        print("estimate only; no adaptation run started")
        return

    try:
        client.datasets.adapt(ds.dataset_id, column_mapping=MAPPING)
        client.datasets.wait_for_completion(ds.dataset_id, timeout=3600)
    except Exception as exc:
        print("\nAdaptive Data failed:", type(exc).__name__, exc)
        print(
            "If this is the multimodal/fine-tuning restriction, use the Adaption app's "
            "VLM dataset path instead of the API, or contact support@adaptionlabs.ai."
        )
        raise SystemExit(1)

    print("adapted dataset:", ds.dataset_id)
    if not args.train:
        print("pass --train to launch AutoScientist")
        return

    run = client.autoscientist.create(
        dataset_id=ds.dataset_id,
        model=args.model,
        training_method="instruction",
        target_win_rate=args.target_win_rate,
    )
    print("run", run.id, run.status)
    run = client.autoscientist.wait_for_completion(run.id)
    print(
        "status",
        run.status,
        "best_win_rate",
        run.best_win_rate,
        "download_available",
        run.download_available,
    )
    if run.status == "failed":
        print("error", run.error)
        return
    if run.download_available:
        out = ROOT / "olympic-vlm.tgz"
        with client.autoscientist.with_streaming_response.download(run.id) as response:
            response.stream_to_file(str(out))
        print("wrote", out)


if __name__ == "__main__":
    main()
