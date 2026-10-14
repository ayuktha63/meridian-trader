"""Re-running a question must not look like more evidence."""
import copy

from app.research import dedupe


def _record(rid, *, created=1.0, trades=40, params=None, sha="d1", cost="crypto_perp",
            frm=100, to=200, kind="engine_walk_forward", commit="aaa", seeds=None):
    return {
        "id": rid, "created_at": created, "kind": kind, "stage": "walk_forward",
        "strategy": {"name": "engine", "version": "engine@1", "parameters": params or {"k": 1}},
        "symbol": "BTCUSD", "timeframe": "1h", "venue": "coindcx", "cost_profile": cost,
        "datasets": [{"sha256": sha, "first": frm, "last": to}],
        "window": {"from": frm, "to": to},
        "code": {"costs": "c1", "engine": "e1", "execution": "x1", "research": "r1",
                 "git": {"commit": commit, "dirty": True}},
        "seeds": seeds or {"bootstrap": 7}, "metrics": {"trades": trades},
    }


def test_identical_rerun_is_one_question_and_adds_no_trades():
    s = dedupe.summarise([_record("a", created=1), _record("b", created=2),
                          _record("c", created=3)])
    assert (s["raw_runs"], s["unique_configurations"], s["duplicate_runs"]) == (3, 1, 2)
    assert (s["raw_trades"], s["unique_trades"]) == (120, 40)
    assert s["groups"][0]["canonical"] == "a"          # earliest is canonical


def test_commit_alone_does_not_make_a_new_question():
    assert dedupe.fingerprint(_record("a", commit="x")) == dedupe.fingerprint(_record("b", commit="y"))


def test_changed_parameters_data_costs_or_seed_are_new_questions():
    base = dedupe.fingerprint(_record("a"))
    for change in ({"params": {"k": 2}}, {"sha": "d2"}, {"cost": "generic"},
                   {"seeds": {"bootstrap": 8}}, {"frm": 101}):
        assert dedupe.fingerprint(_record("z", **change)) != base, change


def test_code_version_change_is_a_new_question():
    other = _record("z")
    other["code"]["costs"] = "c2"
    assert dedupe.fingerprint(other) != dedupe.fingerprint(_record("a"))


def test_nothing_is_mutated_and_notes_are_not_evidence():
    records = [_record("a"), _record("n", kind="registration")]
    before = copy.deepcopy(records)
    s = dedupe.summarise(records)
    assert records == before
    assert s["raw_runs"] == 1


def test_rerun_with_different_trade_count_is_flagged_non_reproducible():
    s = dedupe.summarise([_record("a", created=1, trades=40), _record("b", created=2, trades=41)])
    assert s["non_reproducible"] == ["b"]


def test_different_questions_on_overlapping_bars_are_flagged_not_independent():
    s = dedupe.summarise([_record("a", params={"k": 1}), _record("b", params={"k": 2})])
    assert s["unique_configurations"] == 2
    assert s["overlapping_pairs"] == [["a", "b"]]


def test_disjoint_windows_do_not_overlap():
    s = dedupe.summarise([_record("a", frm=0, to=100), _record("b", frm=100, to=200, sha="d2")])
    assert s["overlapping_pairs"] == []


def test_independent_days_merge_overlaps_once():
    day = 86400
    s = dedupe.summarise([_record("a", frm=0, to=10 * day, params={"k": 1}),
                          _record("b", frm=5 * day, to=15 * day, params={"k": 2}),
                          _record("c", frm=30 * day, to=40 * day, params={"k": 3})])
    assert s["independent_days"]["BTCUSD/1h"] == 25.0      # 0-15 and 30-40
    assert s["summed_days"]["BTCUSD/1h"] == 30.0           # what naive adding claims
