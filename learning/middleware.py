from django.conf import settings
from django.shortcuts import redirect

# Paths that bypass the onboarding gate entirely
_EXEMPT_PREFIXES = (
    "/learning/onboarding",  # the assessment itself
    "/learning/interests",  # the interest profile form
    "/admin/",
    "/accounts/",  # login / logout / register
    "/static/",
    "/media/",
    "/__debug__/",  # django-debug-toolbar (if installed)
)

# Paths that don't require login at all (public / auth pages)
_PUBLIC_PREFIXES = (
    "/accounts/",
    "/admin/",  # Django admin has its own login form at /admin/login/
    "/static/",
    "/media/",
    "/__debug__/",
)


class OnboardingGateMiddleware:
    """
    Two-stage gate for authenticated users:
      1. Interest profile (→ /learning/interests/)
      2. Vocabulary onboarding assessment (→ /learning/onboarding/)
    Both required before accessing any other page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        redirect_target = self._redirect_target(request)
        if redirect_target:
            return redirect(redirect_target)
        return self.get_response(request)

    # ── helpers ──────────────────────────────────────────────────────────

    def _redirect_target(self, request):
        path = request.path

        # Unauthenticated users: must log in (except on public paths)
        if not request.user.is_authenticated:
            if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
                return None
            return settings.LOGIN_URL or "/accounts/login/"

        # Authenticated: let exempt paths through
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return None

        # Lazy imports to avoid circular refs
        from learning.models import OnboardingSession, UserInterest

        # Stage 1 — interest profile
        if not hasattr(request, "_interest_complete"):
            request._interest_complete = UserInterest.objects.filter(
                user=request.user,
            ).exists()
        if not request._interest_complete:
            return "learning:interests"

        # Stage 2 — vocab onboarding (level assessment)
        if not hasattr(request, "_onboarding_complete"):
            request._onboarding_complete = OnboardingSession.objects.filter(
                user=request.user,
                completed_at__isnull=False,
            ).exists()
        if not request._onboarding_complete:
            return "learning:onboarding"

        return None
