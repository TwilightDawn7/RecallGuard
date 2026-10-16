"""Normalization and idempotency hashing utilities for RecallGuard."""

import hashlib
import re

def normalize_text(text: str) -> str:
    """Lowercases, trims, and converts internal whitespace sequences to '_'."""
    if not text:
        return ""
    trimmed = text.strip().lower()
    return re.sub(r"\s+", "_", trimmed)

def normalize_object(text: str) -> str:
    """Normalizes object value by trimming whitespace."""
    if not text:
        return ""
    return text.strip()

def compute_source_claim_key(source_id: str, subject: str, predicate: str, object_val: str) -> str:
    """
    Computes deterministic SHA-256 idempotency key:
    sha256(f"{source_id}:{norm_subject}:{norm_predicate}:{norm_object}")
    """
    norm_s = normalize_text(subject)
    norm_p = normalize_text(predicate)
    norm_o = normalize_object(object_val)
    raw = f"{source_id.strip()}:{norm_s}:{norm_p}:{norm_o}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
