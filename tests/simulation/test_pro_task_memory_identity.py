import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from roborsi.agents import task_memory_identity as memory


class ProTaskMemoryIdentityTests(unittest.TestCase):
    def test_all_pro_suite_names_have_distinct_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'ROBORSI_HOME': directory}):
                destinations = []
                for family in ['spatial', 'object', 'goal']:
                    for variant in ['lan', 'object', 'swap', 'task']:
                        identity = 'libero_' + family + '_' + variant + '/0'
                        self.assertEqual(memory.key('libero_pick_place', identity), identity)
                        destinations.append(memory.directory('libero_pick_place', identity))
                self.assertEqual(len(set(destinations)), 12)
                self.assertTrue(all(str(p).startswith(directory + '/task_memories/') for p in destinations))

    def test_original_libero_path_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'ROBORSI_HOME': directory}):
                self.assertEqual(memory.directory('libero_pick_place', 'libero_goal/8'),
                                 Path(directory) / 'task_memories/libero_goal/8')

    def test_explicit_identity_wins_over_environment(self):
        with patch.dict(os.environ, {'ROBORSI_CURRENT_SIM_TASK': 'libero_goal_lan/0'}):
            self.assertEqual(memory.key('libero_pick_place'), 'libero_goal_lan/0')
            self.assertEqual(memory.key('libero_pick_place', 'libero_object_task/0'), 'libero_object_task/0')

    def test_invalid_or_path_escape_does_not_create_memory(self):
        for identity in ['../libero_goal/1', 'libero_goal_lan/../1', 'libero_goal_unknown/0',
                         '/libero_goal/0', 'libero_goal_lan/0/extra']:
            self.assertIsNone(memory.key('libero_pick_place', identity))

    def test_other_atomic_tasks_unchanged(self):
        self.assertEqual(memory.key('pick_bottle'), 'pick_bottle')
        self.assertIsNone(memory.directory('pick_bottle'))


if __name__ == '__main__':
    unittest.main()
