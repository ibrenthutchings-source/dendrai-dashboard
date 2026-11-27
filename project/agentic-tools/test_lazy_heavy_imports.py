"""Idle-memory guard: scipy (~55 MB) and the anthropic SDK (~35 MB) must stay lazy.

Importing them at module load made every worker pay ~90 MB whether or not a request
ever needed them — headroom that matters on the memory-limited Railway container.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def test_api_server_import_does_not_load_scipy_or_anthropic():
    code = (
        "import sys; import api_server; "
        "bad=[m for m in ('scipy','anthropic') if m in sys.modules]; "
        "print('LOADED:'+','.join(bad))"
    )
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True, timeout=180)
    line = [l for l in out.stdout.splitlines() if l.startswith("LOADED:")]
    assert line, out.stderr[-500:]
    assert line[0] == "LOADED:", f"heavy modules imported at startup: {line[0]}"


def test_lazy_scipy_pearson_still_gives_p_value():
    import fred_tool
    r, p = fred_tool._pearson([1, 2, 3, 4, 5, 6, 7, 8], [2, 4, 5, 4, 5, 7, 8, 9])
    assert 0.8 < r <= 1.0 and p is not None
