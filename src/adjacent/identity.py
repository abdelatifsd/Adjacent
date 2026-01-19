def normalize_id(x: str) -> str:
    return x.strip().lower()


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    a, b = normalize_id(a), normalize_id(b)
    return (a, b) if a < b else (b, a)
