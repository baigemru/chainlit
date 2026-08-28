import os
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from litestar.exceptions import ClientException, HTTPException

from chainlit.oauth_providers import (
    ACCESS_TOKEN_MISSING,
    Auth0OAuthProvider,
    AWSCognitoOAuthProvider,
    DescopeOAuthProvider,
    GenericOAuthProvider,
    GithubOAuthProvider,
    GitlabOAuthProvider,
    GoogleOAuthProvider,
    KeycloakOAuthProvider,
    OAuthProvider,
    OktaOAuthProvider,
    get_configured_oauth_providers,
    get_oauth_provider,
)
from chainlit.user import User


class TestOAuthProviderBase:
    """Test suite for OAuthProvider base class."""

    def test_oauth_provider_has_required_methods(self):
        """Test that OAuthProvider defines required methods."""
        provider = OAuthProvider()

        # These should be methods
        assert hasattr(provider, "is_configured")
        assert hasattr(provider, "get_raw_token_response")
        assert hasattr(provider, "get_token")
        assert hasattr(provider, "get_user_info")
        assert hasattr(provider, "get_env_prefix")
        assert hasattr(provider, "get_prompt")

    def test_oauth_provider_is_configured_returns_false_when_env_missing(self):
        """Test is_configured returns False when environment variables are missing."""
        provider = OAuthProvider()
        provider.env = ["MISSING_VAR_1", "MISSING_VAR_2"]

        assert provider.is_configured() is False

    def test_oauth_provider_is_configured_returns_true_when_env_present(self):
        """Test is_configured returns True when all environment variables are present."""
        provider = OAuthProvider()
        provider.env = ["TEST_VAR_1", "TEST_VAR_2"]

        with patch.dict(os.environ, {"TEST_VAR_1": "value1", "TEST_VAR_2": "value2"}):
            assert provider.is_configured() is True

    def test_oauth_provider_get_env_prefix(self):
        """Test get_env_prefix converts id to uppercase with underscores."""
        provider = OAuthProvider()
        provider.id = "aws-cognito"

        assert provider.get_env_prefix() == "AWS_COGNITO"

    def test_oauth_provider_get_prompt_returns_provider_specific(self):
        """Test get_prompt returns provider-specific prompt."""
        provider = OAuthProvider()
        provider.id = "github"
        provider.default_prompt = None

        with patch.dict(os.environ, {"OAUTH_GITHUB_PROMPT": "consent"}):
            assert provider.get_prompt() == "consent"

    def test_oauth_provider_get_prompt_returns_global(self):
        """Test get_prompt returns global prompt when provider-specific not set."""
        provider = OAuthProvider()
        provider.id = "github"
        provider.default_prompt = None

        with patch.dict(os.environ, {"OAUTH_PROMPT": "select_account"}):
            assert provider.get_prompt() == "select_account"

    def test_oauth_provider_get_prompt_returns_default(self):
        """Test get_prompt returns default when no env vars set."""
        provider = OAuthProvider()
        provider.id = "github"
        provider.default_prompt = "login"

        assert provider.get_prompt() == "login"

    @pytest.mark.asyncio
    async def test_oauth_provider_abstract_methods_raise_not_implemented(self):
        """Test that abstract methods raise NotImplementedError."""
        provider = OAuthProvider()

        with pytest.raises(NotImplementedError):
            await provider.get_raw_token_response("code", "url")

        with pytest.raises(NotImplementedError):
            await provider.get_token("code", "url")

        with pytest.raises(NotImplementedError):
            await provider.get_user_info("token")


