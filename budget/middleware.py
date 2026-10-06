"""Response headers for pages containing private household information."""

from django.utils.cache import add_never_cache_headers


class PrivatePagesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/static/"):
            add_never_cache_headers(response)
        return response
