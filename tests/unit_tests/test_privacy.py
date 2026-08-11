"""Tests for prompt and tracing boundaries."""

from __future__ import annotations

import pytest

from mortgage_servicing_dashboard.privacy import (
    ApprovedPrompt,
    DataClassification,
    PromptBoundary,
    SensitiveContentError,
    UnsafeTracingConfigurationError,
    assert_remote_tracing_disabled,
    build_privacy_middleware,
)


def test_public_prompt_is_approved_without_content_in_repr() -> None:
    boundary = PromptBoundary(max_chars=100, secret_environment={})
    prompt = boundary.approve(
        "Summarize the public product guide.",
        classification=DataClassification.PUBLIC,
    )

    assert prompt.text == "Summarize the public product guide."
    assert prompt.classification is DataClassification.PUBLIC
    assert prompt.text not in repr(prompt)


def test_boundary_requires_positive_size_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        PromptBoundary(max_chars=0, secret_environment={})


def test_approved_prompt_cannot_bypass_boundary() -> None:
    with pytest.raises(TypeError, match=r"PromptBoundary\.approve"):
        ApprovedPrompt("unsafe", DataClassification.PUBLIC)


@pytest.mark.parametrize(
    "sensitive_text",
    [
        pytest.param(
            "SYNTHETIC reserved email fixture@example.test",
            id="synthetic-reserved-email",
        ),
        pytest.param(
            "SYNTHETIC invalid SSN 000-12-3456",
            id="synthetic-invalid-ssn",
        ),
        pytest.param(
            "SYNTHETIC reserved phone 212-555-0199",
            id="synthetic-reserved-phone",
        ),
        pytest.param(
            "SYNTHETIC invalid account 0000000000",
            id="synthetic-invalid-account",
        ),
        pytest.param(
            "SYNTHETIC TEST-NET host 192.0.2.1",
            id="synthetic-test-net-ip",
        ),
        pytest.param(
            "SYNTHETIC api_key=SYNTHETIC_NOT_A_CREDENTIAL",
            id="synthetic-credential-assignment",
        ),
        pytest.param(
            "SYNTHETIC Bearer SYNTHETIC_NOT_A_TOKEN",
            id="synthetic-bearer-token",
        ),
        pytest.param(
            "SYNTHETIC invalid -----BEGIN PRIVATE KEY-----",
            id="synthetic-private-key-marker",
        ),
        pytest.param(
            "SYNTHETIC reserved URL https://example.test/path?token=SYNTHETIC_NOT_A_TOKEN",
            id="synthetic-secret-url",
        ),
    ],
)
def test_sensitive_patterns_are_rejected_without_echo(sensitive_text: str) -> None:
    boundary = PromptBoundary(max_chars=200, secret_environment={})

    with pytest.raises(SensitiveContentError) as error:
        boundary.approve(sensitive_text, classification=DataClassification.SYNTHETIC)

    assert sensitive_text not in str(error.value)


def test_environment_secret_is_rejected_without_echo() -> None:
    secret = "SYNTHETIC_TEST_SECRET_NOT_VALID"
    boundary = PromptBoundary(
        max_chars=100,
        secret_environment={"PROVIDER_API_KEY": secret},
    )

    with pytest.raises(SensitiveContentError, match="environment_secret") as error:
        boundary.approve(
            f"SYNTHETIC accidental secret {secret}",
            classification=DataClassification.SYNTHETIC,
        )

    assert secret not in str(error.value)


def test_empty_oversized_and_control_character_inputs_are_rejected() -> None:
    boundary = PromptBoundary(max_chars=5, secret_environment={})

    for text in (" ", "sixsix", "safe\x00"):
        with pytest.raises(SensitiveContentError):
            boundary.approve(text, classification=DataClassification.PUBLIC)


def test_classification_must_use_enum() -> None:
    boundary = PromptBoundary(max_chars=100, secret_environment={})
    with pytest.raises(TypeError, match="classification"):
        boundary.approve("safe", classification="public")  # type: ignore[arg-type]


def test_remote_tracing_is_fail_closed() -> None:
    for environment in (
        {"LANGSMITH_TRACING": "true"},
        {"LANGCHAIN_TRACING": "1"},
        {"LANGCHAIN_TRACING_V2": "yes"},
    ):
        with pytest.raises(UnsafeTracingConfigurationError):
            assert_remote_tracing_disabled(environment)


def test_false_tracing_values_are_allowed() -> None:
    assert_remote_tracing_disabled(
        {
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING": "0",
            "LANGCHAIN_TRACING_V2": "off",
        }
    )


def test_langchain_privacy_middleware_covers_multiple_surfaces() -> None:
    middleware = build_privacy_middleware()
    assert len(middleware) >= 8
    assert all(item.apply_to_input for item in middleware)
    assert all(item.apply_to_output for item in middleware)
    assert all(item.apply_to_tool_results for item in middleware)
