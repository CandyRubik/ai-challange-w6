import hashlib
import io
import json
import zipfile

import httpx
import pytest

from reviewer.actions import Actions
from reviewer.github import GitHub, GitHubError


def archive(files=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as zipped:
        for name,value in (files or {'decision.json':'{"approved":true}'}).items():
            zipped.writestr(name,value)
    return buffer.getvalue()


def client(*,zip_bytes=None,location='https://signed.example.test/object?token=PRIVATE_SIGNATURE',metadata=None,blob_status=200):
    content = zip_bytes if zip_bytes is not None else archive()
    run = dict(id=10,head_sha='a'*40,repository=dict(id=1))
    artifact = dict(id=20,name='decision-10-1',expired=False,size_in_bytes=len(content),
        digest='sha256:'+hashlib.sha256(content).hexdigest(),workflow_run=dict(id=10,repository_id=1,head_sha='a'*40))
    artifact.update(metadata or {})
    requests = []
    def api(request):
        requests.append(request)
        assert request.url.host=='api.github.com' and request.headers['Authorization']=='Bearer PRIVATE_GITHUB_TOKEN'
        if request.url.path.endswith('/artifacts'):
            return httpx.Response(200,json=dict(total_count=1,artifacts=[artifact]))
        assert request.url.path.endswith('/artifacts/20/zip')
        return httpx.Response(302,headers={'Location':location})
    def blob(request):
        requests.append(request)
        assert 'Authorization' not in request.headers and 'Cookie' not in request.headers
        return httpx.Response(blob_status,content=content,headers={'Location':'https://other.example.test/'})
    github = GitHub('owner/repo','PRIVATE_GITHUB_TOKEN',transport=httpx.MockTransport(api))
    return Actions(github,blob_transport=httpx.MockTransport(blob)),run,requests


def test_signed_archive_is_bounded_verified_and_never_gets_api_auth():
    actions,run,requests = client()
    assert actions.artifact_json(run,1,'decision-10-1','decision.json') == {'approved':True}
    assert len(requests)==3 and requests[-1].url.host=='signed.example.test'


@pytest.mark.parametrize('files',[
    {'../decision.json':'{}'}, {'decision.json':'{}','extra.json':'{}'},
    {'decision.json':'[]'}, {'decision.json':'invalid PRIVATE_CONTENT'},
    {'decision.json':json.dumps({'value':'x'*512_001})},
])
def test_unexpected_unbounded_or_invalid_archive_is_rejected(files):
    actions,run,_ = client(zip_bytes=archive(files))
    with pytest.raises(GitHubError) as error: actions.artifact_json(run,1,'decision-10-1','decision.json')
    assert 'PRIVATE_CONTENT' not in str(error.value)


def test_source_analysis_member_can_use_existing_two_megabyte_contract():
    content = json.dumps({'value':'x'*600_000})
    actions,run,_ = client(zip_bytes=archive({'result.json':content}))
    assert actions.artifact_json(run,1,'decision-10-1','result.json',member_limit=2_000_000)['value']=='x'*600_000


@pytest.mark.parametrize('metadata',[
    {'expired':True}, {'size_in_bytes':2_000_001}, {'digest':'sha256:'+'0'*64},
    {'workflow_run':dict(id=11,repository_id=1,head_sha='a'*40)},
    {'workflow_run':dict(id=10,repository_id=2,head_sha='a'*40)},
    {'workflow_run':dict(id=10,repository_id=1,head_sha='b'*40)},
])
def test_artifact_origin_and_digest_are_required(metadata):
    actions,run,_ = client(metadata=metadata)
    with pytest.raises(GitHubError): actions.artifact_json(run,1,'decision-10-1','decision.json')


@pytest.mark.parametrize('location,status',[
    ('http://signed.example.test/',200), ('https://user:password@signed.example.test/',200),
    ('https://signed.example.test:444/',200), ('https://signed.example.test/',302),
    ('https://signed.example.test/?token=PRIVATE_SIGNATURE',403),
])
def test_unsafe_redirect_or_download_error_is_sanitized(location,status):
    actions,run,requests = client(location=location,blob_status=status)
    with pytest.raises(GitHubError) as error: actions.artifact_json(run,1,'decision-10-1','decision.json')
    assert 'PRIVATE_SIGNATURE' not in str(error.value) and 'PRIVATE_GITHUB_TOKEN' not in str(error.value)
    assert len(requests)<=3


def test_streaming_archive_budget_rejects_large_blob():
    actions,run,requests = client(zip_bytes=b'x'*2_000_001,metadata={'size_in_bytes':1})
    with pytest.raises(GitHubError): actions.artifact_json(run,1,'decision-10-1','decision.json')


@pytest.mark.parametrize('responses',[
    [[]], [dict(total_count=True,jobs=[])], [dict(total_count=1000,jobs=[])],
    [dict(total_count=2,jobs=[{}])],
    [dict(total_count=101,jobs=[{}]*100),dict(total_count=102,jobs=[{}]*2)],
])
def test_collection_rejects_wrong_shape_truncation_overflow_or_racing_count(responses):
    calls = []
    def request(req):
        calls.append(req)
        return httpx.Response(200,json=responses[len(calls)-1])
    api = GitHub('owner/repo','test',transport=httpx.MockTransport(request))
    with pytest.raises(GitHubError): Actions(api).collection('/actions/runs/10/jobs','jobs')


def test_successful_job_proof_uses_complete_specific_attempt():
    calls = []
    def request(req):
        calls.append(req)
        page = int(req.url.params['page'])
        jobs = [dict(name='other')]*100 if page==1 else [dict(name='Authorize',status='completed',conclusion='success')]
        return httpx.Response(200,json=dict(total_count=101,jobs=jobs))
    api = GitHub('owner/repo','test',transport=httpx.MockTransport(request))
    assert Actions(api).successful_job(10,2,'Authorize')
    assert len(calls)==2 and all('/attempts/2/jobs' in str(r.url) for r in calls)


@pytest.mark.parametrize('jobs',[
    [], [dict(name='Authorize',status='completed',conclusion='failure')],
    [dict(name='Authorize',status='in_progress',conclusion=None)],
])
def test_missing_failed_or_pending_job_is_not_authorization(jobs):
    api = GitHub('owner/repo','test',transport=httpx.MockTransport(
        lambda req:httpx.Response(200,json=dict(total_count=len(jobs),jobs=jobs))))
    assert not Actions(api).successful_job(10,1,'Authorize')


def test_duplicate_job_or_artifact_proof_is_rejected():
    job = dict(name='Authorize',status='completed',conclusion='success')
    api = GitHub('owner/repo','test',transport=httpx.MockTransport(
        lambda req:httpx.Response(200,json=dict(total_count=2,jobs=[job,job]))))
    with pytest.raises(GitHubError): Actions(api).successful_job(10,1,'Authorize')
    actions,run,_ = client()
    original = actions.api.request
    def duplicate(method,path,body=None,**kwargs):
        data = original(method,path,body,**kwargs)
        return dict(total_count=2,artifacts=data['artifacts']*2)
    actions.api.request = duplicate
    with pytest.raises(GitHubError): actions.artifact_json(run,1,'decision-10-1','decision.json')
