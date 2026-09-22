"""Build a UPI payment deep link and render it as a QR code, for the
printable invoice. Rendered entirely server-side (qrcode + Pillow, no
network calls) and embedded as a base64 data: URI, so the QR is baked
directly into the HTML and always present at print time.
"""

import base64
from io import BytesIO
from urllib.parse import quote

import qrcode


def build_upi_payment_uri(upi_id: str, payee_name: str, amount, order_id: str) -> str:
    """Standard `upi://pay` deep link -- pa/pn/am/cu/tn are the field names
    every UPI app (GPay, PhonePe, Paytm, ...) recognizes when scanned."""
    params = {
        "pa": upi_id,
        "pn": payee_name,
        "am": f"{float(amount):.2f}",
        "cu": "INR",
        "tn": f"Order {order_id}",
    }
    query = "&".join(f"{key}={quote(str(value))}" for key, value in params.items())
    return f"upi://pay?{query}"


def generate_qr_code_data_uri(data: str) -> str:
    """PNG QR code for `data`, base64-encoded as a data: URI ready for <img src>."""
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
