"""Separate three-prompt quality probe; never part of scored matrix jobs."""
from __future__ import annotations
import asyncio
import json
import os
import signal
from pathlib import Path
import sys
import tempfile
import time


CHECKS = '''
import importlib.util
from pathlib import Path
import sys
root=Path(sys.argv[1]); phase=int(sys.argv[2])
spec=importlib.util.spec_from_file_location('probe_parser',root/'parser.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.parse_records(' a = one , b=two\\nc=three ') == {'a':'one','b':'two','c':'three'}
assert m.parse_records('') == {}
try: m.parse_records('broken')
except ValueError: pass
else: raise AssertionError('missing separators must fail')
if phase == 1:
    assert m.parse_records('a=one,a=two') == {'a':'two'}
else:
    try: m.parse_records('a=one,a=two')
    except ValueError: pass
    else: raise AssertionError('duplicate keys must fail')
if phase == 3:
    assert m.parse_records('# comment\\na=one\\n  # second comment\\nb=two') == {'a':'one','b':'two'}
'''


async def run(binary: Path, env: dict[str, str], output: Path, processes: dict | None = None, session_key: str = "probe") -> dict:
    work = Path(tempfile.mkdtemp(prefix='fx-multi-prompt-'))
    trace = output / 'multi-prompt-trace.log'
    route_enabled = env.get('FX_EXPERIMENT_JEV_ROUTING') == '1'
    model = 'jev/auto' if route_enabled else 'moonshotai/kimi-k3'
    prompts = [
        f'In {work}, create parser.py exporting parse_records(text). Parse key=value records separated by commas or newlines, trim surrounding whitespace, and return a dict of string keys and values. Empty input returns an empty dict; a record without = raises ValueError. Duplicate keys currently use the last value. Implement and test this behavior. Then describe a concrete plan to reject duplicate keys in the next change, but do not implement that plan yet.',
        'Do it. Keep the other behavior and add tests.',
        'Use a subagent to add support for full-line comments beginning with # after optional whitespace, while preserving the validation and all other behavior. Then verify the result.',
    ]
    environment = {**env, 'FX_TRACE_LOG': str(trace), 'FX_TRACE_SCOPES': 'quality,agent,subagent', 'FX_MODEL': model}
    result = {'kind':'multi-prompt-diagnostic-v1', 'routingEnabled':route_enabled, 'prompts':prompts, 'turns':[]}
    session = None
    for phase, prompt in enumerate(prompts, 1):
        command = [str(binary), 'ask', '--json', '--yolo', '--model', model, '--effort', 'high', '--no-fast']
        if session: command += ['--resume-id',session]
        command += ['--',prompt]
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(*command, cwd=work, env=environment, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        if processes is not None: processes[session_key] = process
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 240)
        except (TimeoutError, asyncio.CancelledError) as error:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            await process.wait()
            if isinstance(error, asyncio.CancelledError): raise
            raise RuntimeError('Multi-prompt diagnostic timed out') from None
        finally:
            if processes is not None: processes.pop(session_key, None)
        (output / f'multi-prompt-{phase}-fx.json').write_bytes(stdout)
        (output / f'multi-prompt-{phase}-stderr.log').write_bytes(stderr)
        envelope = {}
        for line in stdout.splitlines():
            try:
                candidate = json.loads(line)
                if isinstance(candidate,dict) and candidate.get('session_id'): envelope = candidate
            except ValueError: pass
        session = envelope.get('session_id',session)
        verifier = await asyncio.create_subprocess_exec(sys.executable, '-c', CHECKS, str(work), str(phase), cwd=work, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            verify_stdout, verify_stderr = await asyncio.wait_for(verifier.communicate(), 20)
        except TimeoutError:
            verifier.kill(); await verifier.wait()
            verify_stdout, verify_stderr = b'', b'Verifier timed out'
        result['turns'].append({'phase':phase,'model':envelope.get('model'),'sessionId':session,'exitCode':process.returncode,'qualityPassed':verifier.returncode==0,'elapsedSeconds':time.monotonic()-started,'usage':envelope.get('usage'),'verifierDiagnostic':verify_stderr.decode('utf-8','replace')[-2000:]})
        (output/'multi-prompt-probe.json').write_text(json.dumps(result,indent=2))
        if process.returncode != 0 or not session: break
    result['allQualityChecksPassed'] = len(result['turns'])==3 and all(t['qualityPassed'] for t in result['turns'])
    (output/'multi-prompt-probe.json').write_text(json.dumps(result,indent=2))
    return result
