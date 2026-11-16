import sys
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_llm import create_app  # noqa: E402
from scholardesk import RunLog, ScholarDesk, llm  # noqa: E402


def make_desk(tmp_path, **fake_options):
    fake = create_app(**fake_options)
    llm.client_factory = lambda: AsyncOpenAI(
        api_key="test", base_url="http://fake/v1",
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=fake), base_url="http://fake"))
    return ScholarDesk(RunLog(tmp_path / "log.jsonl"))


@pytest.fixture()
def desk(tmp_path):
    yield make_desk(tmp_path, delay=0.02)
    llm.client_factory = None


@pytest.fixture()
def make(tmp_path):
    yield lambda **o: make_desk(tmp_path, **o)
    llm.client_factory = None
