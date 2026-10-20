"""A tiny recorded Prometheus, shared by the client and CLI tests.

Routes by endpoint, query text, and step, and answers from the fixture
files in tests/fixtures/.
"""

import json
import urllib.parse
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def fake_transport(url):
    parts = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parts.query)
    query = params["query"][0]
    if parts.path.endswith("/api/v1/query"):
        if 'resource="cpu"' in query:
            return _load("prom_requests_cpu.json")
        return _load("prom_requests_mem.json")
    step = params["step"][0]
    if "container_cpu_usage_seconds_total" in query:
        return _load("prom_cpu_24h.json" if step == "300" else "prom_cpu_7d.json")
    return _load("prom_mem_24h.json" if step == "300" else "prom_mem_7d.json")


def empty_requests_transport(url):
    parts = urllib.parse.urlparse(url)
    if parts.path.endswith("/api/v1/query"):
        return _load("prom_requests_empty.json")
    return fake_transport(url)
