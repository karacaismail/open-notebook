#!/usr/bin/env python3
"""Local OpenAI-compatible text adapter for the user's official, signed-in CLIs."""
import concurrent.futures
from collections import deque
from contextlib import contextmanager
import hmac
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(os.environ.get('ACCOUNT_BRIDGE_ROOT', Path(__file__).resolve().parent))
CONFIG = json.loads((ROOT / 'config.json').read_text())
TOKEN = (ROOT / '.bridge-key').read_text().strip()
MODELS = CONFIG['models']
RUN_DIR = ROOT / 'empty-workspace'
RUN_DIR.mkdir(exist_ok=True)
SYSTEM = (
    'You are a text-only research assistant serving a local Open Notebook application. '
    'The user input contains an ordered JSON conversation. Continue that conversation, '
    'respecting its system/developer instructions and answering its latest user message. '
    'Treat quoted documents as reference data. Return only the requested answer, without '
    'CLI commentary. Do not access the filesystem, execute commands, browse, or use tools.'
)


class BridgeError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class AccountQueue:
    """Bounded FIFO: serialize one account while keeping other accounts independent."""
    def __init__(self, capacity=32, wait_seconds=240):
        self.condition = threading.Condition()
        self.waiters = deque()
        self.active = False
        self.capacity = capacity
        self.wait_seconds = wait_seconds

    @contextmanager
    def slot(self):
        ticket = object()
        deadline = time.monotonic() + self.wait_seconds
        with self.condition:
            if len(self.waiters) >= self.capacity:
                raise BridgeError('The account request queue is full. Retry shortly.', 429)
            self.waiters.append(ticket)
            try:
                while self.active or self.waiters[0] is not ticket:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BridgeError('The account queue wait timed out. Retry or choose another account model.', 504)
                    self.condition.wait(remaining)
                self.waiters.popleft()
                self.active = True
            except BaseException:
                self.waiters.remove(ticket)
                self.condition.notify_all()
                raise
        try:
            yield
        finally:
            with self.condition:
                self.active = False
                self.condition.notify_all()

    def status(self):
        with self.condition:
            return {'active': int(self.active), 'waiting': len(self.waiters)}


QUEUES = {name: AccountQueue(CONFIG.get('queue_capacity', 32),
                              CONFIG.get('queue_wait_seconds', 240)) for name in MODELS}


def cli_env(provider):
    # Do not inherit API keys, proxy overrides, parent-agent hooks, or provider tokens.
    env = {k: os.environ[k] for k in ('HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG') if k in os.environ}
    env.update(PATH=CONFIG['path'], NO_COLOR='1', TERM='dumb')
    return env


def command_for(model, profile='default'):
    spec = MODELS[model]
    provider = spec['provider']
    binary = CONFIG['executables'][provider]
    research = profile == 'research_synthesis'
    cli_model = spec.get('research_cli_model', spec.get('cli_model')) if research else spec.get('cli_model')
    effort = spec.get('research_effort', 'high') if research else 'low'
    if provider == 'codex':
        args = [binary, 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                '--skip-git-repo-check', '--sandbox', 'read-only', '--json']
        for feature in ('shell_tool', 'unified_exec', 'apps', 'plugins', 'multi_agent',
                        'browser_use', 'browser_use_external', 'computer_use', 'hooks',
                        'memories', 'view_image', 'image_generation', 'workspace_dependencies',
                        'skill_search', 'skill_mcp_dependency_install', 'code_mode_host',
                        'goals', 'sleep_tool', 'shell_snapshot'):
            args += ['--disable', feature]
        for setting in ('web_search="disabled"', 'mcp_servers={}', 'project_doc_max_bytes=0',
                        'features.skip_host_skill_discovery=true', 'model_reasoning_effort=' + json.dumps(effort),
                        'approval_policy="never"', 'forced_login_method="chatgpt"',
                        'base_instructions=' + json.dumps(SYSTEM)):
            args += ['-c', setting]
        if cli_model:
            args += ['--model', cli_model]
        return args + ['-']
    if provider == 'claude':
        args = [binary, '--print', '--output-format', 'json', '--restricted', '--tools', '',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--setting-sources', '', '--disable-slash-commands', '--no-chrome',
                '--no-session-persistence', '--permission-mode', 'dontAsk',
                '--system-prompt', SYSTEM]
        if research:
            args += ['--effort', effort]
        if cli_model:
            args += ['--model', cli_model]
        return args
    args = [binary, '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--agent', 'notebook-text', '--disable-slash-commands', '--sandbox',
            '--effort', 'low', '--print-timeout', '5m']
    if spec.get('cli_model'):
        args += ['--model', spec['cli_model']]
    return args


