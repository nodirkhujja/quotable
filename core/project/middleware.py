"""Project-wide middleware.

MediaAwareSecurityMiddleware
    Django's SecurityMiddleware adds `X-Content-Type-Options: nosniff` to
    every response. Safari refuses to play video <source> elements when
    that header is set unless the MIME type matches *exactly*, which fails
    intermittently for media served by Django's dev server (and even some
    production setups). Symptom: video shows the loading spinner forever
    on first load, but plays after a refresh because the file is cached
    and Safari trusts it on the second pass.

    This middleware strips `X-Content-Type-Options` and `X-Frame-Options`
    from any response served under `MEDIA_URL`, so Safari's media engine
    can sniff types and play normally.
"""

from django.conf import settings


class MediaAwareSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # MEDIA_URL like "/media/" → match path prefix "/media"
        prefix = (settings.MEDIA_URL or "/media/").rstrip("/")
        self._media_prefix = prefix + "/"

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(self._media_prefix):
            # Headers that break Safari video playback / inline media
            for header in (
                "X-Content-Type-Options",
                "X-Frame-Options",
                "Cross-Origin-Opener-Policy",
                "Cross-Origin-Embedder-Policy",
                "Cross-Origin-Resource-Policy",
            ):
                if header in response:
                    del response[header]
        return response
