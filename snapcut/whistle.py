"""Find referee whistles: a narrow, steady tone around 3-4.6 kHz lasting >= ~0.15s."""
import numpy as np
from scipy.signal import stft

SR = 22050
BAND = (3000, 4600)
MIN_DUR = 0.15      # seconds of continuous tone
PEAK_DB = 12.0      # peak bin above the band median
MAX_DRIFT = 60.0    # Hz the tone may wander between frames


def load_audio(path):
    return np.fromfile(path, np.int16).astype(np.float32) / 32768


def whistle_onsets(x, t0, t1):
    """Onset times (absolute seconds) of whistle-like tones within [t0, t1]."""
    y = x[int(max(0, t0) * SR):int(t1 * SR)]
    f, t, Z = stft(y, SR, nperseg=1024, noverlap=768)
    band = (f >= BAND[0]) & (f <= BAND[1])
    P = 20 * np.log10(np.abs(Z[band]) + 1e-9)
    fb = f[band]
    peak = P.argmax(axis=0)
    prom = P.max(axis=0) - np.median(P, axis=0)
    tonal = prom > PEAK_DB
    hop = t[1] - t[0]
    need = int(np.ceil(MIN_DUR / hop))
    onsets, i = [], 0
    while i < len(t):
        j = i
        while j + 1 < len(t) and tonal[j] and tonal[j + 1] and abs(fb[peak[j + 1]] - fb[peak[j]]) <= MAX_DRIFT:
            j += 1
        if tonal[i] and j - i + 1 >= need:
            onsets.append(max(0, t0) + t[i])
            i = j + 1
        else:
            i += 1
    return onsets