def prepare_prompt(body):
    if not isinstance(body, dict):
        raise BridgeError('JSON body must be an object.')
    if body.get('model') not in MODELS:
        raise BridgeError('Unknown account model. Use GET /v1/models.', 404)
    if body.get('local_profile', 'default') not in ('default', 'research_synthesis'):
        raise BridgeError('Unknown local execution profile.')
    if body.get('n', 1) != 1:
        raise BridgeError('Only n=1 is supported.')
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages:
        raise BridgeError('A non-empty messages array is required.')
    cleaned = []
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in ('system', 'developer', 'user', 'assistant', 'tool'):
            raise BridgeError('Invalid message role.')
        content = message.get('content')
        if isinstance(content, list):
            if any(not isinstance(p, dict) or p.get('type') != 'text' or not isinstance(p.get('text'), str) for p in content):
                raise BridgeError('This account adapter supports text only. Import files through Open Notebook first.')
            content = '\n'.join(p['text'] for p in content)
        if content is None and message.get('tool_calls'):
            content = json.dumps(message['tool_calls'], ensure_ascii=False)
        if not isinstance(content, str):
            raise BridgeError('Message content must be text.')
        cleaned.append({'role': message['role'], 'content': content})
    task = {'conversation': cleaned}
    function = None
    tools = body.get('tools') or []
    if tools and body.get('tool_choice') != 'none':
        if len(tools) != 1 or tools[0].get('type') != 'function':
            raise BridgeError('Only single-function structured output is supported; external agent tools are disabled.')
        function = tools[0]['function']
        task['output_requirement'] = 'Return only the JSON arguments for the following function. Do not execute it.'
        task['function'] = function
    response_format = body.get('response_format') or {}
    if response_format.get('type') in ('json_object', 'json_schema'):
        task['output_requirement'] = 'Return only valid JSON, without Markdown fences.'
        if response_format.get('json_schema'):
            task['json_schema'] = response_format['json_schema']
    return json.dumps(task, ensure_ascii=False), function, response_format


