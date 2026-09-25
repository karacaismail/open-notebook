import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from engine import AccountProvider, ServiceError


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['completed', 'failed', 'running', 'wrong_input', 'corrupt_result'])
async def test_receipt_recovery_is_read_only_and_checks_identity(tmp_path, monkeypatch, state):
    key = tmp_path / 'key'; key.write_text('test-key')
    provider = AccountProvider(key)
    stage = {'provider': 'ChatGPT', 'request_id': 'a' * 32, 'account_profile': 'research_review'}
    body = provider.request_body(stage, 'Frozen evidence')
    result = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Saved report'}}], 'usage': {}}
    receipt = {'state': state if state in ('completed', 'failed', 'running') else 'completed',
               'request_sha256': sha({k: v for k, v in body.items() if k != 'local_request_id'}),
               'result': result, 'result_sha256': sha(result), 'status': 502, 'message': 'CLI stopped'}
    if state == 'wrong_input': receipt['request_sha256'] = 'wrong'
    if state == 'corrupt_result': receipt['result']['choices'][0]['message']['content'] = 'Changed'
    get = AsyncMock(return_value=httpx.Response(200, json=receipt))
    post = AsyncMock(side_effect=AssertionError('Recovery must never submit'))
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    if state == 'completed':
        assert (await provider.recover(stage, 'Frozen evidence'))[0] == 'Saved report'
    else:
        with pytest.raises(ServiceError) as error: await provider.recover(stage, 'Frozen evidence')
        assert error.value.settled == (state == 'failed')
        if state in ('wrong_input', 'corrupt_result'): assert error.value.kind == 'integrity_error'
    assert get.await_count == 1 and post.await_count == 0


@pytest.mark.asyncio
async def test_review_resumes_saved_response_without_resubmission(tmp_path):
    from review_execution import ReviewRunner
    from context_preparation import digest
    provider = type('Provider', (), {'recover': AsyncMock(return_value=('Saved report', {})),
                                   'synthesize': AsyncMock(side_effect=AssertionError('Duplicate request'))})()
    engine = type('Engine', (), {'provider': provider,
        'input_path': lambda *a: tmp_path / 'input.md', 'measure_input': lambda *a: {'fits': True}})()
    runner = ReviewRunner(engine, {'id': 'test'}, {'provider': 'ChatGPT'}, 'Input')
    runner.progress = AsyncMock()
    runner.state = {'jobs': {'P1': {'input_sha256': digest('Frozen evidence'),
                                  'status': 'in_flight', 'request_id': 'a' * 32}}}
    assert (await runner.call('P1', 'Frozen evidence', 'research_review'))[0] == 'Saved report'
    assert json.loads(runner.path.read_text())['jobs']['P1']['status'] == 'completed'
    assert provider.synthesize.await_count == 0
    assert runner.progress.call_args.args[-1] == 'a' * 32


@pytest.mark.asyncio
async def test_pending_recovery_observes_same_request_until_saved(tmp_path, monkeypatch):
    import engine
    provider=AccountProvider(tmp_path/'key')
    provider.recover=AsyncMock(side_effect=[
        ServiceError('Pending',409,kind='submission_uncertain',pending=True), ('Saved report',{})])
    sleep=AsyncMock();monkeypatch.setattr(engine.asyncio,'sleep',sleep)
    stage={'request_id':'a'*32}
    assert (await provider.recover_pending(stage,'Frozen input'))[0]=='Saved report'
    assert provider.recover.call_args_list[0]==provider.recover.call_args_list[1]
    assert sleep.await_count==1


@pytest.mark.asyncio
async def test_unknown_receipt_is_not_polled_or_resubmitted(tmp_path):
    provider=AccountProvider(tmp_path/'key')
    provider.recover=AsyncMock(side_effect=ServiceError('Unknown',409,kind='submission_uncertain'))
    with pytest.raises(ServiceError):await provider.recover_pending({'request_id':'a'*32},'Frozen input')
    assert provider.recover.await_count==1
