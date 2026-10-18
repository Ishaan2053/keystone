"""The api container gets the LLM keys from .env or the shell (issue #69).

.env is excluded from the image, so Compose's `environment` is the only way in.
"""

import json
import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")

ROOT = os.path.dirname(os.path.dirname(__file__))
KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY")


def api_environment(tmp_path, **values):
    # An empty --env-file keeps a developer's real .env out of the render.
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    # Keep the inherited env (Windows needs it to find the compose plugin), but drop
    # COMPOSE_* so an exported COMPOSE_FILE or COMPOSE_PROFILES can't change the render.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in KEYS and not k.upper().startswith("COMPOSE_")
    }
    env.update(FALKORDB_PASSWORD="x", API_DOMAIN="api.example.invalid", **values)
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["api"]["environment"]


def test_llm_keys_reach_the_api_container(tmp_path):
    environment = api_environment(
        tmp_path, GEMINI_API_KEY="SENTINEL-GEMINI", GROQ_API_KEY="SENTINEL-GROQ"
    )
    assert environment["GEMINI_API_KEY"] == "SENTINEL-GEMINI"
    assert environment["GROQ_API_KEY"] == "SENTINEL-GROQ"


def test_llm_keys_default_to_empty(tmp_path):
    environment = api_environment(tmp_path)
    assert environment["GEMINI_API_KEY"] == ""
    assert environment["GROQ_API_KEY"] == ""
