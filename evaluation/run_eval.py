"""
CWC Healthcare Fax — Accuracy Evaluation Harness
Spec §8, §13, and §17: run every sample through the live AI service and report results.

This is a measurement TOOL, NOT a test. It calls the live AI service by design.
  - Do NOT name it test_*.
  - Do NOT collect with pytest or CI.
  - Lives in evaluation/, not tests/.
  - evaluation/run_eval.py is explicitly exempt from the no-live-call test rule.

Use --dry-run for offline demos: replays recorded responses from
evaluation/fixtures/classify_responses/{basename}.json.

Usage:
  python evaluation/run_eval.py [--repeat N] [--dry-run] [--datasets-path PATH]
  python evaluation/run_eval.py --extract-eval [--datasets-path PATH]
                                [--confidence-threshold 0.85]
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
import requests

# Load CWC_DATASET_PATH from cwc-ai-service/.env when it's not already in the environment
def _load_dataset_path_from_env_file() -> None:
    env_file = Path(__file__).parent.parent / "cwc-ai-service" / ".env"
    if env_file.exists() and not os.environ.get("CWC_DATASET_PATH"):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CWC_DATASET_PATH="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    os.environ["CWC_DATASET_PATH"] = val
                    break

_load_dataset_path_from_env_file()

# ── Paths ─────────────────────────────────────────────────────────────────────
EVAL_DIR          = Path(__file__).parent.resolve()
GOLDEN_FILE       = EVAL_DIR / "golden_set.yml"
GOLDEN_META_FILE  = EVAL_DIR / "golden_metadata.yml"
REPORTS_DIR       = EVAL_DIR / "reports"
FIXTURES_DIR      = EVAL_DIR / "fixtures" / "classify_responses"
AI_BASE           = "http://localhost:5002"
BACKEND_BASE      = "http://localhost:8080/api"
# A local model on a laptop GPU takes ~60 s per fax and longer on big ones;
# Claude took ~15 s. 120 s was a hard failure waiting to happen.
CLASSIFY_TIMEOUT  = float(os.environ.get("CWC_EVAL_CLASSIFY_TIMEOUT", "600"))
EXTRACT_TIMEOUT   = float(os.environ.get("CWC_EVAL_EXTRACT_TIMEOUT", "600"))
# The auto-route gate the backend enforces (routing-config.yml auto-route-min-confidence).
GATE_MIN_CONFIDENCE = float(os.environ.get("CWC_EVAL_GATE", "0.90"))

REPORTS_DIR.mkdir(exist_ok=True)
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

# ── Caveat (spec §7 / task requirement) ───────────────────────────────────────
CAVEAT = (
    "This evaluation uses 53 documents in three blocks, and they are NOT of equal "
    "evidential weight. The 36 'avenir_reference' samples were filed and named by CWC "
    "themselves, so their expected_folder and expected_file_name are observed ground "
    "truth — but the taxonomy in categories.yml was derived from that same folder tree, "
    "so scores on that block measure internal consistency, not generalisation. The 12 "
    "'new_inbound' samples were labelled by reading the documents against a taxonomy "
    "built before they were seen, and are the closest thing here to a held-out set. The "
    "5 'original_inbound' samples were relabelled from taxonomy v1.0. Report the "
    "per-block breakdown, never the headline number alone. A defensible accuracy figure "
    "still requires a labelled set that played no part in building the taxonomy."
)

# ── Routing resolution (mirrors RoutingEngineService.suggest()) ────────────────

def fetch_routing_config() -> dict:
    try:
        r = requests.get(f"{BACKEND_BASE}/routing/config", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [WARN] Cannot reach backend routing config: {e}. Using fallback.", flush=True)
        return _fallback_routing()

def _fallback_routing() -> dict:
    routing_yml = Path(__file__).parent.parent / "cwc-backend" / "src" / "main" / "resources" / "routing-config.yml"
    if routing_yml.exists():
        with open(routing_yml) as f:
            raw = yaml.safe_load(f)
        return raw["cwc"]["routing"]
    return {"mode": "SUGGEST", "manualReviewFolder": "Manual-Review",
            "autoRouteDisabled": ["PRESCRIPTION"],
            "rules": [], "subtypeOverrides": [], "senderOverrides": []}

def resolve_folder(category: str | None, subtype: str | None,
                   sender_fax: str | None, cfg: dict) -> tuple[str, str]:
    """Returns (folder, rule_matched). Mirrors Java RoutingEngineService."""
    manual = cfg.get("manualReviewFolder", "Manual-Review")

    # 1. Sender overrides
    for so in cfg.get("senderOverrides", []):
        if sender_fax and sender_fax == so.get("senderFax"):
            return so["folder"], f"sender:{sender_fax}"

    # 2. Subtype overrides
    for so in cfg.get("subtypeOverrides", []):
        if category == so.get("category") and subtype == so.get("subtype"):
            return so["folder"], f"subtype:{category}/{subtype}"

    # 3. Category rules
    for rule in cfg.get("rules", []):
        if category == rule.get("category"):
            return rule["folder"], f"category:{category}"

    return manual, "unmapped"


# ── Filename parsing (mirrors FaxFilenameParser.java) ─────────────────────────

import re as _re

_FNAME_RE = _re.compile(
    r"^\((\d{3})\)(\d{3})-(\d{4})_(\d{4}-\d{2}-\d{2})_(\d{4})(AM|PM)(?:\.pdf)?$",
    _re.IGNORECASE,
)

def _parse_sender_fax_e164(filename: str) -> str | None:
    """Extract the E.164 sender fax number from a CWC-format filename, or None."""
    m = _FNAME_RE.match(Path(filename).name)
    if not m:
        return None
    return f"+1{m.group(1)}{m.group(2)}{m.group(3)}"


# ── AI service call ────────────────────────────────────────────────────────────

def classify_live(file_path: str, sample: dict) -> dict:
    # Parse sender fax number from filename so the prompt context matches the
    # production backend pipeline (which also passes sender_fax_number).
    # Without this, the prompt renders <<sender_fax_number>> as "unknown", which
    # is a different input from the backend and can shift the runner-up at temperature=0.
    sender = _parse_sender_fax_e164(Path(file_path).name)
    payload = {
        "file_path":          file_path,
        "tracking_id":        f"eval-{Path(file_path).stem[:20]}",
        "sender_fax_number":  sender,
        "received_at":        None,
    }
    t0 = time.perf_counter()
    r = requests.post(f"{AI_BASE}/classify", json=payload, timeout=CLASSIFY_TIMEOUT)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    r.raise_for_status()
    result = r.json()
    result["_latency_ms"] = latency_ms
    return result


def classify_dry_run(file_path: str, sample: dict) -> dict:
    key = Path(file_path).name
    fixture_file = FIXTURES_DIR / (key + ".json")
    if not fixture_file.exists():
        print(f"  [DRY-RUN] No fixture for {key} — falling back to live call", flush=True)
        return classify_live(file_path, sample)
    with open(fixture_file) as f:
        result = json.load(f)
    result["_latency_ms"] = result.get("_latency_ms", 0)
    result["_dry_run"] = True
    return result


def save_fixture(file_path: str, result: dict) -> None:
    key = Path(file_path).name
    fixture_file = FIXTURES_DIR / (key + ".json")
    with open(fixture_file, "w") as f:
        json.dump(result, f, indent=2)


# ── Evaluation core ────────────────────────────────────────────────────────────

def _norm(s: str | None) -> str:
    """
    Compare names the way a human filing clerk would: case-insensitively, with
    runs of whitespace, hyphens and underscores flattened. "Home Care Form.pdf",
    "home care form.pdf" and "Home-Care Form.pdf" are the same filing decision.
    """
    if not s:
        return ""
    out = s.strip().lower()
    for ch in ("-", "_"):
        out = out.replace(ch, " ")
    return " ".join(out.split())


def evaluate_sample(
    sample: dict,
    datasets_path: str,
    dry_run: bool,
    routing_cfg: dict,
    save_fixtures: bool,
) -> dict:
    fname    = sample["file"]
    fpath    = os.path.join(datasets_path, fname)
    exp_cat  = sample["expected_category"]
    exp_sub  = sample.get("expected_subtype")
    exp_fold = sample["expected_folder"]

    if not os.path.exists(fpath):
        return {"file": fname, "error": f"File not found: {fpath}",
                "category_pass": False, "subtype_pass": False, "folder_pass": False}

    classify_fn = classify_dry_run if dry_run else classify_live
    result = classify_fn(fpath, sample)

    if save_fixtures and not dry_run:
        save_fixture(fpath, result)

    pred_cat  = result.get("documentCategory") or result.get("document_category")
    pred_sub  = result.get("documentSubtype")  or result.get("document_subtype")
    cal_conf  = result.get("calibratedConfidence") or result.get("calibrated_confidence")
    band      = result.get("confidenceBand")   or result.get("confidence_band")
    force_rev = result.get("forceManualReview") or result.get("force_manual_review") or False
    evidence  = result.get("evidence") or []
    latency   = result.get("_latency_ms", 0)
    sender    = result.get("senderCallbackFax") or result.get("sender_callback_fax")

    pred_fold, rule = resolve_folder(pred_cat, pred_sub, sender, routing_cfg)

    cat_pass  = pred_cat  == exp_cat
    sub_pass  = pred_sub  == exp_sub
    fold_pass = pred_fold == exp_fold

    # ── Rename scoring (taxonomy v2.0) ─────────────────────────────────────
    # CWC's manual step is "decide the folder AND the name". Scoring only the
    # folder would report the feature as working when half of it is wrong.
    pred_spec = result.get("specification")
    pred_name = result.get("suggestedFileName")
    pred_alt  = result.get("alternateFileName")

    exp_spec  = sample.get("expected_specification")
    exp_name  = sample.get("expected_file_name")
    # CWC's own naming is inconsistent (their tree holds both "Home Care Form.pdf"
    # and "Homecare form.pdf"), so alternatives are explicitly allowed.
    accepted  = [exp_spec] + list(sample.get("accept_specifications") or [])
    accepted  = [a for a in accepted if a]

    spec_pass = bool(pred_spec) and _norm(pred_spec) in {_norm(a) for a in accepted}

    # Filenames now lead with the sending facility: "BioReference Health_Occult
    # Blood.pdf". The golden set records CWC's own names, which have no prefix,
    # so comparing the whole string would fail every document whose letterhead
    # was read and report a total collapse in naming accuracy where nothing has
    # actually regressed.
    #
    # The prefix is scored separately. What filename_pass still measures is the
    # thing it always measured: did we arrive at the name CWC would have typed.
    pred_facility = result.get("facility")
    pred_stem = pred_name or ""
    if pred_facility and pred_stem.startswith(f"{pred_facility}_"):
        pred_stem = pred_stem[len(pred_facility) + 1:]

    name_pass = bool(pred_stem) and bool(exp_name) and _norm(pred_stem) == _norm(exp_name)
    facility_pass = bool(pred_facility)

    # Gate: held when band=LOW or forceManualReview=True
    auto_routable = (band in ("HIGH", "MEDIUM")) and not force_rev

    return {
        "file":                   fname,
        "block":                  sample.get("block", "unspecified"),
        "expected_category":      exp_cat,
        "predicted_category":     pred_cat,
        "expected_subtype":       exp_sub,
        "predicted_subtype":      pred_sub,
        "expected_folder":        exp_fold,
        "predicted_folder":       pred_fold,
        "expected_specification": exp_spec,
        "predicted_specification": pred_spec,
        "expected_file_name":     exp_name,
        "predicted_file_name":    pred_name,
        "predicted_file_stem":    pred_stem,
        "predicted_facility":     pred_facility,
        "facility_pass":          facility_pass,
        "predicted_alternate_name": pred_alt,
        "specification_pass":     spec_pass,
        "filename_pass":          name_pass,
        "naming_source":          result.get("namingSource"),
        "rule_matched":           rule,
        "calibrated_confidence":  cal_conf,
        "confidence_band":        band,
        "force_manual_review":    force_rev,
        "auto_routable":          auto_routable,
        "category_pass":          cat_pass,
        "subtype_pass":           sub_pass,
        "folder_pass":            fold_pass,
        "latency_ms":             latency,
        "evidence":               evidence,
        "reason":                 result.get("reason"),
        "runner_up_category":     result.get("runnerUpCategory"),
        "runner_up_confidence":   result.get("runnerUpConfidence"),
    }


# ── Multi-repeat wrapper ───────────────────────────────────────────────────────

def evaluate_sample_repeated(
    sample: dict,
    datasets_path: str,
    dry_run: bool,
    routing_cfg: dict,
    n_repeat: int,
    save_fixtures: bool,
) -> dict:
    if n_repeat == 1:
        return evaluate_sample(sample, datasets_path, dry_run, routing_cfg, save_fixtures)

    runs = []
    for i in range(n_repeat):
        print(f"    repeat {i+1}/{n_repeat}…", flush=True)
        r = evaluate_sample(sample, datasets_path, dry_run, routing_cfg, False)
        runs.append(r)

    cats    = [r["predicted_category"] for r in runs]
    confs   = [r["calibrated_confidence"] for r in runs if r["calibrated_confidence"] is not None]
    latencies = [r["latency_ms"] for r in runs]
    deterministic = len(set(cats)) == 1

    merged = runs[0].copy()
    merged["repeat_categories"]     = cats
    merged["repeat_confidences"]    = confs
    merged["mean_confidence"]       = round(sum(confs) / len(confs), 4) if confs else None
    merged["confidence_std"]        = round(_std(confs), 4) if len(confs) > 1 else 0.0
    merged["mean_latency_ms"]       = int(sum(latencies) / len(latencies))
    merged["deterministic"]         = deterministic
    merged["n_repeat"]              = n_repeat
    if save_fixtures:
        fname = sample["file"]
        save_fixture(os.path.join(datasets_path, fname), runs[0])
    return merged


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


# ── Confusion matrix ───────────────────────────────────────────────────────────

def build_confusion(results: list[dict]) -> dict:
    categories = sorted(set(
        r[k] for r in results
        for k in ("expected_category", "predicted_category")
        if r.get(k)
    ))
    idx = {c: i for i, c in enumerate(categories)}
    n   = len(categories)
    matrix = [[0] * n for _ in range(n)]
    for r in results:
        if r.get("expected_category") and r.get("predicted_category"):
            matrix[idx[r["expected_category"]]][idx[r["predicted_category"]]] += 1
    return {"labels": categories, "matrix": matrix}


# ── Report generation ──────────────────────────────────────────────────────────

def build_report(results: list[dict], routing_cfg: dict, n_repeat: int) -> dict:
    total     = len(results)
    cat_pass  = sum(1 for r in results if r.get("category_pass"))
    sub_pass  = sum(1 for r in results if r.get("subtype_pass"))
    fold_pass = sum(1 for r in results if r.get("folder_pass"))
    spec_pass = sum(1 for r in results if r.get("specification_pass"))
    name_pass = sum(1 for r in results if r.get("filename_pass"))
    # How often a letterhead was legible enough to prefix the name. Not an
    # accuracy figure - there is no ground truth for the facility in the golden
    # set - so it is reported as coverage and never folded into a pass rate.
    fac_read  = sum(1 for r in results if r.get("facility_pass"))

    # Per-block breakdown. The blocks differ in evidential weight (see CAVEAT),
    # so a single headline number is misleading by construction.
    by_block: dict[str, dict] = {}
    for r in results:
        b = r.get("block", "unspecified")
        slot = by_block.setdefault(b, {"n": 0, "category": 0, "subtype": 0,
                                       "folder": 0, "specification": 0, "filename": 0})
        slot["n"] += 1
        for key, flag in (("category", "category_pass"), ("subtype", "subtype_pass"),
                          ("folder", "folder_pass"), ("specification", "specification_pass"),
                          ("filename", "filename_pass")):
            if r.get(flag):
                slot[key] += 1
    for slot in by_block.values():
        n = slot["n"] or 1
        for key in ("category", "subtype", "folder", "specification", "filename"):
            slot[key + "_accuracy"] = round(slot[key] / n, 4)

    # Gate quality
    auto_correct  = sum(1 for r in results if r.get("auto_routable") and r.get("category_pass"))
    auto_wrong    = sum(1 for r in results if r.get("auto_routable") and not r.get("category_pass"))
    review_correct = sum(1 for r in results if not r.get("auto_routable") and r.get("category_pass"))
    review_wrong   = sum(1 for r in results if not r.get("auto_routable") and not r.get("category_pass"))

    # The same gate at the backend's real threshold: HIGH band AND calibrated
    # confidence >= 0.90 (AsyncProcessingService). The band-only gate above is
    # kept so reports stay comparable with earlier runs.
    def _gated(r):
        return (r.get("confidence_band") == "HIGH"
                and not r.get("force_manual_review")
                and (r.get("calibrated_confidence") or 0) >= GATE_MIN_CONFIDENCE)
    g90 = {
        "threshold":      GATE_MIN_CONFIDENCE,
        "auto_correct":   sum(1 for r in results if _gated(r) and r.get("category_pass")),
        "auto_wrong":     sum(1 for r in results if _gated(r) and not r.get("category_pass")),
        "review_correct": sum(1 for r in results if not _gated(r) and r.get("category_pass")),
        "review_wrong":   sum(1 for r in results if not _gated(r) and not r.get("category_pass")),
    }
    lats = [r.get("latency_ms") for r in results if isinstance(r.get("latency_ms"), (int, float))]

    confusion = build_confusion(results)

    return {
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "version":        "1.0",
        "caveat":         CAVEAT,
        "n_samples":      total,
        "n_repeat":       n_repeat,
        "routing_mode":   routing_cfg.get("mode", "SUGGEST"),
        "model":          _MODEL_INFO,
        "avg_latency_ms": int(sum(lats) / len(lats)) if lats else None,
        "gate_quality_90": g90,
        "summary": {
            "category_accuracy":      round(cat_pass  / total, 4) if total else 0,
            "subtype_accuracy":       round(sub_pass  / total, 4) if total else 0,
            "folder_accuracy":        round(fold_pass / total, 4) if total else 0,
            "specification_accuracy": round(spec_pass / total, 4) if total else 0,
            "filename_accuracy":      round(name_pass / total, 4) if total else 0,
            "category_pass":     cat_pass,
            "subtype_pass":      sub_pass,
            "folder_pass":       fold_pass,
            "specification_pass": spec_pass,
            "filename_pass":     name_pass,
            "facility_read":     fac_read,
            "facility_coverage": round(fac_read / (total or 1), 4),
            "n_samples":         total,
        },
        "by_block": by_block,
        "gate_quality": {
            "auto_correct":    auto_correct,
            "auto_wrong":      auto_wrong,
            "review_correct":  review_correct,
            "review_wrong":    review_wrong,
            "description": {
                "auto_correct":   "Auto-routable AND correct (the win)",
                "auto_wrong":     "Auto-routable AND WRONG — THE NUMBER TO DRIVE TO ZERO",
                "review_correct": "Held for review but would have been correct (cost of caution)",
                "review_wrong":   "Held for review and correctly held",
            },
        },
        "confusion_matrix": confusion,
        "per_file":         results,
    }


_MODEL_INFO: dict = {}


def _model_slug() -> str:
    m = (_MODEL_INFO.get("model") or "").strip()
    return ("_" + re.sub(r"[^A-Za-z0-9.-]+", "-", m)) if m else ""


def fetch_model_info() -> dict:
    """Which model the AI service is actually calling, recorded in the report."""
    try:
        h = requests.get(f"{AI_BASE}/health", timeout=5).json()
        return {k: h.get(k) for k in ("provider", "model", "extractionModel", "endpoint")}
    except Exception as exc:
        return {"error": str(exc)}


def write_json_report(report: dict) -> Path:
    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS_DIR / f"eval_{ts}{_model_slug()}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return path


def write_md_report(report: dict, json_path: Path) -> Path:
    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS_DIR / f"eval_{ts}{_model_slug()}.md"

    s   = report["summary"]
    gq  = report["gate_quality"]
    pct = lambda v: f"{v*100:.0f}%"

    lines = [
        "# CWC Fax Classification — Evaluation Report",
        "",
        f"**Generated:** {report['timestamp']}",
        f"**Routing mode:** {report['routing_mode']}  |  "
        f"**Samples:** {report['n_samples']}  |  "
        f"**Repeats:** {report['n_repeat']}",
        f"**Model:** {report.get('model', {}).get('model', 'unknown')} "
        f"({report.get('model', {}).get('provider', '?')})  |  "
        f"**Avg latency:** {(report.get('avg_latency_ms') or 0) / 1000:.1f} s per fax",
        "",
        "---",
        "",
        "## ⚠ Caveat — Read Before Citing These Numbers",
        "",
        f"> {CAVEAT}",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Metric | Score |",
        f"|---|---|",
        f"| Category accuracy | {pct(s['category_accuracy'])} ({s['category_pass']}/{s['n_samples']}) |",
        f"| Subtype accuracy  | {pct(s['subtype_accuracy'])}  ({s['subtype_pass']}/{s['n_samples']}) |",
        f"| Folder accuracy   | {pct(s['folder_accuracy'])}   ({s['folder_pass']}/{s['n_samples']}) |",
        f"| Specification accuracy | {pct(s.get('specification_accuracy', 0))} ({s.get('specification_pass', 0)}/{s['n_samples']}) |",
        f"| Filename accuracy | {pct(s.get('filename_accuracy', 0))} ({s.get('filename_pass', 0)}/{s['n_samples']}) |",
        f"| Facility read | {pct(s.get('facility_coverage', 0))} ({s.get('facility_read', 0)}/{s['n_samples']}) |",
        "",
        "---",
        "",
        "## Per-block breakdown",
        "",
        "> The blocks are not equally strong evidence. `avenir_reference` shares its",
        "> origin with the taxonomy; `new_inbound` is the closest thing to held-out data.",
        "",
        "| Block | n | Category | Subtype | Folder | Specification | Filename |",
        "|---|---|---|---|---|---|---|",
        *[
            f"| {b} | {v['n']} | {pct(v['category_accuracy'])} | {pct(v['subtype_accuracy'])} "
            f"| {pct(v['folder_accuracy'])} | {pct(v['specification_accuracy'])} "
            f"| {pct(v['filename_accuracy'])} |"
            for b, v in sorted(report.get("by_block", {}).items())
        ],
        "",
        "---",
        "",
        "## Gate Quality",
        "",
        "> Gate quality matters more than raw accuracy: a wrong auto-route",
        "> creates real work; a correct-but-held document is merely cautious.",
        "",
        f"| Outcome | Count |",
        f"|---|---|",
        f"| Auto-routable AND correct | {gq['auto_correct']} |",
        f"| **Auto-routable AND WRONG** (→ drive to zero) | **{gq['auto_wrong']}** |",
        f"| _At the {report['gate_quality_90']['threshold']*100:.0f}% gate the backend enforces:_ | |",
        f"| Auto-routed AND correct | {report['gate_quality_90']['auto_correct']} |",
        f"| **Auto-routed AND WRONG** | **{report['gate_quality_90']['auto_wrong']}** |",
        f"| Sent to Manual-Review — would have been correct | {report['gate_quality_90']['review_correct']} |",
        f"| Sent to Manual-Review — correctly held | {report['gate_quality_90']['review_wrong']} |",
        f"| Held for review — would have been correct (cost of caution) | {gq['review_correct']} |",
        f"| Held for review — correctly held | {gq['review_wrong']} |",
        "",
        "---",
        "",
        "## Confusion Matrix",
        "",
    ]

    cm     = report["confusion_matrix"]
    labels = cm["labels"]
    matrix = cm["matrix"]

    if labels:
        short = [lb.replace("_", " ") for lb in labels]
        header = "| Expected \\ Predicted | " + " | ".join(short) + " |"
        sep    = "|---|" + "|".join(["---"] * len(labels)) + "|"
        lines += [header, sep]
        for i, row in enumerate(matrix):
            lines.append(f"| **{short[i]}** | " + " | ".join(str(v) for v in row) + " |")
    else:
        lines.append("*(no data)*")

    lines += [
        "",
        "---",
        "",
        "## Per-File Results",
        "",
        "| File | Exp Category | Pred Category | Exp Subtype | Pred Subtype | Exp Folder | Pred Folder | Conf | Band | FMR | Cat | Sub | Fold | Latency |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in report["per_file"]:
        if r.get("error"):
            lines.append(f"| {r['file'][:40]} | ERROR | {r['error']} |||||||||||||")
            continue
        passmark = lambda v: "✓" if v else "✗"
        conf = f"{r.get('calibrated_confidence', 0)*100:.0f}%" if r.get("calibrated_confidence") else "—"
        fmr  = "Y" if r.get("force_manual_review") else "N"
        lines.append(
            f"| {r['file'][:40]} "
            f"| {r.get('expected_category','—')[:22]} "
            f"| {r.get('predicted_category','—')[:22]} "
            f"| {r.get('expected_subtype','—')[:22]} "
            f"| {r.get('predicted_subtype','—')[:22]} "
            f"| {r.get('expected_folder','—')[:20]} "
            f"| {r.get('predicted_folder','—')[:20]} "
            f"| {conf} "
            f"| {r.get('confidence_band','—')} "
            f"| {fmr} "
            f"| {passmark(r.get('category_pass'))} "
            f"| {passmark(r.get('subtype_pass'))} "
            f"| {passmark(r.get('folder_pass'))} "
            f"| {r.get('latency_ms','—')}ms |"
        )

    lines += ["", "---", "", f"JSON report: `{json_path.name}`", ""]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ── Console output ─────────────────────────────────────────────────────────────

def print_report(report: dict) -> None:
    s  = report["summary"]
    gq = report["gate_quality"]
    pct = lambda v: f"{v*100:.0f}%"

    print()
    print("=" * 72)
    print("CWC FAX CLASSIFICATION — EVALUATION REPORT")
    print("=" * 72)
    print()
    print("CAVEAT:")
    print()
    for line in _wrap(CAVEAT, 70):
        print(f"  {line}")
    print()
    print("-" * 72)
    print("SUMMARY")
    print("-" * 72)
    print(f"  Category accuracy      : {pct(s['category_accuracy'])}  ({s['category_pass']}/{s['n_samples']})")
    print(f"  Subtype accuracy       : {pct(s['subtype_accuracy'])}  ({s['subtype_pass']}/{s['n_samples']})")
    print(f"  Folder accuracy        : {pct(s['folder_accuracy'])}  ({s['folder_pass']}/{s['n_samples']})")
    print(f"  Specification accuracy : {pct(s.get('specification_accuracy', 0))}  ({s.get('specification_pass', 0)}/{s['n_samples']})")
    print(f"  Filename accuracy      : {pct(s.get('filename_accuracy', 0))}  ({s.get('filename_pass', 0)}/{s['n_samples']})")
    print(f"  Facility read          : {pct(s.get('facility_coverage', 0))}  ({s.get('facility_read', 0)}/{s['n_samples']})")
    print()
    blocks = report.get("by_block", {})
    if blocks:
        print("-" * 72)
        print("PER-BLOCK  (avenir_reference shares its origin with the taxonomy)")
        print("-" * 72)
        print(f"  {'block':20} {'n':>4} {'cat':>7} {'sub':>7} {'fold':>7} {'spec':>7} {'name':>7}")
        for b, v in sorted(blocks.items()):
            print(f"  {b:20} {v['n']:>4} {pct(v['category_accuracy']):>7} "
                  f"{pct(v['subtype_accuracy']):>7} {pct(v['folder_accuracy']):>7} "
                  f"{pct(v['specification_accuracy']):>7} {pct(v['filename_accuracy']):>7}")
        print()
    print("-" * 72)
    print("GATE QUALITY  (what matters for production)")
    print("-" * 72)
    print(f"  Auto-routable AND correct                    : {gq['auto_correct']}")
    print(f"  Auto-routable AND WRONG (→ drive to zero)   : {gq['auto_wrong']}")
    print(f"  Held for review — would have been correct   : {gq['review_correct']}")
    print(f"  Held for review — correctly held            : {gq['review_wrong']}")
    print()
    print("-" * 72)
    print("CONFUSION MATRIX  (rows=expected, cols=predicted)")
    print("-" * 72)
    cm     = report["confusion_matrix"]
    labels = cm["labels"]
    matrix = cm["matrix"]
    if labels:
        col_w = max(len(lb) for lb in labels) + 2
        short = [lb[:col_w] for lb in labels]
        print("  " + " " * 24 + "  ".join(f"{s:>{col_w}}" for s in short))
        for i, row in enumerate(matrix):
            marker = " <<" if any(row[j] for j in range(len(labels)) if j != i) else ""
            print(f"  {labels[i]:<24}" + "  ".join(f"{v:>{col_w}}" for v in row) + marker)
    print()
    print("-" * 72)
    print("PER-FILE RESULTS")
    print("-" * 72)
    for r in report["per_file"]:
        if r.get("error"):
            print(f"  ERROR: {r['file']}: {r['error']}")
            continue
        P = lambda v: "PASS" if v else "FAIL"
        conf = f"{r.get('calibrated_confidence', 0)*100:.0f}%" if r.get("calibrated_confidence") else "—"
        cat_mark = P(r.get("category_pass"))
        sub_mark = P(r.get("subtype_pass"))
        fold_mark = P(r.get("folder_pass"))
        fmr = " [REVIEW]" if r.get("force_manual_review") else ""
        print(f"  {r['file']}")
        print(f"    Category : {r.get('expected_category','—')} → {r.get('predicted_category','—')}  [{cat_mark}]")
        print(f"    Subtype  : {r.get('expected_subtype','—')} → {r.get('predicted_subtype','—')}  [{sub_mark}]")
        print(f"    Folder   : {r.get('expected_folder','—')} → {r.get('predicted_folder','—')}  [{fold_mark}]")
        print(f"    Conf     : {conf}  Band: {r.get('confidence_band','—')}{fmr}  Latency: {r.get('latency_ms','—')}ms")
        if r.get("runner_up_category"):
            print(f"    Runner-up: {r['runner_up_category']} ({r.get('runner_up_confidence',''):.2f})")
        if r.get("evidence"):
            for ev in (r["evidence"] if isinstance(r["evidence"], list) else [])[:2]:
                print(f"    Evidence : \"{ev}\"")
        if not r.get("category_pass") and r.get("reason"):
            print(f"    Reason   : {r.get('reason','')[:120]}")
        print()


# ══════════════════════════════════════════════════════════════════════════════
# FIELD-LEVEL EXTRACTION EVALUATION  (§17 — Phase 9)
# ══════════════════════════════════════════════════════════════════════════════

_DIGITS_ONLY = re.compile(r"\D")


def _normalize(value: Any) -> str | None:
    """Normalize a value to a comparable string (uppercase, strip whitespace)."""
    if value is None:
        return None
    if isinstance(value, list):
        return None  # handled separately
    return str(value).strip().upper()


def _normalize_phone(value: str) -> str | None:
    """Strip all non-digits; remove leading country code 1."""
    d = _DIGITS_ONLY.sub("", value)
    if d.startswith("1") and len(d) == 11:
        d = d[1:]
    return d if d else None


def _is_phone_key(key: str) -> bool:
    return bool(re.search(r"fax$|phone$", key, re.I))


def _values_match(field_key: str, actual: Any, expected: Any, fuzzy: bool = False) -> bool:
    """
    Return True if actual matches expected for the given field.
    Handles:
      - null expected / null actual
      - list fields (order-insensitive subset: every expected item must appear in actual)
      - phone/fax normalization
      - fuzzy string matching (ratio >= 0.80) when fuzzy=True
    """
    if expected is None and actual is None:
        return True
    if expected is None and actual is not None:
        return False   # expected null but got a value
    if actual is None:
        return False   # expected a value but got null

    # List fields: every expected item must appear in actual
    if isinstance(expected, list):
        actual_list = actual if isinstance(actual, list) else [actual]
        actual_norm = [_normalize(a) for a in actual_list]
        for exp_item in expected:
            item_norm = _normalize(exp_item)
            if item_norm in actual_norm:
                continue
            # fuzzy check against list members
            scores = [difflib.SequenceMatcher(None, item_norm, a).ratio()
                      for a in actual_norm if a]
            if scores and max(scores) >= 0.80:
                continue
            return False
        return True

    # Phone/fax normalization
    if _is_phone_key(field_key):
        exp_d = _normalize_phone(str(expected))
        act_d = _normalize_phone(str(actual))
        if exp_d and act_d:
            return exp_d == act_d

    exp_s = _normalize(expected)
    act_s = _normalize(actual)
    if exp_s == act_s:
        return True
    if fuzzy and exp_s and act_s:
        ratio = difflib.SequenceMatcher(None, exp_s, act_s).ratio()
        return ratio >= 0.80
    return False


def _dig(data: dict, dot_path: str) -> Any:
    """Extract a value from a nested dict using dot-path notation."""
    # e.g. "categoryData.requestedMedication.name"
    parts = dot_path.split(".")
    # The outer keys are "core" and "categoryData"
    if parts[0] in ("core", "categoryData"):
        sub = data.get(parts[0], {})
        for p in parts[1:]:
            if not isinstance(sub, dict):
                return None
            sub = sub.get(p)
        return sub
    return data.get(dot_path)


def _get_confidence(extraction: dict, dot_path: str) -> float | None:
    """Get the (adjusted) confidence for a field from extraction.fieldConfidences."""
    fc = extraction.get("fieldConfidences", {})
    return fc.get(dot_path)


# ── Live extraction call via AI service ──────────────────────────────────────

def extract_live(file_path: str, tracking_id: str, category: str) -> dict:
    payload = {
        "file_path":    file_path,
        "tracking_id":  tracking_id,
        "category":     category,
    }
    t0 = time.perf_counter()
    r = requests.post(f"{AI_BASE}/extract", json=payload, timeout=EXTRACT_TIMEOUT)
    r.raise_for_status()
    result = r.json()
    result["_latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return result


# ── Per-sample field scoring ──────────────────────────────────────────────────

def score_extraction_sample(
    sample: dict,
    datasets_path: str,
    confidence_threshold: float,
) -> dict:
    """
    Run extraction for one sample and score it against golden labels.

    Returns a dict with:
      file, category, field_results (list), totals, confidently_wrong
    """
    fname    = sample["file"]
    category = sample["category"]
    fpath    = os.path.join(datasets_path, fname)

    if not os.path.exists(fpath):
        return {"file": fname, "error": f"File not found: {fpath}",
                "field_results": [], "totals": {}, "confidently_wrong": []}

    tracking_id = f"eval-{Path(fname).stem[:20]}"

    try:
        result = extract_live(fpath, tracking_id, category)
    except Exception as exc:
        return {"file": fname, "error": str(exc),
                "field_results": [], "totals": {}, "confidently_wrong": []}

    extraction = result.get("extraction", {})
    field_results = []

    for dot_path, label_entry in sample.get("fields", {}).items():
        if isinstance(label_entry, dict):
            expected = label_entry.get("value", "__MISSING__")
            is_review = label_entry.get("review", False)
            fuzzy     = label_entry.get("fuzzy", False)
        else:
            expected  = label_entry
            is_review = False
            fuzzy     = False

        if expected == "__MISSING__":
            continue

        actual     = _dig(result, dot_path)
        confidence = _get_confidence(extraction, dot_path)
        match      = _values_match(dot_path, actual, expected, fuzzy=fuzzy)

        field_results.append({
            "field":            dot_path,
            "expected":         expected,
            "actual":           actual,
            "match":            match,
            "confidence":       confidence,
            "is_review":        is_review,
            "fuzzy":            fuzzy,
        })

    # Aggregate totals
    tp = sum(1 for r in field_results if r["expected"] is not None and r["match"])
    fp = sum(1 for r in field_results if r["expected"] is None  and r["actual"] is not None)
    fn = sum(1 for r in field_results if r["expected"] is not None and not r["match"])
    tn = sum(1 for r in field_results if r["expected"] is None  and r["actual"] is None)

    # Confidently wrong: confidence >= threshold AND value doesn't match
    # Exclude review fields (labels not yet verified)
    confidently_wrong = [
        r for r in field_results
        if not r["is_review"]
        and r["confidence"] is not None
        and r["confidence"] >= confidence_threshold
        and not r["match"]
    ]

    return {
        "file":              fname,
        "category":          category,
        "field_results":     field_results,
        "totals":            {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "confidently_wrong": confidently_wrong,
        "extraction_latency_ms": result.get("_latency_ms", 0),
    }


# ── Aggregate field-level report ─────────────────────────────────────────────

def build_field_report(
    sample_scores: list[dict],
    confidence_threshold: float,
) -> dict:
    """
    Aggregate per-sample scores into per-field precision/recall and overall stats.
    """
    # Per-field accumulators
    per_field_tp: dict[str, int] = {}
    per_field_fp: dict[str, int] = {}
    per_field_fn: dict[str, int] = {}
    per_field_cw: dict[str, int] = {}  # confidently wrong

    total_cw = 0
    all_confidently_wrong = []

    for ss in sample_scores:
        if ss.get("error"):
            continue
        for r in ss["field_results"]:
            f = r["field"]
            per_field_tp.setdefault(f, 0)
            per_field_fp.setdefault(f, 0)
            per_field_fn.setdefault(f, 0)
            per_field_cw.setdefault(f, 0)
            if r["expected"] is not None and r["match"]:
                per_field_tp[f] += 1
            elif r["expected"] is None and r["actual"] is not None:
                per_field_fp[f] += 1
            elif r["expected"] is not None and not r["match"]:
                per_field_fn[f] += 1

        for cw in ss["confidently_wrong"]:
            per_field_cw[cw["field"]] = per_field_cw.get(cw["field"], 0) + 1
            total_cw += 1
            all_confidently_wrong.append({
                "file":       ss["file"],
                "field":      cw["field"],
                "expected":   cw["expected"],
                "actual":     cw["actual"],
                "confidence": cw["confidence"],
            })

    # Build per-field precision / recall
    all_fields = sorted(set(per_field_tp) | set(per_field_fp) | set(per_field_fn))
    field_metrics = []
    for f in all_fields:
        tp = per_field_tp.get(f, 0)
        fp = per_field_fp.get(f, 0)
        fn = per_field_fn.get(f, 0)
        cw = per_field_cw.get(f, 0)
        prec = tp / (tp + fp) if (tp + fp) > 0 else None
        rec  = tp / (tp + fn) if (tp + fn) > 0 else None
        field_metrics.append({
            "field":     f,
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 4) if prec is not None else None,
            "recall":    round(rec,  4) if rec  is not None else None,
            "confidently_wrong": cw,
        })

    # Overall totals
    all_tp = sum(per_field_tp.values())
    all_fp = sum(per_field_fp.values())
    all_fn = sum(per_field_fn.values())
    overall_prec = all_tp / (all_tp + all_fp) if (all_tp + all_fp) > 0 else None
    overall_rec  = all_tp / (all_tp + all_fn) if (all_tp + all_fn) > 0 else None

    return {
        "timestamp":             datetime.now(timezone.utc).isoformat(),
        "confidence_threshold":  confidence_threshold,
        "overall": {
            "tp":              all_tp,
            "fp":              all_fp,
            "fn":              all_fn,
            "precision":       round(overall_prec, 4) if overall_prec is not None else None,
            "recall":          round(overall_rec,  4) if overall_rec  is not None else None,
            "confidently_wrong": total_cw,
            "description_cw":  (
                f"Fields returned with confidence >= {confidence_threshold} that "
                f"do not match the golden label. THE NUMBER TO DRIVE TO ZERO."
            ),
        },
        "per_field":            field_metrics,
        "confidently_wrong_list": all_confidently_wrong,
        "per_sample":           sample_scores,
    }


def print_field_report(report: dict) -> None:
    ov = report["overall"]
    pct = lambda v: f"{v*100:.0f}%" if v is not None else "—"
    ct  = report["confidence_threshold"]

    print()
    print("=" * 72)
    print("CWC FAX EXTRACTION — FIELD-LEVEL EVALUATION REPORT")
    print("=" * 72)
    print()
    print(f"  Overall precision : {pct(ov['precision'])}  ({ov['tp']} TP / {ov['tp']+ov['fp']} attempts)")
    print(f"  Overall recall    : {pct(ov['recall'])}  ({ov['tp']} TP / {ov['tp']+ov['fn']} expected)")
    print()
    print(f"  Confidently-wrong (conf >= {ct}) : {ov['confidently_wrong']}"
          f"  ← THE NUMBER TO DRIVE TO ZERO")
    print()

    print("-" * 72)
    print(f"{'FIELD':<50} {'P':>5} {'R':>5} {'CW':>4}")
    print("-" * 72)
    for fm in report["per_field"]:
        prec = pct(fm["precision"])
        rec  = pct(fm["recall"])
        cw   = fm["confidently_wrong"]
        cw_mark = f" *** {cw}" if cw > 0 else f"   {cw}"
        print(f"  {fm['field']:<48} {prec:>5} {rec:>5}{cw_mark}")
    print()

    if report["confidently_wrong_list"]:
        print("-" * 72)
        print(f"CONFIDENTLY-WRONG DETAILS (conf >= {ct})")
        print("-" * 72)
        for cw in report["confidently_wrong_list"]:
            print(f"  {cw['file']}")
            print(f"    field      : {cw['field']}")
            print(f"    expected   : {cw['expected']!r}")
            print(f"    actual     : {cw['actual']!r}")
            print(f"    confidence : {cw['confidence']:.2f}")
            print()

    print("-" * 72)
    print("PER-SAMPLE DETAILS")
    print("-" * 72)
    for ss in report["per_sample"]:
        if ss.get("error"):
            print(f"  ERROR: {ss['file']}: {ss['error']}")
            continue
        t = ss["totals"]
        print(f"  {ss['file']}  [{ss['category']}]  latency={ss.get('extraction_latency_ms','—')}ms")
        print(f"    TP={t['tp']}  FP={t['fp']}  FN={t['fn']}  TN={t['tn']}"
              f"  CW={len(ss['confidently_wrong'])}")
        for r in ss["field_results"]:
            ok  = "✓" if r["match"] else "✗"
            rev = " [review]" if r["is_review"] else ""
            conf_s = f"{r['confidence']:.2f}" if r["confidence"] is not None else "—"
            print(f"    {ok} {r['field']:<46} conf={conf_s}{rev}")
            if not r["match"]:
                print(f"        expected={r['expected']!r}  got={r['actual']!r}")
        print()


def write_field_json_report(report: dict) -> Path:
    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS_DIR / f"field_eval_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return path


# ── Field-eval entry point ────────────────────────────────────────────────────

def run_field_eval(datasets_path: str, confidence_threshold: float) -> None:
    if not GOLDEN_META_FILE.exists():
        print(f"ERROR: {GOLDEN_META_FILE} not found. Cannot run field evaluation.")
        sys.exit(1)

    with open(GOLDEN_META_FILE, encoding="utf-8") as f:
        golden_meta = yaml.safe_load(f)
    samples = golden_meta["samples"]
    print(f"Loaded {len(samples)} samples from golden_metadata.yml")
    print(f"Confidence-wrong threshold : {confidence_threshold}")
    print()

    sample_scores = []
    for sample in samples:
        fname = sample["file"]
        print(f"Extracting: {fname}", flush=True)
        ss = score_extraction_sample(sample, datasets_path, confidence_threshold)
        if ss.get("error"):
            print(f"  ERROR: {ss['error']}", flush=True)
        else:
            t  = ss["totals"]
            cw = len(ss["confidently_wrong"])
            print(f"  TP={t['tp']} FP={t['fp']} FN={t['fn']}  "
                  f"CW={cw}  latency={ss.get('extraction_latency_ms','—')}ms", flush=True)
        sample_scores.append(ss)

    report = build_field_report(sample_scores, confidence_threshold)
    print_field_report(report)

    json_path = write_field_json_report(report)
    print(f"JSON report : {json_path}")
    print()

    if report["overall"]["confidently_wrong"] > 0:
        print(f"WARNING: {report['overall']['confidently_wrong']} field(s) returned with "
              f"conf >= {confidence_threshold} that do not match golden labels.")
        sys.exit(1)


# ── _wrap helper ──────────────────────────────────────────────────────────────

def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines, line = [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = (line + " " + w).lstrip()
    if line:
        lines.append(line)
    return lines


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="CWC Fax Classification + Extraction Evaluation")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Run each sample N times to check temperature=0 determinism")
    parser.add_argument("--dry-run", action="store_true",
                        help="Replay saved fixtures — no live API calls")
    parser.add_argument("--datasets-path", default=None,
                        help="Path to real fax PDFs. Defaults to CWC_DATASET_PATH env var.")
    parser.add_argument("--save-fixtures", action="store_true", default=True,
                        help="Save live responses as fixtures for future --dry-run (default: on)")
    parser.add_argument("--no-save-fixtures", dest="save_fixtures", action="store_false")
    parser.add_argument(
        "--extract-eval", action="store_true",
        help="Run Phase 9 field-level extraction evaluation against golden_metadata.yml",
    )
    parser.add_argument(
        "--confidence-threshold", type=float, default=0.85,
        help="Confidence threshold for 'confidently wrong' count (default: 0.85)",
    )
    args = parser.parse_args()

    datasets_path = (
        args.datasets_path
        or os.environ.get("CWC_DATASET_PATH")
        or str(Path(__file__).parent.parent / "cwc healthcare datasets")
    )

    # Field-level extraction evaluation mode
    if args.extract_eval:
        print(f"Datasets path : {datasets_path}")
        print()
        run_field_eval(datasets_path, args.confidence_threshold)
        return

    global _MODEL_INFO
    _MODEL_INFO = fetch_model_info()
    print(f"AI service    : {_MODEL_INFO}")
    # Recorded fixtures are the Claude responses the --dry-run demo replays.
    # A run on another model must not overwrite them.
    if args.save_fixtures and not str(_MODEL_INFO.get("model", "")).startswith("claude"):
        args.save_fixtures = False
        print("Fixtures      : not saved (non-Claude model; keeps the recorded demo responses intact)")
    print(f"Datasets path : {datasets_path}")
    print(f"Repeat        : {args.repeat}")
    print(f"Dry-run       : {args.dry_run}")
    print()

    # Load golden set
    with open(GOLDEN_FILE, encoding="utf-8") as f:
        golden = yaml.safe_load(f)
    samples = golden["samples"]
    print(f"Loaded {len(samples)} samples from golden_set.yml")

    # Load routing config
    routing_cfg = fetch_routing_config()
    print(f"Routing mode: {routing_cfg.get('mode', '?')}")
    print()

    # Run evaluation
    results = []
    for sample in samples:
        fname = sample["file"]
        print(f"Evaluating: {fname}", flush=True)
        r = evaluate_sample_repeated(
            sample, datasets_path, args.dry_run,
            routing_cfg, args.repeat, args.save_fixtures,
        )
        cat_mark  = "PASS" if r.get("category_pass")  else "FAIL"
        fold_mark = "PASS" if r.get("folder_pass")    else "FAIL"
        print(f"  -> {r.get('predicted_category','—')} [{cat_mark}]  folder:{r.get('predicted_folder','—')} [{fold_mark}]"
              f"  conf:{r.get('calibrated_confidence',0)*100:.0f}%"
              f"  {r.get('latency_ms','—')}ms", flush=True)
        results.append(r)

    # Build and output report
    report = build_report(results, routing_cfg, args.repeat)
    print_report(report)

    json_path = write_json_report(report)
    md_path   = write_md_report(report, json_path)

    print(f"JSON report : {json_path}")
    print(f"Markdown    : {md_path}")
    print()

    # Exit code: 0 if all pass, 1 if any fail
    if report["gate_quality"]["auto_wrong"] > 0:
        print(f"WARNING: {report['gate_quality']['auto_wrong']} auto-routed docs would have been WRONG.")
    if report["summary"]["category_accuracy"] < 1.0:
        print(f"WARNING: {report['n_samples'] - report['summary']['category_pass']} misclassified.")
        sys.exit(1)


if __name__ == "__main__":
    main()