class TestGithubOAuthProvider:
    """Test suite for GithubOAuthProvider."""

    def test_github_provider_initialization(self):
        """Test GithubOAuthProvider initialization."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITHUB_CLIENT_ID": "test_client_id",
                "OAUTH_GITHUB_CLIENT_SECRET": "test_secret",
            },
        ):
            provider = GithubOAuthProvider()

            assert provider.id == "github"
            assert provider.client_id == "test_client_id"
            assert provider.client_secret == "test_secret"
            assert "scope" in provider.authorize_params
            assert provider.authorize_params["scope"] == "user:email"

    def test_github_provider_with_custom_urls(self):
        """Test GithubOAuthProvider with custom URLs."""
        # Need to set env vars before importing/instantiating since they're class-level
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITHUB_CLIENT_ID": "test_id",
                "OAUTH_GITHUB_CLIENT_SECRET": "test_secret",
                "OAUTH_GITHUB_AUTH_URL": "https://custom.github.com/oauth/authorize",
                "OAUTH_GITHUB_TOKEN_URL": "https://custom.github.com/oauth/token",
                "OAUTH_GITHUB_USER_INFO_URL": "https://custom.github.com/api/user",
            },
            clear=False,
        ):
            # Re-import to get the updated class-level attributes
            from importlib import reload

            import chainlit.oauth_providers as oauth_module

            reload(oauth_module)

            provider = oauth_module.GithubOAuthProvider()

            assert provider.authorize_url == "https://custom.github.com/oauth/authorize"
            assert provider.token_url == "https://custom.github.com/oauth/token"
            assert provider.user_info_url == "https://custom.github.com/api/user"

    @pytest.mark.asyncio
    async def test_github_get_raw_token_response_success(self):
        """Test GitHub get_raw_token_response with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITHUB_CLIENT_ID": "test_id",
                "OAUTH_GITHUB_CLIENT_SECRET": "test_secret",
            },
        ):
            provider = GithubOAuthProvider()

            mock_response = Mock()
            mock_response.text = "access_token=test_token&token_type=bearer"
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=mock_response
                )

                result = await provider.get_raw_token_response(
                    "test_code", "http://localhost"
                )

                assert "access_token" in result
                assert result["access_token"][0] == "test_token"

    @pytest.mark.asyncio
    async def test_github_get_token_success(self):
        """Test GitHub get_token with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITHUB_CLIENT_ID": "test_id",
                "OAUTH_GITHUB_CLIENT_SECRET": "test_secret",
            },
        ):
            provider = GithubOAuthProvider()

            mock_response = Mock()
            mock_response.text = "access_token=github_token_123&token_type=bearer"
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=mock_response
                )

                token = await provider.get_token("test_code", "http://localhost")

                assert token == "github_token_123"

    @pytest.mark.asyncio
    async def test_github_get_token_missing_access_token(self):
        """Test GitHub get_token raises error when access_token is missing."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITHUB_CLIENT_ID": "test_id",
                "OAUTH_GITHUB_CLIENT_SECRET": "test_secret",
            },
        ):
            provider = GithubOAuthProvider()

            mock_response = Mock()
            mock_response.text = "error=invalid_grant"
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=mock_response
                )

                with pytest.raises(ClientException) as exc_info:
                    await provider.get_token("test_code", "http://localhost")

                assert exc_info.value.status_code == 400
                assert ACCESS_TOKEN_MISSING in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_github_get_user_info_success(self):
        """Test GitHub get_user_info with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITHUB_CLIENT_ID": "test_id",
                "OAUTH_GITHUB_CLIENT_SECRET": "test_secret",
            },
        ):
            provider = GithubOAuthProvider()

            mock_user_response = Mock()
            mock_user_response.json.return_value = {
                "login": "testuser",
                "avatar_url": "https://github.com/avatar.png",
                "email": "test@example.com",
            }
            mock_user_response.raise_for_status = Mock()

            mock_emails_response = Mock()
            mock_emails_response.json.return_value = [
                {"email": "test@example.com", "primary": True, "verified": True}
            ]
            mock_emails_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_get = AsyncMock(
                    side_effect=[mock_user_response, mock_emails_response]
                )
                mock_client.return_value.__aenter__.return_value.get = mock_get

                github_user, user = await provider.get_user_info("test_token")

                assert github_user["login"] == "testuser"
                assert "emails" in github_user
                assert isinstance(user, User)
                assert user.identifier == "testuser"
                assert user.metadata["provider"] == "github"
                assert user.metadata["image"] == "https://github.com/avatar.png"


class TestGoogleOAuthProvider:
    """Test suite for GoogleOAuthProvider."""

    def test_google_provider_initialization(self):
        """Test GoogleOAuthProvider initialization."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GOOGLE_CLIENT_ID": "google_client_id",
                "OAUTH_GOOGLE_CLIENT_SECRET": "google_secret",
            },
        ):
            provider = GoogleOAuthProvider()

            assert provider.id == "google"
            assert provider.client_id == "google_client_id"
            assert provider.client_secret == "google_secret"
            assert (
                provider.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
            )
            assert "scope" in provider.authorize_params
            assert "userinfo.profile" in provider.authorize_params["scope"]

    @pytest.mark.asyncio
    async def test_google_get_token_success(self):
        """Test Google get_token with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GOOGLE_CLIENT_ID": "google_id",
                "OAUTH_GOOGLE_CLIENT_SECRET": "google_secret",
            },
        ):
            provider = GoogleOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "access_token": "google_access_token",
                "token_type": "Bearer",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=mock_response
                )

                token = await provider.get_token(
                    "auth_code", "http://localhost/callback"
                )

                assert token == "google_access_token"

    @pytest.mark.asyncio
    async def test_google_get_user_info_success(self):
        """Test Google get_user_info with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GOOGLE_CLIENT_ID": "google_id",
                "OAUTH_GOOGLE_CLIENT_SECRET": "google_secret",
            },
        ):
            provider = GoogleOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "email": "user@gmail.com",
                "name": "Test User",
                "picture": "https://google.com/photo.jpg",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                    return_value=mock_response
                )

                google_user, user = await provider.get_user_info("test_token")

                assert google_user["email"] == "user@gmail.com"
                assert isinstance(user, User)
                assert user.identifier == "user@gmail.com"
                assert user.metadata["provider"] == "google"
                assert user.metadata["image"] == "https://google.com/photo.jpg"


