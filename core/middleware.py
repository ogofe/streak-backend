from django.http import JsonResponse

from .observability import monotonic_time, record_request_exception, record_request_metric
from .tenant import TenantRequired, organization_context, resolve_organization


class RequestMetricsMiddleware:
    """Measure request latency and emit structured operational logs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = monotonic_time()
        try:
            response = self.get_response(request)
        except Exception as exc:
            record_request_exception(request, (monotonic_time() - started) * 1000, exc)
            raise
        duration_ms = (monotonic_time() - started) * 1000
        response["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        record_request_metric(request, response, duration_ms)
        return response


class TenantResolutionMiddleware:
    """Attach request.organization and pin Postgres RLS context per request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organization = resolve_organization(request)
        with organization_context(request.organization):
            try:
                return self.get_response(request)
            except TenantRequired as exc:
                return JsonResponse({"detail": str(exc)}, status=403)
