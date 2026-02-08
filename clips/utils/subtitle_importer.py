"""
Subtitle Importer for Quotable
Automatically import quotes from .srt subtitle files
"""


def parse_srt_time(time_str):
    """
    Convert SRT timestamp to seconds
    Example: '00:00:55,422' -> 55.422
    """
    # Split into hours, minutes, seconds, milliseconds
    # Format: HH:MM:SS,mmm
    hours, minutes, rest = time_str.split(":")
    seconds, milliseconds = rest.split(",")

    total_seconds = int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000
    return total_seconds


def parse_srt_file(srt_file_path):
    """
    Parse an SRT file and return list of subtitle entries

    Returns:
        List of dicts with: {
            'index': int,
            'start_time': float (seconds),
            'end_time': float (seconds),
            'text': str
        }
    """
    with open(srt_file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split by double newline (separates subtitle blocks)
    blocks = content.strip().split("\n\n")

    subtitles = []

    for block in blocks:
        lines = block.strip().split("\n")

        if len(lines) < 3:
            continue

        # Line 1: Index number
        try:
            index = int(lines[0])
        except ValueError:
            continue

        # Line 2: Timestamps
        # Format: 00:00:55,422 --> 00:00:59,256
        timestamp_line = lines[1]
        if "-->" not in timestamp_line:
            continue

        start_str, end_str = timestamp_line.split("-->")
        start_time = parse_srt_time(start_str.strip())
        end_time = parse_srt_time(end_str.strip())

        # Line 3+: Text (can be multiple lines)
        text = "\n".join(lines[2:])

        subtitles.append({"index": index, "start_time": start_time, "end_time": end_time, "text": text})

    return subtitles


def group_subtitles_into_quotes(subtitles, max_gap=2.0, min_length=20):
    """
    Group consecutive subtitles into meaningful quotes

    Args:
        subtitles: List of subtitle dicts from parse_srt_file()
        max_gap: Maximum gap in seconds between subtitles to group them
        min_length: Minimum character length for a quote

    Returns:
        List of quote dicts with start_time, end_time, and text
    """
    quotes = []
    current_quote = []
    current_start = None
    last_end = 0

    for sub in subtitles:
        gap = sub["start_time"] - last_end

        if not current_quote:
            # Start new quote
            current_start = sub["start_time"]
            current_quote.append(sub["text"])
            last_end = sub["end_time"]
        elif gap < max_gap:
            # Continue current quote
            current_quote.append(sub["text"])
            last_end = sub["end_time"]
        else:
            # Save current quote and start new one
            full_text = " ".join(current_quote)
            if len(full_text) >= min_length:
                quotes.append({"start_time": current_start, "end_time": last_end, "text": full_text})

            # Start new quote
            current_quote = [sub["text"]]
            current_start = sub["start_time"]
            last_end = sub["end_time"]

    # Don't forget the last quote
    if current_quote:
        full_text = " ".join(current_quote)
        if len(full_text) >= min_length:
            quotes.append({"start_time": current_start, "end_time": last_end, "text": full_text})

    return quotes


def import_quotes_from_srt(source_id, srt_file_path, max_gap=2.0, min_length=20):
    """
    Import quotes from SRT file into database

    Args:
        source_id: ID of the Source model (movie/TV show)
        srt_file_path: Path to .srt file
        max_gap: Maximum gap in seconds to group subtitles
        min_length: Minimum character length for quotes

    Returns:
        Number of quotes created
    """
    # Import here to avoid circular imports
    from clips.models import Quote, Source

    # Get the source
    try:
        source = Source.objects.get(id=source_id)
    except Source.DoesNotExist:
        raise ValueError(f"Source with id {source_id} not found")

    # Parse the SRT file
    subtitles = parse_srt_file(srt_file_path)
    print(f"Parsed {len(subtitles)} subtitle entries")

    # Group into quotes
    quotes = group_subtitles_into_quotes(subtitles, max_gap, min_length)
    print(f"Grouped into {len(quotes)} quotes")

    # Create Quote objects
    created_count = 0
    for quote_data in quotes:
        Quote.objects.create(
            source=source,
            text=quote_data["text"],
            start_time=quote_data["start_time"],
            end_time=quote_data["end_time"],
        )
        created_count += 1

    print(f"Successfully created {created_count} quotes!")
    return created_count


# For testing/debugging
if __name__ == "__main__":
    # Test parsing
    srt_path = "/media/subtitles/file.srt"
    subtitles = parse_srt_file(srt_path)

    print(f"Total subtitles: {len(subtitles)}")
    print("\nFirst 3 subtitles:")
    for sub in subtitles[:3]:
        print(f"\n{sub['index']}")
        print(f"{sub['start_time']:.2f}s -> {sub['end_time']:.2f}s")
        print(f"{sub['text']}")

    # Test grouping
    quotes = group_subtitles_into_quotes(subtitles)
    print(f"\n\nTotal quotes: {len(quotes)}")
    print("\nFirst 3 quotes:")
    for i, quote in enumerate(quotes[:3], 1):
        print(f"\nQuote {i}:")
        print(f"{quote['start_time']:.2f}s -> {quote['end_time']:.2f}s")
        print(f"{quote['text']}")
