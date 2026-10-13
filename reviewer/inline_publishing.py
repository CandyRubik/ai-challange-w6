"""Publish bounded COMMENT batches and recover writes through owned review records."""
import hashlib
import json
import re
import time

from .github import GitHubError, bot_authored, raw_body
from .inline import PREFIX, plan_inline
from .report import normalize_comment

BATCH = '<!-- ai-review:batch:v1:'


def published_reviews(reviews):
    return {r['id'] for r in reviews if bot_authored(r) and r.get('state') == 'COMMENTED'
            and type(r.get('id')) is int
            and re.fullmatch(re.escape(BATCH)+re.escape(str(r.get('commit_id')))+r':[0-9a-f]{64} -->',raw_body(r))
            and re.fullmatch(r'[0-9a-f]{40}',str(r.get('commit_id')))}


def matches(comment, group, head, published):
    return (bot_authored(comment) and not comment.get('in_reply_to_id')
            and comment.get('pull_request_review_id') in published and comment.get('commit_id') == head
            and raw_body(comment).startswith(group['marker'])
            and all(comment.get(key) == value for key,value in group['anchor'].items()))


def update_comment(api, comment, body, guard):
    if raw_body(comment) != body:
        guard()
        api.request('PATCH',f'/pulls/comments/{comment["id"]}',{'body':body})
        guard()


def publish_inline(api, context, result, repository, *, guard):
    guard()
    files = api.items(f'/pulls/{context.pr_number}/files')
    guard()
    groups, fallback = plan_inline(context,result,files,repository)
    comments = api.items(f'/pulls/{context.pr_number}/comments')
    reviews = api.items(f'/pulls/{context.pr_number}/reviews')
    published, missing = published_reviews(reviews), []
    for group in groups:
        existing = next((c for c in comments if matches(c,group,context.head_sha,published)), None)
        if existing:
            update_comment(api,existing,group['body'],guard)
        else:
            missing.append(group)
    if missing:
        batch_id = hashlib.sha256(json.dumps([group['key'] for group in groups]).encode()).hexdigest()
        marker = f'{BATCH}{context.head_sha}:{batch_id} -->'
        already = any(bot_authored(r) and r.get('commit_id') == context.head_sha and raw_body(r) == marker for r in reviews)
        if not already:
            guard()
            try:
                api.request('POST',f'/pulls/{context.pr_number}/reviews',dict(commit_id=context.head_sha,event='COMMENT',
                    body=marker,comments=[dict(**g['anchor'],body=g['body']) for g in missing]))
            except GitHubError:
                pass  # An unknown POST is confirmed below; this run never repeats it blindly.
        for attempt in range(3):
            if attempt:
                time.sleep(0.2 * 2**(attempt-1))
            guard()
            try:
                reviews = api.items(f'/pulls/{context.pr_number}/reviews')
                comments = api.items(f'/pulls/{context.pr_number}/comments')
            except GitHubError:
                continue
            published = published_reviews(reviews)
            batch = {r['id'] for r in reviews if r.get('id') in published and r.get('commit_id') == context.head_sha and raw_body(r) == marker}
            if batch and all(any(matches(c,g,context.head_sha,batch) and (already or raw_body(c) == g['body']) for c in comments) for g in missing):
                if already:
                    for group in missing:
                        comment = next(c for c in comments if matches(c,group,context.head_sha,batch))
                        update_comment(api,comment,group['body'],guard)
                break
        else:
            raise GitHubError('Inline POST could not be confirmed; no blind retry; rerun required')
    guard()
    if result.execution == 'complete' and result.status != 'error':
        active = {group['marker'] for group in groups}
        for comment in comments:
            body = raw_body(comment)
            marker = body.split('\n',1)[0]
            if (not bot_authored(comment) or comment.get('in_reply_to_id') or comment.get('pull_request_review_id') not in published
                or not re.fullmatch(re.escape(PREFIX)+r'[0-9a-f]{40}:[0-9a-f]{64} -->',marker) or marker in active):
                continue
            note = f'<!-- ai-review:superseded:{context.head_sha} -->'
            if note in body:
                continue
            original = body.split('\n\n--- Original report ---\n\n')[-1]
            retired = (marker+'\n'+note+'\nSuperseded by the latest complete review; check its summary for current findings. This does not prove a fix.\n'
                +f'[Current run]({context.run_url})\n\n--- Original report ---\n\n'+original)
            retired = normalize_comment(retired)
            if len(retired.encode()) > 64_000 or not retired.endswith(original):
                fallback.append('Existing inline cannot be marked superseded while preserving its original text')
                continue
            guard()
            api.request('PATCH',f'/pulls/comments/{comment["id"]}',{'body':retired})
            guard()
    return fallback
