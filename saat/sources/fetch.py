import time
from pathlib import Path

import requests

from saat.paths import RAW

USER_AGENT = "saat-climatechain/0.1 (research prototype)"


def download(url: str, name: str, min_bytes: int = 1) -> Path:
    """Download once into data/raw and return the cached path."""
    path = RAW / name
    if path.exists() and path.stat().st_size >= min_bytes:
        return path
    tmp = path.with_suffix(path.suffix + ".part")
    for attempt in range(4):
        try:
            with requests.get(url, stream=True, timeout=120, headers={"User-Agent": USER_AGENT}) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            break
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 + 3 * attempt)
    tmp.rename(path)
    return path