class TestOktaOAuthProvider:
    """Test suite for OktaOAuthProvider."""

    def test_okta_provider_initialization(self):
        """Test OktaOAuthProvider initialization."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_OKTA_CLIENT_ID": "okta_client_id",
                "OAUTH_OKTA_CLIENT_SECRET": "okta_secret",
                "OAUTH_OKTA_DOMAIN": "dev-12345.okta.com",
            },
            clear=False,
        ):
            from importlib import reload

            import chainlit.oauth_providers as oauth_module

            reload(oauth_module)

            provider = oauth_module.OktaOAuthProvider()

            assert provider.id == "okta"
            assert provider.client_id == "okta_client_id"
            assert "dev-12345.okta.com" in provider.authorize_url

    def test_okta_authorization_server_path_default(self):
        """Test Okta uses default authorization server."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_OKTA_CLIENT_ID": "okta_id",
                "OAUTH_OKTA_CLIENT_SECRET": "okta_secret",
                "OAUTH_OKTA_DOMAIN": "dev-12345.okta.com",
            },
        ):
            provider = OktaOAuthProvider()

            assert provider.get_authorization_server_path() == "/default"

    def test_okta_authorization_server_path_custom(self):
        """Test Okta uses custom authorization server."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_OKTA_CLIENT_ID": "okta_id",
                "OAUTH_OKTA_CLIENT_SECRET": "okta_secret",
                "OAUTH_OKTA_DOMAIN": "dev-12345.okta.com",
                "OAUTH_OKTA_AUTHORIZATION_SERVER_ID": "custom_server",
            },
        ):
            provider = OktaOAuthProvider()

            assert provider.get_authorization_server_path() == "/custom_server"

    def test_okta_authorization_server_path_false(self):
        """Test Okta with no authorization server."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_OKTA_CLIENT_ID": "okta_id",
                "OAUTH_OKTA_CLIENT_SECRET": "okta_secret",
                "OAUTH_OKTA_DOMAIN": "dev-12345.okta.com",
                "OAUTH_OKTA_AUTHORIZATION_SERVER_ID": "false",
            },
        ):
            provider = OktaOAuthProvider()

            assert provider.get_authorization_server_path() == ""


class TestAuth0OAuthProvider:
    """Test suite for Auth0OAuthProvider."""

    def test_auth0_provider_initialization(self):
        """Test Auth0OAuthProvider initialization."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_AUTH0_CLIENT_ID": "auth0_client_id",
                "OAUTH_AUTH0_CLIENT_SECRET": "auth0_secret",
                "OAUTH_AUTH0_DOMAIN": "dev-12345.auth0.com",
            },
        ):
            provider = Auth0OAuthProvider()

            assert provider.id == "auth0"
            assert provider.client_id == "auth0_client_id"
            assert "dev-12345.auth0.com" in provider.domain

    def test_auth0_with_original_domain(self):
        """Test Auth0 with separate original domain."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_AUTH0_CLIENT_ID": "auth0_id",
                "OAUTH_AUTH0_CLIENT_SECRET": "auth0_secret",
                "OAUTH_AUTH0_DOMAIN": "custom.domain.com",
                "OAUTH_AUTH0_ORIGINAL_DOMAIN": "dev-12345.auth0.com",
            },
        ):
            provider = Auth0OAuthProvider()

            assert "custom.domain.com" in provider.domain
            assert "dev-12345.auth0.com" in provider.original_domain


