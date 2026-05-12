from django.urls import path

from . import views

app_name = "quiz"

urlpatterns = [
    path("quiz/", views.QuizPageView.as_view(), name="quiz"),
    path("quiz/submit/", views.QuizSubmitView.as_view(), name="quiz-submit"),
    path("quiz/next/", views.QuizNextBatchView.as_view(), name="quiz-next"),
    path("quiz/summary/", views.QuizSummaryView.as_view(), name="quiz-summary"),
    path(
        "quiz/free-production/grade/", views.QuizFreeProductionGradeView.as_view(), name="quiz-free-production-grade"
    ),
    path("quiz/word-bridge/generate/", views.WordBridgeGenerateView.as_view(), name="word-bridge-generate"),
    path("quiz/word-bridge/check/", views.WordBridgeCheckView.as_view(), name="word-bridge-check"),
    path("quiz/scene/", views.QuizSceneView.as_view(), name="quiz-scene"),
    path("quiz/scene/check/", views.QuizSceneCheckView.as_view(), name="quiz-scene-check"),
    path("quiz/scene/personal/", views.QuizPersonalSentenceView.as_view(), name="quiz-scene-personal"),
    path("quiz/connectors/", views.ConnectorQuizView.as_view(), name="connector-quiz"),
    path("ai/check-sentence/", views.AICheckSentenceView.as_view(), name="ai-check-sentence"),
]
