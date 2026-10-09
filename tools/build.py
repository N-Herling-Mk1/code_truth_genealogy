# -*- coding: utf-8 -*-
"""
build.py  --  code_truth_genealogy
==================================

    python tools/build.py

  [1/5] SCAN       units/<id>/unit.json  ->  one record per reviewed code unit
  [2/5] VERIFY     fields + enums (rules read from reference/*.csv), parents, cycles
  [3/5] INTEGRITY  re-hash every file against its seal in registry/<id>.json
                   OK | MODIFIED | MISSING     (a unit seen for the first time is sealed)
  [4/5] LOCK       registry/<id>.json  (committed -- `git diff` is the audit)
  [5/5] EMIT       docs/data.js, docs/units/, docs/prompts/<id>.md,
                   docs/lineage/<id>_lineage.zip

Exit code 0 = clean, 1 = schema/graph error (nothing written),
          2 = site built but at least one unit is MODIFIED or MISSING.

The seal is never rewritten here. To re-seal a DRAFT or record a review:
    python tools/seal.py <id> [--status READ --evidence "..."]
"""
import csv
import datetime as _dt
import difflib
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS, REG, DOCS, REF = ROOT / "units", ROOT / "registry", ROOT / "docs", ROOT / "reference"
REPO = "https://github.com/N-Herling-Mk1/code_truth_genealogy"
SITE = "https://n-herling-mk1.github.io/code_truth_genealogy/"
ID_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*_mk[0-9]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FENCE = {"python": "python", "powershell": "powershell", "javascript": "javascript",
         "html": "html", "css": "css", "text": ""}


def log(stage, msg):
    print(f"[{stage}] {msg}", flush=True)


def bar(stage, i, n, label):
    w = 20
    k = int(w * i / max(n, 1))
    print(f"[{stage}] [{'#' * k}{'.' * (w - k)}] {i}/{n}  {label}", flush=True)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_text(path):
    """Text for display/diff. Line endings normalised for DISPLAY only; hashes use raw bytes."""
    return path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")