class TestGenericOAuthProvider:
    """Test suite for GenericOAuthProvider."""

    def test_generic_provider_initialization(self):
        """Test GenericOAuthProvider initialization."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GENERIC_CLIENT_ID": "generic_id",
                "OAUTH_GENERIC_CLIENT_SECRET": "generic_secret",
                "OAUTH_GENERIC_AUTH_URL": "https://auth.example.com/oauth/authorize",
                "OAUTH_GENERIC_TOKEN_URL": "https://auth.example.com/oauth/token",
                "OAUTH_GENERIC_USER_INFO_URL": "https://auth.example.com/oauth/userinfo",
                "OAUTH_GENERIC_SCOPES": "openid profile email",
            },
        ):
            provider = GenericOAuthProvider()

            assert provider.id == "generic"
            assert provider.client_id == "generic_id"
            assert provider.authorize_url == "https://auth.example.com/oauth/authorize"
            assert provider.token_url == "https://auth.example.com/oauth/token"
            assert provider.user_info_url == "https://auth.example.com/oauth/userinfo"

    def test_generic_provider_custom_name(self):
        """Test GenericOAuthProvider with custom name."""
        # Generic provider id is set at class definition time
        with patch.dict(
            os.environ,
            {
                "OAUTH_GENERIC_NAME": "my-custom-provider",
                "OAUTH_GENERIC_CLIENT_ID": "generic_id",
                "OAUTH_GENERIC_CLIENT_SECRET": "generic_secret",
                "OAUTH_GENERIC_AUTH_URL": "https://auth.example.com/oauth/authorize",
                "OAUTH_GENERIC_TOKEN_URL": "https://auth.example.com/oauth/token",
                "OAUTH_GENERIC_USER_INFO_URL": "https://auth.example.com/oauth/userinfo",
                "OAUTH_GENERIC_SCOPES": "openid profile",
            },
            clear=False,
        ):
            from importlib import reload

            import chainlit.oauth_providers as oauth_module

            reload(oauth_module)

            provider = oauth_module.GenericOAuthProvider()

            assert provider.id == "my-custom-provider"

    def test_generic_provider_custom_user_identifier(self):
        """Test GenericOAuthProvider with custom user identifier field."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GENERIC_CLIENT_ID": "generic_id",
                "OAUTH_GENERIC_CLIENT_SECRET": "generic_secret",
                "OAUTH_GENERIC_AUTH_URL": "https://auth.example.com/oauth/authorize",
                "OAUTH_GENERIC_TOKEN_URL": "https://auth.example.com/oauth/token",
                "OAUTH_GENERIC_USER_INFO_URL": "https://auth.example.com/oauth/userinfo",
                "OAUTH_GENERIC_SCOPES": "openid",
                "OAUTH_GENERIC_USER_IDENTIFIER": "username",
            },
        ):
            provider = GenericOAuthProvider()

            assert provider.user_identifier == "username"


