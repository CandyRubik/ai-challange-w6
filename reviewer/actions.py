"""Bounded Actions metadata/artifacts. Signed downloads never receive API auth."""
import hashlib
import io
import json
import re
import zipfile

import httpx

from .github import GitHubError


def positive(value):
    if type(value) is not int or value < 1:
        raise GitHubError('Actions identifier is invalid')
    return value


class Actions:
    def __init__(self,api,*,blob_transport=None):
        self.api,self.blob_transport = api,blob_transport

    def collection(self,path,key,*,params=None):
        rows,total = [],None
        for page in range(1,11):
            data = self.api.request('GET',path,params={**(params or {}),'page':page,'per_page':100})
            if not isinstance(data,dict):
                raise GitHubError('Actions collection must be an object')
            count,items = data.get('total_count'),data.get(key)
            if (type(count) is not int or not 0<=count<1000 or not isinstance(items,list)
                or len(items)>100 or any(not isinstance(item,dict) for item in items)):
                raise GitHubError('Actions collection is invalid or exceeds the complete-result budget')
            if total is not None and total!=count:
                raise GitHubError('Actions collection changed during pagination')
            total = count
            rows.extend(items)
            if len(rows)==total:
                return rows
            if len(rows)>total or len(items)<100:
                raise GitHubError('Actions collection is incomplete')
        raise GitHubError('Actions pagination budget exceeded')

    def run_attempt(self,run_id,attempt):
        return self.api.request('GET',f'/actions/runs/{positive(run_id)}/attempts/{positive(attempt)}')

    def successful_job(self,run_id,attempt,name):
        jobs = self.collection(f'/actions/runs/{positive(run_id)}/attempts/{positive(attempt)}/jobs','jobs')
        matches = [job for job in jobs if job.get('name')==name]
        if len(matches)>1:
            raise GitHubError('Actions job proof is ambiguous')
        return bool(matches and matches[0].get('status')=='completed' and matches[0].get('conclusion')=='success')

    def artifact_json(self,run,attempt,name,member,*,member_limit=512_000):
        positive(attempt)
        if type(member_limit) is not int or not 0<member_limit<=2_000_000:
            raise GitHubError('Actions member budget is invalid')
        run_id = positive(run['id'])
        artifacts = self.collection(f'/actions/runs/{run_id}/artifacts','artifacts')
        matches = [a for a in artifacts if a.get('name')==name]
        if len(matches)!=1:
            raise GitHubError('Actions artifact is missing or ambiguous')
        artifact = matches[0]
        origin = artifact.get('workflow_run') or {}
        if (artifact.get('expired') is not False or origin.get('id')!=run_id
            or origin.get('repository_id')!=run['repository']['id'] or origin.get('head_sha')!=run['head_sha']
            or type(artifact.get('size_in_bytes')) is not int or not 0<artifact['size_in_bytes']<=2_000_000
            or not re.fullmatch(r'sha256:[0-9a-f]{64}',artifact.get('digest') or '')):
            raise GitHubError('Actions artifact metadata does not prove its origin or bounds')
        archive = self._archive(positive(artifact['id']))
        if 'sha256:'+hashlib.sha256(archive).hexdigest()!=artifact['digest']:
            raise GitHubError('Actions artifact digest mismatch')
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                entries = zipped.infolist()
                if len(entries)!=1 or entries[0].filename!=member or entries[0].file_size>member_limit:
                    raise GitHubError('Actions archive must contain one bounded expected JSON member')
                with zipped.open(entries[0]) as stream:
                    raw = stream.read(member_limit+1)
                if len(raw)>member_limit:
                    raise GitHubError('Actions JSON member exceeds size limit')
                data = json.loads(raw)
                if not isinstance(data,dict):
                    raise GitHubError('Actions JSON member must be an object')
                return data
        except (zipfile.BadZipFile,RuntimeError,ValueError,OSError,KeyError):
            raise GitHubError('Actions archive or JSON member is invalid') from None

    def _archive(self,artifact_id):
        try:
            with self.api.client.stream('GET',f'/repos/{self.api.repository}/actions/artifacts/{artifact_id}/zip') as response:
                if response.status_code!=302:
                    raise GitHubError('Actions archive did not provide a signed download')
                url = httpx.URL(response.headers.get('Location',''))
                if url.scheme!='https' or not url.host or url.port not in (None,443) or url.username or url.password or url.fragment:
                    raise GitHubError('Actions download URL is invalid')
            with httpx.Client(timeout=20,follow_redirects=False,transport=self.blob_transport) as client:
                with client.stream('GET',url) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65_536):
                        content.extend(chunk)
                        if len(content)>2_000_000:
                            raise GitHubError('Actions archive exceeds size limit')
                    return bytes(content)
        except (httpx.HTTPError,httpx.InvalidURL,ValueError):
            raise GitHubError('Actions archive download failed') from None
