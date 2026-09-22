"""Flask extensions, instantiated here and initialized against the app in create_app().

Kept separate from __init__.py so blueprint modules can import `limiter`
(for per-route rate limits) without circular imports.
"""

from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect

limiter = Limiter(key_func=get_remote_address, default_limits=["60 per minute"], storage_uri="memory://")
cors = CORS()
# Protects server-rendered admin forms (ARCHITECTURE.md Section 12). The
# customer-facing JSON blueprints are explicitly exempted in create_app() --
# CSRFProtect enforces on every POST/PUT/PATCH/DELETE by default, which would
# otherwise break /api/chat, /api/cart, etc. (they're called via fetch() with
# a JSON body, never a csrf_token field).
csrf = CSRFProtect()
