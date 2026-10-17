"""The api service persists api/llm.py's response cache on the host (issue #70)."""
import json
import os

from tests.test_infra import compose_config


def test_api_mounts_llm_cache_read_write():
    env = {'FALKORDB_PASSWORD': 'disposable-test-password', 'API_DOMAIN': 'api.example.invalid'}
    # Docker Desktop on Windows finds the compose CLI plugin under %ProgramFiles%.
    if os.environ.get('ProgramFiles'):
        env['ProgramFiles'] = os.environ['ProgramFiles']
    result = compose_config(env)
    assert result.returncode == 0, result.stderr
    api = json.loads(result.stdout)['services']['api']

    mounts = [m for m in api.get('volumes', []) if m.get('target') == '/app/data/llm_cache']
    assert len(mounts) == 1, api.get('volumes')
    mount = mounts[0]
    assert mount['type'] == 'bind'
    assert os.path.normcase(mount['source']).endswith(os.path.normcase(os.path.join('data', 'llm_cache')))
    assert not mount.get('read_only', False)
