"""Stand-in for bridge/ios_bridge.py: prints scripted protocol events, then optionally idles."""
import json
import sys
import time

mode = sys.argv[1]
def emit(**kw): print(json.dumps(kw), flush=True)

emit(event="status", phase="detecting")
if mode == "ready":
    emit(event="ready", port=1234, password="pw", udid="U1", ios="17.5", protocol=1)
    time.sleep(30)
elif mode == "ready_v2":
    emit(event="ready", port=1234, password="pw", udid="U1", ios="27.0", protocol=2)
    time.sleep(30)
elif mode == "legacy_ready":                     # no protocol field = v1
    emit(event="ready", port=1, password="pw", udid="U", ios="17.5")
    time.sleep(30)
elif mode == "error":
    emit(event="error", code="developer_mode_off", message="mati")
    sys.exit(2)
elif mode == "exit":
    sys.exit(0)
elif mode == "hang":
    time.sleep(30)
