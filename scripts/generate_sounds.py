"""Generate quiz sound effects as base64-encoded WAV files."""

import base64
import io
import math
import struct
import wave

SAMPLE_RATE = 11025


def make_wav(samples):
    """Convert float samples [-1, 1] to WAV bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        data = b"".join(struct.pack("<h", max(-32767, min(32767, int(s * 32767)))) for s in samples)
        w.writeframes(data)
    return buf.getvalue()


def envelope(t, attack=0.005, decay=0.15, total=0.2):
    """Simple AD envelope."""
    if t < attack:
        return t / attack
    elif t < total:
        return 1.0 * math.exp(-((t - attack) / decay) * 4)
    return 0


def gen_correct():
    """Bright bell ding — two tones with FM synthesis for metallic quality."""
    duration = 0.18
    n = int(SAMPLE_RATE * duration)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = envelope(t, attack=0.002, decay=0.08, total=duration)
        # FM synthesis: carrier + modulator = bell-like tone
        mod = math.sin(2 * math.pi * 1397 * t) * 600  # modulator
        carrier = math.sin(2 * math.pi * 1175 * t + mod)  # D6-ish bell
        # Add a bright harmonic
        bright = math.sin(2 * math.pi * 2349 * t) * 0.3
        samples.append((carrier + bright) * env * 0.35)
    return samples


def gen_correct_streak(level):
    """Same ding but higher pitch per streak level."""
    duration = 0.18
    n = int(SAMPLE_RATE * duration)
    samples = []
    pitch_mult = 1.0 + (level * 0.06)  # 6% higher per level
    for i in range(n):
        t = i / SAMPLE_RATE
        env = envelope(t, attack=0.002, decay=0.08, total=duration)
        mod = math.sin(2 * math.pi * 1397 * pitch_mult * t) * 600
        carrier = math.sin(2 * math.pi * 1175 * pitch_mult * t + mod)
        bright = math.sin(2 * math.pi * 2349 * pitch_mult * t) * 0.3
        samples.append((carrier + bright) * env * 0.35)
    return samples


def gen_wrong():
    """Soft wooden knock — low, short, non-punishing."""
    duration = 0.15
    n = int(SAMPLE_RATE * duration)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = envelope(t, attack=0.001, decay=0.05, total=duration)
        # Low tone that drops quickly
        freq = 220 * math.exp(-t * 12)  # pitch drops fast
        tone = math.sin(2 * math.pi * freq * t)
        # Add noise burst at start for "knock" feel
        noise = (hash(i) % 2000 - 1000) / 1000.0 if t < 0.008 else 0
        samples.append((tone * 0.7 + noise * 0.3) * env * 0.25)
    return samples


def gen_complete():
    """3-note ascending chime with bell harmonics."""
    notes = [1047, 1319, 1568]  # C6, E6, G6 — major chord
    total_dur = 0.4
    samples = [0.0] * int(SAMPLE_RATE * total_dur)
    for idx, freq in enumerate(notes):
        delay = idx * 0.1
        note_dur = 0.22
        n = int(SAMPLE_RATE * note_dur)
        for i in range(n):
            t = i / SAMPLE_RATE
            si = int((delay + t) * SAMPLE_RATE)
            if si >= len(samples):
                break
            env = envelope(t, attack=0.002, decay=0.12, total=note_dur)
            # FM bell
            mod = math.sin(2 * math.pi * freq * 2.5 * t) * freq * 0.8
            tone = math.sin(2 * math.pi * freq * t + mod)
            shimmer = math.sin(2 * math.pi * freq * 2 * t) * 0.2
            samples[si] += (tone + shimmer) * env * 0.22
    # Clamp
    peak = max(abs(s) for s in samples) or 1
    samples = [s / peak * 0.4 for s in samples]
    return samples


if __name__ == "__main__":
    sounds = {
        "correct": gen_correct(),
        "wrong": gen_wrong(),
        "complete": gen_complete(),
    }

    for name, samples in sounds.items():
        wav_bytes = make_wav(samples)
        b64 = base64.b64encode(wav_bytes).decode("ascii")
        print(f"const SND_{name.upper()} = 'data:audio/wav;base64,{b64}';")
    print()
