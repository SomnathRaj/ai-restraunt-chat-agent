import pytest

from app.models.db import get_db


@pytest.fixture
def seeded_faq(app):
    with app.app_context():
        db = get_db()
        db.faq.insert_many(
            [
                {
                    "faq_id": "parking",
                    "question": "Do you have parking?",
                    "answer": "Yes, free parking is available right outside.",
                    "category": "facilities",
                    "keywords": ["parking", "car park", "park"],
                    "active": True,
                },
                {
                    "faq_id": "hours",
                    "question": "What are your opening hours?",
                    "answer": "We are open 11am to 11pm daily.",
                    "category": "hours",
                    "keywords": ["hours", "timing", "open"],
                    "active": True,
                },
                {
                    "faq_id": "old-promo",
                    "question": "Tell me about the summer promo?",
                    "answer": "That promo has expired.",
                    "category": "promo",
                    "keywords": ["promo", "summer", "offer"],
                    "active": False,
                },
            ]
        )
    return app


def test_search_faq_returns_answer_verbatim_for_known_question(client, seeded_faq):
    response = client.get("/api/faq/search?q=" + "Do you have parking?")
    assert response.status_code == 200
    data = response.get_json()
    assert data["matched"] is True
    assert data["faq_id"] == "parking"
    assert data["answer"] == "Yes, free parking is available right outside."


def test_search_faq_matches_via_keyword_not_exact_question(client, seeded_faq):
    response = client.get("/api/faq/search?q=" + "Is there a car park nearby?")
    data = response.get_json()
    assert data["matched"] is True
    assert data["faq_id"] == "parking"


def test_search_faq_no_match_is_honest_not_fabricated(client, seeded_faq):
    response = client.get("/api/faq/search?q=" + "Do you deliver to the moon?")
    assert response.status_code == 200
    assert response.get_json() == {"matched": False}


def test_search_faq_ignores_inactive_entries(client, seeded_faq):
    response = client.get("/api/faq/search?q=" + "Tell me about the summer promo")
    assert response.get_json()["matched"] is False


def test_search_faq_requires_a_query(client, seeded_faq):
    response = client.get("/api/faq/search?q=")
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"
