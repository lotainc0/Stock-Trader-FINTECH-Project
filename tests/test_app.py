"""Smoke test of the Streamlit app through Streamlit's own AppTest harness (no browser needed)."""
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_app_runs_an_analysis_offline():
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=300)
    at.run()
    assert not at.exception
    at.sidebar.text_input[0].set_value("MSFT")
    at.sidebar.selectbox[0].set_value("sample")      # data source
    at.sidebar.button[0].click()
    at.run()
    assert not at.exception, at.exception
    text = " ".join(m.value for m in at.markdown) + " ".join(str(m.value) for m in at.metric)
    assert "Lighthouse score" in text or "Strong Buy" in text or "Buy" in text
