import copy

import pytest

from casepath.contracts import CaseIndexRecord
from casepath.ingestion.cases.branch_handoff import main, reconcile


def fixture():
    def case(key, facts):
        return CaseIndexRecord(case_id=key, title=key, facts_text=facts)

    common = case("base", "事实一")
    first = case("lexrel.a", "事实二")
    second = case("lexrel.b", "事实三")
    third = case("lexrel.c", "事实四")
    supplement = case("spc", "事实五")
    subset = [{"sample": {"uniqid": "b", "input": "事实三"}}]
    return (
        [common, first, supplement],
        [common, first, second],
        [first, second, third],
        subset,
        ["spc"],
    )


def test_overlapping_data_is_counted_once():
    report = reconcile(*fixture())
    assert report["counts"] == {
        "p2_canonical": 3,
        "p3_canonical": 3,
        "lexrel_full_evaluation": 3,
        "p3_selected_increment": 1,
        "p3_selected_already_in_full": 1,
        "p2_full_overlap": 1,
        "p3_full_overlap": 2,
        "full_additional_to_p2": 2,
        "full_additional_to_p3": 1,
        "p3_unloaded_spc_now_in_p2": 1,
        "p2_and_full_distinct_cases": 5,
        "p3_and_full_distinct_cases": 4,
    }
    assert "事实" not in str(report)


@pytest.mark.parametrize(
    "problem", ["id", "facts", "conflict", "subset", "extra", "cross_duplicate"]
)
def test_reconciliation_rejects_inconsistent_corpora(problem):
    p2, p3, full, subset, extra = copy.deepcopy(fixture())
    if problem == "id":
        p2.append(p2[0])
    elif problem == "facts":
        p2[0].facts_text = "事 实 二"
    elif problem == "conflict":
        p3[1] = p3[1].model_copy(update={"facts_text": "不同事实"})
    elif problem == "subset":
        subset[0]["sample"]["input"] = "不同事实"
    elif problem == "cross_duplicate":
        p2.append(full[2].model_copy(update={"case_id": "different-id"}))
    else:
        extra.append("missing")
    with pytest.raises(ValueError):
        reconcile(p2, p3, full, subset, extra)


@pytest.mark.parametrize(
    "output",
    [
        "canonical/result.json",
        "inputs/result.json",
        "manifests/result.json",
        "reports/lexrel_retrieval.manifest.json",
    ],
)
def test_output_cannot_overwrite_protected_data(tmp_path, output):
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--data-root",
                str(tmp_path / "data"),
                "--p3-data-root",
                str(tmp_path / "p3"),
                "--output",
                str(tmp_path / "data" / output),
            ]
        )
    assert error.value.code == 2