class TestDescopeOAuthProvider:
    """Test suite for DescopeOAuthProvider."""

    def test_descope_provider_initialization(self):
        """Test DescopeOAuthProvider initialization."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_DESCOPE_CLIENT_ID": "descope_client_id",
                "OAUTH_DESCOPE_CLIENT_SECRET": "descope_secret",
            },
        ):
            provider = DescopeOAuthProvider()

            assert provider.id == "descope"
            assert provider.client_id == "descope_client_id"
            assert provider.client_secret == "descope_secret"
            assert "openid profile email" in provider.authorize_params["scope"]

    @pytest.mark.asyncio
    async def test_descope_get_token_success(self):
        """Test Descope get_token with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_DESCOPE_CLIENT_ID": "descope_id",
                "OAUTH_DESCOPE_CLIENT_SECRET": "descope_secret",
            },
        ):
            provider = DescopeOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "access_token": "descope_access_token",
                "token_type": "Bearer",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=mock_response
                )

                token = await provider.get_token(
                    "auth_code", "http://localhost/callback"
                )

                assert token == "descope_access_token"

    @pytest.mark.asyncio
    async def test_descope_get_user_info_success(self):
        """Test Descope get_user_info with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_DESCOPE_CLIENT_ID": "descope_id",
                "OAUTH_DESCOPE_CLIENT_SECRET": "descope_secret",
            },
        ):
            provider = DescopeOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "email": "user@descope.com",
                "name": "Descope User",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                    return_value=mock_response
                )

                descope_user, user = await provider.get_user_info("test_token")

                assert descope_user["email"] == "user@descope.com"
                assert isinstance(user, User)
                assert user.identifier == "user@descope.com"
                assert user.metadata["provider"] == "descope"


class TestAWSCognitoOAuthProvider:
    """Test suite for AWSCognitoOAuthProvider."""

    def test_cognito_provider_initialization(self):
        """Test AWSCognitoOAuthProvider initialization."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_COGNITO_CLIENT_ID": "cognito_client_id",
                "OAUTH_COGNITO_CLIENT_SECRET": "cognito_secret",
                "OAUTH_COGNITO_DOMAIN": "my-app.auth.us-east-1.amazoncognito.com",
            },
        ):
            provider = AWSCognitoOAuthProvider()

            assert provider.id == "aws-cognito"
            assert provider.client_id == "cognito_client_id"
            assert provider.client_secret == "cognito_secret"
            assert "openid profile email" in provider.scopes

    def test_cognito_provider_custom_scopes(self):
        """Test AWSCognitoOAuthProvider with custom scopes."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_COGNITO_CLIENT_ID": "cognito_id",
                "OAUTH_COGNITO_CLIENT_SECRET": "cognito_secret",
                "OAUTH_COGNITO_DOMAIN": "my-app.auth.us-east-1.amazoncognito.com",
                "OAUTH_COGNITO_SCOPE": "openid email phone",
            },
        ):
            provider = AWSCognitoOAuthProvider()

            assert provider.scopes == "openid email phone"
            assert provider.authorize_params["scope"] == "openid email phone"

    @pytest.mark.asyncio
    async def test_cognito_get_token_success(self):
        """Test Cognito get_token with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_COGNITO_CLIENT_ID": "cognito_id",
                "OAUTH_COGNITO_CLIENT_SECRET": "cognito_secret",
                "OAUTH_COGNITO_DOMAIN": "my-app.auth.us-east-1.amazoncognito.com",
            },
        ):
            provider = AWSCognitoOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "access_token": "cognito_access_token",
                "token_type": "Bearer",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=mock_response
                )

                token = await provider.get_token(
                    "auth_code", "http://localhost/callback"
                )

                assert token == "cognito_access_token"

    @pytest.mark.asyncio
    async def test_cognito_get_user_info_success(self):
        """Test Cognito get_user_info with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_COGNITO_CLIENT_ID": "cognito_id",
                "OAUTH_COGNITO_CLIENT_SECRET": "cognito_secret",
                "OAUTH_COGNITO_DOMAIN": "my-app.auth.us-east-1.amazoncognito.com",
            },
        ):
            provider = AWSCognitoOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "email": "user@cognito.com",
                "picture": "https://cognito.com/photo.jpg",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                    return_value=mock_response
                )

                cognito_user, user = await provider.get_user_info("test_token")

                assert cognito_user["email"] == "user@cognito.com"
                assert isinstance(user, User)
                assert user.identifier == "user@cognito.com"
                assert user.metadata["provider"] == "aws-cognito"
                assert user.metadata["image"] == "https://cognito.com/photo.jpg"


class TestGitlabOAuthProvider:
    """Test suite for GitlabOAuthProvider."""

    def test_gitlab_provider_initialization(self):
        """Test GitlabOAuthProvider initialization."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITLAB_CLIENT_ID": "gitlab_client_id",
                "OAUTH_GITLAB_CLIENT_SECRET": "gitlab_secret",
                "OAUTH_GITLAB_DOMAIN": "gitlab.example.com",
            },
        ):
            provider = GitlabOAuthProvider()

            assert provider.id == "gitlab"
            assert provider.client_id == "gitlab_client_id"
            assert provider.client_secret == "gitlab_secret"
            assert "gitlab.example.com" in provider.domain
            assert "openid profile email" in provider.authorize_params["scope"]

    def test_gitlab_provider_strips_trailing_slash(self):
        """Test GitlabOAuthProvider strips trailing slash from domain."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITLAB_CLIENT_ID": "gitlab_id",
                "OAUTH_GITLAB_CLIENT_SECRET": "gitlab_secret",
                "OAUTH_GITLAB_DOMAIN": "gitlab.example.com/",
            },
        ):
            provider = GitlabOAuthProvider()

            assert not provider.domain.endswith("/")

    @pytest.mark.asyncio
    async def test_gitlab_get_token_success(self):
        """Test Gitlab get_token with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITLAB_CLIENT_ID": "gitlab_id",
                "OAUTH_GITLAB_CLIENT_SECRET": "gitlab_secret",
                "OAUTH_GITLAB_DOMAIN": "gitlab.example.com",
            },
        ):
            provider = GitlabOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "access_token": "gitlab_access_token",
                "token_type": "Bearer",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=mock_response
                )

                token = await provider.get_token(
                    "auth_code", "http://localhost/callback"
                )

                assert token == "gitlab_access_token"

    @pytest.mark.asyncio
    async def test_gitlab_get_user_info_success(self):
        """Test Gitlab get_user_info with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITLAB_CLIENT_ID": "gitlab_id",
                "OAUTH_GITLAB_CLIENT_SECRET": "gitlab_secret",
                "OAUTH_GITLAB_DOMAIN": "gitlab.example.com",
            },
        ):
            provider = GitlabOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "email": "user@gitlab.com",
                "picture": "https://gitlab.com/avatar.png",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                    return_value=mock_response
                )

                gitlab_user, user = await provider.get_user_info("test_token")

                assert gitlab_user["email"] == "user@gitlab.com"
                assert isinstance(user, User)
                assert user.identifier == "user@gitlab.com"
                assert user.metadata["provider"] == "gitlab"
                assert user.metadata["image"] == "https://gitlab.com/avatar.png"


class TestKeycloakOAuthProvider:
    """Test suite for KeycloakOAuthProvider."""

    def test_keycloak_provider_initialization(self):
        """Test KeycloakOAuthProvider initialization."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_KEYCLOAK_CLIENT_ID": "keycloak_client_id",
                "OAUTH_KEYCLOAK_CLIENT_SECRET": "keycloak_secret",
                "OAUTH_KEYCLOAK_REALM": "my-realm",
                "OAUTH_KEYCLOAK_BASE_URL": "https://keycloak.example.com",
            },
        ):
            provider = KeycloakOAuthProvider()

            assert provider.client_id == "keycloak_client_id"
            assert provider.client_secret == "keycloak_secret"
            assert provider.realm == "my-realm"
            assert provider.base_url == "https://keycloak.example.com"
            assert "profile email openid" in provider.authorize_params["scope"]

    def test_keycloak_provider_custom_name(self):
        """Test KeycloakOAuthProvider with custom name."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_KEYCLOAK_NAME": "my-keycloak",
                "OAUTH_KEYCLOAK_CLIENT_ID": "keycloak_id",
                "OAUTH_KEYCLOAK_CLIENT_SECRET": "keycloak_secret",
                "OAUTH_KEYCLOAK_REALM": "my-realm",
                "OAUTH_KEYCLOAK_BASE_URL": "https://keycloak.example.com",
            },
            clear=False,
        ):
            from importlib import reload

            import chainlit.oauth_providers as oauth_module

            reload(oauth_module)

            provider = oauth_module.KeycloakOAuthProvider()

            assert provider.id == "my-keycloak"

    @pytest.mark.asyncio
    async def test_keycloak_get_token_success(self):
        """Test Keycloak get_token with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_KEYCLOAK_CLIENT_ID": "keycloak_id",
                "OAUTH_KEYCLOAK_CLIENT_SECRET": "keycloak_secret",
                "OAUTH_KEYCLOAK_REALM": "my-realm",
                "OAUTH_KEYCLOAK_BASE_URL": "https://keycloak.example.com",
            },
        ):
            provider = KeycloakOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "access_token": "keycloak_access_token",
                "refresh_token": "keycloak_refresh_token",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=mock_response
                )

                token = await provider.get_token(
                    "auth_code", "http://localhost/callback"
                )

                assert token == "keycloak_access_token"
                assert provider.refresh_token == "keycloak_refresh_token"

    @pytest.mark.asyncio
    async def test_keycloak_get_user_info_success(self):
        """Test Keycloak get_user_info with successful response."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_KEYCLOAK_CLIENT_ID": "keycloak_id",
                "OAUTH_KEYCLOAK_CLIENT_SECRET": "keycloak_secret",
                "OAUTH_KEYCLOAK_REALM": "my-realm",
                "OAUTH_KEYCLOAK_BASE_URL": "https://keycloak.example.com",
            },
        ):
            provider = KeycloakOAuthProvider()

            mock_response = Mock()
            mock_response.json.return_value = {
                "email": "user@keycloak.com",
                "name": "Keycloak User",
            }
            mock_response.raise_for_status = Mock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                    return_value=mock_response
                )

                keycloak_user, user = await provider.get_user_info("test_token")

                assert keycloak_user["email"] == "user@keycloak.com"
                assert isinstance(user, User)
                assert user.identifier == "user@keycloak.com"
                assert user.metadata["provider"] == "keycloak"


class TestHelperFunctions:
    """Test suite for helper functions."""

    def test_get_oauth_provider_returns_correct_provider(self):
        """Test get_oauth_provider returns the correct provider."""
        provider = get_oauth_provider("github")

        assert provider is not None
        assert provider.id == "github"

    def test_get_oauth_provider_returns_none_for_unknown(self):
        """Test get_oauth_provider returns None for unknown provider."""
        provider = get_oauth_provider("unknown_provider")

        assert provider is None

    def test_get_configured_oauth_providers_empty_when_none_configured(self):
        """Test get_configured_oauth_providers returns empty list when none configured."""
        # Clear all OAuth environment variables
        with patch.dict(os.environ, {}, clear=True):
            configured = get_configured_oauth_providers()

            assert configured == []

    def test_get_configured_oauth_providers_returns_configured(self):
        """Test get_configured_oauth_providers returns configured providers."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITHUB_CLIENT_ID": "github_id",
                "OAUTH_GITHUB_CLIENT_SECRET": "github_secret",
                "OAUTH_GOOGLE_CLIENT_ID": "google_id",
                "OAUTH_GOOGLE_CLIENT_SECRET": "google_secret",
            },
        ):
            configured = get_configured_oauth_providers()

            assert "github" in configured
            assert "google" in configured


