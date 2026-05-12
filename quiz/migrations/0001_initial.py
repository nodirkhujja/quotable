# Moves quiz models from learning app to quiz app (ORM state only).
# Tables already exist as learning_* — SeparateDatabaseAndState skips all DDL.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("clips", "0007_reel_reelline_reelview_and_more"),
        ("learning", "0045_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="QuizAttempt",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                            ),
                        ),
                        (
                            "quiz_type",
                            models.CharField(
                                choices=[
                                    ("flash", "Flash Match"),
                                    ("cloze", "Cloze"),
                                    ("produce", "Define & Use"),
                                    ("match", "Match"),
                                    ("listen", "Listen"),
                                    ("teach", "Teach"),
                                ],
                                max_length=10,
                            ),
                        ),
                        (
                            "direction",
                            models.CharField(
                                choices=[("l2_l1", "English → Uzbek"), ("l1_l2", "Uzbek → English")],
                                default="l2_l1",
                                max_length=5,
                            ),
                        ),
                        ("correct", models.BooleanField()),
                        ("user_answer", models.CharField(blank=True, max_length=300)),
                        (
                            "chosen_wrong",
                            models.CharField(
                                blank=True,
                                help_text="Wrong option the user picked (confusion detection)",
                                max_length=300,
                            ),
                        ),
                        ("response_time_ms", models.PositiveIntegerField(default=0)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "note",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="quiz_attempts",
                                to="learning.wordnote",
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="quiz_attempts",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        (
                            "vocab",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="quiz_attempts",
                                to="learning.linevocab",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "learning_quizattempt",
                        "ordering": ["-created_at"],
                    },
                ),
                migrations.CreateModel(
                    name="ReviewSession",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                            ),
                        ),
                        ("started_at", models.DateTimeField(auto_now_add=True)),
                        ("ended_at", models.DateTimeField(blank=True, null=True)),
                        ("quotes_reviewed", models.PositiveIntegerField(default=0)),
                        ("correct_answers", models.PositiveIntegerField(default=0)),
                        (
                            "session_type",
                            models.CharField(
                                choices=[
                                    ("cloze", "Fill in the Blank"),
                                    ("shadow", "Shadow Mode"),
                                    ("review", "Quote Review"),
                                    ("quiz", "Multiple Choice Quiz"),
                                    ("mixed", "Mixed"),
                                ],
                                default="mixed",
                                max_length=20,
                            ),
                        ),
                        ("score", models.PositiveIntegerField(default=0)),
                        ("best_combo", models.PositiveIntegerField(default=0)),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="review_sessions",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "learning_reviewsession",
                    },
                ),
                migrations.CreateModel(
                    name="ClozeResult",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                            ),
                        ),
                        ("target_word", models.CharField(max_length=100)),
                        ("user_answer", models.CharField(max_length=100)),
                        ("is_correct", models.BooleanField(default=False)),
                        ("answered_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "quote",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="cloze_results",
                                to="clips.quote",
                            ),
                        ),
                        (
                            "session",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="cloze_results",
                                to="quiz.reviewsession",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "learning_clozemresult",
                    },
                ),
                migrations.CreateModel(
                    name="SavedSentence",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                            ),
                        ),
                        (
                            "word",
                            models.CharField(
                                blank=True, default="", help_text="Target word/phrase the learner used", max_length=120
                            ),
                        ),
                        ("uzbek_prompt", models.TextField(blank=True, default="")),
                        ("english", models.TextField(help_text="The English the learner produced")),
                        ("quiz_type", models.CharField(default="mastered_bridge", max_length=40)),
                        ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                        (
                            "note",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="saved_sentences",
                                to="learning.wordnote",
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="saved_sentences",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "learning_savedsentence",
                        "ordering": ["-created_at"],
                        "indexes": [
                            models.Index(fields=["user", "-created_at"], name="learning_sa_user_id_1b8e74_idx")
                        ],
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
