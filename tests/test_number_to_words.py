from app.utils.number_to_words import amount_in_words, number_to_words


def test_number_to_words_zero():
    assert number_to_words(0) == "Zero"


def test_number_to_words_below_twenty():
    assert number_to_words(7) == "Seven"
    assert number_to_words(19) == "Nineteen"


def test_number_to_words_tens_and_ones():
    assert number_to_words(42) == "Forty Two"
    assert number_to_words(90) == "Ninety"


def test_number_to_words_hundreds():
    assert number_to_words(190) == "One Hundred Ninety"
    assert number_to_words(105) == "One Hundred Five"


def test_number_to_words_thousands():
    assert number_to_words(1890) == "One Thousand Eight Hundred Ninety"
    assert number_to_words(50000) == "Fifty Thousand"


def test_number_to_words_lakhs_and_crores_indian_grouping():
    assert number_to_words(150000) == "One Lakh Fifty Thousand"
    assert number_to_words(12345678) == "One Crore Twenty Three Lakh Forty Five Thousand Six Hundred Seventy Eight"


def test_amount_in_words_whole_rupees():
    assert amount_in_words(1890) == "Rupees One Thousand Eight Hundred Ninety Only"
    assert amount_in_words(1890.00) == "Rupees One Thousand Eight Hundred Ninety Only"


def test_amount_in_words_with_paise():
    assert amount_in_words(190.50) == "Rupees One Hundred Ninety and Fifty Paise Only"


def test_amount_in_words_zero_rupees_with_paise():
    assert amount_in_words(0.75) == "Rupees Zero and Seventy Five Paise Only"


def test_amount_in_words_zero():
    assert amount_in_words(0) == "Rupees Zero Only"


def test_amount_in_words_avoids_float_rounding_artifacts():
    # Float noise from summing many price*qty lines can land a fraction of a
    # cent under a whole number -- must still read as a clean 190.00, not
    # "One Hundred Eighty Nine and Ninety Nine Paise".
    assert amount_in_words(189.99999999999997) == "Rupees One Hundred Ninety Only"