# ---------------------------------------------------------------- reference
def load_reference():
    with open(REF / "schema.csv", newline="", encoding="utf-8") as f:
        schema = list(csv.DictReader(f))
    enums = {}
    with open(REF / "enums.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            enums.setdefault(r["enum"], []).append(r["value"])
    return schema, enums


# ---------------------------------------------------------------- SCAN
def scan():
    dirs = sorted(d for d in UNITS.iterdir() if d.is_dir()) if UNITS.exists() else []
    units = {}
    for i, d in enumerate(dirs, 1):
        meta = d / "unit.json"
        if not meta.exists():
            bar("1/5 scan", i, len(dirs), f"{d.name}  !! no unit.json, skipped")
            continue
        u = json.loads(meta.read_text(encoding="utf-8"))
        u["_dir"] = d.name
        units[d.name] = u
        bar("1/5 scan", i, len(dirs), d.name)
    return units


# ---------------------------------------------------------------- VERIFY
def verify(units, schema, enums):
    problems = []
    known = {r["field"] for r in schema}
    for uid, u in units.items():
        def bad(msg):
            problems.append(f"{uid}: {msg}")
        if u.get("id") != uid:
            bad(f"id {u.get('id')!r} does not match folder name")
        if not ID_RE.match(uid):
            bad("id is not <slug>_mkN (lowercase, digits, underscores)")
        for extra in sorted(set(u) - known - {"_dir"}):
            bad(f"unknown field {extra!r} (not in reference/schema.csv)")
        reviewed = u.get("review_status") != "DRAFT"
        for r in schema:
            f, t, req = r["field"], r["type"], r["required"]
            need = (req == "yes" or (req == "if reviewed" and reviewed)
                    or (req == "if experimental" and u.get("experimental") is True))
            if f not in u:
                bad(f"missing field {f!r}")
                continue
            v = u[f]
            empty = v in ("", None) or (t == "list" and f == "files" and not v)
            if need and empty:
                bad(f"{f} is required ({req})")
                continue
            if v in ("", None):
                continue
            if t in ("str", "id") and not isinstance(v, str):
                bad(f"{f} must be a string")
            elif t == "date" and not (isinstance(v, str) and DATE_RE.match(v)):
                bad(f"{f} must be YYYY-MM-DD")
            elif t == "bool" and not isinstance(v, bool):
                bad(f"{f} must be true/false")
            elif t == "list" and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                bad(f"{f} must be a list of strings")
            elif t.startswith("enum:") and v not in enums.get(t[5:], []):
                bad(f"{f}={v!r} not in enum {t[5:]} {enums.get(t[5:], [])}")
            elif t == "parents":
                if not isinstance(v, list):
                    bad("parents must be a list")
                    continue
                for p in v:
                    if not (isinstance(p, dict) and set(p) == {"id", "rel"}):
                        bad(f"parent entry must be {{id, rel}}: {p!r}")
                    elif p["id"] not in units:
                        bad(f"parent {p['id']!r} does not exist")
                    elif p["rel"] not in enums.get("rel", []):
                        bad(f"parent rel {p['rel']!r} not in {enums.get('rel', [])}")
                    elif p["id"] == uid:
                        bad("unit lists itself as parent")
        for f in u.get("files") or []:
            if "/" in f or "\\" in f or f == "unit.json":
                bad(f"files entry {f!r} must be a plain file name inside the unit folder")
        pf = u.get("prompt_file")
        if pf and pf not in (u.get("files") or []):
            bad("prompt_file must also be listed in files (so it is hashed)")
        sb = u.get("superseded_by")
        if sb and sb not in units:
            bad(f"superseded_by {sb!r} does not exist")
    return problems


def graph(units):
    """Adds children / depth / ancestors. Returns problems (cycles)."""
    for u in units.values():
        u["children"] = []
    for uid, u in units.items():
        for p in u.get("parents", []):
            if p.get("id") in units:
                units[p["id"]]["children"].append(uid)
    state, problems = {}, []

    def visit(uid, trail):
        if state.get(uid) == 2:
            return
        if state.get(uid) == 1:
            problems.append("cycle: " + " -> ".join(trail + [uid]))
            return
        state[uid] = 1
        u, anc, depth = units[uid], [], 0
        for p in u["parents"]:
            if p["id"] not in units:
                continue
            visit(p["id"], trail + [uid])
            pu = units[p["id"]]
            depth = max(depth, pu.get("depth", 0) + 1)
            for a in pu.get("ancestors", []) + [p["id"]]:
                if a not in anc:
                    anc.append(a)
        u["depth"], u["ancestors"] = depth, anc
        state[uid] = 2

    for uid in units:
        visit(uid, [])
    for u in units.values():
        u["children"].sort()
    return problems


# ---------------------------------------------------------------- INTEGRITY
def integrity(units):
    worst = {}
    ids = list(units)
    for i, uid in enumerate(ids, 1):
        u = units[uid]
        lock_path = REG / f"{uid}.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else None
        seal = (lock or {}).get("seal")
        current = []
        for f in u["files"]:
            p = UNITS / uid / f
            current.append({"path": f, "sha256": sha256(p) if p.exists() else None,
                            "bytes": p.stat().st_size if p.exists() else None})
        if seal is None:                                  # first sight: seal it
            missing = [c["path"] for c in current if c["sha256"] is None]
            if missing:
                u["integrity"], u["integrity_detail"] = "MISSING", missing
                u["seal"] = {"sealed": None, "files": current}
            else:
                u["seal"] = {"sealed": _dt.date.today().isoformat(), "files": current}
                u["integrity"], u["integrity_detail"] = "OK", []
                u["_new"] = True
        else:
            u["seal"] = seal
            sealed = {s["path"]: s["sha256"] for s in seal["files"]}
            now = {c["path"]: c["sha256"] for c in current}
            gone = [p for p in sealed if now.get(p) is None]
            changed = [p for p in sealed if now.get(p) and now[p] != sealed[p]]
            unsealed = [p for p in now if p not in sealed]
            if gone:
                u["integrity"], u["integrity_detail"] = "MISSING", gone
            elif changed or unsealed:
                u["integrity"], u["integrity_detail"] = "MODIFIED", changed + unsealed
            else:
                u["integrity"], u["integrity_detail"] = "OK", []
        tag = u["integrity"] + ("  (new, sealed)" if u.get("_new") else "")
        if u["integrity"] != "OK":
            tag = f"!! {u['integrity']} SINCE REVIEW: {', '.join(u['integrity_detail'])}"
        bar("3/5 integrity", i, len(ids), f"{uid}  {tag}")
        worst[uid] = u["integrity"]
    return worst


# ---------------------------------------------------------------- EMIT helpers
META_KEYS = ("id", "title", "language", "date", "parents", "delta", "files", "source_path",
             "ai_model", "prompt_file", "review_status", "review_date", "review_evidence",
             "experimental", "experimental_reason", "applies_to", "tags", "notes", "superseded_by")


def chain_text(units, uid):
    order = sorted(units[uid]["ancestors"], key=lambda a: (units[a]["depth"], a)) + [uid]
    return " -> ".join(order)


def prompt_md(units, u):
    uid, primary = u["id"], u["files"][0]
    code = read_text(UNITS / uid / primary) if (UNITS / uid / primary).exists() else ""
    sha = next((s["sha256"] for s in u["seal"]["files"] if s["path"] == primary), None)
    o = [f"# Ground-truth code unit: {uid}", "",
         f"**{u['title']}**", "",
         "You are being given a code unit from my `code_truth_genealogy` repo. Treat the code "
         "below as the reference implementation for this task.", "",
         "## Status", "",
         f"- review_status: **{u['review_status']}**"
         + (f" (reviewed {u['review_date']})" if u.get("review_date") else ""),
         f"- review_evidence: {u.get('review_evidence') or 'none recorded'}",
         f"- integrity vs seal: **{u['integrity']}**",
         f"- sha256 ({primary}): `{sha}`"]
    if u["review_status"] == "DRAFT":
        o += ["", "> WARNING: this unit is a DRAFT. It has NOT been reviewed by the author. "
                  "Do not treat it as verified; check it before building on it."]
    if u.get("experimental"):
        o += ["", f"> EXPERIMENTAL: the method is not vetted. {u['experimental_reason']}"]
    if u["integrity"] != "OK":
        o += ["", f"> WARNING: {u['integrity']} SINCE REVIEW -- the file no longer matches its seal."]
    o += ["", "## Lineage", "", f"- chain: `{chain_text(units, uid)}`"]
    for p in u["parents"]:
        o.append(f"- parent: `{p['id']}` ({p['rel']})")
    if not u["parents"]:
        o.append("- root unit (no parent)")
    o += [f"- changed vs parent: {u['delta']}", "",
          "## Context", "",
          f"- language: {u['language']}", f"- written: {u['date']}", f"- generated by: {u['ai_model']}"]
    if u.get("applies_to"):
        o.append(f"- applies to: {', '.join(u['applies_to'])}")
    if u.get("tags"):
        o.append(f"- tags: {', '.join(u['tags'])}")
    if u.get("notes"):
        o += ["", "## Notes", ""] + [f"- {n}" for n in u["notes"]]
    if u.get("prompt_file") and (UNITS / uid / u["prompt_file"]).exists():
        o += ["", "## Prompt originally used", "", "````text",
              read_text(UNITS / uid / u["prompt_file"]).rstrip("\n"), "````"]
    o += ["", f"## Code: {primary}", "", "````" + FENCE.get(u["language"], ""),
          code.rstrip("\n"), "````", "",
          "## How to reuse this", "",
          "1. Keep the public names, signatures and conventions above unless I ask for a change.",
          "2. If you change behaviour, say exactly what changed and why, as a list I can paste "
          "into the `delta` field of a new unit.",
          "3. Return complete files, not fragments. Do not invent data; do not add placeholder code.",
          f"4. A changed file is a NEW unit (a revision or variant of `{uid}`), never an edit of this one.",
          "5. If something in this code looks wrong, tell me before building on it.", "",
          f"Source: {SITE}#{uid}", ""]
    return "\n".join(o)


def write_zip(units, u, out):
    """Unit + all ancestors. Fixed timestamps so an unchanged lineage gives an identical zip."""
    order = sorted(u["ancestors"], key=lambda a: (units[a]["depth"], a)) + [u["id"]]
    lines = [f"LINEAGE of {u['id']}  (root first)", f"source: {SITE}#{u['id']}", ""]
    for a in order:
        au = units[a]
        par = ", ".join(f"{p['id']} ({p['rel']})" for p in au["parents"]) or "ROOT"
        lines += [f"{a}", f"  title      {au['title']}", f"  parent     {par}",
                  f"  status     {au['review_status']}" + ("  EXPERIMENTAL" if au.get("experimental") else ""),
                  f"  integrity  {au['integrity']}", f"  delta      {au['delta']}"]
        lines += [f"  sha256     {s['sha256']}  {s['path']}" for s in au["seal"]["files"]]
        lines.append("")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def add(name, data):
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
        add("LINEAGE.txt", "\n".join(lines).encode("utf-8"))
        for a in order:
            for f in units[a]["files"]:
                p = UNITS / a / f
                if p.exists():
                    add(f"{a}/{f}", p.read_bytes())


def diffs(units, u):
    primary = UNITS / u["id"] / u["files"][0]
    if not primary.exists():
        return []
    new = read_text(primary).splitlines()
    out = []
    for p in u["parents"]:
        pu = units[p["id"]]
        pp = UNITS / p["id"] / pu["files"][0]
        if not pp.exists():
            continue
        old = read_text(pp).splitlines()
        lines = list(difflib.unified_diff(old, new, f"{p['id']}/{pu['files'][0]}",
                                          f"{u['id']}/{u['files'][0]}", lineterm="", n=3))
        out.append({"parent": p["id"], "rel": p["rel"], "lines": lines,
                    "added": sum(1 for x in lines[2:] if x.startswith("+")),
                    "removed": sum(1 for x in lines[2:] if x.startswith("-"))})
    return out


# ---------------------------------------------------------------- main
def main():
    print("=" * 64)
    print(" code_truth_genealogy build")
    print("=" * 64)
    schema, enums = load_reference()
    log("0/5 reference", f"schema.csv: {len(schema)} fields   enums.csv: {len(enums)} enums")

    units = scan()
    if not units:
        log("1/5 scan", "no units found under units/ -- building an empty site")

    problems = verify(units, schema, enums)
    if not problems:
        problems = graph(units)
    log("2/5 verify", f"{len(units)} unit(s): " + ("OK" if not problems else f"{len(problems)} problem(s)"))
    for x in problems:
        log("2/5 verify", f"   !! {x}")
    if problems:
        sys.exit("[2/5 verify] FAILED -- nothing written")
    roots = [uid for uid, u in units.items() if not u["parents"]]
    log("2/5 verify", f"graph: {len(roots)} root(s), max depth "
                      f"{max((u['depth'] for u in units.values()), default=0)}, acyclic")

    worst = integrity(units)

    REG.mkdir(exist_ok=True)
    for i, (uid, u) in enumerate(units.items(), 1):
        rec = {k: u[k] for k in META_KEYS}
        rec["seal"] = u["seal"]
        out = REG / f"{uid}.json"
        text = json.dumps(rec, indent=1, ensure_ascii=False) + "\n"
        changed = (not out.exists()) or out.read_text(encoding="utf-8") != text
        if changed:
            out.write_bytes(text.encode("utf-8"))
        bar("4/5 lock", i, len(units), f"registry/{uid}.json  " + ("written" if changed else "unchanged"))

    for sub in ("units", "prompts", "lineage"):
        shutil.rmtree(DOCS / sub, ignore_errors=True)
        (DOCS / sub).mkdir(parents=True)
    # newest on top: date, then depth (a child sits above its parent), then id
    order = sorted(units.values(), key=lambda u: (u["date"], u["depth"], u["id"]), reverse=True)
    payload_units = []
    for i, u in enumerate(order, 1):
        uid = u["id"]
        (DOCS / "units" / uid).mkdir()
        for f in u["files"]:
            if (UNITS / uid / f).exists():
                shutil.copyfile(UNITS / uid / f, DOCS / "units" / uid / f)
        (DOCS / "prompts" / f"{uid}.md").write_bytes(prompt_md(units, u).encode("utf-8"))
        write_zip(units, u, DOCS / "lineage" / f"{uid}_lineage.zip")
        primary = UNITS / uid / u["files"][0]
        rec = {k: u[k] for k in META_KEYS}
        rec.update({"seal": u["seal"], "integrity": u["integrity"],
                    "integrity_detail": u["integrity_detail"], "children": u["children"],
                    "depth": u["depth"], "ancestors": u["ancestors"],
                    "code": read_text(primary) if primary.exists() else "",
                    "diffs": diffs(units, u)})
        payload_units.append(rec)
        bar("5/5 emit", i, len(order), f"{uid}  (copy, prompt .md, lineage .zip, diff)")
    payload = {"built": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "repo": REPO,
               "site": SITE, "units": payload_units}
    (DOCS / "data.js").write_bytes(("// generated by tools/build.py -- do not edit\nwindow.CODE_TRUTH = "
                                    + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
                                    + ";\n").encode("utf-8"))
    log("5/5 emit", "wrote docs/data.js")

    flagged = [uid for uid, s in worst.items() if s != "OK"]
    print("=" * 64)
    if flagged:
        print(f" BUILT WITH {len(flagged)} INTEGRITY FLAG(S): {', '.join(flagged)}")
        print(" the site shows MODIFIED SINCE REVIEW on those units")
        print("=" * 64)
        sys.exit(2)
    print(f" done -- {len(units)} unit(s), all seals OK -- open docs/index.html")
    print("=" * 64)


if __name__ == "__main__":
    main()
