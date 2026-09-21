def estimate_audio_duration(text: str) -> float:
    """Estimate audio duration for narration text.
    
    Uses a conservative estimate of ~138 WPM (2.3 words per second).
    """
    word_count = len(str(text).split())
    return word_count / 2.3 if word_count > 0 else 0.0
