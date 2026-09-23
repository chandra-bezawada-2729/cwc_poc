"""Local models report null / string / percentage confidences; none may crash extraction."""
from services.extraction_validator import validate_extraction, _as_confidence


def test_null_and_odd_confidences_do_not_crash():
    _, adjusted = validate_extraction(
        {"core.patientName": None, "core.mrn": "0.8", "core.providerName": 90, "core.dob": "bad"},
        {"patientName": None, "mrn": "12345", "providerName": "Dr X", "dob": None},
        {},
    )
    assert adjusted["core.patientName"] == 0.0
    assert adjusted["core.dob"] == 0.0
    assert 0.0 < adjusted["core.providerName"] <= 1.0
    assert 0.0 < adjusted["core.mrn"] <= 1.0


def test_as_confidence():
    assert _as_confidence(None) == 0.0
    assert _as_confidence(0.75) == 0.75
    assert _as_confidence("90%") == 0.9
    assert _as_confidence(True) == 0.0
    assert _as_confidence(3) == 0.03
