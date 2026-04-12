"""Tests for CompanyService - fetches company profiles, clients, domain rules from Notion"""
import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
from orchestrator.services.company_service import CompanyService


@pytest.fixture
def company_svc():
    with patch.dict("os.environ", {
        "NOTION_API_KEY": "test-key",
        "NOTION_DB_COMPANY_PROFILES": "db-profiles-id",
        "NOTION_DB_CLIENTES": "db-clientes-id",
        "NOTION_DB_DOMAIN_RULES": "db-rules-id",
    }):
        svc = CompanyService()
        yield svc


class TestGetProfile:
    @pytest.mark.asyncio
    async def test_returns_profile_for_known_account(self, company_svc):
        """Should return company profile with clients and domain rules"""
        mock_profile_response = {
            "results": [{
                "id": "page-1",
                "properties": {
                    "Nome": {"title": [{"text": {"content": "Mendes Consultoria"}}]},
                    "Conta Email": {"email": "diogenes@empresa.com"},
                    "Setor": {"select": {"name": "Tecnologia"}},
                    "Tom": {"select": {"name": "profissional"}},
                    "Assinatura": {"rich_text": [{"text": {"content": "Att, Diogenes"}}]},
                    "Idioma Padrao": {"select": {"name": "pt-BR"}},
                }
            }]
        }
        mock_clients_response = {
            "results": [{
                "properties": {
                    "Nome": {"title": [{"text": {"content": "XYZ Corp"}}]},
                    "Contatos": {"rich_text": [{"text": {"content": "joao@xyz.com, maria@xyz.com"}}]},
                    "Projeto Ativo": {"rich_text": [{"text": {"content": "Migracao Cloud"}}]},
                    "Prioridade": {"select": {"name": "Alta"}},
                    "Notas": {"rich_text": [{"text": {"content": "Prazo junho"}}]},
                    "Company Profile": {"relation": [{"id": "page-1"}]},
                }
            }]
        }
        mock_rules_response = {
            "results": [{
                "properties": {
                    "Dominio": {"title": [{"text": {"content": "@pagar.me"}}]},
                    "Categoria": {"select": {"name": "financeiro"}},
                    "Prioridade Minima": {"select": {"name": "Alta"}},
                    "Acao Padrao": {"select": {"name": "notificar"}},
                    "Company Profile": {"relation": [{"id": "page-1"}]},
                }
            }]
        }

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "db-profiles-id" in url:
                resp.json.return_value = mock_profile_response
            elif "db-clientes-id" in url:
                resp.json.return_value = mock_clients_response
            elif "db-rules-id" in url:
                resp.json.return_value = mock_rules_response
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            profile = await company_svc.get_profile("diogenes@empresa.com")

        assert profile["nome"] == "Mendes Consultoria"
        assert profile["tom"] == "profissional"
        assert len(profile["clientes"]) == 1
        assert profile["clientes"][0]["nome"] == "XYZ Corp"
        assert len(profile["domain_rules"]) == 1
        assert profile["domain_rules"][0]["dominio"] == "@pagar.me"

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_account(self, company_svc):
        """Should return empty dict if account not found"""
        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"results": []}
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            profile = await company_svc.get_profile("unknown@test.com")

        assert profile == {}

    @pytest.mark.asyncio
    async def test_cache_returns_same_result(self, company_svc):
        """Second call within TTL should return cached result without API call"""
        company_svc._cache["test@test.com"] = (time.time(), {"nome": "Cached"})
        profile = await company_svc.get_profile("test@test.com")
        assert profile["nome"] == "Cached"


class TestDomainMatching:
    def test_exact_domain_match(self, company_svc):
        rules = [{"dominio": "@pagar.me", "categoria": "financeiro"}]
        match = company_svc.match_domain_rule("user@pagar.me", rules)
        assert match is not None
        assert match["categoria"] == "financeiro"

    def test_subdomain_match(self, company_svc):
        rules = [{"dominio": "@pagar.me", "categoria": "financeiro"}]
        match = company_svc.match_domain_rule("user@sub.pagar.me", rules)
        assert match is not None

    def test_no_match(self, company_svc):
        rules = [{"dominio": "@pagar.me", "categoria": "financeiro"}]
        match = company_svc.match_domain_rule("user@google.com", rules)
        assert match is None
