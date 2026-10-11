import subprocess
import sys


def test_core_imports_and_scenarios_work_when_sc2_imports_are_blocked():
    code = """
import sys
from importlib.abc import MetaPathFinder
class NoSC2(MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == "sc2" or fullname.startswith("sc2."):
            raise ImportError("SC2 deliberately unavailable")
sys.meta_path.insert(0, NoSC2())
import sc2mind.state, sc2mind.actions, sc2mind.mind
import asyncio
from sc2mind.scenarios import run_scenarios
result = asyncio.run(run_scenarios())
assert result["passed"] == result["total"]
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
