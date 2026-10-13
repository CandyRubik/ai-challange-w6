from reviewer.report import normalize_comment, safe_comment


def test_normalization_is_idempotent_and_keeps_text_without_mentions_or_control_bytes():
    body='@team\x00\t\nПример <source>'
    normalized=normalize_comment(body)
    assert normalized=='@\u200bteam\t\nПример <source>'
    assert normalize_comment(normalized)==normalized


def test_presentation_limit_preserves_utf8_and_reports_shortening():
    body=safe_comment('Пример @team\n'*10_000)
    assert len(body.encode())<58_128 and '@team' not in body
    assert body.endswith('full result is in the run artifact.')
