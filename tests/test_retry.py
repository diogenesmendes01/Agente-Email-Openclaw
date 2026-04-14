import pytest
from tenacity import retry, stop_after_attempt, wait_none, retry_if_exception_type


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_retries_on_timeout(self):
        """Verify that a tenacity-decorated function retries on TimeoutError."""
        call_count = 0

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_none(),
            retry=retry_if_exception_type(TimeoutError),
            reraise=True,
        )
        async def flaky_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("timeout")
            return "success"

        result = await flaky_call()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_does_not_retry_on_value_error(self):
        """Non-retryable exceptions should propagate immediately."""
        call_count = 0

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_none(),
            retry=retry_if_exception_type(TimeoutError),
            reraise=True,
        )
        async def bad_call():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await bad_call()
        assert call_count == 1  # no retry
