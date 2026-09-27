"""Tests for the pre-retrieval guardrail layer (implementation.md Phase 5).

The two properties that matter more than routing accuracy are asserted directly rather
than inferred:

* **no PII value escapes** -- no refusal message, link, or logged line may contain the
  matched substring (INV-3 / FR-6);
* **no PII message is logged at all** -- not even truncated.
"""

from __future__ import annotations

import logging

import pytest

from src.guards import copy
from src.guards.policy import (
    ADVICE_PATTERNS,
    GRIEVANCE_PATTERNS,
    KIND_ADVICE,
    KIND_GRIEVANCE,
    KIND_PII,
    KIND_RETURNS,
    PII_PATTERNS,
    RETURNS_PATTERNS,
    GuardDecision,
    _detect_pii,
    check_query,
)

# Fake identifiers used only in this file. The repo-wide check in Phase 5's
# Verification asserts these never appear outside tests/.
FAKE_PAN = "ABCDE1234F"
FAKE_AADHAAR = "234567890123"
FAKE_EMAIL = "jane.doe@example.com"
FAKE_PHONE = "+91 98765 43210"
FAKE_OTP = "482913"
FAKE_ACCOUNT = "50100234567890"

PII_SECRETS = (FAKE_PAN, FAKE_AADHAAR, FAKE_EMAIL, FAKE_PHONE, FAKE_OTP, FAKE_ACCOUNT)


class TestPiiDetection:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            (f"My PAN is {FAKE_PAN}", "pan"),
            (f"aadhaar number {FAKE_AADHAAR}", "aadhaar"),
            (f"aadhaar {FAKE_AADHAAR[:4]} {FAKE_AADHAAR[4:8]} {FAKE_AADHAAR[8:]}", "aadhaar"),
            (f"mail me at {FAKE_EMAIL}", "email"),
            (f"call {FAKE_PHONE}", "phone"),
            ("my otp is 482913", "otp"),
            ("one time password 739104", "otp"),
            (f"account number is {FAKE_ACCOUNT}", "account_number"),
            ("ifsc HDFC0000123", "account_number"),
            ("folio 123456789", "account_number"),
        ],
    )
    def test_it_labels_the_kind(self, query: str, expected: str) -> None:
        assert expected in _detect_pii(query)

    def test_the_plan_example_is_pii(self) -> None:
        assert check_query(f"My PAN is {FAKE_PAN} and Aadhaar {FAKE_AADHAAR}").kind == KIND_PII

    def test_labels_only_no_values(self) -> None:
        labels = _detect_pii(f"My PAN is {FAKE_PAN} and Aadhaar {FAKE_AADHAAR}")
        assert set(labels) <= {label for label, _ in PII_PATTERNS}
        assert not any(secret in " ".join(labels) for secret in PII_SECRETS)

    def test_a_query_with_two_kinds_reports_both(self) -> None:
        labels = _detect_pii(f"my pan {FAKE_PAN}, email {FAKE_EMAIL}")
        assert {"pan", "email"} <= set(labels)

    @pytest.mark.parametrize(
        "query",
        [
            "What is the exit load?",
            "expense ratio of hdfc equity fund",
            "What is the minimum SIP amount?",
            "What is the lock in period for ELSS?",
            "What is the NAV of HDFC Large Cap?",
            # The plan's phone pattern also matches any ISO or DD-MM-YYYY date, because a
            # date is 8 digits and 2 separators inside its character class. A date is not
            # PII, and this corpus publishes fetched_at dates, so these must be allowed.
            "What was the exit load as on 2026-09-27?",
            "exit load as of 15-08-2026",
            "as on 27/09/2026 what was the NAV",
        ],
    )
    def test_no_false_positive_on_factual_queries(self, query: str) -> None:
        assert _detect_pii(query) == []
        assert check_query(query).allowed


