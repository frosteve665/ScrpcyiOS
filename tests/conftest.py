import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "client"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
sys.path.insert(0, os.path.dirname(__file__))


import pytest


@pytest.fixture(scope="session")
def hevc_mp4(tmp_path_factory):
    """Self-generated 2 s HEVC clip (320x240, keyframe every 15). No external files needed."""
    import av
    import numpy as np

    path = tmp_path_factory.mktemp("media") / "t.mp4"
    with av.open(str(path), "w") as c:
        s = c.add_stream("libx265", rate=30)
        s.width, s.height, s.pix_fmt = 320, 240, "yuv420p"
        s.options = {"x265-params": "keyint=15:log-level=none"}
        for i in range(60):
            frame = av.VideoFrame.from_ndarray(np.full((240, 320, 3), (i * 4) % 255, dtype=np.uint8), format="rgb24")
            for p in s.encode(frame):
                c.mux(p)
        for p in s.encode():
            c.mux(p)
    return str(path)
