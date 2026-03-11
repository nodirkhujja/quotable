import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("learning", "0007_wordnote_emotion_vibe"),
    ]

    operations = [
        migrations.CreateModel(
            name="VocabWord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("word", models.CharField(max_length=100, unique=True)),
                ("frequency_rank", models.PositiveIntegerField(unique=True)),
                (
                    "tier",
                    models.IntegerField(
                        choices=[
                            (1, "Tier 1 \u2014 Core"),
                            (2, "Tier 2 \u2014 Common"),
                            (3, "Tier 3 \u2014 Familiar"),
                            (4, "Tier 4 \u2014 Advanced"),
                            (5, "Tier 5 \u2014 Expert"),
                        ]
                    ),
                ),
                (
                    "pos",
                    models.CharField(
                        choices=[
                            ("n", "Noun"),
                            ("v", "Verb"),
                            ("adj", "Adjective"),
                            ("adv", "Adverb"),
                            ("phr", "Phrase"),
                            ("etc", "Other"),
                        ],
                        default="etc",
                        max_length=5,
                    ),
                ),
                ("uzbek_translation", models.CharField(max_length=200)),
                ("uzbek_synonyms", models.CharField(blank=True, max_length=500)),
                ("example_sentence", models.TextField(blank=True)),
                ("gif_url", models.URLField(blank=True)),
            ],
            options={
                "ordering": ["frequency_rank"],
            },
        ),
        migrations.CreateModel(
            name="OnboardingSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("words_shown", models.PositiveIntegerField(default=0)),
                ("words_known", models.PositiveIntegerField(default=0)),
                ("projected_total", models.PositiveIntegerField(default=0)),
                (
                    "level",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("beginner", "Beginner"),
                            ("intermediate", "Intermediate"),
                            ("upper_intermediate", "Upper-Intermediate"),
                            ("advanced", "Advanced"),
                        ],
                        max_length=20,
                    ),
                ),
                ("tier_breakdown", models.JSONField(default=dict)),
                ("weak_tiers", models.JSONField(default=list)),
                ("strong_tiers", models.JSONField(default=list)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="OnboardingResult",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("known", models.BooleanField()),
                ("response_time_ms", models.PositiveIntegerField(default=0)),
                ("answered_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="results",
                        to="learning.onboardingsession",
                    ),
                ),
                (
                    "word",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="learning.vocabword",
                    ),
                ),
            ],
            options={
                "unique_together": {("session", "word")},
            },
        ),
    ]
