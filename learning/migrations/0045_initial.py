# Removes quiz models from learning ORM state (tables stay; quiz app now owns them).
# SeparateDatabaseAndState: state sees DeleteModel, DB sees nothing.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("learning", "0044_phonemeweakness_pronunciationassessment"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="clozeresult",
                    name="quote",
                ),
                migrations.RemoveField(
                    model_name="clozeresult",
                    name="session",
                ),
                migrations.RemoveField(
                    model_name="quizattempt",
                    name="note",
                ),
                migrations.RemoveField(
                    model_name="quizattempt",
                    name="user",
                ),
                migrations.RemoveField(
                    model_name="quizattempt",
                    name="vocab",
                ),
                migrations.RemoveField(
                    model_name="reviewsession",
                    name="user",
                ),
                migrations.RemoveIndex(
                    model_name="savedsentence",
                    name="learning_sa_user_id_1b8e74_idx",
                ),
                migrations.DeleteModel(
                    name="ClozeResult",
                ),
                migrations.DeleteModel(
                    name="QuizAttempt",
                ),
                migrations.DeleteModel(
                    name="ReviewSession",
                ),
                migrations.DeleteModel(
                    name="SavedSentence",
                ),
            ],
            database_operations=[],
        ),
    ]
