#!/usr/bin/env python3
"""
End-to-end harness for the CWC inbound-folder automation.

WHAT THIS IS
    A faithful port of InboundScannerService + OutboundFilingService, run against
    the REAL cwc.ingest_ledger table in Postgres, the REAL categories.yml,
    routing-config.yml and naming_service.py.

WHAT THIS IS NOT
    The classifier. There is no Anthropic key here, so the classification step is
    replaced by hand labels from evaluation/golden_set.yml — labels derived by
    reading each of the 48 documents. Everything downstream of classification is
    the production code path; everything about model ACCURACY is out of scope and
    must be measured with run_eval.py against a live AI service.

WHAT IT DOES EXERCISE, FOR REAL
    · discovery, sync-artefact filtering, readiness probing
    · SHA-256 content identity and the ledger's UNIQUE claim (in Postgres)
    · idempotency across repeat scans, re-drops, and renames
    · the confidence gate and auto-route-disabled categories
    · filename generation, collision suffixing
    · outbound filing and archival of the original
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import yaml

# ── Wire in the real service code ─────────────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "cwc-ai-service"))
from services import naming_service as ns          # noqa: E402  the real module

CATEGORIES_YML = REPO / "cwc-ai-service" / "config" / "categories.yml"
ROUTING_YML    = REPO / "cwc-backend" / "src" / "main" / "resources" / "routing-config.yml"
GOLDEN_YML     = REPO / "evaluation" / "golden_set.yml"

DSN = os.environ.get("CWC_DSN", "host=/home/claude/pgrun port=5433 user=postgres dbname=cwc")


# ══════════════════════════════════════════════════════════════════════════════
# Port of FileReadinessChecker
# ══════════════════════════════════════════════════════════════════════════════

IGNORED_PREFIXES = ("~$", ".~", "._")
IGNORED_SUFFIXES = (".tmp", ".temp", ".partial", ".crdownload", ".part",
                    ".lock", ".ds_store", "thumbs.db", ".filepart")
SUPPORTED_EXT    = (".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg")

MAGIC = [b"%PDF", b"\x89PNG", b"\xff\xd8\xff", b"II\x2a\x00", b"MM\x00\x2a"]

READY, AWAITING_HYDRATION, UNSTABLE, UNSUPPORTED = (
    "READY", "AWAITING_HYDRATION", "UNSTABLE", "UNSUPPORTED")


def is_ignorable_name(p: Path) -> bool:
    n = p.name.lower()
    if n.startswith(IGNORED_PREFIXES) or n.endswith(IGNORED_SUFFIXES):
        return True
    return not n.endswith(SUPPORTED_EXT)


def probe(path: Path, last_size, last_mtime, min_stable_ms: int):
    """Mirrors FileReadinessChecker.probe()."""
    try:
        st = path.stat()
    except OSError as e:
        return (UNSTABLE, -1, -1, f"cannot stat: {e}")
    if not path.is_file():
        return (UNSUPPORTED, 0, 0, "not a regular file")

    size, mtime = st.st_size, int(st.st_mtime * 1000)

    if size == 0:
        return (AWAITING_HYDRATION, 0, mtime, "zero bytes on disk")

    if last_size is not None and last_mtime is not None:
        if last_size != size or last_mtime != mtime:
            return (UNSTABLE, size, mtime,
                    f"changed since last poll (size {last_size}->{size})")
    else:
        age_ms = int(time.time() * 1000) - mtime
        if age_ms < min_stable_ms:
            return (UNSTABLE, size, mtime,
                    f"first sighting, modified {age_ms}ms ago (< {min_stable_ms}ms)")

    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError as e:
        return (AWAITING_HYDRATION, size, mtime, f"head unreadable: {e}")

    if len(head) < 4:
        return (AWAITING_HYDRATION, size, mtime,
                f"only {len(head)} bytes readable despite reported size {size}")
    if not any(head.startswith(m) for m in MAGIC):
        return (UNSUPPORTED, size, mtime, f"unrecognised magic: {head[:4]!r}")
    return (READY, size, mtime, "ok")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
# Port of RoutingEngineService.resolveDestination / uniqueDestination
# ══════════════════════════════════════════════════════════════════════════════

def unique_destination(directory: Path, file_name: str, suffix_tpl: str = " ({n})") -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate
    stem, dot, ext = file_name.rpartition(".")
    if not dot:
        stem, ext = file_name, ""
    else:
        ext = "." + ext
    for n in range(2, 1000):
        candidate = directory / f"{stem}{suffix_tpl.replace('{n}', str(n))}{ext}"
        if not candidate.exists():
            return candidate
    return directory / f"{int(time.time())}__{file_name}"


# ══════════════════════════════════════════════════════════════════════════════
# Harness
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Config:
    inbound: Path
    outbound: Path
    archive_dir: str = "_processed"
    failed_dir: str = "_failed"
    min_stable_ms: int = 0          # 0 for the harness: files are copied, not synced
    max_per_cycle: int = 100
    archive_by_date: bool = True
    # Prepended to a file's inbound-relative path when resolving golden labels.
    label_prefix: str = ""
    # Root of the labelled corpus, used to build the content-hash label index.
    dataset_root: Path | None = None


@dataclass
class Stats:
    discovered: int = 0
    skipped_known: int = 0
    not_ready: int = 0
    ignored: int = 0
    filed: int = 0
    to_review: int = 0
    unlabelled: int = 0
    events: list = field(default_factory=list)


class Harness:
    def __init__(self, cfg: Config, conn):
        self.cfg = cfg
        self.conn = conn
        self.tax = yaml.safe_load(CATEGORIES_YML.read_text())
        routing = yaml.safe_load(ROUTING_YML.read_text())["cwc"]["routing"]
        self.rules = {r["category"]: r["folder"] for r in routing["rules"]}
        self.manual_folder = routing["manual-review-folder"]
        self.auto_disabled = set(routing.get("auto-route-disabled") or [])
        self.file_uncertain = routing.get("file-uncertain-to-review-folder", True)
        self.collision_suffix = (routing.get("naming") or {}).get("collision-suffix", " ({n})")
        self.naming_style = (routing.get("naming") or {}).get("style", "HOUSE")
        self.labels = self._load_labels()
        self.hash_index = self._build_hash_index(cfg.dataset_root)

    # ── Labels stand in for the classifier ────────────────────────────────────
    def _load_labels(self) -> dict:
        """
        Keyed by the golden set's `file` value — a PATH, not a basename.

        Basenames are NOT unique across this corpus: the inbound samples include
        "Sample 2.pdf" and CWC's ROI Consent folder contains a completely
        different "Sample 2.pdf". Keying on basename silently mislabels five
        documents (they inherit the ROI label, get auto-route-disabled, and land
        in Manual-Review). Found by this harness on its first run.
        """
        gs = yaml.safe_load(GOLDEN_YML.read_text())
        by_path, basename_counts = {}, {}
        for s in gs["samples"]:
            rec = {
                "category": s["expected_category"],
                "subtype": s.get("expected_subtype"),
                "specification": s.get("expected_specification"),
                "expected_folder": s["expected_folder"],
                "expected_file_name": s.get("expected_file_name"),
                "block": s.get("block"),
            }
            key = s["file"].replace("\\", "/")
            by_path[key] = rec
            base = Path(key).name
            basename_counts[base] = basename_counts.get(base, 0) + 1

        # A basename is only usable as a fallback key when it is unambiguous.
        by_unique_basename = {
            Path(k).name: v for k, v in by_path.items()
            if basename_counts[Path(k).name] == 1
        }
        return {"by_path": by_path, "by_basename": by_unique_basename,
                "ambiguous": {b for b, c in basename_counts.items() if c > 1}}

    def _build_hash_index(self, dataset_root: Path) -> dict:
        """
        sha256 -> label, computed over the labelled corpus.

        Stands in for the classifier's behaviour on a document it has seen before
        under a different name: the same bytes must produce the same
        classification. Without this, a fax re-sent under a new filename looks
        unclassifiable to the harness, which would mask exactly the collision and
        re-drop behaviour we are trying to test.
        """
        index = {}
        if not dataset_root or not dataset_root.exists():
            return index
        for rel, rec in self.labels["by_path"].items():
            candidate = dataset_root / rel
            if candidate.is_file():
                index[sha256(candidate)] = rec
        return index

    def lookup_label(self, file: Path, sha: str | None = None):
        """Resolve by relative path, then unambiguous basename, then content hash."""
        rel = file.relative_to(self.cfg.inbound).as_posix()
        for key in (f"{self.cfg.label_prefix}{rel}" if self.cfg.label_prefix else rel, rel):
            if key in self.labels["by_path"]:
                return self.labels["by_path"][key]
        if file.name in self.labels["by_basename"]:
            return self.labels["by_basename"][file.name]
        if sha and sha in self.hash_index:
            return self.hash_index[sha]
        return None

    # ── Phase 1: discovery (port of InboundScannerService.discover) ───────────
    def discover(self, stats: Stats):
        archive_root = self.cfg.inbound / self.cfg.archive_dir
        failed_root  = self.cfg.inbound / self.cfg.failed_dir

        candidates = []
        for p in sorted(self.cfg.inbound.rglob("*")):
            if not p.is_file():
                continue
            if archive_root in p.parents or failed_root in p.parents:
                continue
            if is_ignorable_name(p):
                stats.ignored += 1
                continue
            candidates.append(p)

        cur = self.conn.cursor()
        for f in candidates:
            if stats.discovered >= self.cfg.max_per_cycle:
                break
            state, size, mtime, detail = probe(f, None, None, self.cfg.min_stable_ms)
            if state == UNSUPPORTED:
                stats.ignored += 1
                continue
            if state != READY:
                stats.not_ready += 1
                stats.events.append(("NOT_READY", f.name, detail))
                continue

            sha = sha256(f)
            # The real claim: ON CONFLICT DO NOTHING against the UNIQUE hash.
            cur.execute("""
                INSERT INTO cwc.ingest_ledger
                    (content_sha256, source_path, source_file_name,
                     source_size_bytes, source_mtime_ms, state)
                VALUES (%s,%s,%s,%s,%s,'DISCOVERED')
                ON CONFLICT (content_sha256) DO NOTHING
                RETURNING id
            """, (sha, str(f), f.name, size, mtime))
            row = cur.fetchone()
            if row:
                stats.discovered += 1
            else:
                stats.skipped_known += 1
                stats.events.append(("SKIPPED_DUPLICATE", f.name,
                                     f"sha {sha[:12]} already in ledger"))
        self.conn.commit()

    # ── Phase 2: classify (labels) + name + route + file + archive ────────────
    def process_due(self, stats: Stats):
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT * FROM cwc.ingest_ledger
            WHERE state IN ('DISCOVERED','FAILED','AWAITING_HYDRATION','UNSTABLE')
            ORDER BY first_seen_at ASC
        """)
        rows = cur.fetchall()
        w = self.conn.cursor()

        for row in rows:
            src = Path(row["source_path"])
            name = row["source_file_name"]

            if not src.exists():
                w.execute("UPDATE cwc.ingest_ledger SET state='QUARANTINED', "
                          "last_error=%s, completed_at=now() WHERE id=%s",
                          ("source file no longer exists", row["id"]))
                continue

            label = self.lookup_label(src, row["content_sha256"])
            if label is None:
                # No label = the classifier would have run. Treat as UNKNOWN so it
                # exercises the auto-route-disabled path rather than being skipped.
                stats.unlabelled += 1
                label = {"category": "UNKNOWN", "subtype": None,
                         "specification": None, "block": None}

            category = label["category"]
            names = ns.build_suggested_names(
                self.tax, category, label["subtype"], label["specification"], name)

            # ── Confidence gate ──────────────────────────────────────────────
            # No live model, so band is assumed HIGH. auto-route-disabled is real
            # config and still applies — that is the part being tested here.
            auto_disabled = category in self.auto_disabled
            to_review = auto_disabled and self.file_uncertain

            folder = self.manual_folder if to_review else self.rules.get(category, self.manual_folder)
            file_name = (names["alternateFileName"] if self.naming_style == "SNAKE"
                         else names["suggestedFileName"])

            dest_dir = self.cfg.outbound / folder
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = unique_destination(dest_dir, file_name, self.collision_suffix)
            shutil.copy2(src, dest)

            # ── Archive the original ─────────────────────────────────────────
            arc = self.cfg.inbound / self.cfg.archive_dir
            if self.cfg.archive_by_date:
                arc = arc / date.today().isoformat()
            arc.mkdir(parents=True, exist_ok=True)
            archived = unique_destination(arc, src.name)
            shutil.move(str(src), str(archived))

            w.execute("""
                UPDATE cwc.ingest_ledger
                   SET state='FILED', routed_folder=%s, routed_file_name=%s,
                       routed_path=%s, archived_path=%s, completed_at=now(),
                       last_error=NULL
                 WHERE id=%s
            """, (folder, dest.name, str(dest), str(archived), row["id"]))

            if to_review:
                stats.to_review += 1
            stats.filed += 1
            stats.events.append(("FILED", name, f"{folder}/{dest.name}"))

        self.conn.commit()

    def run_cycle(self) -> Stats:
        stats = Stats()
        self.discover(stats)
        self.process_due(stats)
        return stats


