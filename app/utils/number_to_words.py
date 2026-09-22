"""Convert a rupee amount to its English words form, Indian numbering system
(thousand/lakh/crore) -- used on the printable invoice, next to the numeric
total, matching the "Rupees ... Only" convention on Indian receipts/cheques.
"""

_ONES = [
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digits(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (" " + _ONES[ones] if ones else "")


def _three_digits(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    if hundreds:
        return _ONES[hundreds] + " Hundred" + (" " + _two_digits(rest) if rest else "")
    return _two_digits(rest)


def number_to_words(n: int) -> str:
    """Whole non-negative integer -> English words, Indian grouping (crore/lakh/thousand)."""
    if n == 0:
        return "Zero"

    parts = []
    crore, n = divmod(n, 1_00_00_000)
    if crore:
        parts.append(_three_digits(crore) + " Crore")
    lakh, n = divmod(n, 1_00_000)
    if lakh:
        parts.append(_two_digits(lakh) + " Lakh")
    thousand, n = divmod(n, 1_000)
    if thousand:
        parts.append(_two_digits(thousand) + " Thousand")
    if n:
        parts.append(_three_digits(n))
    return " ".join(parts)


def amount_in_words(amount) -> str:
    """Rupee amount (int/float/Decimal) -> "Rupees ... [and ... Paise] Only"."""
    # + 1e-9 guards against a stored value like 189.99999999 from float math
    # rounding down to 189 paise-worth instead of the intended 190.00.
    rupees_float = round(float(amount) + 1e-9, 2)
    rupees = int(rupees_float)
    paise = round((rupees_float - rupees) * 100)

    words = f"Rupees {number_to_words(rupees)}"
    if paise:
        words += f" and {number_to_words(paise)} Paise"
    return words + " Only"
