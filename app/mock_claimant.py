"""Mock claimant portal: claim submission and scripted replies to information requests."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from app import config, db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def attach(claim_id: str, src: Path, round_: int = 0, filename: str | None = None) -> str:
    n = (db.one("SELECT COUNT(*) AS n FROM documents WHERE claim_id=?", (claim_id,)) or {"n": 0})["n"] + 1
    doc_id = f"{claim_id}-D{n:02d}"
    dest = config.docs_dir() / claim_id / f"{doc_id}_{filename or src.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copy(src, dest)
    db.ex("INSERT INTO documents VALUES (?,?,?,?,?,?)", (doc_id, claim_id, str(dest), filename or src.name, round_,
                                                       _now()))
    return doc_id


def submit_claim(claim_id: str, claimant_id: str, files: list[Path], submitted_on: str | None = None) -> str:
    db.ex("INSERT INTO claims(claim_id, claimant_id, submitted_on, status, data, is_historical) "
          "VALUES (?,?,?,?,?,0)", (claim_id, claimant_id, submitted_on or config.SIM_DATE.isoformat(), "RECEIVED", "{}"))
    for f in files:
        attach(claim_id, Path(f))
    return claim_id


def scripted_reply(claim_id: str, round_: int) -> dict:
    """Attach the claimant's pre-generated reply documents for this round, if any."""
    rows = db.q("SELECT * FROM info_responses WHERE claim_id=? AND round=?", (claim_id, round_))
    if not rows:
        return {"doc_ids": [], "note": None}
    ids = [attach(claim_id, Path(r["path"]), round_, r["filename"]) for r in rows]
    return {"doc_ids": ids, "note": rows[0]["note"]}
