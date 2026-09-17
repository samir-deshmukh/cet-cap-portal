from backend.admin.pipeline import identify


def test_identify_cet_filename_with_two_digit_year_and_round():
    assert identify('BBA_26_C1.pdf') == ('CUTOFFS', 'BBA', 2026, 'C1', 1.0)
    assert identify('MBA_25_C2.pdf') == ('CUTOFFS', 'MBA', 2025, 'C2', 1.0)
    assert identify('BBA_26_C1_SM.pdf') == ('SEATS', 'BBA', 2026, 'C1', 1.0)
