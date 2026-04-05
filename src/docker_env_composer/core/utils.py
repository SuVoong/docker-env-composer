import time

def _elapsed(start: float) -> str:
    secs = time.time() - start
    if secs < 60:
        return f"{secs:.1f}s"
    return f"{int(secs // 60)}m {secs % 60:.0f}s"
