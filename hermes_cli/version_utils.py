"""Shared version-string helpers."""


def version_tuple(v: str) -> tuple[int, ...]:
    """Parse '0.13.0' into (0, 13, 0) for comparison. Non-numeric segments become 0."""
    parts = []
    for segment in v.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)
