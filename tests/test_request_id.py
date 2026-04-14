import pytest
from orchestrator.middleware.request_id import get_request_id, request_id_var


class TestRequestIdMiddleware:
    def test_request_id_var_default_is_dash(self):
        """Without middleware, request_id should be '-'."""
        assert request_id_var.get("-") == "-"

    @pytest.mark.asyncio
    async def test_middleware_sets_request_id(self):
        """Middleware should set a request ID in the context var."""
        from orchestrator.middleware.request_id import RequestIdMiddleware
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport

        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)

        @app.get("/test")
        async def test_endpoint():
            rid = get_request_id()
            return {"request_id": rid}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test")
            data = response.json()
            assert len(data["request_id"]) == 8
            assert data["request_id"] != "-"
            # Also check X-Request-ID header
            assert "x-request-id" in response.headers
