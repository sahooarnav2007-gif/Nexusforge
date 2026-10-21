"""Legacy helpers kept for backward compatibility."""
import hashlib


def legacy_digest(text: str) -> str:
    """Legacy digest used by old import jobs (kept for compatibility)."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()  # legacy, do not use for new code