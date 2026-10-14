"""Apply authenticated owner effects while preserving the original review."""
from datetime import datetime,timezone

from .actions import Actions
from .artifacts import preview
from .contracts import digest
from .debt_publishing import verify_debt
from .models import ReviewResult
from .owner_journal import read_journal
from .owner_report import OWNER_MARKER,debt_note,finding_note,journal,render
from .publishing import (StaleReview,_publish,assert_current,invalidate_own_status,publish,upsert_comment)


def record_digest(records):
    return digest({key:value.model_dump() for key,value in records.items()})


def publish_owned(api,context,raw,repository,*,expected=None,clock=None):
    """Read Actions authority directly; supplied/captured records never grant it."""
    preview(context,raw)
    result = ReviewResult.model_validate(raw['result'])
    verify_debt(repository,context,result)
    clock = clock or (lambda:datetime.now(timezone.utc))
    try:
        records = read_journal(Actions(api),repository,context,now=clock())
        stamp = record_digest(records)
        if expected is not None and stamp!=expected:
            raise StaleReview('Owner journal changed since capture; rerun required')
        if not records:
            return publish(api,context,raw,repository=repository)
        decisions = {key:value.decision for key,value in records.items()}
        rendered,plan = render(context,raw,decisions,now=clock())
        def guard():
            assert_current(api,context)
            current = read_journal(Actions(api),repository,context,now=clock())
            if record_digest(current)!=stamp or render(context,raw,decisions,now=clock())[1]!=plan:
                raise StaleReview('Owner journal or exception applicability changed; rerun required')
        guard()
        upsert_comment(api,context.pr_number,journal(context,decisions,plan),marker=OWNER_MARKER,before_write=guard)
        notes = {item.debt_id:debt_note(item.debt_id,plan,decisions) for item in result.project_debt}
        notes = {key:note for key,note in notes.items() if note}
        return _publish(api,context,rendered,raw,repository,owner_guard=guard,
            finding_note=lambda findings:finding_note(findings,plan,decisions),debt_notes=notes)
    except (StaleReview,RuntimeError):
        try:
            invalidate_own_status(api,context)
        except RuntimeError:
            pass
        raise
