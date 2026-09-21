import json
import server
from preliminary import trace

def test_profiles_keep_synthesis_isolated():
    for name,model,effort in [('chatgpt-account','gpt-6-astra','ultra'),('claude-account','claude-fable-5-1','max'),('gemini-account','gemini-3.1-pro-high','high')]:
        cmd=server.command_for(name,'preliminary_research')
        assert model in cmd and (effort in cmd or any(effort in x for x in cmd))
    old=server.command_for('chatgpt-account','research_synthesis')
    assert 'web_search="disabled"' in old
    web=server.command_for('chatgpt-account','preliminary_research')
    assert 'web_search="live"' in web and web[web.index('code_mode_host')-1]=='--enable'
    claude=server.command_for('claude-account','preliminary_research')
    assert claude[claude.index('--tools')+1]=='WebSearch,WebFetch'

def test_tool_evidence_requires_search_and_read_not_final_claims():
    claim=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'I searched and read'}})
    assert not trace(claim,'codex')[0]['searched']
    events=[{'type':'item.completed','item':{'type':'web_search','action':{'type':'search'}}},{'type':'item.completed','item':{'type':'web_search','action':{'type':'other'},'query':'https://example.org/'}}]
    result=trace('\n'.join(map(json.dumps,events)),'codex')[0]
    assert result['searched'] and result['read_sources']
    event={'event':'step_update','step_update':{'state':'DONE','tool_name':'run_command','tool_info':{'parameters':{}}}}
    assert trace(json.dumps(event),'gemini')[0]['unexpected_tools']==['run_command']
