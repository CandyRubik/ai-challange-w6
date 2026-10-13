import json
from reviewer import review
from tests.test_reviewer import repository, commit


def test_unchanged_repeated_tail_is_not_a_new_bug(tmp_path):
    root,_=repository(tmp_path)
    path='app/repeated.py'
    base=commit(root,{path:'pass\n'*300})
    head=commit(root,{path:'pass\n'*150+'added = 1\n'+'pass\n'*150})
    class WrongDiagnosis:
        name='controlled-false-diagnosis'
        def generate_json(self,*,messages,schema,**kwargs):
            payload=json.loads(messages[-1]['content'])
            f=next(f for f in payload['fragments'] if f['path']==path and f['role']=='head' and f['kind']=='text'
                   and f['line']<=152<f['line']+len(f['text'].splitlines()))
            ref=dict(fragment_id=f['id'],line=152,end_line=152)
            if schema['title']=='Verification':
                return json.dumps(dict(decisions=[dict(index=c['index'],verdict='confirmed',reason='Synthetic wrong confirmation',
                    counterargument='Synthetic control',evidence=[ref]) for c in payload['candidates']]))
            return json.dumps(dict(criteria=[dict(path=path,rule_id='BUGS',verdict='violation',reason='Synthetic false diagnosis',evidence=[ref])],
                findings=[dict(path=path,rule_id='BUGS',severity='critical',summary='Synthetic false diagnosis',scenario='Unchanged pass line is blamed',
                    impact='False block',recommendation='Do not blame unchanged lines',evidence=[ref])],recommendations=[]))
    result=review(root,base,head,model=WrongDiagnosis())
    assert result.status=='error' and not result.findings
    assert result.trace['model_calls']==1


def test_git_line_ranges_cover_insert_delete_replace_and_empty_files():
    from reviewer.git import line_edits
    assert line_edits('', 'новое\n')==[('insert',0,0,0,1)]
    assert line_edits('старое\n','')==[('delete',0,1,0,0)]
    assert line_edits('a\nb\nc\n','a\nx\nc\n')==[('replace',1,2,1,2)]
    assert line_edits('a\nc\n','a\nb\nc\n')==[('insert',1,1,1,2)]
    assert line_edits('a\nb\nc\n','a\nc\n')==[('delete',1,2,1,1)]
    assert line_edits('unchanged\n','unchanged\n')==[]


def test_git_line_failure_is_bounded_and_does_not_expose_source(tmp_path, monkeypatch):
    import subprocess
    import pytest
    from reviewer.git import GitError, line_edits
    def timeout(argv,**kwargs):
        assert kwargs['timeout']==30 and '--no-ext-diff' in argv and '--no-textconv' in argv
        assert 'source-secret' not in repr(argv)
        raise subprocess.TimeoutExpired(argv,30,output=b'source-secret')
    monkeypatch.setattr(subprocess,'run',timeout)
    with pytest.raises(GitError) as error:
        line_edits('source-secret','changed')
    assert 'source-secret' not in str(error.value)
