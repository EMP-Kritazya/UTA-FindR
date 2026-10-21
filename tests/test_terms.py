import pytest

from ingest.terms import Term, parse_term, term_range


@pytest.mark.parametrize("text", ["Spring 2023", "2023 Spring", "2023-SPRING", "2023-1", "2232"])
def test_all_formats_parse_to_same_term(text):
    # UTA writes one term five ways; they must all mean the same thing.
    assert parse_term(text) == Term(2023, 1)


def test_parsing_ignores_case():
    assert parse_term("fall 2024") == Term(2024, 3)


def test_conversions():
    t = parse_term("Fall 2026")
    assert t.key == "2026-3"
    assert t.strm == "2268"  # matches S3 folder names like 2026-FALL_2268-...
    assert t.label == "Fall 2026"
    assert t.semester == "Fall"  # value DM/courses expects for `semester=`


def test_terms_sort_in_calendar_order():
    spring, summer, fall = parse_term("Spring 2023"), parse_term("Summer 2023"), parse_term("Fall 2023")
    assert spring < summer < fall < parse_term("Spring 2024")
    # Postgres sorts term_key as text, so the text form must sort correctly too.
    assert sorted(["2024-1", "2023-3", "2023-1", "2023-2"]) == ["2023-1", "2023-2", "2023-3", "2024-1"]


def test_next_after_fall_is_spring_of_next_year():
    # The year boundary is where a naive `season + 1` would produce season 4.
    assert parse_term("Fall 2023").next() == Term(2024, 1)
    assert parse_term("Spring 2023").next() == Term(2023, 2)


def test_term_range_is_inclusive():
    terms = term_range("Fall 2023", "Spring 2024")
    assert [t.label for t in terms] == ["Fall 2023", "Spring 2024"]


def test_term_range_spring_2023_onward_includes_summers():
    keys = [t.key for t in term_range("Spring 2023", "Spring 2024")]
    assert keys == ["2023-1", "2023-2", "2023-3", "2024-1"]


@pytest.mark.parametrize("bad", ["Winter 2023", "2023-4", "Spring", "", "2239"])
def test_rejects_unknown_terms(bad):
    with pytest.raises(ValueError):
        parse_term(bad)