class TestOAuthProviderEdgeCases:
    """Test suite for OAuth provider edge cases."""

    @pytest.mark.asyncio
    async def test_provider_handles_http_error(self):
        """Test provider handles HTTP errors gracefully."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GITHUB_CLIENT_ID": "test_id",
                "OAUTH_GITHUB_CLIENT_SECRET": "test_secret",
            },
        ):
            provider = GithubOAuthProvider()

            mock_response = Mock()
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Error", request=Mock(), response=Mock()
            )

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=mock_response
                )

                with pytest.raises(httpx.HTTPStatusError):
                    await provider.get_raw_token_response("code", "url")

    def test_provider_strips_trailing_slash_from_domain(self):
        """Test providers strip trailing slashes from domains."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_AUTH0_CLIENT_ID": "auth0_id",
                "OAUTH_AUTH0_CLIENT_SECRET": "auth0_secret",
                "OAUTH_AUTH0_DOMAIN": "dev-12345.auth0.com/",
            },
        ):
            provider = Auth0OAuthProvider()

            assert not provider.domain.endswith("/")

    def test_provider_with_prompt_parameter(self):
        """Test provider includes prompt parameter when configured."""
        with patch.dict(
            os.environ,
            {
                "OAUTH_GOOGLE_CLIENT_ID": "google_id",
                "OAUTH_GOOGLE_CLIENT_SECRET": "google_secret",
                "OAUTH_GOOGLE_PROMPT": "consent",
            },
        ):
            provider = GoogleOAuthProvider()

            assert "prompt" in provider.authorize_params
            assert provider.authorize_params["prompt"] == "consent"


