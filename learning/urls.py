from django.urls import path

from . import views

app_name = "learning"

urlpatterns = [
    # PRACTICE HUB
    path("practice/", views.PracticeHubView.as_view(), name="practice"),
    # FAVORITES
    path("favorites/", views.FavoriteListView.as_view(), name="favorite-list"),
    path("favorites/<int:quote_id>/toggle/", views.FavoriteToggleView.as_view(), name="favorite-toggle"),
    path("favorites/<int:quote_id>/update/", views.FavoriteUpdateView.as_view(), name="favorite-update"),
    # MASTERY & SPACED REPETITION
    path("mastery/<int:quote_id>/", views.MasteryStatusView.as_view(), name="mastery-status"),
    path("mastery/<int:quote_id>/update/", views.MasteryUpdateView.as_view(), name="mastery-update"),
    # WORD NOTES (CRUD)
    path("words/", views.WordNotePageView.as_view(), name="word_note_list"),
    path("words/quote/<int:quote_id>/", views.WordNoteCreateView.as_view(), name="word-create"),
    path(
        "words/transcript/<int:transcript_id>/",
        views.WordNoteCreateFromTranscriptView.as_view(),
        name="word-create-transcript",
    ),
    # DetailView ham update (PATCH), ham delete (DELETE) so'rovlarini qabul qiladi
    path("words/<int:note_id>/", views.WordNoteDetailView.as_view(), name="word-detail"),
    # REVIEW SYSTEM
    path("review/", views.ReviewPageView.as_view(), name="review"),
    path("review/queue/", views.ReviewQueueView.as_view(), name="review-queue"),
    # QUIZ
    path("quiz/", views.QuizPageView.as_view(), name="quiz"),
    # GRAMMAR
    path("grammar/", views.GrammarHubView.as_view(), name="grammar"),
    path("grammar/<int:unit_id>/", views.GrammarUnitView.as_view(), name="grammar-unit"),
    # SESSION TRACKING
    path("session/start/", views.SessionStartView.as_view(), name="session-start"),
    path("session/end/", views.SessionEndView.as_view(), name="session-end"),
    path("dictionary/", views.DictionaryLookupView.as_view(), name="dictionary"),
    path("translate/", views.translate_word, name="translate_word"),
    # VOCABULARY ONBOARDING
    path("onboarding/", views.OnboardingWelcomeView.as_view(), name="onboarding"),
    path("onboarding/data/", views.OnboardingDataView.as_view(), name="onboarding-data"),
    path("onboarding/progress/", views.OnboardingProgressView.as_view(), name="onboarding-progress"),
    path("onboarding/complete/", views.OnboardingCompleteView.as_view(), name="onboarding-complete"),
    # SHADOWING
    path("shadowing/", views.ShadowingView.as_view(), name="shadowing"),
    # PROGRESS DASHBOARD
    path("progress/", views.ProgressView.as_view(), name="progress"),
]
