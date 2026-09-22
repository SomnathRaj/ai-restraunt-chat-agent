"""Shared page-number pagination math for admin list views (menu/FAQ/orders/
chat sessions) -- every list uses the same {page, page_size, total_count,
total_pages, skip} shape, so this is computed in one place.
"""

DEFAULT_PAGE_SIZE = 20


def paginate(total_count: int, page: int, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    """Clamp page/page_size to sane values and return pagination metadata,
    including the Mongo `skip` value for this page.

    Never raises on a bad page number (e.g. page=0, page=9999, or a
    negative page_size) -- clamps into range instead, so a hand-edited or
    stale URL never 500s the admin list view.
    """
    page_size = page_size if page_size and page_size > 0 else DEFAULT_PAGE_SIZE
    total_pages = max(1, -(-total_count // page_size))  # ceil division
    page = max(1, min(page or 1, total_pages))
    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "skip": (page - 1) * page_size,
    }