class TestIntentRouting:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            (f"My PAN is {FAKE_PAN} and Aadhaar {FAKE_AADHAAR}", KIND_PII),
            ("Should I buy HDFC Large Cap?", KIND_ADVICE),
            ("Which is the best performing ELSS?", KIND_RETURNS),
            ("What is the 1 year return?", KIND_RETURNS),
            ("How do I raise a grievance with SEBI?", KIND_GRIEVANCE),
            ("What is the exit load?", None),
            ("expense ratio of hdfc equity fund", None),
        ],
    )
    def test_the_plans_table_routes_as_specified(self, query: str, expected: str | None) -> None:
        decision = check_query(query)
        assert decision.kind == expected
        assert decision.allowed is (expected is None)

    @pytest.mark.parametrize(
        "query",
        [
            "Should I buy HDFC Large Cap?",
            "which fund should i choose",
            "best mutual fund for long term",
            "is it a good time to buy",
            "can you recommend a scheme",
            "suggest a fund for me",
            "is HDFC Small Cap suitable for me",
            "can i sell my ELSS now",
            "is it worth investing in large cap",
        ],
    )
    def test_advice_phrasings(self, query: str) -> None:
        assert check_query(query).kind == KIND_ADVICE

    @pytest.mark.parametrize(
        "query",
        [
            "What is the 1 year return?",
            "what is the cagr of HDFC Large Cap",
            "tell me the xirr",
            "how has HDFC Flexi Cap performed",
            "how much did I earn in returns",
            "which fund performed best",
            "show me the ranking of large cap funds",
            "top performing HDFC scheme",
            "since inception return of ELSS",
            "compare the returns of large cap and flexi cap",
        ],
    )
    def test_returns_phrasings(self, query: str) -> None:
        assert check_query(query).kind == KIND_RETURNS

    @pytest.mark.parametrize(
        "query",
        [
            "How do I raise a grievance with SEBI?",
            "I want to file a complaint",
            "who is the regulator for this",
            "my refund was reversed",
            "my money is stuck",
            "I want to raise a chargeback",
            "is this a legal matter",
        ],
    )
    def test_grievance_phrasings(self, query: str) -> None:
        assert check_query(query).kind == KIND_GRIEVANCE

    @pytest.mark.parametrize(
        "query",
        [
            "What is the exit load?",
            "expense ratio of hdfc equity fund",
            "What is the minimum SIP amount?",
            "What is the lock in period for ELSS?",
            "What is the NAV of HDFC Large Cap?",
            "What is the exit load of HDFC Small Cap?",
            "benchmark of hdfc balanced advantage",
            "risk level of HDFC Large Cap",
            "minimum investment for ELSS",
        ],
    )
    def test_factual_queries_are_allowed(self, query: str) -> None:
        decision = check_query(query)
        assert decision.allowed
        assert decision.kind is None
        assert decision.message is None
        assert decision.link_url is None

    def test_advice_wins_over_returns_when_both_match(self) -> None:
        # "Should I buy the best performing ELSS?" matches both gates. The documented
        # order is advice first, and it is the safer of the two answers to show.
        assert check_query("Should I buy the best performing ELSS?").kind == KIND_ADVICE

    def test_returns_wins_over_grievance_when_both_match(self) -> None:
        assert check_query("What are the returns? I want to complain to SEBI").kind == (
            KIND_RETURNS
        )

    def test_pii_wins_over_every_intent(self) -> None:
        # The one ordering that is a safety property rather than a convention: a message
        # containing a PAN must never be answered with copy that invites more typing.
        query = f"Should I buy HDFC Large Cap? My PAN is {FAKE_PAN}"
        decision = check_query(query)
        assert decision.kind == KIND_PII
        assert decision.message == copy.PII_MESSAGE
        assert "AMFI" not in decision.message


class TestRefusalCopy:
    @pytest.mark.parametrize(
        ("query", "url", "label"),
        [
            ("Should I buy HDFC Large Cap?", copy.AMFI_INVESTOR_EDUCATION_URL,
             copy.AMFI_INVESTOR_EDUCATION_LABEL),
            ("How do I raise a grievance with SEBI?", copy.SEBI_SCORES_URL,
             copy.SEBI_SCORES_LABEL),
        ],
    )
    def test_links_are_attached(self, query: str, url: str, label: str) -> None:
        decision = check_query(query)
        assert decision.link_url == url
        assert decision.link_label == label

    def test_a_returns_refusal_links_the_named_scheme_page(self) -> None:
        decision = check_query("What is the 1 year return of HDFC ELSS Tax Saver?")
        assert decision.kind == KIND_RETURNS
        assert decision.link_url is not None
        assert decision.link_url.startswith("https://groww.in/")

    def test_a_returns_refusal_without_a_scheme_has_no_fabricated_link(self) -> None:
        decision = check_query("What is the 1 year return?")
        assert decision.kind == KIND_RETURNS
        assert decision.link_url is None
        assert "http" not in decision.message

    def test_a_pii_refusal_has_no_link_and_no_interpolation(self) -> None:
        decision = check_query(f"My PAN is {FAKE_PAN}")
        assert decision.link_url is None
        assert decision.message == copy.PII_MESSAGE
        assert "{" not in decision.message and "}" not in decision.message

    def test_every_refusal_message_is_non_empty(self) -> None:
        for query in ("Should I buy?", "1 year return?", "raise a complaint",
                      f"my pan is {FAKE_PAN}"):
            assert check_query(query).message


