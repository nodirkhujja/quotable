from django.urls import path

from . import views

app_name = "clips"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("watch/<int:source_id>/", views.watch_source, name="watch_source"),
    path("quote/<int:pk>/", views.QuoteDetailView.as_view(), name="quote_detail"),
    path("history/save/", views.save_watch_history, name="history_save"),
    path("test/", views.ui_test, name="ui_test"),
]
