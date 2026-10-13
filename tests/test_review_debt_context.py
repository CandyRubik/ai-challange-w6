import pytest

from reviewer.ci import check_context
from tests.review_debt_fixtures import setup
from tests.test_reviewer import git


@pytest.mark.parametrize('tamper',['hash','registry'])
def test_context_binds_registry_to_trusted_git_base(tmp_path,tamper):
    api,context,artifact,repo,*_ = setup(tmp_path)
    assert context.debt_sha256 == artifact['result']['debt_sha256']
    assert context.debt_registry.entries[0].status == 'active'
    git(repo.root,'checkout','--detach',context.base_sha)
    env = dict(GITHUB_REPOSITORY='owner/repo',GITHUB_RUN_ID='10',GITHUB_RUN_ATTEMPT='1',
        GITHUB_WORKFLOW_SHA=context.base_sha,GITHUB_REF='refs/heads/main')
    check_context(repo.root,context,env)
    updates = {'debt_sha256':'0'*64} if tamper == 'hash' else {
        'debt_registry':context.debt_registry.model_copy(update={'entries':[]})}
    with pytest.raises(ValueError,match='debt registry'):
        check_context(repo.root,context.model_copy(update=updates),env)


def test_invalid_base_confirmation_is_rejected_before_analysis(tmp_path):
    with pytest.raises(ValueError,match='current profile owner'):
        setup(tmp_path,confirmed_by='outsider')
