"""Middleware for security."""
from collections import OrderedDict

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

# The CDN FastAPI loads the Swagger UI and ReDoc bundles from. Allowed as an origin
# rather than as pinned file URLs, so the policy can't silently break the docs the next
# time FastAPI bumps the bundle version (it has already moved swagger-ui-dist 4 -> 5).
DOCS_CDN = "https://cdn.jsdelivr.net"
CSP = {
    "default-src": "'self'",
    "img-src": [
        "*",
        # For SWAGGER UI
        "data:",
    ],
    "connect-src": "'self'",
    "script-src": ["'self'", DOCS_CDN],
    "style-src": ["'self'", "'unsafe-inline'", DOCS_CDN],
    # script-src-elem / style-src-elem OVERRIDE script-src / style-src for markup
    # elements rather than adding to them, so each has to repeat the full list. Both
    # docs UIs bootstrap from an inline <script>/<style>, hence 'unsafe-inline'.
    "script-src-elem": ["'self'", "'unsafe-inline'", DOCS_CDN],
    "style-src-elem": [
        "'self'",
        "'unsafe-inline'",
        DOCS_CDN,
        # For REDOC's google fonts
        "https://fonts.googleapis.com",
    ],
    "font-src": ["'self'", "https://fonts.gstatic.com"],
    # REDOC renders inside a web worker created from a blob URL.
    "worker-src": ["'self'", "blob:"],
    "frame-ancestors": "'none'",
}


def parse_policy(policy) -> str:
    """Parse a given policy dict to string."""
    if isinstance(policy, str):
        # parse the string into a policy dict
        policy_string = policy
        policy = OrderedDict()

        for policy_part in policy_string.split(";"):
            policy_parts = policy_part.strip().split(" ")
            policy[policy_parts[0]] = " ".join(policy_parts[1:])

    policies = []
    for section, content in policy.items():
        if not isinstance(content, str):
            content = " ".join(content)
        policy_part = f"{section} {content}"

        policies.append(policy_part)

    parsed_policy = "; ".join(policies)

    return parsed_policy


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    def __init__(self, app: FastAPI, csp: bool = True) -> None:
        """Init SecurityHeadersMiddleware.

        :param app: FastAPI instance
        :param csp: If a CSP header should be sent;
            defaults to :py:obj:`True`
        """
        super().__init__(app)
        self.csp = csp
        self.policy = parse_policy(CSP)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Dispatch of the middleware.

        :param request: Incoming request
        :param call_next: Function to process the request
        :return: Return response coming from from processed request
        """
        response = await call_next(request)
        if self.csp:
            response.headers["Content-Security-Policy"] = self.policy

        return response