import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

FILE = Path(__file__).resolve().parents[2] / "roborsi/agents/repair_evidence.py"
spec = importlib.util.spec_from_file_location("repair_evidence", FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RepairEvidenceTests(unittest.TestCase):
    def test_late_failure_and_done_survive_with_full_native_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p = root / "roborsi/embodied/skills/base/grasp_object/libero/policy.py"
            p.parent.mkdir(parents=True)
            source = "def dispatch_runtime(state, args):\n    return {'grasped': False}, None\n"
            p.write_text(source)
            p.with_name("SKILL.md").write_text("harness: existing_contract")
            trace = [{"tool_call": {"tool": "look"}, "result": {"ok": True}} for _ in range(80)]
            trace[65] = {"tool_call": {"tool": "grasp_object"}, "result": {"ok": True, "grasped": False}}
            trace[79] = {"tool_call": {"tool": "done", "args": {"success": False}}, "result": {"acknowledged": True}}
            result = module.repair_evidence(trace, "libero", repo=root)
            self.assertIn('"trace_index": 65', result)
            self.assertIn('"trace_index": 79', result)
            self.assertIn(source, result)
            self.assertIn("harness: existing_contract", result)

    def test_normal_negative_query_does_not_select_native_source(self):
        trace = [{"tool_call": {"tool": "is_holding"}, "result": {"ok": True, "holding": False}}]
        result = module.repair_evidence(trace, "libero")
        self.assertNotIn("sha256=", result)

    def test_path_traversal_cannot_read_source(self):
        trace = [{"tool_call": {"tool": "../../secret"}, "result": {"ok": False}}]
        self.assertNotIn("sha256=", module.repair_evidence(trace, "libero"))

    def test_excluded_namespace_is_empty(self):
        self.assertEqual(module.repair_evidence([], "unknown"), "")


if __name__ == "__main__":
    unittest.main()
