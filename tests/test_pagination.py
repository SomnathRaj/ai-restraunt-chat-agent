from app.utils.pagination import paginate


def test_paginate_basic_math():
    meta = paginate(total_count=45, page=2, page_size=20)
    assert meta == {"page": 2, "page_size": 20, "total_count": 45, "total_pages": 3, "skip": 20}


def test_paginate_defaults_page_size_when_missing_or_invalid():
    assert paginate(total_count=10, page=1, page_size=0)["page_size"] == 20
    assert paginate(total_count=10, page=1, page_size=-5)["page_size"] == 20


def test_paginate_clamps_page_above_total_pages():
    meta = paginate(total_count=10, page=999, page_size=20)
    assert meta["page"] == 1
    assert meta["total_pages"] == 1


def test_paginate_clamps_page_below_one():
    meta = paginate(total_count=10, page=0, page_size=20)
    assert meta["page"] == 1
    meta = paginate(total_count=10, page=-3, page_size=20)
    assert meta["page"] == 1


def test_paginate_zero_total_count_still_reports_one_page():
    meta = paginate(total_count=0, page=1, page_size=20)
    assert meta["total_pages"] == 1
    assert meta["page"] == 1
    assert meta["skip"] == 0


def test_paginate_skip_calculation():
    assert paginate(total_count=100, page=1, page_size=20)["skip"] == 0
    assert paginate(total_count=100, page=3, page_size=20)["skip"] == 40
