import main


def test_index_response_declares_utf8_html():
    response = main.serve_index()

    assert response.media_type == "text/html; charset=utf-8"
