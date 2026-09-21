"""Flask extensions, instantiated here and initialized against the app in create_app().

Kept separate from __init__.py so blueprint modules can import `limiter`
(for per-route rate limits) without circular imports.
"""

from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["60 per minute"], storage_uri="memory://")
cors = CORS()
