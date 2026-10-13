"""Bounded diagnostics with no model values or arbitrary field names."""
from pydantic import ValidationError


def response_failure(error):
    if isinstance(error, ValidationError):
        known_types = {'json_invalid','missing','extra_forbidden','literal_error','string_type','int_type',
                       'string_too_short','string_pattern_mismatch','greater_than_equal','too_long','too_short',
                       'value_error','model_type','model_attributes_type','list_type'}
        known_fields = {'assessments','decisions','recommendations','path','rule_id','verdict','reason','evidence',
                        'finding','findings','criteria','severity','summary','scenario','impact','recommendation',
                        'fragment_id','start_line','end_line','line','quote','index','counterargument'}
        errors = error.errors(include_url=False, include_context=False, include_input=False)[:10]
        return {'code':'schema', 'types':sorted({e['type'] if e['type'] in known_types else 'other' for e in errors}),
                'fields':sorted({part if part in known_fields else 'other' for e in errors for part in e['loc'] if isinstance(part,str)})}
    if isinstance(error, (KeyError, IndexError)):
        return {'code':'unknown_reference'}
    codes = {'Evidence range exceeds supplied immutable source':'evidence_range',
             'Blank lines are not evidence':'blank_evidence', 'Metadata has no code lines':'metadata_range',
             'Decision identity contradicts candidate':'decision_identity', 'No completion choices':'api_envelope',
             'Missing completion content':'api_envelope', 'Incomplete response':'incomplete_completion',
             'Model response exceeds budget':'response_budget'}
    return {'code':codes.get(str(error),'invalid_structure') if isinstance(error,ValueError) else 'invalid_structure'}