class TestKeycloakDirectGrant:
    @pytest.fixture
    def keycloak_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OAUTH_KEYCLOAK_CLIENT_ID", "kc-client")
        monkeypatch.setenv("OAUTH_KEYCLOAK_CLIENT_SECRET", "kc-secret")
        monkeypatch.setenv("OAUTH_KEYCLOAK_REALM", "myrealm")
        monkeypatch.setenv("OAUTH_KEYCLOAK_BASE_URL", "https://kc.example.com")

    def test_registration_url_derived(self, keycloak_env):
        from chainlit.oauth_providers import KeycloakOAuthProvider

        provider = KeycloakOAuthProvider()
        assert provider.registration_url == (
            "https://kc.example.com/realms/myrealm/protocol/openid-connect/registrations"
        )

    def test_forgot_password_url_derived_when_enabled(
        self, keycloak_env, monkeypatch: pytest.MonkeyPatch
    ):
        from chainlit.oauth_providers import KeycloakOAuthProvider

        assert KeycloakOAuthProvider().forgot_password_url is None

        monkeypatch.setenv("OAUTH_KEYCLOAK_FORGOT_PASSWORD", "true")
        provider = KeycloakOAuthProvider()
        assert provider.forgot_password_url == (
            "https://kc.example.com/realms/myrealm/login-actions/reset-credentials"
            "?client_id=kc-client"
        )

    def test_direct_grant_flag(self, keycloak_env, monkeypatch: pytest.MonkeyPatch):
        from chainlit.oauth_providers import KeycloakOAuthProvider

        assert KeycloakOAuthProvider().is_direct_grant_enabled() is False

        monkeypatch.setenv("OAUTH_KEYCLOAK_DIRECT_GRANT", "true")
        assert KeycloakOAuthProvider().is_direct_grant_enabled() is True

    async def test_get_token_with_password_invalid_grant(
        self, keycloak_env, monkeypatch: pytest.MonkeyPatch
    ):
        import httpx

        from chainlit.oauth_providers import KeycloakOAuthProvider

        provider = KeycloakOAuthProvider()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid_grant"})

        transport = httpx.MockTransport(handler)
        original_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *args, **kwargs: original_client(transport=transport),
        )

        with pytest.raises(HTTPException) as exc_info:
            await provider.get_token_with_password("user", "bad")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "credentialssignin"

    async def test_get_token_with_password_success(
        self, keycloak_env, monkeypatch: pytest.MonkeyPatch
    ):
        import urllib.parse

        import httpx

        from chainlit.oauth_providers import KeycloakOAuthProvider

        provider = KeycloakOAuthProvider()
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode()
            seen["data"] = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
            return httpx.Response(200, json={"access_token": "ropc-token"})

        transport = httpx.MockTransport(handler)
        original_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *args, **kwargs: original_client(transport=transport),
        )

        token = await provider.get_token_with_password("user", "pw")

        assert token == "ropc-token"
        assert seen["data"]["grant_type"] == "password"
        assert seen["data"]["username"] == "user"
        assert seen["data"]["client_id"] == "kc-client"


