import json
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(__file__))


def compose_config(env, *extra_args):
    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", ""),
    }
    # Docker Desktop on Windows finds the compose CLI plugin under %ProgramFiles%;
    # without it `docker compose` is unknown and fails with "unknown flag: --format".
    if os.name == "nt":
        if os.environ.get("ProgramFiles"):
            clean_env["ProgramFiles"] = os.environ["ProgramFiles"]
        # Windows has no /dev/null; its empty-file device is `nul`.
        extra_args = [os.devnull if arg == "/dev/null" else arg for arg in extra_args]
    clean_env.update(env)
    return subprocess.run(
        ["docker", "compose", *extra_args, "config", "--format", "json"],
        cwd=ROOT,
        env=clean_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_missing_password_refuses_config():
    for value in (None, ""):
        env = {"API_DOMAIN": "api.example.invalid"}
        if value is None:
            env.pop("FALKORDB_PASSWORD", None)
        else:
            env["FALKORDB_PASSWORD"] = value

        result = compose_config(env, "--env-file", "/dev/null")

        assert result.returncode != 0
        assert "FALKORDB_PASSWORD" in (result.stderr + result.stdout)


def test_service_bindings_and_volumes():
    result = compose_config(
        {
            "FALKORDB_PASSWORD": "disposable-test-password",
            "API_DOMAIN": "api.example.invalid",
        }
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    services = config["services"]

    assert set(["falkordb", "api", "caddy"]).issubset(services)
    assert all(service.get("network_mode") != "host" for service in services.values())

    falkor_ports = services["falkordb"].get("ports", [])
    assert any(
        p.get("published") == "6379" and p.get("host_ip") == "127.0.0.1" for p in falkor_ports
    )
    assert any(
        p.get("published") == "3000" and p.get("host_ip") == "127.0.0.1" for p in falkor_ports
    )

    assert services["api"].get("ports") in (None, [])

    caddy_ports = services["caddy"].get("ports", [])
    assert any(p.get("published") == "80" and p.get("target") == 80 for p in caddy_ports)
    assert any(p.get("published") == "443" and p.get("target") == 443 for p in caddy_ports)

    falkor_mounts = services["falkordb"].get("volumes", [])
    assert any(m.get("target") == "/data" and m.get("type") == "volume" for m in falkor_mounts)

    caddy_mounts = services["caddy"].get("volumes", [])
    assert any(m.get("target") == "/data" and m.get("type") == "volume" for m in caddy_mounts)
    assert any(m.get("target") == "/config" and m.get("type") == "volume" for m in caddy_mounts)


def load_compose_config(password='p a$$;"quote\\slash', domain="api.example.invalid"):
    result = compose_config(
        {
            "FALKORDB_PASSWORD": password,
            "API_DOMAIN": domain,
        }
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_falkordb_auth_uses_config_file_not_split_prone_redis_args():
    password = 'p a$$;"quote\\slash'
    config = load_compose_config(password=password)
    services = config["services"]
    falkordb = services["falkordb"]

    assert falkordb["environment"]["REDIS_ARGS"] == "/data/redis.conf"
    assert password not in falkordb["environment"]["REDIS_ARGS"]
    assert services["falkordb-config"]["command"] == [
        "/bin/sh",
        "/usr/local/bin/write-falkordb-redis-conf.sh",
    ]
    assert password not in " ".join(services["falkordb-config"]["command"])
    assert (
        falkordb["depends_on"]["falkordb-config"]["condition"] == "service_completed_successfully"
    )


def test_config_writer_preserves_special_password_and_persistence_args(tmp_path):
    password = 'p a$$;"quote\\slash'
    script = os.path.join(ROOT, "scripts", "write-falkordb-redis-conf.sh")
    result = subprocess.run(
        # Windows has no /bin/sh; use the sh on PATH (Git for Windows ships one).
        [shutil.which("sh") if os.name == "nt" else "/bin/sh", script],
        env={
            "PATH": os.environ.get("PATH", ""),
            "FALKORDB_PASSWORD": password,
            "FALKORDB_CONFIG_DIR": str(tmp_path),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert password not in result.stdout + result.stderr
    redis_conf = (tmp_path / "redis.conf").read_text()
    assert 'requirepass "p a$$;\\"quote\\\\slash"' in redis_conf
    assert "appendonly yes" in redis_conf
    assert "appendfsync everysec" in redis_conf
    assert "dir /data" in redis_conf


def test_healthcheck_authenticates_without_shell_or_secret_echo():
    password = 'p a$$;"quote\\slash'
    config = load_compose_config(password=password)
    healthcheck = config["services"]["falkordb"]["healthcheck"]["test"]

    assert healthcheck == ["CMD", "redis-cli", "ping"]
    assert password not in " ".join(healthcheck)


def test_readme_unauthenticated_probe_removes_rediscli_auth():
    # The Compose checks moved from the README to docs/operations.md.
    readme = open(os.path.join(ROOT, "docs", "operations.md"), encoding="utf-8").read()

    assert 'env -u REDISCLI_AUTH redis-cli GRAPH.QUERY tenant_falkordb "RETURN 1"' in readme


def test_hosted_mcp_proxy_contract():
    with open(os.path.join(ROOT, "Caddyfile"), encoding="utf-8") as handle:
        caddyfile = handle.read()
    assert "reverse_proxy api:8000" in caddyfile
    # Caddy sets the one trusted header from the real socket peer on every
    # upstream request; a client-supplied value can never survive.
    assert "header_up X-Keystone-Client-IP {remote_host}" in caddyfile

    config = load_compose_config()
    services = config["services"]
    api = services["api"]

    # An explicit single-process uvicorn that never interprets proxy headers:
    # Caddy overwrites the client IP and is the only public ingress.
    command = api["command"]
    assert isinstance(command, list)
    assert command[0] == "uvicorn"
    assert "--no-proxy-headers" in command
    assert "--workers" in command and command[command.index("--workers") + 1] == "1"
    assert "--reload" not in command

    # Blank by default: every tenant in TENANTS is public; a value narrows it.
    assert api["environment"]["PUBLIC_MCP_TENANTS"] == ""

    # The API is internal-only; Caddy remains the sole published service.
    assert api.get("ports") in (None, [])
    assert "8000" in [str(entry) for entry in api.get("expose", [])]
    caddy_ports = services["caddy"].get("ports", [])
    assert any(p.get("published") == "80" for p in caddy_ports)
    assert any(p.get("published") == "443" for p in caddy_ports)
