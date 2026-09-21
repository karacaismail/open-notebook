import json
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
import server


class BridgeTests(unittest.TestCase):
    def test_research_records_requested_model_and_effort_without_attesting_resolved_model(self):
        with patch.object(server,'run_cli',return_value=('Report',{'total_tokens':10})):
            result=server.completion(self.request(local_profile='research_synthesis'))
        self.assertEqual(result['execution']['requested_model'],'claude-fable-5-1')
        self.assertEqual(result['execution']['requested_effort'],'max')
        self.assertIn('not independently attested',result['execution']['model_selection'])

    def request(self, **extra):
        return dict(model='claude-account', messages=[{'role': 'user', 'content': 'Merhaba'}], **extra)

    def test_multimodal_input_is_rejected_instead_of_silently_lost(self):
        body = self.request()
        body['messages'][0]['content'] = [{'type': 'image_url', 'image_url': {'url': 'http://example.com/x.png'}}]
        with self.assertRaises(server.BridgeError):
            server.prepare_prompt(body)

    def test_unrecognized_model_cannot_become_a_cli_argument(self):
        body = self.request()
        body['model'] = '--dangerously-skip-permissions'
        with self.assertRaises(server.BridgeError):
            server.prepare_prompt(body)

    def test_request_failure_releases_account_lock(self):
        with patch.object(server, 'run_cli', side_effect=server.BridgeError('test', 502)):
            with self.assertRaises(server.BridgeError):
                server.completion(self.request())
        self.assertEqual(server.QUEUES['claude-account'].status(), {'active': 0, 'waiting': 0})

    def test_concurrent_requests_wait_and_run_in_fifo_order(self):
        queue = server.AccountQueue(capacity=4, wait_seconds=4)
        order = []
        def work(number):
            with queue.slot():
                order.append(number)
        with queue.slot():
            threads = []
            for i in range(3):
                t = threading.Thread(target=work, args=(i,)); t.start(); threads.append(t)
                deadline = time.monotonic() + 1
                while queue.status()['waiting'] != i + 1 and time.monotonic() < deadline:
                    time.sleep(0.005)
            time.sleep(2.05)  # Reproduce the previous 2-second rejection boundary.
            self.assertEqual(order, [])
        for t in threads: t.join(2)
        self.assertEqual(order, [0, 1, 2])
        self.assertEqual(queue.status(), {'active': 0, 'waiting': 0})

    def test_timeout_removes_waiter_without_blocking_next_request(self):
        queue = server.AccountQueue(wait_seconds=0.03)
        with queue.slot():
            with self.assertRaises(server.BridgeError) as error:
                with queue.slot(): pass
            self.assertEqual(error.exception.status, 504)
        with queue.slot(): pass
        self.assertEqual(queue.status(), {'active': 0, 'waiting': 0})

    def test_queue_capacity_is_bounded(self):
        queue = server.AccountQueue(capacity=0)
        with self.assertRaises(server.BridgeError) as error:
            with queue.slot(): pass
        self.assertEqual(error.exception.status, 429)

    def test_structured_function_result_is_openai_compatible(self):
        tool = {'type': 'function', 'function': {'name': 'summary', 'parameters': {'type': 'object'}}}
        with patch.object(server, 'run_cli', return_value=('```json\n{"summary":"ok"}\n```', {})):
            data = server.completion(self.request(tools=[tool]))
        choice = data['choices'][0]
        self.assertEqual(choice['finish_reason'], 'tool_calls')
        call = choice['message']['tool_calls'][0]
        self.assertEqual(call['function']['name'], 'summary')
        self.assertEqual(json.loads(call['function']['arguments']), {'summary': 'ok'})

    def test_invalid_json_is_not_presented_as_success(self):
        with patch.object(server, 'run_cli', return_value=('not JSON', {})):
            with self.assertRaises(server.BridgeError):
                server.completion(self.request(response_format={'type': 'json_object'}))

    def test_provider_api_keys_and_parent_agent_variables_are_not_inherited(self):
        with patch.dict(server.os.environ, {'OPENAI_API_KEY': 'secret', 'ANTHROPIC_API_KEY': 'secret', 'GEMINI_API_KEY': 'secret', 'CLAUDECODE': '1'}):
            env = server.cli_env('claude')
        for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'CLAUDECODE'):
            self.assertNotIn(key, env)

    def test_provider_refusal_is_not_misreported_as_missing_login(self):
        from unittest.mock import Mock
        process = Mock(returncode=1)
        process.communicate.return_value = (json.dumps({'is_error': True,
            'stop_reason': 'refusal', 'result': 'Provider declined this request.'}), '')
        with patch.object(server.subprocess, 'Popen', return_value=process):
            with self.assertRaises(server.BridgeError) as error:
                server.run_cli('claude-account', 'test')
        self.assertEqual(error.exception.status, 403)
        self.assertIn('declined', str(error.exception))

    def test_gemini_missing_text_profile_cannot_fall_back_to_general_agent(self):
        available = subprocess.CompletedProcess([], 0, stdout='other-agent\n', stderr='')
        with patch.object(server.subprocess, 'run', return_value=available), patch.object(server.subprocess, 'Popen') as process:
            with self.assertRaises(server.BridgeError) as error:
                server.run_cli('gemini-account', 'test')
        self.assertEqual(error.exception.status, 503)
        process.assert_not_called()

    def test_research_profile_raises_effort_without_enabling_tools(self):
        normal = server.command_for('chatgpt-account')
        research = server.command_for('chatgpt-account', 'research_synthesis')
        self.assertIn('model_reasoning_effort="low"', normal)
        self.assertIn('model_reasoning_effort="max"', research)
        self.assertEqual(research[research.index('--model')+1], 'gpt-6-astra')
        self.assertNotIn('--model', normal)
        self.assertIn('web_search="disabled"', research)
        claude = server.command_for('claude-account', 'research_synthesis')
        self.assertEqual(claude[claude.index('--effort')+1], 'max')
        self.assertEqual(claude[claude.index('--model')+1], 'claude-fable-5-1')
        self.assertEqual(claude[claude.index('--tools')+1], '')
        with self.assertRaises(server.BridgeError):
            server.prepare_prompt(self.request(local_profile='arbitrary-command'))
        with patch.object(server,'run_cli',return_value=('Test synthesis',{})) as run:
            server.completion(self.request(local_profile='research_synthesis'))
            self.assertEqual(run.call_args.args[2], 'research_synthesis')

    def test_partial_provider_outputs_are_not_accepted(self):
        from unittest.mock import Mock
        examples = [
            ('claude-account', json.dumps({'result':'Partial answer','stop_reason':'max_tokens','usage':{}})),
            ('chatgpt-account', '\n'.join(json.dumps(x) for x in [
                {'type':'item.completed','item':{'type':'agent_message','text':'Partial answer'}},
                {'type':'turn.failed','error':{'message':'interrupted'}}])),
        ]
        for model, stdout in examples:
            process = Mock(returncode=0)
            process.communicate.return_value = (stdout, '')
            with patch.object(server.subprocess,'Popen',return_value=process):
                with self.assertRaises(server.BridgeError) as error:
                    server.run_cli(model,'test','research_synthesis')
                self.assertIn('partial output',str(error.exception))

    def test_codex_progress_is_not_included_in_the_final_report(self):
        from unittest.mock import Mock
        events=[{'type':'item.completed','item':{'type':'agent_message','text':text}}
                for text in ('I will compare the evidence.','I found a disagreement.','Final evidence-based report.')]
        events.append({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':20}})
        process=Mock(returncode=0)
        process.communicate.return_value=('\n'.join(json.dumps(e) for e in events),'')
        with patch.object(server.subprocess,'Popen',return_value=process):
            text,usage=server.run_cli('chatgpt-account','test','research_synthesis')
        self.assertEqual(text,'Final evidence-based report.')
        self.assertEqual(usage['total_tokens'],120)

if __name__ == '__main__':
    unittest.main()