class TestKeycloakDirectGrantErrors:
    """Error mapping of KeycloakOAuthProvider.get_token_with_password."""

    def _provider(self, monkeypatch: pytest.MonkeyPatch):
        from chainlit.oauth_providers import KeycloakOAuthProvider

        monkeypatch.setenv("OAUTH_KEYCLOAK_BASE_URL", "https://kc.example.com")
        monkeypatch.setenv("OAUTH_KEYCLOAK_REALM", "realm")
        monkeypatch.setenv("OAUTH_KEYCLOAK_CLIENT_ID", "client-id")
        monkeypatch.setenv("OAUTH_KEYCLOAK_CLIENT_SECRET", "client-secret")
        return KeycloakOAuthProvider()

    def _mock_token_response(self, monkeypatch, status_code, json_body):
        import httpx

        async def post(self, url, data=None, **kwargs):
            return httpx.Response(
                status_code,
                json=json_body,
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", post)

    async def test_pending_required_actions_map_to_accountnotsetup(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = self._provider(monkeypatch)
        self._mock_token_response(
            monkeypatch,
            400,
            {
                "error": "invalid_grant",
                "error_description": "Account is not fully set up",
            },
        )

        with pytest.raises(HTTPException) as exc_info:
            await provider.get_token_with_password("user@example.com", "pw")

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "accountnotsetup"

    async def test_wrong_credentials_still_map_to_credentialssignin(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = self._provider(monkeypatch)
        self._mock_token_response(
            monkeypatch,
            401,
            {
                "error": "invalid_grant",
                "error_description": "Invalid user credentials",
            },
        )

        with pytest.raises(HTTPException) as exc_info:
            await provider.get_token_with_password("user@example.com", "bad")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "credentialssignin"
