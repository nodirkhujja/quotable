from django.contrib import admin

from clips.models import Favorite, Quote, Source

admin.site.register(Source)
admin.site.register(Quote)
admin.site.register(Favorite)
