import base64

from app.utils.upi_qr import build_upi_payment_uri, generate_qr_code_data_uri


def test_build_upi_payment_uri_includes_standard_fields():
    uri = build_upi_payment_uri("restaurant@upi", "Raj Mahal", 620, "ORD-20260922-0001")
    assert uri.startswith("upi://pay?")
    assert "pa=restaurant%40upi" in uri
    assert "pn=Raj%20Mahal" in uri
    assert "am=620.00" in uri
    assert "cu=INR" in uri
    assert "tn=Order%20ORD-20260922-0001" in uri


def test_build_upi_payment_uri_formats_amount_to_two_decimals():
    assert "am=100.00" in build_upi_payment_uri("x@upi", "Shop", 100, "ORD-1")
    assert "am=99.50" in build_upi_payment_uri("x@upi", "Shop", 99.5, "ORD-1")


def test_generate_qr_code_data_uri_returns_a_valid_png_data_uri():
    data_uri = generate_qr_code_data_uri("upi://pay?pa=x@upi&am=1.00")
    assert data_uri.startswith("data:image/png;base64,")
    png_bytes = base64.b64decode(data_uri.split(",", 1)[1])
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_qr_code_data_uri_encodes_the_exact_input_data():
    # Different input data must produce a different (larger, since the UPI
    # link is longer) QR image -- confirms the data actually flows through
    # to the QR payload rather than being ignored.
    short = generate_qr_code_data_uri("a")
    long = generate_qr_code_data_uri("upi://pay?pa=restaurant@upi&pn=Raj+Mahal&am=1890.00&cu=INR&tn=Order+ORD-1")
    assert short != long
