"""Reuse a complete same-snapshot source without another model call."""
import re

from .actions import Actions
from .decision_records import AnalysisSource
from .owner_journal import read_review
from .debt_publishing import verify_debt


def source_inputs(env):
    value = env.get('SOURCE_REVIEW_RUN','')
    if not value:
        return None
    attempt = env.get('SOURCE_REVIEW_ATTEMPT','1')
    if (env.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or not re.fullmatch(r'[1-9]\d{0,19}',value)
        or not re.fullmatch(r'[1-9]\d?',attempt) or int(attempt)>20):
        raise ValueError('Manual source run/attempt is invalid')
    return int(value),int(attempt)


def capture_source(api,repository,context,env):
    selected = source_inputs(env)
    if selected is None:
        return context
    artifact = read_review(Actions(api),repository,*selected,context.pr_number,context=context)
    source = AnalysisSource(run_id=selected[0],run_attempt=selected[1],result_sha256=artifact.result_sha256)
    return context.model_copy(update={'analysis_source':source,'analysis_origin':'manual'})


def check_source(context,env):
    selected = source_inputs(env)
    if context.analysis_origin=='owner_journal' and selected is None:
        return  # The authenticated captured journal is checked by owner_runtime.
    actual = (context.analysis_source.run_id,context.analysis_source.run_attempt) if context.analysis_source else None
    if selected!=actual or context.analysis_origin!=('manual' if selected else None):
        raise ValueError('Captured analysis source differs from the manual request')


def reuse_result(api,repository,context):
    source = context.analysis_source
    artifact = read_review(Actions(api),repository,source.run_id,source.run_attempt,context.pr_number,context=context)
    if artifact.result_sha256!=source.result_sha256:
        raise ValueError('Original analysis result digest changed')
    verify_debt(repository,context,artifact.result)
    prior = artifact.result.trace
    reference = source.model_dump()
    trace = dict(model=prior.get('model',''),model_calls=0,api_calls=0,wire_inputs=[],token_usage=[],
        source_analysis=reference,original_analysis_source=prior.get('original_analysis_source',reference),
        original_analysis=prior.get('original_analysis',prior))
    return artifact.result.model_copy(update={'trace':trace})
