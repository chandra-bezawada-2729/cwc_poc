"""
Side-by-side comparison of two run_eval.py JSON reports (e.g. Claude Opus vs local Qwen).

  python evaluation/compare_evals.py <baseline>.json <candidate>.json [BaselineLabel CandidateLabel]
  e.g. python evaluation/compare_evals.py evaluation/reports/eval_20260912T192050Z.json evaluation/reports/eval_..._qwen3.5-9b.json Opus Qwen

Recomputes both gates from the per-file rows, so an older report without
gate_quality_90 (the Opus baseline) is compared on exactly the same rule.
Writes evaluation/reports/compare_<a>_vs_<b>.md and prints it.
"""
import json
import sys
from pathlib import Path

GATE = 0.90


def load(p):
    r = json.loads(Path(p).read_text(encoding="utf-8"))
    rows = {x["file"]: x for x in r["per_file"]}
    name = (r.get("model") or {}).get("model") or Path(p).stem
    return r, rows, name


def gates(rows):
    band = lambda x: x.get("auto_routable")
    g90 = lambda x: (x.get("confidence_band") == "HIGH" and not x.get("force_manual_review")
                     and (x.get("calibrated_confidence") or 0) >= GATE)
    out = {}
    for label, f in (("band", band), ("90", g90)):
        out[label] = {
            "auto_correct": sum(1 for x in rows if f(x) and x.get("category_pass")),
            "auto_wrong":   sum(1 for x in rows if f(x) and not x.get("category_pass")),
            "held_correct": sum(1 for x in rows if not f(x) and x.get("category_pass")),
            "held_wrong":   sum(1 for x in rows if not f(x) and not x.get("category_pass")),
        }
    return out


def pct(n, d):
    return f"{n}/{d} ({n / d * 100:.0f}%)" if d else "—"


def main(a, b, la=None, lb=None):
    ra, rowsa, na = load(a)
    rb, rowsb, nb = load(b)
    na, nb = la or na, lb or nb
    common = sorted(set(rowsa) & set(rowsb))
    A = [rowsa[f] for f in common]
    B = [rowsb[f] for f in common]
    n = len(common)
    ga, gb = gates(A), gates(B)

    def acc(rows, key):
        return sum(1 for x in rows if x.get(key))

    lat = lambda rows: (sum(x.get("latency_ms") or 0 for x in rows) / max(1, len(rows))) / 1000
    L = [f"# Evaluation comparison: {na} vs {nb}", "",
         f"{n} documents scored by both runs (baseline `{Path(a).name}`, candidate `{Path(b).name}`).", "",
         f"| Metric | {na} | {nb} |", "|---|---|---|"]
    for label, key in (("Category", "category_pass"), ("Folder", "folder_pass"),
                       ("Subtype", "subtype_pass"), ("Specification", "specification_pass"),
                       ("Filename", "filename_pass")):
        L.append(f"| {label} accuracy | {pct(acc(A, key), n)} | {pct(acc(B, key), n)} |")
    L.append(f"| Avg classify latency | {lat(A):.1f} s | {lat(B):.1f} s |")
    L += ["", f"## Gate quality at the {GATE*100:.0f}% auto-route gate (what the backend enforces)", "",
          f"| Outcome | {na} | {nb} |", "|---|---|---|",
          f"| Auto-routed AND correct | {ga['90']['auto_correct']} | {gb['90']['auto_correct']} |",
          f"| **Auto-routed AND WRONG** | **{ga['90']['auto_wrong']}** | **{gb['90']['auto_wrong']}** |",
          f"| Manual-Review, would have been correct | {ga['90']['held_correct']} | {gb['90']['held_correct']} |",
          f"| Manual-Review, correctly held | {ga['90']['held_wrong']} | {gb['90']['held_wrong']} |",
          "", "Band-only gate (earlier reports' definition): auto-routed and wrong "
          f"{ga['band']['auto_wrong']} vs {gb['band']['auto_wrong']}.", ""]

    blocks = sorted({x.get("block") or "?" for x in A})
    if blocks != ["?"]:
        L += ["## Category accuracy by block", "", f"| Block | n | {na} | {nb} |", "|---|---|---|---|"]
        for bl in blocks:
            fa = [x for x in A if (x.get("block") or "?") == bl]
            fb = [rowsb[x["file"]] for x in fa]
            L.append(f"| {bl} | {len(fa)} | {pct(acc(fa, 'category_pass'), len(fa))} | {pct(acc(fb, 'category_pass'), len(fb))} |")
        L.append("")

    diffs = [(f, rowsa[f], rowsb[f]) for f in common
             if rowsa[f].get("category_pass") != rowsb[f].get("category_pass")
             or rowsa[f].get("predicted_category") != rowsb[f].get("predicted_category")]
    L += [f"## Documents where the two differ ({len(diffs)})", ""]
    if diffs:
        L += [f"| File | Expected | {na} | {nb} | {nb} conf |", "|---|---|---|---|---|"]
        for f, x, y in diffs:
            mark = lambda r: ("✓ " if r.get("category_pass") else "✗ ") + str(r.get("predicted_category"))
            L.append(f"| {f} | {x.get('expected_category')} | {mark(x)} | {mark(y)} | "
                     f"{(y.get('calibrated_confidence') or 0) * 100:.0f}% |")
    else:
        L.append("None.")

    wrong_b = [y for y in B if not y.get("category_pass")]
    if wrong_b:
        L += ["", f"## Every {nb} miss", "", "| File | Expected | Predicted | Conf | Auto-routed at 90%? |", "|---|---|---|---|---|"]
        for y in wrong_b:
            g = (y.get("confidence_band") == "HIGH" and not y.get("force_manual_review")
                 and (y.get("calibrated_confidence") or 0) >= GATE)
            L.append(f"| {y['file']} | {y.get('expected_category')} | {y.get('predicted_category')} | "
                     f"{(y.get('calibrated_confidence') or 0) * 100:.0f}% | {'**YES**' if g else 'no'} |")

    md = "\n".join(L) + "\n"
    out = Path(b).parent / f"compare_{Path(a).stem}_vs_{Path(b).stem}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"Written: {out}")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 5):
        sys.exit(__doc__)
    main(*sys.argv[1:])
