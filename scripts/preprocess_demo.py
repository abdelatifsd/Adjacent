#!/usr/bin/env python3
"""
Preprocess Kaggle e-commerce demo data for Adjacent ingestion.

Converts integer IDs to strings to match product schema.
"""

import json
from pathlib import Path


def main():
    # Paths
    input_file = Path("data/demo/kaggle_ecommerce.json")
    output_file = Path("data/demo/kaggle_ecommerce_fixed.json")

    print(f"Reading from: {input_file}")

    # Load data
    with input_file.open() as f:
        data = json.load(f)

    print(f"Found {len(data)} products")

    # Transform: Convert ID to string
    for item in data:
        if "id" in item:
            item["id"] = str(item["id"])

    # Save
    with output_file.open("w") as f:
        json.dump(data, f, indent=2)

    print(f"✓ Saved to: {output_file}")
    print(f"✓ Processed {len(data)} products")
    print("\nSample record:")
    print(json.dumps(data[0], indent=2)[:200] + "...")


if __name__ == "__main__":
    main()
