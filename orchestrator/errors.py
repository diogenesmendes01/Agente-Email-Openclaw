"""Typed errors so the job queue knows whether to retry or fail-fast.

Usage:
    try:
        await some_external_call()
    except Exception as e:
        raise classify_exception(e) from e
"""
from __future__ import annotations

import asyncio
import json


class RetryableError(Exception):
    """Erro transitorio - job queue deve retentar.

    Exemplos: timeout de rede, 5xx, rate limit, DB temporariamente off.
    """


class FatalError(Exception):
    """Erro permanente - retry nao vai resolver, marcar job como failed.

    Exemplos: JSON malformado, schema violado, KeyError, 4xx (exceto 429).
    """


def classify_exception(exc: BaseException) -> Exception:
    """Mapeia uma excecao generica para Retryable ou Fatal.

    Se ja for um dos tipos, devolve sem wrap. Default conservador
    para excecoes desconhecidas: FatalError (evita retry em loop de
    bug desconhecido).
    """
    # Ja tipada
    if isinstance(exc, (RetryableError, FatalError)):
        return exc  # type: ignore[return-value]

    # Network / IO transientes
    try:
        import httpx
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
            return RetryableError(str(exc))
        if isinstance(exc, httpx.HTTPStatusError):
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None) if response is not None else None
            if status is None:
                return RetryableError(f"HTTP (no response): {exc}")
            if status >= 500 or status == 429:
                return RetryableError(f"HTTP {status}: {exc}")
            return FatalError(f"HTTP {status}: {exc}")
    except ImportError:
        pass

    # Pydantic schema validation (v2: ValidationError does NOT subclass ValueError)
    try:
        import pydantic
        if isinstance(exc, pydantic.ValidationError):
            return FatalError(f"pydantic.ValidationError: {exc}")
    except ImportError:
        pass

    # Gmail API
    try:
        from googleapiclient.errors import HttpError as GApiError
        if isinstance(exc, GApiError):
            status = getattr(exc, "status_code", None)
            if status is None and hasattr(exc, "resp"):
                status = getattr(exc.resp, "status", None)
            if status is not None:
                try:
                    status_int = int(status)
                except (TypeError, ValueError):
                    status_int = 0
                if status_int >= 500 or status_int == 429:
                    return RetryableError(f"Gmail API {status}: {exc}")
                if status_int > 0:
                    return FatalError(f"Gmail API {status}: {exc}")
            return FatalError(f"Gmail API: {exc}")
    except ImportError:
        pass

    # asyncpg
    try:
        import asyncpg
        if isinstance(exc, asyncpg.PostgresConnectionError):
            return RetryableError(f"Postgres connection: {exc}")
        if isinstance(exc, (asyncpg.UniqueViolationError, asyncpg.ForeignKeyViolationError)):
            return FatalError(f"Postgres constraint: {exc}")
    except ImportError:
        pass

    # asyncio
    if isinstance(exc, asyncio.TimeoutError):
        return RetryableError("asyncio timeout")

    # Programming errors / data errors
    if isinstance(exc, (json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError, ValueError)):
        return FatalError(f"{type(exc).__name__}: {exc}")

    # Default conservador: Fatal (evita queima de quota em bug desconhecido)
    return FatalError(f"Unclassified: {type(exc).__name__}: {exc}")
