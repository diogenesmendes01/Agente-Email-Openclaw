import pytest


def test_retryable_is_exception():
    from orchestrator.errors import RetryableError
    e = RetryableError("transient")
    assert isinstance(e, Exception)
    assert str(e) == "transient"


def test_fatal_is_exception():
    from orchestrator.errors import FatalError
    e = FatalError("bad data")
    assert isinstance(e, Exception)


def test_retryable_and_fatal_are_distinct():
    from orchestrator.errors import RetryableError, FatalError
    assert not issubclass(RetryableError, FatalError)
    assert not issubclass(FatalError, RetryableError)


def test_classify_exception_known_retryable():
    from orchestrator.errors import classify_exception, RetryableError
    import httpx
    assert isinstance(classify_exception(httpx.TimeoutException("t")), RetryableError)
    assert isinstance(classify_exception(httpx.ConnectError("c")), RetryableError)


def test_classify_exception_known_fatal():
    import json
    from orchestrator.errors import classify_exception, FatalError
    assert isinstance(classify_exception(json.JSONDecodeError("x", "y", 0)), FatalError)
    assert isinstance(classify_exception(KeyError("missing")), FatalError)


def test_classify_passes_through_already_typed():
    from orchestrator.errors import classify_exception, RetryableError, FatalError
    rt = RetryableError("x")
    ft = FatalError("y")
    assert classify_exception(rt) is rt
    assert classify_exception(ft) is ft


def test_classify_unknown_exception_defaults_fatal():
    from orchestrator.errors import classify_exception, FatalError
    class WeirdError(Exception):
        pass
    result = classify_exception(WeirdError("x"))
    assert isinstance(result, FatalError)


def test_classify_googleapi_5xx_retryable():
    """Gmail HTTP 503 -> Retryable."""
    from orchestrator.errors import classify_exception, RetryableError
    from googleapiclient.errors import HttpError

    # Build a fake HttpError. The constructor signature is HttpError(resp, content).
    class FakeResp:
        status = 503
        reason = "Service Unavailable"

    err = HttpError(FakeResp(), b"server error")
    result = classify_exception(err)
    assert isinstance(result, RetryableError)


def test_classify_googleapi_404_fatal():
    """Gmail HTTP 404 -> Fatal."""
    from orchestrator.errors import classify_exception, FatalError
    from googleapiclient.errors import HttpError

    class FakeResp:
        status = 404
        reason = "Not Found"

    err = HttpError(FakeResp(), b"not found")
    result = classify_exception(err)
    assert isinstance(result, FatalError)


def test_classify_pydantic_validation_error_fatal():
    """Pydantic ValidationError -> Fatal."""
    from orchestrator.errors import classify_exception, FatalError
    from pydantic import BaseModel, ValidationError as PydanticVE

    class M(BaseModel):
        x: int

    try:
        M(x="not a number")
    except PydanticVE as e:
        result = classify_exception(e)
        assert isinstance(result, FatalError)
    else:
        pytest.fail("ValidationError was not raised")
