from django.urls import path

from . import views

app_name = "learning"

urlpatterns = [
    # PRACTICE HUB
    path("practice/", views.PracticeHubView.as_view(), name="practice"),
    # AI SENTENCE CHECK (Gemini)
    path("ai/check-sentence/", views.AICheckSentenceView.as_view(), name="ai-check-sentence"),
    # INTEREST PROFILE (required after onboarding)
    path("interests/", views.InterestProfileView.as_view(), name="interests"),
    path("interests/save/", views.InterestSaveView.as_view(), name="interests-save"),
    # COACH
    path("coach/", views.CoachView.as_view(), name="coach"),
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
    # WORD BATTLE — single-word deep drill (stage-aware rounds)
    path("battle/<int:note_id>/", views.WordBattleDataView.as_view(), name="word-battle"),
    # DEEP LEARN — focused single-word study page (per-note, no time pressure).
    # Two routes share the same data: the full-page route (direct URL access)
    # and a `/fragment/` route that returns just the content partial. The
    # notebook overlay fetches the fragment and injects it on the same page,
    # which preserves the user's tap gesture for autoplay-with-sound.
    path("deep/<int:note_id>/", views.DeepLearnView.as_view(), name="deep-learn"),
    path("deep/<int:note_id>/fragment/", views.DeepLearnFragmentView.as_view(), name="deep-learn-fragment"),
    # SRS: schedule next review after Challenge completion
    path(
        "words/<int:note_id>/review-complete/", views.WordNoteReviewCompleteView.as_view(), name="word-review-complete"
    ),
    # REVIEW SYSTEM
    path("review/", views.ReviewPageView.as_view(), name="review"),
    path("review/queue/", views.ReviewQueueView.as_view(), name="review-queue"),
    # QUIZ
    path("streak/", views.StreakPageView.as_view(), name="streak"),
    path("collection/", views.CollectionPageView.as_view(), name="collection"),
    path("quiz/", views.QuizPageView.as_view(), name="quiz"),
    path("quiz/submit/", views.QuizSubmitView.as_view(), name="quiz-submit"),
    path("quiz/next/", views.QuizNextBatchView.as_view(), name="quiz-next"),
    # Free-production (T8): grade a learner's 2-3 sentences using a target word.
    # Frontend posts here for the AI grade, then submits via /quiz/submit/ for mastery.
    path(
        "quiz/free-production/grade/", views.QuizFreeProductionGradeView.as_view(), name="quiz-free-production-grade"
    ),
    # Mastered-word bridge — Uzbek → English production drill for words that
    # crossed the mastery threshold. Anti-context-lock pedagogy: each due
    # mastered word gets a fresh AI-generated prompt that REQUIRES the word.
    path("quiz/word-bridge/generate/", views.WordBridgeGenerateView.as_view(), name="word-bridge-generate"),
    path("quiz/word-bridge/check/", views.WordBridgeCheckView.as_view(), name="word-bridge-check"),
    # Continuity layer — "teacher voice" summary fetched at end-of-quiz.
    # Returns streak, today's saved sentences, words due tomorrow,
    # mastered-today count. Used to render the journey-style header
    # on the results screen.
    path("quiz/summary/", views.QuizSummaryView.as_view(), name="quiz-summary"),
    # Ready to Watch — comprehensible-input page (TEST). Surfaces
    # scenes the user can already understand (≥88% coverage). Page +
    # data endpoint are split: page is a thin shell, JS hits the data
    # endpoint after mount.
    path("ready-to-watch/", views.ReadyToWatchPageView.as_view(), name="ready-to-watch"),
    path("ready-to-watch/data/", views.ReadyToWatchDataView.as_view(), name="ready-to-watch-data"),
    # Scene Recall — voice-first production drill. Muted Friends scene +
    # Uzbek translation → user speaks the English word from memory.
    # Hits the same SR engine the quiz uses (compute_next_review) so
    # the two drills can't desync the review schedule.
    path("scene-recall/", views.SceneRecallPageView.as_view(), name="scene-recall"),
    path("scene-recall/data/", views.SceneRecallDataView.as_view(), name="scene-recall-data"),
    path("scene-recall/answer/", views.SceneRecallAnswerView.as_view(), name="scene-recall-answer"),
    # Notebook Companion — single contextual line at the top of /words/.
    # Reads state, returns ONE observation. The touch endpoint counts
    # passive replays as a review (perfect=True) and pushes next_review
    # forward via the same SR engine the quiz uses.
    path("words/companion/", views.WordNotebookCompanionView.as_view(), name="words-companion"),
    path(
        "words/companion/touch/<int:note_id>/",
        views.WordNotebookCompanionTouchView.as_view(),
        name="words-companion-touch",
    ),
    # Own the Word — focused acquisition mode (60-90s ritual on ONE word with
    # video + voice + 2-turn AI dialogue). Separate from the practice queue.
    path("own/", views.OwnTheWordPageView.as_view(), name="own-the-word"),
    path("own/grade/", views.OwnTheWordGradeView.as_view(), name="own-the-word-grade"),
    path("own/close/", views.OwnTheWordCloseView.as_view(), name="own-the-word-close"),
    # Contextual Production Challenge (AI scene + 4-axis voice/text grader).
    # Currently fired as the FIRST question on quiz page load (testing).
    path("quiz/scene/", views.QuizSceneView.as_view(), name="quiz-scene"),
    path("quiz/scene/check/", views.QuizSceneCheckView.as_view(), name="quiz-scene-check"),
    # "Now you · about your life" — Generation Effect turn. Saves the
    # learner's self-generated sentence to WordNote.usage_examples for
    # surfacing as a recall cue on the next encounter.
    path("quiz/scene/personal/", views.QuizPersonalSentenceView.as_view(), name="quiz-scene-personal"),
    # CONNECTOR QUIZ (sentence builder)
    path("quiz/connectors/", views.ConnectorQuizView.as_view(), name="connector-quiz"),
    # GRAMMAR
    path("grammar/", views.GrammarHubView.as_view(), name="grammar"),
    path("grammar/<int:unit_id>/", views.GrammarUnitView.as_view(), name="grammar-unit"),
    path("grammar/<int:unit_id>/explain/", views.GrammarExplainView.as_view(), name="grammar-explain"),
    path("grammar/<int:unit_id>/ai-questions/", views.GrammarAIQuestionsView.as_view(), name="grammar-ai-questions"),
    path("grammar/<int:unit_id>/check-bridge/", views.GrammarCheckBridgeView.as_view(), name="grammar-check-bridge"),
    path("grammar/<int:unit_id>/check-voice/", views.GrammarCheckVoiceView.as_view(), name="grammar-check-voice"),
    # SESSION TRACKING
    path("session/start/", views.SessionStartView.as_view(), name="session-start"),
    path("session/end/", views.SessionEndView.as_view(), name="session-end"),
    path("dictionary/", views.DictionaryLookupView.as_view(), name="dictionary"),
    path("translate/", views.translate_word, name="translate_word"),
    # PLACEMENT TEST (level assessment)
    path("onboarding/", views.OnboardingWelcomeView.as_view(), name="onboarding"),
    path("onboarding/placement/", views.PlacementSubmitView.as_view(), name="placement-submit"),
    # Legacy onboarding endpoints (old swipe-card SPA) — kept for now; may be removed.
    path("onboarding/data/", views.OnboardingDataView.as_view(), name="onboarding-data"),
    path("onboarding/progress/", views.OnboardingProgressView.as_view(), name="onboarding-progress"),
    path("onboarding/complete/", views.OnboardingCompleteView.as_view(), name="onboarding-complete"),
    path(
        "onboarding/grammar-complete/",
        views.OnboardingGrammarCompleteView.as_view(),
        name="onboarding-grammar-complete",
    ),
    path(
        "onboarding/phrases-complete/",
        views.OnboardingPhrasesCompleteView.as_view(),
        name="onboarding-phrases-complete",
    ),
    # SHADOWING
    path("shadowing/", views.ShadowingView.as_view(), name="shadowing"),
    # TRACKING LOGS
    path("shadow/log/", views.ShadowingLogView.as_view(), name="shadow-log"),
    path("pronunciation/assess/", views.PronunciationAssessView.as_view(), name="pronunciation-assess"),
    path("grammar/log/", views.GrammarPracticeLogView.as_view(), name="grammar-log"),
    path("watch/log/", views.WatchTimeLogView.as_view(), name="watch-log"),
    path("flashcard/log/", views.FlashcardLogView.as_view(), name="flashcard-log"),
    path("page/log/", views.PageVisitLogView.as_view(), name="page-log"),
    path("page/log/<int:visit_id>/", views.PageVisitUpdateView.as_view(), name="page-log-update"),
    # WATCH OVERLAY — learning context for the watch page (one fetch, zero clips coupling)
    path("watch-overlay/<int:source_id>/", views.WatchOverlayView.as_view(), name="watch-overlay"),
    # TTS
    path("tts/", views.TTSView.as_view(), name="tts"),
    # PROGRESS DASHBOARD
    path("progress/", views.ProgressView.as_view(), name="progress"),
    # DETAILED STATS
    path("stats/", views.StatsView.as_view(), name="stats"),
]
