import asyncio
import io
import json
from pathlib import Path
import struct
import time
import uuid
import pytest
from playwright.async_api import async_playwright
from browser_runtime import BrowserAttention
from extension_bridge import (ExtensionBridge, ORIGIN, atomic_json, read_frame, validate_command, write_frame)

PACKAGE = Path(__file__).resolve().parents[2] / 'browser-extension'


@pytest.mark.asyncio
async def test_oversized_unicode_packet_has_actionable_error_and_no_command(tmp_path):
    bridge = ExtensionBridge(tmp_path)
    atomic_json(tmp_path/'connection.json', {'origin': ORIGIN, 'heartbeat_at': time.time()})
    with pytest.raises(BrowserAttention) as exc:
        await bridge.request('fill','Gemini','probe:Gemini',text='ş'*500000)
    assert exc.value.kind == 'context_limit' and 'UTF-8' in str(exc.value) and 'kesilmedi' in str(exc.value)
    assert not list((tmp_path/'commands').glob('*.json'))


def test_source_cards_cannot_bypass_infrastructure_filter_but_vendor_docs_are_sources():
    from extension_research import report_sources, is_evidence_url
    sandbox='https://connector-openai-deep-research.web-sandbox.oaiusercontent.com/report'
    content, sources = report_sources('A report '+sandbox,[{'url':'https://chatgpt.com/c/test','title':'conversation'}])
    assert sources == []
    for url in ('https://openai.com/research/', 'https://ai.google.dev/docs', 'https://docs.anthropic.com/en/docs'):
        assert is_evidence_url(url)
    content, sources=report_sources('A report',[{'url':sandbox,'title':'internal'}, {'url':'https://openai.com/research/','title':'primary'}])
    assert sources == ['https://openai.com/research/'] and sandbox not in content
    assert not is_evidence_url('javascript:alert(1)')


def test_sources_exclude_ui_icons_and_markdown_code_delimiters():
    from extension_research import report_markdown
    from workflow import citations
    html='<h1>Evidence</h1><img src="https://t0.gstatic.com/faviconV2?url=https://example.org/"/><a href="https://example.org/primary">Primary</a><code>https://example.org/primary</code><img alt="Scientific figure" src="https://example.org/figure.png"/>'
    report=report_markdown(html)
    assert 'favicon' not in report
    assert 'Scientific figure' in report
    assert citations(report)==['https://example.org/primary','https://example.org/figure.png']


def test_native_framing_and_invalid_commands():
    message = {'type': 'heartbeat', 'text': 'Türkçe araştırma'}
    stream = io.BytesIO(); write_frame(stream, message); stream.seek(0)
    assert read_frame(stream) == message
    class Partial(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))
    assert read_frame(Partial(stream.getvalue())) == message
    for payload in (struct.pack('=I', 100)+b'{}', struct.pack('=I', 20_000_000)):
        with pytest.raises(ValueError): read_frame(io.BytesIO(payload))
    good = {'id': uuid.uuid4().hex, 'op': 'inspect', 'provider': 'Gemini', 'job': 'probe:Gemini', 'expires_at': time.time()+100}
    validate_command(good)
    for patch in ({'job': '../../other'}, {'op': 'eval'}, {'provider': 'Other'}, {'id': '../bad'}, {'expires_at': 1}):
        with pytest.raises(ValueError): validate_command(good | patch)


@pytest.mark.asyncio
async def test_bridge_private_spool_response_and_timeout(tmp_path):
    bridge = ExtensionBridge(tmp_path)
    atomic_json(tmp_path/'connection.json', {'origin': ORIGIN, 'heartbeat_at': time.time()})
    task = asyncio.create_task(bridge.request('inspect', 'Gemini', 'probe:Gemini', timeout=2))
    command = None
    for _ in range(30):
        files = list((tmp_path/'commands').glob('*.json'))
        if files:
            command = json.loads(files[0].read_text())
            assert files[0].stat().st_mode & 0o777 == 0o600
            break
        await asyncio.sleep(.01)
    assert command is not None
    atomic_json(tmp_path/'results'/(command['id']+'.json'), {'id': command['id'], 'result': {'composer_visible': True}})
    assert await task == {'composer_visible': True}
    assert not list((tmp_path/'commands').glob('*.json'))
    with pytest.raises(BrowserAttention) as err:
        await bridge.request('inspect', 'Gemini', 'probe:Gemini', timeout=.05)
    assert err.value.kind == 'interrupted'
    assert not list((tmp_path/'commands').glob('*.json'))


