from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("learning", "0022_add_sentence_build_game"),
    ]

    operations = [
        migrations.AddField(
            model_name="onboardingsession",
            name="grammar_breakdown",
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name="onboardingsession",
            name="grammar_level",
            field=models.CharField(max_length=20, blank=True, default=""),
        ),
    ]