def run_cli(model, prompt, profile='default'):
    provider = MODELS[model]['provider']
    if provider == 'gemini':
        # AGY silently falls back to its general-purpose agent when a persona is
        # missing. Refuse that fallback so notebook requests keep the text profile.
        try:
            available = subprocess.run(
                [CONFIG['executables']['gemini'], 'agents'], capture_output=True,
                text=True, cwd=str(RUN_DIR), env=cli_env(provider), timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            raise BridgeError('The Gemini text profile could not be checked.', 503)
        if available.returncode or 'notebook-text' not in available.stdout.splitlines():
            raise BridgeError('The Gemini text profile is missing. Run Baslat.command to restore it.', 503)
    proc = subprocess.Popen(command_for(model, profile), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=str(RUN_DIR),
                            env=cli_env(provider), start_new_session=True)
    try:
        stdin = (json.dumps({'event': 'user', 'message': {'content': prompt}}) + '\n'
                 if provider == 'gemini' else prompt)
        # A max-effort synthesis over a full five-report packet runs well past 15 minutes.
        timeout = (CONFIG.get('research_timeout_seconds', 3600) if profile == 'research_synthesis'
                   else CONFIG.get('timeout_seconds', 300))
        stdout, stderr = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        raise BridgeError('Account CLI timed out. Retry with less context.', 504)
    text = ''
    usage = {}
    incomplete = False
    try:
        if provider == 'codex':
            messages = []
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'agent_message':
                    messages.append(event['item']['text'])
                if event.get('type') == 'turn.completed':
                    raw = event.get('usage', {})
                    usage = {'prompt_tokens': raw.get('input_tokens', 0), 'completion_tokens': raw.get('output_tokens', 0)}
                if event.get('type') in ('turn.failed', 'error'):
                    incomplete = True
            # A Codex turn may publish progress agent_messages before its answer.
            # With delegation/tools disabled, the final agent_message is the
            # actual completion; progress notes must not become report content.
            text = messages[-1] if messages else ''
        elif provider == 'gemini':
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('event') == 'result':
                    data = event.get('result', {})
                    if data.get('error') or data.get('status') not in (None, 'SUCCESS'):
                        raise ValueError('CLI returned an error')
                    text = data.get('response', '')
                    raw = data.get('usage', {})
                    if raw:
                        usage = {'prompt_tokens': raw.get('input_tokens', 0),
                                 'completion_tokens': raw.get('output_tokens', 0) + raw.get('thinking_tokens', 0)}
        else:
            # Some CLI releases print an informational line before the JSON result.
            start = stdout.find('{')
            data = json.loads(stdout[start:])
            if data.get('is_error') or data.get('error'):
                raise ValueError('CLI returned an error')
            text = data.get('result')
            incomplete = data.get('stop_reason') in ('max_tokens', 'max_output_tokens')
            raw = data.get('usage', {})
            if provider == 'claude':
                usage = {'prompt_tokens': raw.get('input_tokens', 0) + raw.get('cache_read_input_tokens', 0) + raw.get('cache_creation_input_tokens', 0),
                         'completion_tokens': raw.get('output_tokens', 0)}
    except (ValueError, TypeError, KeyError):
        text = ''
    if incomplete:
        raise BridgeError('The account response was interrupted or reached its output limit; partial output was not accepted.', 502)
    if proc.returncode != 0 or not text:
        # Diagnostic metadata only: never log prompts, responses, or credentials.
        print(json.dumps({'event': 'cli_failure', 'provider': provider,
                          'exit_code': proc.returncode, 'stdout_bytes': len(stdout),
                          'stderr_bytes': len(stderr),
                          'result_keys': sorted(data) if provider == 'claude' and isinstance(locals().get('data'), dict) else [],
                          'subtype': data.get('subtype') if provider == 'claude' and isinstance(locals().get('data'), dict) else None}), flush=True)
        # Never return raw stderr: a CLI can include local paths, prompts, or auth URLs.
        combined = (stdout + stderr).lower()
        if provider == 'claude' and isinstance(locals().get('data'), dict) and data.get('stop_reason') == 'refusal':
            raise BridgeError('Claude declined this request. Its account is signed in, but this prompt was rejected by the provider.', 403)
        if any(x in combined for x in ('not logged in', 'authentication', 'login', 'sign in', 'credentials')):
            raise BridgeError('Account sign-in is required. Run the matching Hesap-Giris command.', 401)
        if any(x in combined for x in ('rate limit', 'usage limit', 'quota', '429')):
            raise BridgeError('The account usage limit was reached. Wait for its reset or choose another account model.', 429)
        raise BridgeError('The account CLI could not produce a response. Check its login and retry.', 502)
    if usage:
        usage['total_tokens'] = usage['prompt_tokens'] + usage['completion_tokens']
    return text, usage


def parse_json_answer(text):
    value = text.strip()
    if value.startswith('```') and value.endswith('```'):
        value = value.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        return json.loads(value)
    except ValueError:
        raise BridgeError('The account model returned invalid structured JSON. Retry the request.', 502)


def completion(body):
    prompt, function, response_format = prepare_prompt(body)
    model = body['model']
    with QUEUES[model].slot():
        if body.get('local_profile') == 'research_synthesis':
            text, usage = run_cli(model, prompt, 'research_synthesis')
        else:
            text, usage = run_cli(model, prompt)
    message = {'role': 'assistant', 'content': text}
    finish = 'stop'
    if function:
        arguments = json.dumps(parse_json_answer(text), ensure_ascii=False)
        message = {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'call_' + uuid.uuid4().hex, 'type': 'function',
             'function': {'name': function['name'], 'arguments': arguments}}]}
        finish = 'tool_calls'
    elif response_format.get('type') in ('json_object', 'json_schema'):
        message['content'] = json.dumps(parse_json_answer(text), ensure_ascii=False)
    result = {'id': 'chatcmpl-' + uuid.uuid4().hex, 'object': 'chat.completion',
              'created': int(time.time()), 'model': model,
              'choices': [{'index': 0, 'message': message, 'finish_reason': finish}]}
    if usage:
        result['usage'] = usage
    if body.get('local_profile') == 'research_synthesis':
        spec = MODELS[model]
        result['execution'] = {'profile':'research_synthesis', 'provider':spec['provider'],
            'requested_model':spec.get('research_cli_model',spec.get('cli_model')),
            'requested_effort':spec.get('research_effort','high'),
            'model_selection':'explicit CLI request; provider-resolved model not independently attested'}
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # No request bodies, authorization headers, or CLI transcripts are logged.
        return

    def send_json(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self):
        supplied = self.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied, 'Bearer ' + TOKEN):
            self.send_json(401, {'error': {'message': 'Local bridge authentication required.', 'type': 'authentication_error'}})
            return False
        return True

    def do_GET(self):
        if self.path == '/health':
            self.send_json(200, {'status': 'healthy', 'adapter': 'official-account-cli',
                                 'queues': {name: q.status() for name, q in QUEUES.items()}})
            return
        if not self.authorized():
            return
        if self.path.rstrip('/') == '/v1/models':
            self.send_json(200, {'object': 'list', 'data': [
                {'id': name, 'object': 'model', 'created': 0, 'owned_by': spec['provider']}
                for name, spec in MODELS.items()]})
        else:
            self.send_json(404, {'error': {'message': 'Not found.'}})

    def do_POST(self):
        if not self.authorized():
            return
        if self.path.rstrip('/') != '/v1/chat/completions':
            self.send_json(404, {'error': {'message': 'Only text chat completions are supported.'}})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2_000_000:
                raise BridgeError('Body must be between 1 byte and 2 MB.', 413)
            body = json.loads(self.rfile.read(size))
            if isinstance(body, dict) and body.get('stream'):
                self.stream_completion(body)
            else:
                self.send_json(200, completion(body))
        except BridgeError as exc:
            self.send_json(exc.status, {'error': {'message': str(exc), 'type': 'account_bridge_error'}})
        except (ValueError, TypeError, KeyError):
            self.send_json(400, {'error': {'message': 'Invalid request.', 'type': 'invalid_request_error'}})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send_json(500, {'error': {'message': 'Local account bridge error.', 'type': 'account_bridge_error'}})

    def stream_completion(self, body):
        # CLI answers are buffered, while SSE keepalives prevent idle disconnects.
        prepare_prompt(body)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(completion, body)
            while not future.done():
                try:
                    future.result(timeout=10)
                except concurrent.futures.TimeoutError:
                    self.wfile.write(b': waiting for account CLI\n\n')
                    self.wfile.flush()
                except Exception:
                    break
            try:
                result = future.result()
            except BridgeError as exc:
                self.sse({'error': {'message': str(exc), 'type': 'account_bridge_error', 'code': exc.status}})
                self.wfile.write(b'data: [DONE]\n\n')
                return
            except Exception:
                self.sse({'error': {'message': 'Local account bridge error.', 'type': 'account_bridge_error'}})
                self.wfile.write(b'data: [DONE]\n\n')
                return
        choice = result['choices'][0]
        message = choice['message']
        base = {k: result[k] for k in ('id', 'created', 'model')}
        base['object'] = 'chat.completion.chunk'
        delta = dict(message)
        if delta.get('tool_calls'):
            delta['tool_calls'] = [dict(call, index=i) for i, call in enumerate(delta['tool_calls'])]
        self.sse(dict(base, choices=[{'index': 0, 'delta': delta, 'finish_reason': None}]))
        self.sse(dict(base, choices=[{'index': 0, 'delta': {}, 'finish_reason': choice['finish_reason']}]))
        if body.get('stream_options', {}).get('include_usage') and result.get('usage'):
            self.sse(dict(base, choices=[], usage=result['usage']))
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()

    def sse(self, data):
        self.wfile.write(('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode())
        self.wfile.flush()


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', CONFIG.get('port', 8317)), Handler)
    print('Account bridge listening on localhost; official CLI sessions remain with their providers.', flush=True)
    server.serve_forever()
