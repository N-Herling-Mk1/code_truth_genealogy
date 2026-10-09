# -*- coding: utf-8 -*-
"""
seal.py  --  record a review and/or (re-)seal one unit
======================================================

    python tools/seal.py <id>                                  re-seal a DRAFT after editing it
    python tools/seal.py <id> --status READ --evidence "..."   record a review (file must match its seal)
    python tools/seal.py <id> --force                          re-seal a REVIEWED unit (think twice)

A reviewed file should never change: make a new unit instead. --force exists
for genuine accidents (e.g. a line-ending rewrite) and prints what it overrode.
Run  python tools/build.py  afterwards.
"""
import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS, REG = ROOT / "units", ROOT / "registry"
LADDER = ["DRAFT", "READ", "RUN", "VERIFIED", "SUPERSEDED", "RETIRED"]


def log(msg):
    print(f"[seal] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id")
    ap.add_argument("--status", choices=LADDER)
    ap.add_argument("--evidence", default=None)
    ap.add_argument("--date", default=_dt.date.today().isoformat(), help="review date, YYYY-MM-DD")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    meta_path, lock_path = UNITS / a.id / "unit.json", REG / f"{a.id}.json"
    if not meta_path.exists():
        sys.exit(f"[seal] no such unit: units/{a.id}/unit.json")
    u = json.loads(meta_path.read_text(encoding="utf-8"))
    log(f"(1/3) unit {a.id}  status {u['review_status']}")

    now = []
    for i, f in enumerate(u["files"], 1):
        p = UNITS / a.id / f
        if not p.exists():
            sys.exit(f"[seal] file missing: units/{a.id}/{f}")
        now.append({"path": f, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "bytes": p.stat().st_size})
        log(f"(2/3) hashed {i}/{len(u['files'])}  {f}  {now[-1]['sha256'][:16]}...")

    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    old = {s["path"]: s["sha256"] for s in lock.get("seal", {}).get("files", [])}
    drift = [n["path"] for n in now if old.get(n["path"]) != n["sha256"]]
    matches = bool(old) and not drift and set(old) == {n["path"] for n in now}

    if not matches:
        if old and u["review_status"] != "DRAFT" and not a.force:
            sys.exit(f"[seal] REFUSED: {a.id} is {u['review_status']} and differs from its seal "
                     f"({', '.join(drift) or 'file list changed'}).\n"
                     "       A reviewed file must not change -- create a new unit, or pass --force.")
        if old and a.force and u["review_status"] != "DRAFT":
            log(f"!! --force: overriding the seal of a {u['review_status']} unit ({', '.join(drift)})")
        if a.status and a.status != "DRAFT" and old and not a.force:
            sys.exit("[seal] REFUSED: the file changed since it was sealed. Re-seal first "
                     f"(python tools/seal.py {a.id}), re-read it, then record the review.")
        lock["seal"] = {"sealed": _dt.date.today().isoformat(), "files": now}
        log("(3/3) seal " + ("created" if not old else "REPLACED"))
    else:
        log("(3/3) files match the existing seal")

    if a.status:
        if a.status != "DRAFT" and not (a.evidence or u.get("review_evidence")):
            sys.exit("[seal] --evidence is required when recording a review")
        u["review_status"] = a.status
        if a.status != "DRAFT":
            u["review_date"] = a.date
            if a.evidence:
                u["review_evidence"] = a.evidence
        meta_path.write_bytes((json.dumps(u, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
        log(f"      unit.json: review_status={a.status}  review_date={u.get('review_date')}")

    REG.mkdir(exist_ok=True)
    rec = {k: v for k, v in u.items()}
    rec["seal"] = lock["seal"]
    lock_path.write_bytes((json.dumps(rec, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    log(f"      wrote registry/{a.id}.json -- now run: python tools/build.py")


if __name__ == "__main__":
    main()
