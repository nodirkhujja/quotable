import mimetypes
import os
import re

from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, HttpResponse
from django.urls import include, path


def ranged_media(request, path):
    """Serve media files with HTTP Range support (needed for video seeking).

    Safari-incompatible security headers (X-Content-Type-Options nosniff,
    X-Frame-Options, COOP/COEP/CORP) are stripped by MediaAwareSecurityMiddleware
    — see core/project/middleware.py.
    """
    full_path = os.path.join(settings.MEDIA_ROOT, path)
    if not os.path.isfile(full_path):
        raise Http404
    content_type, _ = mimetypes.guess_type(full_path)
    content_type = content_type or "application/octet-stream"
    file_size = os.path.getsize(full_path)

    # Cache headers help Safari trust the response and avoid re-fetching
    # the entire file on every seek.
    cache_headers = {
        "Cache-Control": "public, max-age=3600",
        "Vary": "Range",
    }

    range_header = request.META.get("HTTP_RANGE")
    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1
            with open(full_path, "rb") as f:
                f.seek(start)
                data = f.read(length)
            resp = HttpResponse(data, status=206, content_type=content_type)
            resp["Content-Length"] = length
            resp["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            resp["Accept-Ranges"] = "bytes"
            for k, v in cache_headers.items():
                resp[k] = v
            return resp

    f = open(full_path, "rb")  # FileResponse closes the file on response completion
    resp = FileResponse(f, content_type=content_type)
    resp["Content-Length"] = file_size
    resp["Accept-Ranges"] = "bytes"
    for k, v in cache_headers.items():
        resp[k] = v
    return resp


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),  # login, logout, signup, google oauth
    path("", include("clips.urls", namespace="clips")),
    path("", include("users.urls", namespace="users")),
    path("learning/", include("learning.urls", namespace="learning")),
]

if settings.DEBUG:
    media_url = settings.MEDIA_URL.lstrip("/")
    urlpatterns += [
        path(f"{media_url}<path:path>", ranged_media),
    ]
