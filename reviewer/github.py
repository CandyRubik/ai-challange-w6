"""Fixed-origin GitHub transport. Failed writes are never retried implicitly."""
import re

import httpx


class GitHubError(RuntimeError):
    pass


class UncertainCommentPost(GitHubError):
    """A comment may already exist; do not blindly repeat its POST."""


class UncertainSummaryPost(UncertainCommentPost):
    """A summary may already exist; failure finalization must not create another."""


def bot_authored(record):
    user = record.get('user') or {}
    return isinstance(user,dict) and user.get('login') == 'github-actions[bot]' and user.get('type') == 'Bot'


def raw_body(record):
    body = record.get('body')
    return body if isinstance(body,str) else ''


class GitHub:
    def __init__(self, repository, token, *, transport=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not token:
            raise ValueError('GitHub repository and token are required')
        self.repository = repository
        self.client = httpx.Client(base_url='https://api.github.com',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'}, timeout=20, follow_redirects=False, transport=transport)

    def request(self, method, path, body=None, *, params=None):
        if path and (not path.startswith('/') or path.startswith('//') or '..' in path.split('/')):
            raise ValueError('GitHub path must stay within the configured repository')
        try:
            response = self.client.request(method, f'/repos/{self.repository}{path}', json=body, params=params)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise GitHubError('GitHub response exceeds size limit')
            return response.json()
        except httpx.HTTPStatusError as error:
            raise GitHubError(f'GitHub {method} failed: HTTP {error.response.status_code}') from None
        except (httpx.RequestError, ValueError):
            raise GitHubError(f'GitHub {method} failed or returned invalid JSON') from None

    def items(self, path, *, params=None):
        result = []
        for page in range(1, 21):
            data = self.request('GET', path, params={**(params or {}), 'per_page': 100, 'page': page})
            if not isinstance(data, list) or any(not isinstance(item,dict) for item in data):
                raise GitHubError('GitHub collection is invalid')
            result.extend(data)
            if len(data) < 100:
                return result
        raise GitHubError('GitHub pagination limit reached; collection is incomplete')

    def pull(self, number):
        if type(number) is not int or number < 1:
            raise ValueError('PR number must be a positive integer')
        return self.request('GET', f'/pulls/{number}')

    def open_pulls(self):
        return self.items('/pulls', params={'state': 'open', 'base': 'main'})
