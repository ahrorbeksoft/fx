"""Synthetic process fixture checks the diagnostic harness, not model quality."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fx_jev_agent.multi_prompt_probe import run


class ProbeTest(unittest.TestCase):
    def test_probe_preserves_failed_grades_and_resumes_the_same_session(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            stub=root/'fx-fixture'
            stub.write_text('#!' + sys.executable + '\n' + '''
import json
from pathlib import Path
import sys
log=Path('calls.json'); calls=json.loads(log.read_text()) if log.exists() else []
calls.append(sys.argv[1:]); log.write_text(json.dumps(calls))
# Deliberately wrong implementation: all verifier failures must remain visible.
Path('parser.py').write_text('def parse_records(text): return {}\\n')
print(json.dumps({'session_id':'same-session','model':'fixture','exit_code':0,'output':'fixture'}))
''')
            stub.chmod(0o700)
            result=asyncio.run(run(stub, {'PATH':os.environ.get('PATH','')},root))
            self.assertEqual(len(result['turns']),3)
            self.assertFalse(result['allQualityChecksPassed'])
            self.assertTrue(all(not turn['qualityPassed'] for turn in result['turns']))
            self.assertEqual({t['sessionId'] for t in result['turns']},{'same-session'})
            # Locate the recorded workspace from the first explicit prompt.
            work=Path(result['prompts'][0].split(', create parser.py')[0].removeprefix('In '))
            import shutil
            self.addCleanup(shutil.rmtree,work,True)
            calls=json.loads((work/'calls.json').read_text())
            self.assertNotIn('--resume-id',calls[0])
            for call in calls[1:]: self.assertEqual(call[call.index('--resume-id')+1],'same-session')


if __name__=='__main__': unittest.main()