@pytest.mark.asyncio
async def test_extension_driver_real_dom():
    script = (PACKAGE/'dom-driver.js').read_text()
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        async def run(provider, html, op='inspect', params=None, url=None):
            context = await browser.new_context()
            async def fixture(route):
                await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=html)
            await context.route('**/*', fixture)
            page = await context.new_page()
            await page.goto(url or {'Gemini':'https://gemini.google.com/app','ChatGPT':'https://chatgpt.com/atlas/','Claude':'https://claude.ai/new'}[provider])
            await page.evaluate(script)
            args = {'op':op,'provider':provider,'params':params or {},'marker':'ON-test-marker','expires_at':time.time()+100}
            result = await page.evaluate('(arg)=>globalThis.__openNotebookResearchDriver(arg)',args)
            return context,page,result
        try:
            for html in (
                '<button aria-pressed="false">Research mode</button>',
                '<button hidden aria-pressed="true">Research mode</button>',
                '<div style="opacity:0"><button aria-pressed="true">Research mode</button></div>',
                '<button>Research</button>',
                '<button aria-pressed="true">Deep think</button>',
            ):
                ctx,page,result = await run('Claude',html)
                assert result['research_selected'] is False
                await ctx.close()
            ctx,page,result=await run('Gemini','<button aria-label="Deep Research öğesinin seçimini kaldır">x</button>')
            assert result['research_selected'] is True
            await ctx.close()
            ctx,page,result=await run('Gemini','<button>Oturum aç</button><div class="ql-editor" contenteditable="true"></div>')
            assert result['sign_in_visible'] is True and result['composer_visible'] is True
            await ctx.close()
            ctx,page,result=await run('Claude','<h1>Güvenlik doğrulaması yapılıyor</h1>','submit')
            assert result['error']['kind']=='verification_required'
            await ctx.close()
            ctx,page,result=await run('Claude','<button onclick="window.sent=true">Send</button>','submit')
            assert result['error'] and not await page.evaluate('!!window.sent')
            await ctx.close()
            ctx,page,result=await run('Claude','<button aria-pressed="false" onclick="this.setAttribute(\'aria-pressed\',\'true\')">Research</button>','select_research')
            assert result['research_selected'] is True
            await ctx.close()
            html='<div role="tablist" aria-label="Deep Research tabs"><button>Reports</button></div><div id="prompt-textarea" contenteditable="true"><span contenteditable="false">Deep research</span> </div><button aria-label="Send prompt">Send</button>'
            ctx,page,result=await run('ChatGPT',html,'fill',{'text':'A real prompt ON-test-marker'})
            assert result.get('prepared') is True, result
            assert await page.locator('[contenteditable="false"]').inner_text()=='Deep research'
            assert 'A real prompt ON-test-marker' in await page.locator('#prompt-textarea').inner_text()
            await ctx.close()
            html='<button aria-pressed="true">Research mode</button><div class="ProseMirror" contenteditable="true">User draft</div>'
            ctx,page,result=await run('Claude',html,'fill',{'text':'New prompt ON-test-marker'})
            assert result['error'] and await page.locator('.ProseMirror').inner_text()=='User draft'
            await ctx.close()
            ctx,page,result=await run('Claude','',url='https://claude.ai.evil.example/')
            assert result['error']['kind']=='login_required'
            await ctx.close()
            # Prompt text never supplies completion evidence; report without our marker is rejected.
            html='<div data-testid="user-message">Research complete ON-test-marker</div><div data-testid="assistant-message">'+('ordinary reply '*110)+'</div>'
            ctx,page,result=await run('Claude',html,'collect',{'research_started':False})
            assert result['report'] is None
            await ctx.close()
            # Rich text paragraph normalization must not cause a retry to append twice.
            html='<button aria-pressed="true">Research mode</button><div class="ProseMirror" contenteditable="true"></div>'
            prompt='First paragraph\n\nSecond paragraph ON-test-marker'
            ctx,page,result=await run('Claude',html,'fill',{'text':prompt})
            assert result['prepared'] is True
            result=await page.evaluate('(text)=>globalThis.__openNotebookResearchDriver({op:"fill",provider:"Claude",params:{text},marker:"ON-test-marker",expires_at:Date.now()/1000+30})',prompt)
            assert result['prepared'] is True
            assert (await page.locator('.ProseMirror').inner_text()).count('ON-test-marker')==1
            await ctx.close()
            # The observed icon glyph must not hide an unchecked Research menu item.
            html='<button aria-checked="false" onclick="this.setAttribute(\'aria-checked\',\'true\')">\ue0d0 Research</button>'
            ctx,page,result=await run('Claude',html,'select_research')
            assert result['research_selected'] is True
            await ctx.close()
            # Turkish native plan approval: exact task marker and visible control.
            html='<user-query>ON-test-marker</user-query><button onclick="window.started=true">Araştırmayı başlatın</button>'
            ctx,page,result=await run('Gemini',html,'start_plan')
            assert result['clicked'] and await page.evaluate('window.started') is True
            await ctx.close()
            ctx,page,result=await run('Gemini',html.replace('ON-test-marker','another-task'),'start_plan')
            assert result['error']['kind']=='submission_uncertain'
            assert await page.evaluate('window.started') is None
            await ctx.close()
            ctx,page,result=await run('Gemini',html.replace('<button ','<button style="display:none" '),'start_plan')
            assert result['clicked'] is False
            await ctx.close()
            # A finite paused entrance animation may finish, but static hidden
            # controls remain rejected by the negative cases above.
            html='<div class="ng-animating"><button aria-label="Deep Research öğesinin seçimini kaldır">x</button></div><script>const a=document.querySelector(".ng-animating").animate([{opacity:0},{opacity:1}],{duration:250,fill:"both"});a.pause();</script>'
            ctx,page,result=await run('Gemini',html,'select_research')
            assert result['research_selected'] is True
            await ctx.close()
            # File transfer is nonblocking; polling never attaches the same file
            # twice, and a filename rendered as a div is recognized.
            html='<button aria-pressed="true">Research mode</button><input type="file" onchange="window.uploadCount=(window.uploadCount||0)+1"><div class="ProseMirror" contenteditable="true"></div>'
            prompt='Full evidence '*1600+' ON-test-marker'
            ctx,page,result=await run('Claude',html,'fill',{'text':prompt})
            assert result['pending_upload'] and not result['prepared']
            result=await page.evaluate('(text)=>__openNotebookResearchDriver({op:"fill",provider:"Claude",params:{text},marker:"ON-test-marker",expires_at:Date.now()/1000+30})',prompt)
            assert result['pending_upload'] and await page.evaluate('window.uploadCount')==1
            await page.evaluate('document.body.insertAdjacentHTML("beforeend","<div>input-packet.md</div>")')
            result=await page.evaluate('(text)=>__openNotebookResearchDriver({op:"fill",provider:"Claude",params:{text},marker:"ON-test-marker",expires_at:Date.now()/1000+30})',prompt)
            assert result['prepared'] and await page.evaluate('window.uploadCount')==1
            assert 'ON-test-marker' in await page.locator('.ProseMirror').inner_text()
            await ctx.close()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_research_app_frame_collects_only_finished_source_report():
    script=(PACKAGE/'research-frame.js').read_text()
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True)
        context=await browser.new_context()
        async def fixture(route):await route.fulfill(content_type='text/html; charset=utf-8',body='<iframe></iframe>')
        await context.route('**/*',fixture)
        page=await context.new_page()
        await page.goto('https://connector-openai-deep-research.web-sandbox.oaiusercontent.com/')
        await page.locator('iframe').evaluate('(frame)=>{frame.contentDocument.body.innerHTML="<button>Stop research</button><article>Partial report</article>";}')
        await page.evaluate(script)
        cmd={'op':'collect','expires_at':time.time()+100,'params':{}}
        result=await page.evaluate('(cmd)=>__openNotebookResearchFrame(cmd)',cmd)
        assert result['busy'] and result['research_progress'] and result['report'] is None
        html='<button>Export</button><button>Expand</button><div role="button"><h1>Finished report</h1><p>'+('Evidence content '*120)+'</p><a href="https://example.org/primary">Primary evidence</a></div>'
        await page.locator('iframe').evaluate('(frame,html)=>{frame.contentDocument.body.innerHTML=html;}',html)
        result=await page.evaluate('(cmd)=>__openNotebookResearchFrame(cmd)',cmd)
        assert result['research_complete'] and not result['busy']
        assert result['report']['links']==[{'title':'Primary evidence','url':'https://example.org/primary'}]
        assert 'Finished report' in result['report']['html']
        await page.goto('https://example.org/')
        await page.evaluate(script)
        assert await page.evaluate('typeof globalThis.__openNotebookResearchFrame')=='undefined'
        await context.close();await browser.close()


def test_block_source_cards_become_valid_separate_markdown_links():
    from extension_research import report_markdown
    html='<a href="https://one.example/source"><div>one.example</div><div>First source</div></a><a href="https://two.example/source"><div>Second source</div></a>'
    text=report_markdown(html)
    assert text=='[one.example First source](https://one.example/source)[Second source](https://two.example/source)'
