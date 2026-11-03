"""Portable Ver.21 checkpoints: explicit, versioned, size-limited CSV bundle."""
from __future__ import annotations

from io import BytesIO
import json
import zipfile

import pandas as pd

FORMAT_VERSION = 1
TABLES = {
    "targets": "historical_target_results.csv",
    "target_log": "acquisition_manifest.csv",
    "horse_raw": "horse_history_raw.csv",
    "horse_log": "horse_acquisition.csv",
    "affinity": "race_affinity.csv",
}
MAX_UNCOMPRESSED = 75_000_000
MAX_ARCHIVE = 25_000_000


def pack_checkpoint(tables: dict[str, pd.DataFrame]) -> bytes:
    if any(key not in TABLES for key in tables):
        raise ValueError("Unknown checkpoint table")
    destination = BytesIO()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({
            "format_version": FORMAT_VERSION,
            "tables": sorted(tables),
            "note": "Local download; Streamlit session data is not durable storage",
        }, ensure_ascii=False))
        for key, frame in tables.items():
            if not isinstance(frame, pd.DataFrame):
                raise ValueError(f"{key} must be a DataFrame")
            archive.writestr(TABLES[key], frame.to_csv(index=False).encode("utf-8-sig"))
    blob = destination.getvalue()
    if len(blob) > MAX_ARCHIVE:
        raise ValueError("Checkpoint too large; export smaller batches")
    return blob


def load_checkpoint(blob: bytes) -> dict[str, pd.DataFrame]:
    if len(blob) > MAX_ARCHIVE:
        raise ValueError("Checkpoint exceeds 25MB")
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        members = archive.infolist()
        if len(members) > len(TABLES) + 1 or sum(i.file_size for i in members) > MAX_UNCOMPRESSED:
            raise ValueError("Checkpoint exceeds safe uncompressed limits")
        if len({i.filename for i in members}) != len(members):
            raise ValueError("Duplicate archive members")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format_version") != FORMAT_VERSION:
            raise ValueError("Unsupported checkpoint version")
        keys = manifest.get("tables")
        if not isinstance(keys, list) or len(keys) != len(set(keys)):
            raise ValueError("Invalid checkpoint table list")
        if any(k not in TABLES for k in keys):
            raise ValueError("Unknown table in manifest")
        if set(archive.namelist()) != {"manifest.json"} | {TABLES[k] for k in keys}:
            raise ValueError("Archive contents do not match manifest")
        result = {}
        for key in keys:
            binary = archive.read(TABLES[key])
            if not binary:
                raise ValueError(f"{key}: empty CSV")
            result[key] = pd.read_csv(
                BytesIO(binary), encoding="utf-8-sig",
                dtype={"馬ID": "string", "レースID": "string",
                       "horse_id": "string", "race_id": "string"},
            )
        return result
