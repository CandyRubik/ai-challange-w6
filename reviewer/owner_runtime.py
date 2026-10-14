"""Capture/replay owner authority independently of paid analysis."""
from .actions import Actions
from .owner_journal import OWNER_WORKFLOW,read_journal
from .owner_publishing import record_digest as stamp
from .publishing import StaleReview


def enabled(repository,context):
    return OWNER_WORKFLOW in repository.paths(context.base_sha)


def fresh_request(env):
    value = env.get('REVIEW_FRESH','false')
    if value not in ('true','false') or value=='true' and env['GITHUB_EVENT_NAME']!='workflow_dispatch':
        raise ValueError('Fresh analysis requires an explicit owner manual dispatch')
    return value=='true'


def cache_source(context,records):
    for key,record in sorted(records.items(),key=lambda item:tuple(map(int,item[0].split('-'))),reverse=True):
        d = record.decision
        if record.source and (d.base_sha,d.head_sha,d.profile_sha256)==(context.base_sha,context.head_sha,context.profile_sha256):
            return record.source
    return None


def capture_owners(api,repository,context,env):
    fresh = fresh_request(env)
    if not enabled(repository,context):
        return context
    records = read_journal(Actions(api),repository,context)
    context = context.model_copy(update={'owner_records':records,'owner_sha256':stamp(records)})
    source = None if fresh or context.analysis_source else cache_source(context,records)
    return context.model_copy(update={'analysis_source':source,'analysis_origin':'owner_journal'}) if source else context


def check_owners(repository,context,env):
    fresh = fresh_request(env)
    if enabled(repository,context):
        if context.owner_sha256!=stamp(context.owner_records) or any(key!=record.id for key,record in context.owner_records.items()):
            raise ValueError('Captured owner journal digest/identity mismatch')
    elif context.owner_records or context.owner_sha256 or context.analysis_origin=='owner_journal':
        raise ValueError('Owner authority is unavailable in the trusted base')
    if context.analysis_origin=='owner_journal' and (fresh or cache_source(context,context.owner_records)!=context.analysis_source):
        raise ValueError('Captured owner analysis source differs from its journal')


def assert_owners(api,repository,context):
    if enabled(repository,context) and stamp(read_journal(Actions(api),repository,context))!=context.owner_sha256:
        raise StaleReview('Owner journal changed since capture; rerun required')
