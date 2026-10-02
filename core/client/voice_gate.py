"""录音与输出层共用的 VAD 判定；不改变现有阈值。"""

def vad_reasons(stats: dict, min_speech_sec: float, min_ratio: float) -> tuple:
    speech = stats.get('speech_sec')
    ratio = stats.get('ratio')
    if not isinstance(speech, (int, float)):
        return ()
    reasons = []
    if speech < min_speech_sec:
        reasons.append('speech_duration')
    if isinstance(ratio, (int, float)) and ratio < min_ratio:
        reasons.append('speech_ratio')
    return tuple(reasons)
