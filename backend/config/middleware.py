import re


class BrowserSecurityHeadersMiddleware:
    """Add browser protections to Django responses, including API errors."""

    CONTENT_SECURITY_POLICY = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    PERMISSIONS_POLICY = "camera=(self), microphone=(), geolocation=()"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.CONTENT_SECURITY_POLICY)
        response.setdefault("Permissions-Policy", self.PERMISSIONS_POLICY)
        if request.path.startswith("/api/measurement-import/"):
            # Include errors produced outside the diagnostic API view.
            response["Cache-Control"] = "no-store"
        if re.fullmatch(r"/api/boxes/[0-9]+/history/preview/", request.path):
            # Cover native CSRF refusals that never reach APIView finalization.
            response["Cache-Control"] = "private, no-store"
        return response
