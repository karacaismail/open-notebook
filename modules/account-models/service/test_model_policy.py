import pytest
import model_policy as m

def test_ranks_verified_catalog_and_max_supported_effort(monkeypatch):
    monkeypatch.setattr(m,'discover',lambda *a:{'quota':'unknown','models':[
        {'model':'claude-opus-5','efforts':['low','max']},{'model':'claude-fable-5-1','efforts':['high','xhigh','max']}]})
    p=m.ModelPolicy({'claude':'test'})
    assert p.choose({'provider':'claude'})['model']=='claude-fable-5-1'
    assert p.choose({'provider':'claude'})['effort']=='max'
    assert p.choose({'provider':'claude'},['claude-fable-5-1'])['model']=='claude-opus-5'

def test_quota_cooldown_survives_restart_without_sending_again(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'discover',lambda *a:pytest.fail('must not discover during cooldown'))
    p=m.ModelPolicy({'codex':'test'},tmp_path/'state.json');p.limited('codex')
    with pytest.raises(m.SelectionError) as e:m.ModelPolicy({'codex':'test'},tmp_path/'state.json').choose({'provider':'codex'})
    assert e.value.status==429

def test_exhausted_account_does_not_switch_to_weaker_model(monkeypatch):
    monkeypatch.setattr(m,'discover',lambda *a:{'quota':'exhausted','models':[{'model':'gpt-6-astra','efforts':['ultra']}]})
    with pytest.raises(m.SelectionError) as e:m.ModelPolicy({'codex':'test'}).choose({'provider':'codex'})
    assert e.value.status==429

def test_no_invented_model_when_discovery_empty(monkeypatch):
    monkeypatch.setattr(m,'discover',lambda *a:{'quota':'unknown','models':[]})
    with pytest.raises(m.SelectionError):m.ModelPolicy({'gemini':'test'}).choose({'provider':'gemini'})

def test_completion_uses_live_selection_and_preserves_prompt_on_unavailable_model(monkeypatch):
    import server
    choices=[];calls=[]
    def choose(spec,excluded=()):
        choices.append(list(excluded));return {'model':'preferred' if not excluded else 'available','effort':'max','policy':'live'}
    def run(model,prompt,profile,selection):
        calls.append((prompt,selection['model']))
        if selection['model']=='preferred':raise server.BridgeError('Unavailable model',404)
        return 'Complete source-based report',{'research_trace':{'reported_models':['available']}}
    monkeypatch.setattr(server.MODEL_POLICY,'choose',choose);monkeypatch.setattr(server,'run_cli',run)
    result=server.completion({'model':'claude-account','local_profile':'preliminary_research','messages':[{'role':'user','content':'The full unchanged evidence'}]})
    assert calls[0][0]==calls[1][0] and choices==[[],['preferred']]
    assert result['execution']['requested_model']=='available'

def test_completion_does_not_retry_partial_output_or_quota(monkeypatch):
    import server
    monkeypatch.setattr(server.MODEL_POLICY,'choose',lambda *a:{'model':'preferred','effort':'max'})
    calls=[]
    def fail(*args):calls.append(args);raise server.BridgeError('Partial response rejected',502)
    monkeypatch.setattr(server,'run_cli',fail)
    with pytest.raises(server.BridgeError):server.completion({'model':'claude-account','local_profile':'preliminary_research','messages':[{'role':'user','content':'full packet'}]})
    assert len(calls)==1
