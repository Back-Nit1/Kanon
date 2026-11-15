import os
import tempfile

# Tests must not append to the developer's real traces/timing.jsonl.
os.environ.setdefault("KANON_TIMING_LOG", os.path.join(tempfile.mkdtemp(), "timing.jsonl"))
