from django.urls import path

from . import views

app_name = "learning"

urlpatterns = [
    # FAVORITES
    path("favorites/", views.favorite_list, name="favorite-list"),
    path("favorites/<int:quote_id>/toggle/", views.favorite_toggle, name="favorite-toggle"),
    path("favorites/<int:quote_id>/update/", views.favorite_update, name="favorite-update"),
    # MASTERY & SPACED REPETITION
    path("mastery/<int:quote_id>/", views.mastery_status, name="mastery-status"),
    path("mastery/<int:quote_id>/update/", views.mastery_update, name="mastery-update"),
    # WORD NOTES (CRUD)
    path("words/", views.word_note_list, name="word-list"),
    path("words/quote/<int:quote_id>/", views.word_note_create, name="word-create"),
    path("words/<int:note_id>/update/", views.word_note_update, name="word-update"),
    path("words/<int:note_id>/delete/", views.word_note_delete, name="word-delete"),
    # REVIEW SYSTEM
    path("review/queue/", views.review_queue, name="review-queue"),
]
