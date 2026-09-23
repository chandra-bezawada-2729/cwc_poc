"""
Unit tests for services/naming_service.py — the facility prefix in particular.

The naming layer reproduces CWC's own 36 filenames, and that property is the
evidence it is right. The facility prefix changes the name, so these tests pin
down exactly what it changes and — more importantly — what it does not.
"""
import pytest
import yaml
from pathlib import Path

from services import naming_service as n

_TAXONOMY = yaml.safe_load(
    (Path(__file__).parent.parent / "config" / "categories.yml").read_text(encoding="utf-8")
)


def name(category, subtype, spec, facility=None, original="Sample 1.pdf"):
    return n.build_suggested_names(
        _TAXONOMY, category, subtype, spec, original, facility=facility,
    )


class TestFacilityPrefix:
    def test_prefixes_the_house_name_rather_than_replacing_it(self):
        r = name("CONSULTATION_REPORT", "OFFICE_VISIT_NOTE", "Ophthalmology",
                 facility="Vision Care Ophthalmology, PC")
        assert r["suggestedFileName"] == "Vision Care Ophthalmology_Ophthalmology Consult.pdf"

    def test_no_facility_still_produces_cwc_s_own_name(self):
        """
        The load-bearing case. A fax whose letterhead could not be read must be
        named the way CWC has always named it, not with a placeholder prefix and
        not with a leading separator.
        """
        r = name("CONSULTATION_REPORT", "OFFICE_VISIT_NOTE", "Ophthalmology")
        assert r["suggestedFileName"] == "Ophthalmology Consult.pdf"
        assert r["facility"] is None

    @pytest.mark.parametrize("blank", [None, "", "   ", "  .  "])
    def test_unusable_facility_is_treated_as_absent(self, blank):
        r = name("LAB_REPORT", None, "Occult Blood", facility=blank)
        assert not r["suggestedFileName"].startswith("_")
        assert r["suggestedFileName"] == "Occult Blood.pdf"

    def test_two_documents_from_one_sender_stay_distinguishable(self):
        """
        The reason the specification is kept after the facility. Without it both
        of these would be "Manhattan Diagnostic Radiology_Radiology Report.pdf"
        and the second would land as "... (2).pdf" — which reads as a filename
        clash between two different documents, not as two named studies.
        """
        a = name("RADIOLOGY_REPORT", None, "US Abdomen", facility="Manhattan Diagnostic Radiology")
        b = name("RADIOLOGY_REPORT", None, "US Bladder", facility="Manhattan Diagnostic Radiology")
        assert a["suggestedFileName"] != b["suggestedFileName"]
        assert a["suggestedFileName"].endswith("US Abdomen.pdf")
        assert b["suggestedFileName"].endswith("US Bladder.pdf")


class TestFacilityNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("Vision Care Ophthalmology, PC", "Vision Care Ophthalmology"),
        ("BioReference Health, LLC",      "BioReference Health"),
        ("EXTENDED HOME CARE, INC",       "EXTENDED HOME CARE"),
        ("Cooper Kids Therapy Assoc.",    "Cooper Kids Therapy Assoc"),
        ("Northwell Health",              "Northwell Health"),
    ])
    def test_strips_legal_entity_suffixes_and_punctuation(self, raw, expected):
        assert n._normalise_facility(raw) == expected

    @pytest.mark.parametrize("raw", [
        'Some/Clinic\\Name:With*Illegal?Chars',
        'Clinic <One> | Two',
    ])
    def test_filesystem_illegal_characters_never_survive(self, raw):
        import re
        assert not re.search(n._ILLEGAL, n._normalise_facility(raw))

    def test_long_names_are_trimmed_on_a_word_boundary(self):
        """A name cut mid-word reads as corruption rather than truncation."""
        raw = "New York Presbyterian Lower Manhattan Hospital Department of Radiology"
        out = n._normalise_facility(raw)
        assert len(out) <= n._MAX_FACILITY
        assert not out.endswith(" ")
        assert raw.startswith(out)          # a real prefix, not a mangled one
        assert " " not in out[-1:]          # no dangling separator

    def test_an_entity_suffix_alone_is_not_a_facility(self):
        assert n._normalise_facility("LLC") == ""
        assert n._normalise_facility(", PC") == ""


class TestAlternateName:
    def test_alternate_carries_the_facility_in_snake_case(self):
        r = name("LAB_REPORT", None, "Occult Blood", facility="BioReference Health, LLC")
        assert r["alternateFileName"].startswith("bioreference_health_")
        assert r["alternateFileName"].endswith(".pdf")
        assert " " not in r["alternateFileName"]


class TestContract:
    def test_facility_key_is_always_present(self):
        """The backend reads it unconditionally; absence must be None, not missing."""
        assert "facility" in name("FORM", None, "Medical Clearance")
        assert "facility" in name("UNKNOWN", None, None)

    def test_naming_never_raises(self):
        """A naming failure must not be able to fail a classification."""
        assert n.build_suggested_names({}, "NOPE", None, None, "x.pdf", facility="X")["suggestedFileName"]
