"""Phase 8 tests: the Streamlit UI, driven headlessly via `streamlit.testing.v1.AppTest`.

Runs the real app against the real store, same convention as `tests/test_chat_service.py`
and `tests/test_retriever.py`: this project's central claim is that the demo works, so
these tests drive the actual page rather than a mock of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from src.chat_service import EXAMPLE_QUESTIONS
from src.errors import RetrievalError
from src.guards.copy import AMFI_INVESTOR_EDUCATION_URL, DISCLAIMER

APP_PATH = str(Path(__file__).resolve().parent.parent / "src" / "app.py")
TIMEOUT = 60


@pytest.fixture(autouse=True)
def _clear_resource_cache() -> None:
    """Clear `app.py`'s `@st.cache_resource`-wrapped warm-up before every test.

    `st.cache_resource` caches its return value process-wide, keyed by the
    function's own code -- not per `AppTest` run, so a prior test's real,
    successful warm-up (chunk count 30) is still returned to a later test that
    expects a mocked, empty store, silently ignoring that test's monkeypatch
    entirely. Clearing it here trades a little speed (each test now genuinely
    re-warms) for every test being isolated from every other.
    """
    st.cache_resource.clear()


def _run() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=TIMEOUT)
    at.run()
    return at


def _metric_values(at: AppTest) -> list[int]:
    # AppTest's Metric.value is a string (it mirrors the rendered display text).
    return [int(el.value) for el in at.sidebar if type(el).__name__ == "Metric"]


class TestInitialLoad:
    def test_no_exception_on_first_load(self) -> None:
        at = _run()
        assert not at.exception

    def test_welcome_and_disclaimer_are_visible(self) -> None:
        at = _run()
        assert any("expense ratio" in c.value for c in at.caption)
        assert any(i.value == DISCLAIMER for i in at.info)

    def test_three_example_buttons_and_a_reset_button_exist(self) -> None:
        at = _run()
        labels = [b.label for b in at.button]
        for question in EXAMPLE_QUESTIONS:
            assert question in labels
        assert "Reset conversation" in labels

    def test_sidebar_lists_all_five_schemes(self) -> None:
        at = _run()
        sidebar_text = " ".join(
            el.value for el in at.sidebar if hasattr(el, "value") and isinstance(el.value, str)
        )
        for name in (
            "HDFC Large Cap",
            "HDFC Small Cap",
            "HDFC ELSS Tax Saver",
            "HDFC Equity (Flexi Cap)",
            "HDFC Balanced Advantage",
        ):
            assert name in sidebar_text


class TestExampleButtons:
    @pytest.mark.parametrize("index", range(len(EXAMPLE_QUESTIONS)))
    def test_clicking_an_example_produces_one_cited_answer(self, index: int) -> None:
        at = _run()
        at.button[index].click().run()
        assert not at.exception

        assistant = at.chat_message[-1]
        children = list(assistant.children.values())
        info_values = [c.value for c in children if type(c).__name__ == "Info"]
        caption_values = [c.value for c in children if type(c).__name__ == "Caption"]
        assert len(info_values) == 1
        assert info_values[0].startswith("Source: https://groww.in/")
        assert any(v.startswith("Last updated from sources:") for v in caption_values)


class TestChatInputRouting:
    def test_advice_question_shows_amfi_link_and_no_citation(self) -> None:
        at = _run()
        at.chat_input[0].set_value("Should I buy HDFC Large Cap?").run()
        assert not at.exception

        assistant = at.chat_message[-1]
        children = list(assistant.children.values())
        markdown_text = next(c.value for c in children if type(c).__name__ == "Markdown")
        assert "groww.in" not in markdown_text
        caption_values = [c.value for c in children if type(c).__name__ == "Caption"]
        assert any(AMFI_INVESTOR_EDUCATION_URL in v for v in caption_values)

    def test_pii_question_never_echoes_the_pan(self) -> None:
        at = _run()
        at.chat_input[0].set_value("My PAN is ABCDE1234F").run()
        assert not at.exception

        assistant = at.chat_message[-1]
        markdown_text = next(
            c.value for c in assistant.children.values() if type(c).__name__ == "Markdown"
        )
        assert "ABCDE1234F" not in markdown_text
        assert "PAN" in markdown_text  # the generic warning, not the value

    def test_nonsense_question_shows_the_no_answer_message(self) -> None:
        at = _run()
        at.chat_input[0].set_value("Tell me about the fund manager's education background.").run()
        assert not at.exception

        assistant = at.chat_message[-1]
        markdown_text = next(
            c.value for c in assistant.children.values() if type(c).__name__ == "Markdown"
        )
        assert "don't have that" in markdown_text


class TestUserTextIsEscapedNotInjected:
    def test_markdown_link_and_emphasis_syntax_is_neutralized(self) -> None:
        at = _run()
        injected = "[click here](http://evil.example) **bold** _em_"
        at.chat_input[0].set_value(injected).run()
        assert not at.exception

        user_turn = at.chat_message[-2]
        rendered = next(
            c.value for c in user_turn.children.values() if type(c).__name__ == "Markdown"
        )
        # The literal characters survive; the markdown syntax does not parse as a
        # link or emphasis (a real link/bold run would not appear as a Markdown
        # element carrying these literal backslash-escaped characters).
        assert "\\[click here\\]" in rendered
        assert "\\*\\*bold\\*\\*" in rendered


class TestSidebarCounters:
    def test_counts_reflect_the_turn_that_was_just_submitted(self) -> None:
        # Regression test: the counters used to be computed before the current
        # turn was appended to session state, so they always lagged one turn
        # behind what the chat area showed.
        at = _run()
        at.chat_input[0].set_value("What is the exit load of HDFC Large Cap?").run()
        at.chat_input[0].set_value("Should I buy HDFC Large Cap?").run()
        at.chat_input[0].set_value("My PAN is ABCDE1234F").run()

        answered, other = _metric_values(at)
        assert answered == 1
        assert other == 2


class TestShowRetrievedChunksToggle:
    def test_expander_absent_by_default_present_when_checked(self) -> None:
        at = _run()
        at.chat_input[0].set_value("What is the exit load of HDFC Large Cap?").run()
        assistant = at.chat_message[-1]
        assert not any(
            type(c).__name__ == "Expander" for c in assistant.children.values()
        )

        at2 = _run()
        at2.checkbox[0].set_value(True).run()
        at2.chat_input[0].set_value("What is the exit load of HDFC Large Cap?").run()
        assistant2 = at2.chat_message[-1]
        expanders = [c for c in assistant2.children.values() if type(c).__name__ == "Expander"]
        assert len(expanders) == 1
        assert "hdfc-large-cap-direct-growth" in next(iter(expanders[0].children.values())).value


class TestResetConversation:
    def test_reset_clears_chat_history(self) -> None:
        at = _run()
        at.chat_input[0].set_value("What is the exit load of HDFC Large Cap?").run()
        assert len(at.chat_message) == 2

        reset_button = next(b for b in at.button if b.label == "Reset conversation")
        reset_button.click().run()
        assert len(at.chat_message) == 0


class TestMissingIngestState:
    def test_shows_setup_panel_instead_of_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Deliberately mocked at the function level rather than by pointing
        # `config.CHROMA_COLLECTION` at a name that doesn't exist yet: `store.
        # get_collection` is `get_or_create_collection`, so retrieving against a
        # not-yet-existing name silently *creates* it -- a first version of this
        # test did exactly that and left a stray empty collection permanently on
        # disk in the real `data/chroma` store. Mocking `collection_exists` and
        # `retrieve` directly touches nothing on disk.
        monkeypatch.setattr("src.ingest.store.collection_exists", lambda: False)

        def _raise_retrieval_error(*_args: object, **_kwargs: object):
            raise RetrievalError("no vector store for this test")

        monkeypatch.setattr("src.chat_service.retrieve", _raise_retrieval_error)

        at = _run()
        assert not at.exception
        assert any("make ingest" in e.value for e in at.error)

        at.chat_input[0].set_value("What is the exit load of HDFC Large Cap?").run()
        assert not at.exception
        assistant = at.chat_message[-1]
        assert any(
            "make ingest" in c.value
            for c in assistant.children.values()
            if type(c).__name__ == "Error"
        )
