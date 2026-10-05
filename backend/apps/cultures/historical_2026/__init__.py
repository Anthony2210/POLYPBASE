"""Reviewed, deterministic import of the 2026 historical workbook.

Layers:
    workbook.py   standard-library XLSX reader (no third-party dependency)
    decisions.py  every explicit product/stakeholder decision, as constants
    source.py     workbook -> reviewed manifest (pure Python, no database)
    manifest.py   manifest fingerprint, loading and validation
    importer.py   dry-run / apply service working only from the manifest
"""
