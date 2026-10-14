"""Which research records are the same question, and how many questions there were.

The registry keeps every run, as it should: a result that is revised is a new
experiment and the old one stays. But a count taken over *runs* is not a count
of *evidence*. Asking the same deterministic question three times gives one
answer three times, and an aggregate that adds the three up is three times as
sure as the data allows.

`fingerprint` names a question. Two records with the same fingerprint ran the
same strategy, with the same parameters, on the same data, over the same
window, with the same costs, execution model and seeds. Nothing is deleted:
`summarise` groups the records and counts each group once.

What is in the identity, and what is not:

* In: kind, stage, strategy name and version, its parameters, market,
  timeframe, venue, the content hash of every dataset, the window, the cost
  profile, the version of the cost, execution, engine and research code, and
  the seeds.
* Out: the record id, the time it ran, its metrics and verdict, and the git
  commit. The versions above are hashes of the code that matters, so a
  commit that does not touch it does not make a new question. The commit is
  kept on the record for audit.

Two different fingerprints on overlapping data are *not* independent either:
the same bars were looked at twice. `overlaps` finds them so a report can
say so instead of adding them up.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Sequence, Tuple

#: Kinds that report a result about a strategy on data. A registration or a
#: diagnostic is a note, not a measurement, and is never counted as evidence.
MEASUREMENT_KINDS = ("engine_walk_forward", "study", "india_study", "forward",
                     "candidate", "holdout")

_CODE_KEYS = ("costs", "engine", "execution", "research")


def _dataset_ids(record: Dict[str, Any]) -> List[str]:
    return sorted(str(d.get("sha256") or d.get("dataset_id") or "")
                  for d in record.get("datasets") or [])


def identity(record: Dict[str, Any]) -> Dict[str, Any]:
    """The fields that make two records the same question."""
    strategy = record.get("strategy") or {}
    code = record.get("code") or {}
    return {
        "kind": record.get("kind"), "stage": record.get("stage"),
        "strategy": strategy.get("name"), "version": strategy.get("version"),
        "parameters": strategy.get("parameters") or {},
        "symbol": record.get("symbol"), "timeframe": record.get("timeframe"),
        "venue": record.get("venue"), "datasets": _dataset_ids(record),
        "window": record.get("window") or {},
        "cost_profile": record.get("cost_profile"),
        "code": {k: code.get(k) for k in _CODE_KEYS},
        "seeds": record.get("seeds") or {},
    }


def fingerprint(record: Dict[str, Any]) -> str:
    body = json.dumps(identity(record), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _span(record: Dict[str, Any]) -> Tuple[float, float]:
    """The stretch of history a record looked at, from its datasets."""
    window = record.get("window") or {}
    if window.get("from") is not None and window.get("to") is not None:
        return float(window["from"]), float(window["to"])
    spans = [(float(d["first"]), float(d["last"]))
             for d in record.get("datasets") or []
             if d.get("first") is not None and d.get("last") is not None]
    if not spans:
        return 0.0, 0.0
    return min(s[0] for s in spans), max(s[1] for s in spans)


def overlaps(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Whether two different questions were asked of some of the same bars."""
    if a.get("symbol") != b.get("symbol") or a.get("timeframe") != b.get("timeframe"):
        return False
    (a0, a1), (b0, b1) = _span(a), _span(b)
    if a1 <= a0 or b1 <= b0:
        return False
    return min(a1, b1) > max(a0, b0)


def _trades(record: Dict[str, Any]) -> int:
    metrics = record.get("metrics") or {}
    for key in ("trades", "n_trades", "completed_trades"):
        if isinstance(metrics.get(key), (int, float)):
            return int(metrics[key])
    return 0


def independent_coverage(records: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """Seconds of distinct history examined per market, overlaps merged once.

    Fifty configurations run over the same year examined one year, not fifty.
    """
    spans: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for r in records:
        lo, hi = _span(r)
        if r.get("kind") in MEASUREMENT_KINDS and hi > lo:
            spans["{}/{}".format(r.get("symbol"), r.get("timeframe"))].append((lo, hi))
    out: Dict[str, float] = {}
    for key, items in spans.items():
        items.sort()
        total, cur_lo, cur_hi = 0.0, items[0][0], items[0][1]
        for lo, hi in items[1:]:
            if lo > cur_hi:
                total, cur_lo, cur_hi = total + cur_hi - cur_lo, lo, hi
            else:
                cur_hi = max(cur_hi, hi)
        out[key] = total + cur_hi - cur_lo
    return out


def _summed(records: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = defaultdict(float)
    for r in records:
        lo, hi = _span(r)
        if r.get("kind") in MEASUREMENT_KINDS and hi > lo:
            out["{}/{}".format(r.get("symbol"), r.get("timeframe"))] += hi - lo
    return dict(out)


def summarise(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Raw runs against unique questions, and the trades each really adds.

    The earliest record of a group is the canonical one; later runs are
    linked to it. Their trade counts are not added — a deterministic re-run
    produced the same trades — and a re-run whose trade count *differs* from
    its canonical record is reported as non-reproducible, which is a finding.
    """
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("kind") in MEASUREMENT_KINDS:
            groups[fingerprint(record)].append(record)

    canonical: List[Dict[str, Any]] = []
    out_groups: List[Dict[str, Any]] = []
    for fp, members in groups.items():
        members.sort(key=lambda r: (r.get("created_at") or 0, r.get("id") or ""))
        first = members[0]
        canonical.append(first)
        mismatched = [m["id"] for m in members[1:] if _trades(m) != _trades(first)]
        out_groups.append({
            "fingerprint": fp, "canonical": first.get("id"),
            "runs": [m.get("id") for m in members], "repeats": len(members) - 1,
            "strategy": first.get("strategy", {}).get("name"),
            "symbol": first.get("symbol"), "timeframe": first.get("timeframe"),
            "trades": _trades(first), "non_reproducible": mismatched})

    overlapping = sorted({tuple(sorted((a["id"], b["id"])))
                          for i, a in enumerate(canonical) for b in canonical[i + 1:]
                          if overlaps(a, b)})
    raw_runs = sum(len(g["runs"]) for g in out_groups)
    return {
        "raw_runs": raw_runs, "unique_configurations": len(out_groups),
        "duplicate_runs": raw_runs - len(out_groups),
        "raw_trades": sum(_trades(m) for g in groups.values() for m in g),
        "unique_trades": sum(g["trades"] for g in out_groups),
        "overlapping_pairs": [list(p) for p in overlapping],
        "independent_days": {k: round(v / 86400, 1)
                             for k, v in independent_coverage(canonical).items()},
        "summed_days": {k: round(v / 86400, 1) for k, v in _summed(canonical).items()},
        "non_reproducible": [i for g in out_groups for i in g["non_reproducible"]],
        "groups": sorted(out_groups, key=lambda g: (str(g["strategy"]), str(g["symbol"]))),
    }
