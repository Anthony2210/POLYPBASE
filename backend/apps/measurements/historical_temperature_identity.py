"""Explicit product identity decision, never a week or count-based inference.

Callers normalize source label padding before using this policy. Original labels
and source cells remain untouched in evidence and receipts.
"""

PCA_CANONICAL_CODE = "PCA-JKA-1.005"
PCA_SOURCE_CODE = "PCA-CHO-1.005"


def temperature_identity(code):
    return PCA_CANONICAL_CODE if code == PCA_SOURCE_CODE else code


def temperature_source_matches(source_code, box_code):
    return source_code == box_code or (
        source_code == PCA_SOURCE_CODE and box_code == PCA_CANONICAL_CODE
    )
