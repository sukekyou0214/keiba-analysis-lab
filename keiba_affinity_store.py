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


def audit_checkpoint(tables: dict[str, pd.DataFrame]) -> list[str]:
    """Find inconsistencies without silently dropping or inventing rows."""
    issues = []
    target = tables.get("targets")
    log = tables.get("target_log")
    raw = tables.get("horse_raw")
    horse_log = tables.get("horse_log")
    if target is not None:
        required = {"レースID", "馬ID", "日付", "レース名", "着順_数値", "人気"}
        if not required.issubset(target):
            issues.append("対象レースの必須列が不足しています")
        elif target.empty:
            issues.append("対象レースの結果が空です")
        elif target.duplicated(["レースID", "馬ID"]).any():
            issues.append("対象レースに重複する馬IDがあります")
    if log is not None:
        if not {"race_id", "status", "entrants"}.issubset(log):
            issues.append("対象レースの取得ログに必須列がありません")
        elif log.empty:
            issues.append("対象レースの取得ログが空です")
        elif ("year" in log and log["year"].duplicated().any()):
            issues.append("対象レースの年度ログが重複しています")
        elif log.loc[log["status"].eq("成功"), "race_id"].astype("string").duplicated().any():
            issues.append("取得成功レースのIDが重複しています")
        elif target is not None and {"レースID", "馬ID"}.issubset(target):
            counts = target.groupby("レースID").size().to_dict()
            for _, entry in log.loc[log["status"].eq("成功")].iterrows():
                if counts.get(str(entry["race_id"])) != int(entry["entrants"]):
                    issues.append("取得成功と記録された対象レースの頭数が一致しません")
                    break
    if horse_log is not None:
        if not {"horse_id", "status"}.issubset(horse_log):
            issues.append("馬別取得ログの必須列が不足しています")
        elif horse_log["horse_id"].astype("string").duplicated().any():
            issues.append("馬別取得ログに重複があります")
        elif raw is not None and "馬ID" in raw:
            successful = set(horse_log.loc[horse_log["status"].eq("成功"), "horse_id"].astype(str))
            present = set(raw["馬ID"].dropna().astype(str))
            if not successful.issubset(present):
                issues.append("取得成功と記録された馬の戦績データが不足しています")
    if raw is not None and "馬ID" not in raw:
        issues.append("馬別戦績データに馬IDがありません")
    if "affinity" in tables:
        if target is None or log is None or raw is None or horse_log is None:
            issues.append("分析結果の根拠となる取得データが不足しています")
        elif not {"status", "race_id"}.issubset(log) or not log["status"].eq("成功").all():
            issues.append("過去レースの取得が未完了のため分析結果を復元できません")
        elif not {"horse_id", "status"}.issubset(horse_log) or not horse_log["status"].eq("成功").all():
            issues.append("馬別戦績の取得が未完了のため分析結果を復元できません")
        elif {"馬ID", "レースID"}.issubset(target) and "馬ID" in raw:
            from keiba_affinity_pipeline import complete_horse_manifest
            if not complete_horse_manifest(target, horse_log, raw):
                issues.append("馬の戦績と取得ログが一致しないため分析結果を復元できません")
    return issues
