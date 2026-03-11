from django.shortcuts import redirect

# Paths that bypass the onboarding gate entirely
_EXEMPT_PREFIXES = (
    "/learning/onboarding",  # the assessment itself
    "/admin/",
    "/accounts/",  # login / logout / register
    "/static/",
    "/media/",
    "/__debug__/",  # django-debug-toolbar (if installed)
)


class OnboardingGateMiddleware:
    """
    Redirect authenticated users who haven't completed the vocabulary
    onboarding assessment to /learning/onboarding/ before they can
    access any other page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_redirect(request):
            return redirect("learning:onboarding")
        return self.get_response(request)

    # ── helpers ──────────────────────────────────────────────────────────

    def _should_redirect(self, request):
        # Only gate authenticated users
        if not request.user.is_authenticated:
            return False

        # Let exempt paths through immediately (no DB hit)
        path = request.path
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return False

        # Cache the result on the request object so multiple checks
        # within the same request cycle don't repeat the DB query.
        if not hasattr(request, "_onboarding_complete"):
            from learning.models import OnboardingSession  # lazy import avoids circular refs

            request._onboarding_complete = OnboardingSession.objects.filter(
                user=request.user,
                completed_at__isnull=False,
            ).exists()

        return not request._onboarding_complete