class TestNoPiiLeak:
    """The INV-3 property: a PII value must not be observable anywhere."""

    @pytest.mark.parametrize(
        "query",
        [
            f"My PAN is {FAKE_PAN} and Aadhaar {FAKE_AADHAAR}",
            f"mail me at {FAKE_EMAIL}",
            f"call {FAKE_PHONE}",
            "my otp is 482913",
            f"account number is {FAKE_ACCOUNT}",
            f"Should I buy HDFC Large Cap? My PAN is {FAKE_PAN}",
        ],
    )
    def test_no_secret_appears_in_the_decision(self, query: str) -> None:
        decision = check_query(query)
        rendered = " ".join(
            str(part) for part in (decision.kind, decision.message, decision.link_url,
                                   decision.link_label)
        )
        for secret in PII_SECRETS:
            assert secret not in rendered

    @pytest.mark.parametrize(
        "query",
        [
            f"My PAN is {FAKE_PAN} and Aadhaar {FAKE_AADHAAR}",
            f"mail me at {FAKE_EMAIL}",
            f"call {FAKE_PHONE}",
        ],
    )
    def test_no_secret_appears_in_the_logs(
        self, query: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="mf_rag.guards"):
            check_query(query)
        joined = "\n".join(record.getMessage() for record in caplog.records)
        for secret in PII_SECRETS:
            assert secret not in joined

    def test_a_pii_log_line_names_the_labels_and_nothing_else(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="mf_rag.guards"):
            check_query(f"My PAN is {FAKE_PAN} and Aadhaar {FAKE_AADHAAR}")
        joined = "\n".join(record.getMessage() for record in caplog.records)
        # Every matching class is listed, so the spaced Aadhaar is reported as both
        # "aadhaar" and "phone". Over-reporting a class is the safe direction here.
        labels = joined.split("pii_detected=")[-1]
        assert {"aadhaar", "pan", "phone"} == set(labels.split(","))
        assert "MY PAN" not in joined.upper().replace("PII_DETECTED", "")

    def test_a_non_pii_log_line_may_carry_a_short_prefix(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="mf_rag.guards"):
            check_query("What is the exit load of HDFC Large Cap?")
        joined = "\n".join(record.getMessage() for record in caplog.records)
        assert "allowed" in joined
        assert len(joined.split("query_prefix=")[-1]) <= 64


class TestGuardDecisionContract:
    def test_an_allowed_decision_has_no_kind(self) -> None:
        assert check_query("exit load?").kind is None

    @pytest.mark.parametrize("bad", [{"allowed": True, "kind": KIND_ADVICE}])
    def test_allowed_with_a_kind_is_rejected(self, bad: dict) -> None:
        with pytest.raises(ValueError):
            GuardDecision(**bad)

    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            GuardDecision(allowed=False, kind="banana", message="hi")

    def test_a_refusal_without_a_message_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            GuardDecision(allowed=False, kind=KIND_ADVICE)

    @pytest.mark.parametrize("text", ["", "   ", "\n\t "])
    def test_blank_input_is_allowed_not_an_error(self, text: str) -> None:
        # A chat turn must always have a reply; an empty box is Phase 7's "empty" case,
        # not a guard refusal.
        assert check_query(text).allowed


class TestPatternTables:
    @pytest.mark.parametrize(
        "table", [PII_PATTERNS, ADVICE_PATTERNS, RETURNS_PATTERNS, GRIEVANCE_PATTERNS]
    )
    def test_every_table_is_non_empty_and_compiled(self, table: tuple) -> None:
        assert table
        for entry in table:
            assert hasattr(entry, "search") or hasattr(entry[1], "search")

    def test_pii_table_is_label_pattern_pairs(self) -> None:
        for label, pattern in PII_PATTERNS:
            assert isinstance(label, str) and label.islower()
            assert hasattr(pattern, "search")

    @pytest.mark.parametrize("pattern", RETURNS_PATTERNS)
    def test_returns_patterns_do_not_match_a_plain_exit_load_question(
        self, pattern
    ) -> None:
        # Guards must not over-fire on the 9 factual sample questions.
        assert not pattern.search("what is the exit load of hdfc large cap")
