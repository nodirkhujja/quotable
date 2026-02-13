import pysrt

from clips.models import Episode, Quote, Source


def import_quotes_from_srt(source_id, srt_file_path, episode_id=None, min_length=30):
    """
    Automatically import quotes from SRT subtitle file.
    Supports both Movies (Source only) and TV Shows (Source + Episode).
    """
    source = Source.objects.get(id=source_id)

    # Fetch episode if provided (mandatory for TV shows)
    episode = None
    if episode_id:
        episode = Episode.objects.get(id=episode_id)

    subs = pysrt.open(srt_file_path)

    quotes_created_count = 0
    current_quote = []
    start_time = None
    last_sub_end = 0

    for _, sub in enumerate(subs):

        def to_sec(t):
            return t.hours * 3600 + t.minutes * 60 + t.seconds + t.milliseconds / 1000

        sub_start = to_sec(sub.start)
        sub_end = to_sec(sub.end)
        text = sub.text_without_tags.replace("\n", " ").strip()

        # Check gap between this sub and the last one
        gap = sub_start - last_sub_end

        if not current_quote:
            start_time = sub_start
            current_quote.append(text)
            last_sub_end = sub_end
        elif gap < 1.5:  # If gap is small, keep building the same quote
            current_quote.append(text)
            last_sub_end = sub_end
        else:
            # Save the accumulated quote before starting a new one
            full_text = " ".join(current_quote)
            if len(full_text) >= min_length:
                Quote.objects.create(
                    source=source,
                    episode=episode,  # This will be None for Movies
                    text=full_text,
                    start_time=start_time,
                    end_time=last_sub_end,
                )
                quotes_created_count += 1

            # Reset for new quote
            current_quote = [text]
            start_time = sub_start
            last_sub_end = sub_end

    # Save the final quote in the file
    if current_quote:
        full_text = " ".join(current_quote)
        if len(full_text) >= min_length:
            Quote.objects.create(
                source=source, episode=episode, text=full_text, start_time=start_time, end_time=last_sub_end
            )
            quotes_created_count += 1

    return quotes_created_count
