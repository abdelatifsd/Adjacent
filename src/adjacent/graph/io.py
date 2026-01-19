import json
from pathlib import Path
from typing import Dict, Any, Iterator, Tuple


# ----------------------------
# IO
# ----------------------------
def iter_records(input_path: Path) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """
    Yield (lineno, record) from either:
      - .jsonl (one object per line)
      - .json  (a JSON array of objects)

    We use 'lineno' for error messages. For .json, lineno is the array index + 1.
    """
    suffix = input_path.suffix.lower()

    if suffix == ".jsonl":
        with input_path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"Invalid JSON on line {lineno} in {input_path}: {e}"
                    ) from e
                if not isinstance(rec, dict):
                    raise ValueError(
                        f"Line {lineno} in {input_path} must be a JSON object."
                    )
                yield lineno, rec
        return

    if suffix == ".json":
        with input_path.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in {input_path}: {e}") from e

        if not isinstance(data, list):
            raise ValueError(f"{input_path} must be a JSON array of objects (list).")

        for idx, rec in enumerate(data, start=1):
            if not isinstance(rec, dict):
                raise ValueError(f"Item {idx} in {input_path} must be a JSON object.")
            yield idx, rec
        return

    raise ValueError(
        f"Unsupported input format: {input_path}. Expected .json or .jsonl"
    )