# ══════════════════════════════════════════════════════════════════════════════

def reset_ledger(conn):
    cur = conn.cursor()
    cur.execute("TRUNCATE cwc.ingest_ledger RESTART IDENTITY CASCADE")
    conn.commit()


def tree_manifest(root: Path) -> dict:
    """folder -> sorted list of filenames."""
    out = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root)
            folder = str(rel.parent).replace("\\", "/")
            out.setdefault(folder, []).append(p.name)
    for k in out:
        out[k].sort()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inbound", required=True)
    ap.add_argument("--outbound", required=True)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--dataset-root", default="",
                    help="root of the labelled corpus for content-hash label lookup")
    ap.add_argument("--label-prefix", default="",
                    help="prefix prepended to inbound-relative paths when matching golden labels")
    ap.add_argument("--json-out")
    args = ap.parse_args()

    cfg = Config(inbound=Path(args.inbound), outbound=Path(args.outbound),
                 label_prefix=args.label_prefix,
                 dataset_root=Path(args.dataset_root) if args.dataset_root else None)
    conn = psycopg2.connect(DSN)
    if args.reset:
        reset_ledger(conn)

    h = Harness(cfg, conn)
    all_stats = []
    for i in range(args.cycles):
        s = h.run_cycle()
        all_stats.append(s)
        print(f"cycle {i+1}: discovered={s.discovered} skipped_known={s.skipped_known} "
              f"not_ready={s.not_ready} ignored={s.ignored} filed={s.filed} "
              f"to_review={s.to_review} unlabelled={s.unlabelled}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "cycles": [{"discovered": s.discovered, "skipped_known": s.skipped_known,
                        "not_ready": s.not_ready, "ignored": s.ignored,
                        "filed": s.filed, "to_review": s.to_review,
                        "unlabelled": s.unlabelled,
                        "events": s.events} for s in all_stats],
            "manifest": tree_manifest(Path(args.outbound)),
        }, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
