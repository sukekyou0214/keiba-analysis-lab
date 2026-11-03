"""Ver.21 bounded historical horse-result collection."""
import time
import pandas as pd


def collect_batch(target, loader, completed=None, limit=20, delay=1.0, sleeper=time.sleep):
    if not 1 <= limit <= 25 or not 0.5 <= delay <= 30:
        raise ValueError("Invalid batch parameters")
    if target.empty or not {"馬ID", "馬名"}.issubset(target):
        raise ValueError("Target entrants required")
    horses = target[["馬ID", "馬名"]].drop_duplicates("馬ID")
    done = set(completed or [])
    frames, log = [], []
    for index, (_, horse) in enumerate(horses.iterrows()):
        horse_id = str(horse["馬ID"])
        if horse_id in done:
            continue
        if len(log) >= limit:
            break
        if log:
            sleeper(delay)
        try:
            data = loader(horse_id, str(horse["馬名"]))
            if not isinstance(data, pd.DataFrame) or data.empty:
                raise ValueError("Empty result")
            if "馬ID" not in data or not data["馬ID"].astype(str).eq(horse_id).all():
                raise ValueError("Mismatched horse ID")
            frames.append(data)
            log.append({"horse_id": horse_id, "status": "成功", "rows": len(data), "reason": ""})
        except Exception as exc:
            log.append({"horse_id": horse_id, "status": "失敗", "rows": 0, "reason": str(exc)[:180]})
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(), pd.DataFrame(log)



def complete_horse_manifest(target, logs, histories=None):
    """Require one successful acquisition record for every target horse."""
    if logs.empty or not {"horse_id", "status"}.issubset(logs):
        return False
    if logs["horse_id"].duplicated().any():
        return False
    expected = set(target["馬ID"].astype(str))
    observed = set(logs["horse_id"].astype(str))
    if expected != observed or not logs["status"].eq("成功").all():
        return False
    if histories is not None:
        if histories.empty or "馬ID" not in histories:
            return False
        raw_ids = histories["馬ID"].astype("string")
        if raw_ids.isna().any() or set(raw_ids) != expected:
            return False
        if "レースID" in histories:
            known = histories.loc[histories["レースID"].notna() & histories["レースID"].astype(str).ne("")]
            if known.duplicated(["馬ID", "レースID"]).any():
                return False
    return True
