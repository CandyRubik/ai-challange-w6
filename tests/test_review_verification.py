import json

import pytest

from reviewer import review
from tests.test_review_semantic import Model, semantic_repository
from tests.test_reviewer import commit


class Verifier(Model):
    def __init__(self, verdict="rejected", mistake=None):
        super().__init__()
        self.verdict, self.mistake = verdict, mistake

    def generate_json(self, *, messages, **kwargs):
        payload = json.loads(messages[-1]["content"])
        if "candidates" not in payload:
            return super().generate_json(messages=messages, **kwargs)
        self.calls += 1
        source = next(f for f in payload["fragments"] if f["role"] == "head" and "return None" in f["text"])
        decisions = [{"index": c["index"], "verdict": self.verdict, "reason": "Explicit fixture decision",
                      "counterargument": "Consider the caller and supplied contract",
                      "evidence": [{"fragment_id": source["id"], "quote": "return None"}]} for c in payload["candidates"]]
        if self.mistake == "missing":
            decisions = []
        elif self.mistake == "quote":
            decisions[0]["evidence"][0]["quote"] = "invented"
        return json.dumps({"decisions": decisions})


@pytest.mark.parametrize("verdict,status,execution,count", [
    ("rejected", "success", "complete", 0), ("confirmed", "failure", "complete", 1),
    ("inconclusive", "error", "partial", 0),
])
def test_blocking_semantic_findings_need_a_grounded_second_opinion(tmp_path, verdict, status, execution, count):
    root, base = semantic_repository(tmp_path)
    head = commit(root, {"app/state/service.py": "def apply(state):\n    return None\n"})
    model = Verifier(verdict)
    result = review(root, base, head, model=model)
    assert (result.status, result.execution, len(result.findings)) == (status, execution, count)
    assert model.calls == 2
    assert result.trace["verification"][0]["verdict"] == verdict


@pytest.mark.parametrize("mistake", ["missing", "quote"])
def test_invalid_verification_cannot_publish_an_unverified_blocker(tmp_path, mistake):
    root, base = semantic_repository(tmp_path)
    head = commit(root, {"app/state/service.py": "def apply(state):\n    return None\n"})
    result = review(root, base, head, model=Verifier(mistake=mistake))
    assert result.status == "error"
    assert result.findings == []
    assert result.trace["model_calls"] == 2


def test_old_before_code_cannot_refute_a_modified_head(tmp_path):
    class OldProof(Verifier):
        def generate_json(self, *, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            raw = super().generate_json(messages=messages, **kwargs)
            if 'candidates' not in payload:
                draft = json.loads(raw)
                old = next(f for f in payload['fragments'] if f['role'] == 'before' and f['kind'] == 'text')
                draft['findings'][0]['evidence'] = [{'fragment_id': old['id'], 'quote': 'return state'}]
                return json.dumps(draft)
            decision = json.loads(raw)
            old = next(f for f in payload['fragments'] if f['role'] == 'before' and f['kind'] == 'text')
            decision['decisions'][0]['evidence'] = [{'fragment_id': old['id'], 'quote': 'return state'}]
            return json.dumps(decision)
    root, base = semantic_repository(tmp_path)
    head = commit(root, {'app/state/service.py': 'def apply(state):\n    return None\n'})
    result = review(root, base, head, model=OldProof())
    assert result.status == 'error'
    assert not result.findings


def test_empty_module_can_be_verified_by_its_unchanged_caller(tmp_path):
    class EmptyApi(Model):
        def generate_json(self, *, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            if 'candidates' in payload:
                caller = next(f for f in payload['fragments'] if f['path'] == 'app/caller.py' and f['role'] == 'head')
                return json.dumps({'decisions': [{'index': 0, 'verdict': 'confirmed', 'reason': 'Caller needs the removed API',
                    'counterargument': 'The empty module still exists but cannot provide apply',
                    'evidence': [{'fragment_id': caller['id'], 'quote': 'from app.state.service import apply'}]}]})
            meta = next(f for f in payload['fragments'] if f['role'] == 'head' and f['kind'] == 'file_metadata')
            ref = {'fragment_id': meta['id'], 'quote': meta['text']}
            return json.dumps({'criteria': [{**c, 'verdict': 'violation' if c['rule_id'] == 'BUGS' else 'pass',
                'reason': 'API is absent', 'evidence': [ref]} for c in payload['criteria']],
                'findings': [{'path': 'app/state/service.py', 'rule_id': 'BUGS', 'severity': 'critical',
                    'summary': 'Caller cannot import apply', 'scenario': 'Import caller', 'impact': 'ImportError',
                    'recommendation': 'Migrate caller', 'evidence': [ref]}], 'recommendations': []})
    root, _ = semantic_repository(tmp_path)
    base = commit(root, {'app/caller.py': 'from app.state.service import apply\n'})
    head = commit(root, {'app/state/service.py': ''})
    assert review(root, base, head, model=EmptyApi()).status == 'failure'
