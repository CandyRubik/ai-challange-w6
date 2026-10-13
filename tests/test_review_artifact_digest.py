from reviewer.publishing import digest, preview
from tests.test_review_publishing import fixture


def test_old_result_digest_survives_new_optional_model_fields(tmp_path):
    api,context,artifact,*_ = fixture(tmp_path)
    del artifact['result']['debt_sha256']; del artifact['result']['project_debt']
    artifact['result_sha256'] = digest(artifact['result'])
    assert preview(context,artifact)['state'] == 'failure'
