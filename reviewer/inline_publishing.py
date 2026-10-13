"""Publish bounded COMMENT batches and reconcile their declared anchor identities."""
import hashlib
import json
import re
import time

from .github import GitHubError, bot_authored, raw_body
from .inline import PREFIX, LIMIT, plan_inline
from .report import normalize_comment

BATCH = '<!-- ai-review:batch:v2:'


def key_digest(keys):
    return hashlib.sha256(json.dumps(sorted(keys)).encode()).hexdigest()


def owned_batches(reviews):
    batches = {}
    for review in reviews:
        body = raw_body(review)
        if not bot_authored(review) or not body.startswith(BATCH):
            continue
        marker, _, data = body.partition('\n')
        match = re.fullmatch(re.escape(BATCH)+r'([0-9a-f]{40}):([0-9a-f]{64}) -->',marker)
        try:
            if not match or len(body)>2_000:
                raise ValueError('Invalid batch marker')
            record = json.loads(data)
            if not isinstance(record,dict) or set(record) != {'keys','plan_keys'}:
                raise ValueError('Invalid batch metadata')
            for keys in record.values():
                if (not isinstance(keys,list) or not 1<=len(keys)<=LIMIT or len(set(keys))!=len(keys)
                    or any(not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{64}',key) for key in keys)):
                    raise ValueError('Invalid batch keys')
            if (not set(record['keys'])<=set(record['plan_keys']) or key_digest(record['plan_keys'])!=match[2]
                or review.get('commit_id')!=match[1] or type(review.get('id')) is not int or review['id']<1):
                raise ValueError('Batch identity mismatch')
        except (ValueError, TypeError):
            raise GitHubError('Owned inline batch metadata is invalid; no blind retry') from None
        batches[review['id']] = dict(head=match[1],keys=record['keys'],state=review.get('state'),body=body)
    return batches


def group_reviews(batches, group, head, *, published=True):
    return {identity for identity,b in batches.items() if b['head']==head and group['key'] in b['keys']
            and (not published or b['state']=='COMMENTED')}


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


def readback(api, context, guard, attempt):
    if attempt:
        time.sleep(0.2 * 2**(attempt-1))
    guard()
    reviews = api.items(f'/pulls/{context.pr_number}/reviews')
    comments = api.items(f'/pulls/{context.pr_number}/comments')
    return owned_batches(reviews), comments


def reconcile(api, context, groups, batches, comments, guard):
    missing, delayed = [], []
    for group in groups:
        existing = next((c for c in comments if matches(c,group,context.head_sha,group_reviews(batches,group,context.head_sha))),None)
        if existing:
            update_comment(api,existing,group['body'],guard)
        elif (group_reviews(batches,group,context.head_sha,published=False)
              or any(matches(c,group,context.head_sha,{c['pull_request_review_id']}) for c in comments
                     if type(c.get('pull_request_review_id')) is int)):
            delayed.append(group)
        else:
            missing.append(group)
    return missing, delayed


def publish_inline(api, context, result, repository, *, guard):
    guard()
    files = api.items(f'/pulls/{context.pr_number}/files')
    guard()
    groups, fallback = plan_inline(context,result,files,repository)
    comments = api.items(f'/pulls/{context.pr_number}/comments')
    batches = owned_batches(api.items(f'/pulls/{context.pr_number}/reviews'))
    missing, delayed = reconcile(api,context,groups,batches,comments,guard)
    if delayed:
        for attempt in range(3):
            try:
                batches, comments = readback(api,context,guard,attempt)
            except GitHubError:
                continue
            missing, delayed = reconcile(api,context,groups,batches,comments,guard)
            if not delayed:
                break
        else:
            raise GitHubError('Prior inline batch could not be confirmed or is pending; no duplicate POST; rerun required')
    if missing:
        plan_keys = sorted(group['key'] for group in groups)
        marker = f'{BATCH}{context.head_sha}:{key_digest(plan_keys)} -->'
        body = marker+'\n'+json.dumps(dict(keys=sorted(g['key'] for g in missing),plan_keys=plan_keys))
        guard()
        try:
            api.request('POST',f'/pulls/{context.pr_number}/reviews',dict(commit_id=context.head_sha,event='COMMENT',
                body=body,comments=[dict(**g['anchor'],body=g['body']) for g in missing]))
        except GitHubError:
            pass  # Confirm this write below; never repeat the POST in this run.
        for attempt in range(3):
            try:
                batches, comments = readback(api,context,guard,attempt)
            except GitHubError:
                continue
            batch = {identity for identity,b in batches.items() if b['head']==context.head_sha and b['state']=='COMMENTED' and b['body']==body}
            if batch and all(any(matches(c,g,context.head_sha,batch) and raw_body(c)==g['body'] for c in comments) for g in missing):
                break
        else:
            raise GitHubError('Inline POST could not be confirmed; no blind retry; rerun required')
    guard()
    if result.execution == 'complete' and result.status != 'error':
        active = {group['marker'] for group in groups}
        for comment in comments:
            body = raw_body(comment)
            marker = body.split('\n',1)[0]
            owned = batches.get(comment.get('pull_request_review_id'))
            match = re.fullmatch(re.escape(PREFIX)+r'([0-9a-f]{40}):([0-9a-f]{64}) -->',marker)
            if (not bot_authored(comment) or comment.get('in_reply_to_id') or not owned or owned['state']!='COMMENTED'
                or not match or match[1]!=owned['head'] or match[2] not in owned['keys'] or marker in active):
                continue
            note = f'<!-- ai-review:superseded:{context.head_sha} -->'
            if note in body:
                continue
            original = body.split('\n\n--- Original report ---\n\n')[-1]
            retired = normalize_comment(marker+'\n'+note+'\nSuperseded by the latest complete review; check its summary for current findings. This does not prove a fix.\n'
                +f'[Current run]({context.run_url})\n\n--- Original report ---\n\n'+original)
            if len(retired.encode())>64_000 or not retired.endswith(original):
                fallback.append('Existing inline cannot be marked superseded while preserving its original text')
                continue
            update_comment(api,comment,retired,guard)
    return fallback
