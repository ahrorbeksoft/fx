import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fx_jev_agent.routing import POLICY, choice, select


class AdapterTests(unittest.TestCase):
    def test_rejects_invalid_probabilities(self):
        for p in [float('nan'), -1, 2, True]:
            with self.assertRaises(ValueError):
                choice({'type': 'choice', 'choice': 'a', 'probabilities': {'a': p}}, ['a'])

    def test_explicit_and_capability_constraints(self):
        self.assertEqual(select(None, requirements={'explicitModel': POLICY['defaultModel']})['reason'], 'explicit_model')
        with self.assertRaises(ValueError):
            select(None, requirements={'contextTokens': 2_000_000})

    def test_acp_initialization_over_stdio(self):
        request = {'jsonrpc':'2.0', 'id':1, 'method':'initialize', 'params':{'protocolVersion':1, 'clientCapabilities':{}}}
        result = subprocess.run([sys.executable, '-m', 'fx_jev_agent'], input=json.dumps(request)+'\n', text=True, capture_output=True, cwd=ROOT, timeout=10, env={**os.environ, 'PYTHONPATH':str(ROOT)})
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(response['result']['agentInfo']['name'], 'fx-jev-matrix')
        self.assertNotIn('error', response)


if __name__ == '__main__':
    unittest.main()
