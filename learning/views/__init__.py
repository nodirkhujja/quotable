# One import per domain module.
# To add a new view: put it in the right module and add one line here.

from quiz.views import (
    AICheckSentenceView,
    ConnectorQuizView,
    QuizFreeProductionGradeView,
    QuizNextBatchView,
    QuizPageView,
    QuizPersonalSentenceView,
    QuizSceneCheckView,
    QuizSceneView,
    QuizSubmitView,
    QuizSummaryView,
    WordBridgeCheckView,
    WordBridgeGenerateView,
)

from .grammar import (
    GrammarAIQuestionsView,
    GrammarCheckBridgeView,
    GrammarCheckVoiceView,
    GrammarExplainView,
    GrammarHubView,
    GrammarUnitView,
)
from .notebook import (
    DeepLearnFragmentView,
    DeepLearnView,
    DictionaryLookupView,
    WatchOverlayView,
    WordBattleDataView,
    WordNotebookCompanionTouchView,
    WordNotebookCompanionView,
    WordNoteCreateFromTranscriptView,
    WordNoteCreateView,
    WordNoteDetailView,
    WordNotePageView,
    WordNoteReviewCompleteView,
    translate_word,
)
from .onboarding import (
    InterestProfileView,
    InterestSaveView,
    OnboardingCompleteView,
    OnboardingDataView,
    OnboardingGrammarCompleteView,
    OnboardingPhrasesCompleteView,
    OnboardingProgressView,
    OnboardingWelcomeView,
    PlacementSubmitView,
)
from .practice import (
    CoachView,
    OwnTheWordCloseView,
    OwnTheWordGradeView,
    OwnTheWordPageView,
    PracticeHubView,
    ReadyToWatchDataView,
    ReadyToWatchPageView,
    SceneRecallAnswerView,
    SceneRecallDataView,
    SceneRecallPageView,
    ShadowingView,
)
from .progress import CollectionPageView, ProgressView, ReviewPageView, StatsView, StreakPageView
from .review import (
    FavoriteListView,
    FavoriteToggleView,
    FavoriteUpdateView,
    MasteryStatusView,
    MasteryUpdateView,
    ReviewQueueView,
)
from .tracking import (
    FlashcardLogView,
    GrammarPracticeLogView,
    PageVisitLogView,
    PageVisitUpdateView,
    PronunciationAssessView,
    SessionEndView,
    SessionStartView,
    ShadowingLogView,
    TTSView,
    WatchTimeLogView,
)
