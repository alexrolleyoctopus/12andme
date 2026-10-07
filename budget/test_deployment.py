"""Check the opt-in trust boundary used by the private Caddy deployment."""

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.middleware.security import SecurityMiddleware
from django.http import HttpResponse
from config.security import client_ip


class CaddyDeploymentTests(SimpleTestCase):
    @override_settings(TRUST_CADDY=False)
    def test_direct_requests_cannot_choose_their_ip(self):
        request = RequestFactory().get(
            "/", REMOTE_ADDR="192.168.1.20", HTTP_X_12ANDME_CLIENT_IP="192.168.1.99"
        )
        self.assertEqual(client_ip(request), "192.168.1.20")

    @override_settings(TRUST_CADDY=True)
    def test_private_proxy_header_identifies_device(self):
        request = RequestFactory().get(
            "/", REMOTE_ADDR="172.20.0.2", HTTP_X_12ANDME_CLIENT_IP="192.168.1.20"
        )
        self.assertEqual(client_ip(request), "192.168.1.20")

    @override_settings(TRUST_CADDY=True)
    def test_invalid_proxy_ip_falls_back_to_peer(self):
        request = RequestFactory().get(
            "/", REMOTE_ADDR="172.20.0.2", HTTP_X_12ANDME_CLIENT_IP="not-an-address"
        )
        self.assertEqual(client_ip(request), "172.20.0.2")

    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ALLOWED_HOSTS=["budget.home"],
    )
    def test_https_from_caddy_does_not_redirect_forever(self):
        middleware = SecurityMiddleware(lambda request: HttpResponse("ok"))
        request = RequestFactory().get(
            "/", HTTP_HOST="budget.home", HTTP_X_FORWARDED_PROTO="https"
        )
        self.assertEqual(middleware(request).status_code, 200)
        request = RequestFactory().get("/", HTTP_HOST="budget.home")
        response = middleware(request)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://budget.home/")
