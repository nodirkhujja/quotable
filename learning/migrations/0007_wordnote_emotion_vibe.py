from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("learning", "0006_wordnote_mood"),
    ]

    operations = [
        migrations.AddField(
            model_name="wordnote",
            name="emotion_vibe",
            field=models.CharField(
                blank=True,
                choices=[
                    ("nostalgic", "Nostalgic"),
                    ("thrilling", "Thrilling"),
                    ("inspiring", "Inspiring"),
                    ("humorous", "Humorous"),
                    ("tense", "Tense"),
                    ("heartwarming", "Heartwarming"),
                ],
                max_length=20,
            ),
        ),
    ]
