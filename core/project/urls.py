from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

print(settings.DEBUG)
print(settings.SECRET_KEY)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("clips.urls", namespace="clips")),
    path("", include("users.urls", namespace="users")),
    path("learning/", include("learning.urls", namespace="learning")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
