"""Model-facing records avoid duplicated verdicts and copied source text."""
import json
from typing import Literal

from pydantic import Field, model_validator

from .models import Record, Text

REVIEW_PROTOCOL = """Use the output schema exactly. One assessment per requested path/rule.
Put a finding inside its violating assessment; pass/inconclusive must have finding=null.
BUGS covers additional concrete bugs, not duplicates of policy violations. A pass means no
supported introduced defect was found, not a mathematical proof that arbitrary dependencies are safe.
Compare BEFORE with AFTER in that direction. HEAD is the proposed code; never describe BEFORE code as HEAD.
Reason under the supplied contract assumptions; use inconclusive only for context actually needed
to decide the change. Clean equivalent refactors may pass without proving the entire program.
"""

LINE_PROTOCOL = """Cite the displayed fragment_id and inclusive absolute start_line/end_line.
Source text has N| display prefixes; cite only displayed numbers within line..end_line. Select whole causal lines, including
removed guards from before when appropriate. For file_metadata use start_line=end_line=0.
Never copy quotes or use a profile JSON as runtime proof. For verification of a modified file,
include current head code; before-only proof is sufficient only for a deleted file."""


class LineReference(Record):
    fragment_id: Text
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)


class Issue(Record):
    severity: Literal['critical', 'major', 'minor']
    summary: Text
    scenario: Text
    impact: Text
    recommendation: Text


class Assessment(Record):
    path: Text
    rule_id: Text
    verdict: Literal['pass', 'violation', 'inconclusive']
    reason: Text
    evidence: list[LineReference] = Field(min_length=1, max_length=4)
    finding: Issue | None

    @model_validator(mode='after')
    def consistent(self):
        if (self.verdict == 'violation') != (self.finding is not None):
            raise ValueError('Only violations have findings')
        return self


class ReviewResponse(Record):
    assessments: list[Assessment] = Field(max_length=100)
    recommendations: list[Text] = Field(max_length=5)


class VerifiedDecision(Record):
    rule_id: Text | None = None
    path: Text | None = None
    index: int = Field(ge=0)
    verdict: Literal['confirmed', 'rejected', 'inconclusive']
    reason: Text
    counterargument: Text
    evidence: list[LineReference] = Field(min_length=1, max_length=4)


class VerificationResponse(Record):
    decisions: list[VerifiedDecision] = Field(max_length=10)


def protocol(schema):
    return (REVIEW_PROTOCOL if schema["title"] == "DraftReview" else "One decision per candidate index; explain the strongest counterargument.") + "\n" + LINE_PROTOCOL


def response_type(schema):
    return {'DraftReview': ReviewResponse, 'Verification': VerificationResponse}[schema['title']]


def display_payload(payload):
    data = json.loads(json.dumps(payload))
    ordered = sorted(data['fragments'], key=lambda f: {'normative':0, 'before':1, 'head':2}[f['role']])
    unique = {f['id']: f for f in ordered}
    source_lines = {(f['role'], f['path'], f['line'] + i): line for f in ordered if f['kind'] == 'text'
                    for i, line in enumerate(f['text'].splitlines(keepends=True))}
    excerpt_bytes = 0
    for path, changes in data.get('causal_changes', {}).items():
        for change in changes:
            excerpts = {}
            for side in ('before', 'head'):
                source_path = change.get('before_path', path) if side == 'before' else path
                start, stop = change[side]
                excerpts[side + '_code'] = ''.join(f'{line}| {source_lines.get((side, source_path, line), "[not supplied]")}'
                                                   for line in range(start, stop + 1))
            size = sum(len(value.encode()) for value in excerpts.values())
            if size <= 4_000 and excerpt_bytes + size <= 6_000:
                change.update(excerpts)
                excerpt_bytes += size
            else:
                change['code_excerpt_omitted'] = True
    aliases = {identity: f'F{i + 1}' for i, identity in enumerate(unique)}
    data['fragments'] = list(unique.values())
    for fragment in data['fragments']:
        fragment['id'] = aliases[fragment['id']]
        fragment['revision'] = {'normative':'TRUSTED BASE POLICY', 'before':'BEFORE PR, OLD CODE', 'head':'AFTER PR, PROPOSED CODE TO REVIEW'}[fragment['role']]
        if fragment['kind'] == 'text':
            lines = fragment['text'].splitlines(keepends=True)
            fragment['end_line'] = fragment['line'] + len(lines) - 1
            fragment['text'] = ''.join(f"{fragment['line'] + i}| {line}" for i, line in enumerate(lines))
        else:
            fragment['end_line'] = 0
    def translate(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'fragment_id':
                    value[key] = aliases[item]
                else:
                    translate(item)
        elif isinstance(value, list):
            for item in value:
                translate(item)
    translate(data)
    return data, {alias: identity for identity, alias in aliases.items()}


def ground_response(raw, schema, payload, aliases=None, resolutions=None):
    response = response_type(schema).model_validate_json(raw)
    fragments = {f['id']: f for f in payload['fragments']}
    def references(items):
        refs = []
        for item in items:
            identity = aliases[item.fragment_id] if aliases is not None else item.fragment_id
            fragment = fragments[identity]
            if fragment['kind'] == 'file_metadata':
                if (item.start_line, item.end_line) != (0, 0):
                    raise ValueError('Metadata has no code lines')
                refs.append({'fragment_id': identity, 'quote': fragment['text']})
            else:
                lines = fragment['text'].splitlines(keepends=True)
                start, stop = item.start_line - fragment['line'], item.end_line - fragment['line'] + 1
                if not 0 <= start < stop <= len(lines):
                    covering = [f for f in fragments.values() if all(f[key] == fragment[key] for key in ('path', 'sha', 'role', 'kind'))
                        and f['line'] <= item.start_line <= item.end_line < f['line'] + len(f['text'].splitlines())]
                    if len(covering) != 1:
                        raise ValueError('Evidence range exceeds supplied immutable source')
                    fragment, identity = covering[0], covering[0]['id']
                    if resolutions is not None:
                        resolutions.append({'requested': item.fragment_id, 'resolved': identity, 'path': fragment['path'],
                                            'sha': fragment['sha'], 'start_line': item.start_line, 'end_line': item.end_line})
                refs.append({'fragment_id': identity, 'line': item.start_line, 'end_line': item.end_line})
        return refs
    if isinstance(response, VerificationResponse):
        candidates = {c['index']: c for c in payload['candidates']}
        for decision in response.decisions:
            candidate = candidates[decision.index]
            if any(getattr(decision, key) is not None and getattr(decision, key) != candidate[key] for key in ('path', 'rule_id')):
                raise ValueError('Decision identity contradicts candidate')
        return json.dumps({'decisions': [{**d.model_dump(exclude={'evidence', 'path', 'rule_id'}), 'evidence': references(d.evidence)}
                                         for d in response.decisions]})
    criteria, findings = [], []
    for item in response.assessments:
        refs = references(item.evidence)
        criteria.append({**item.model_dump(exclude={'evidence', 'finding'}), 'evidence': refs})
        if item.finding:
            findings.append({'path': item.path, 'rule_id': item.rule_id, **item.finding.model_dump(), 'evidence': refs})
    return json.dumps({'criteria': criteria, 'findings': findings, 'recommendations': response.recommendations})
