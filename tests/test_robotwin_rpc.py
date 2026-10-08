import pytest

pytest.importorskip("msgpack_numpy", reason="RoboTwin RPC array transport requires msgpack_numpy")

import unittest
import os
from pathlib import Path
from unittest.mock import patch

import numpy as np
import requests
from fastapi.testclient import TestClient

from roborsi.embodied.agent_loop import rollout, vlm_io
from roborsi.embodied.agent_loop.env import Observation, Rollout, Step
from roborsi.embodied.sim.robotwin import client, server
from roborsi.agents import baselines
from roborsi.agents.workspace import Workspace
from roborsi.runtime_mode import current_mode, use_run_mode


class RpcContractTest(unittest.TestCase):
    def setUp(self):
        self.native = object()
        server._envs['rpc-test'] = self.native
        self.http = TestClient(server.app, raise_server_exceptions=False)
        self.proxy = client.HttpRobotwinEnv('http://testserver', 'rpc-test', 'click_bell')

    def tearDown(self):
        server._envs.pop('rpc-test', None)
        self.http.close()

    def post(self, url, *, json, timeout):
        self.assertGreaterEqual(timeout, 5400)
        response = self.http.post(url, json=json)
        transported = requests.Response()
        transported.status_code = response.status_code
        transported.headers.update(response.headers)
        transported._content = response.content
        transported.url = url
        return transported

    def test_native_loop_roundtrip_mode_images_and_usage(self):
        actual_entry = rollout.run_rollout
        pixels = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)

        def native(env, **kwargs):
            self.assertIs(env, self.native)
            self.assertEqual(current_mode().value, 'eval')
            self.assertEqual(os.environ.get('ROBORSI_REASONING_EFFORT'), 'medium')
            self.assertEqual(kwargs['restrict_to_names'], {'look'})
            self.assertEqual(kwargs['seed'], 5)
            self.assertEqual(kwargs['tool_budget'], 80)
            vlm_io.record_remote_usage({'prompt_tokens': 20, 'completion_tokens': 3, 'total_tokens': 23, 'vlm_calls': 1, 'metered_calls': 1})
            episode = Rollout(task='click_bell', seed=5, success=True, outcome='predicate_passed_without_done',
                              steps=[Step(Observation(images={'head_camera': pixels}))])
            return rollout.RolloutResult(episode, True, episode.outcome, [{'tool_call': {'tool': 'look'}}], [])

        with patch.dict(os.environ, {'ROBORSI_REASONING_EFFORT': 'medium'}), use_run_mode('eval'), vlm_io.capture_usage() as usage:
            with patch.object(rollout, 'run_rollout', native), patch.object(client.requests, 'post', self.post):
                result = actual_entry(self.proxy, seed=5, task_name='click_bell', instruction='Click the bell',
                                      expected_on_success='Bell visibly pressed', tool_budget=80,
                                      workdir=Path('/tmp/rpc-contract'), restrict_to_names={'look'})
        self.assertTrue(result.success)
        self.assertEqual(usage.total_tokens, 23)
        self.assertEqual(usage.vlm_calls, 1)
        np.testing.assert_array_equal(result.rollout.steps[0].obs.images['head_camera'], pixels)

    def test_baseline_uses_native_environment_and_preserves_failure(self):
        def native(env, **kwargs):
            self.assertIs(env, self.native)
            self.assertEqual(current_mode().value, 'eval')
            self.assertEqual(kwargs['workspace'].root, Path('/tmp/rpc-contract'))
            self.assertEqual(kwargs['ns'], 'robotwin')
            vlm_io.record_remote_usage({'total_tokens': 11, 'vlm_calls': 2, 'unmetered_calls': 1})
            return {'success': False, 'outcome': 'vlm_overclaimed', 'tool_calls': 3, 'trace': []}

        with use_run_mode('eval'), vlm_io.capture_usage() as usage:
            with patch.object(baselines, '_run_openeta', native), patch.object(client.requests, 'post', self.post):
                result = self.proxy.run_baseline_loop(agent_mode='openeta', atomic='click_bell', seed=5,
                    tool_budget=80, model='gpt-5.6-sol', workspace=Workspace('click_bell', 'test', Path('/tmp/rpc-contract')),
                    task_instruction='Click the bell', ns='robotwin')
        self.assertFalse(result['success'])
        self.assertEqual(usage.total_tokens, 11)
        self.assertEqual(usage.unmetered_calls, 1)

    def test_server_error_is_not_converted_to_simulator_failure(self):
        def broken(*args, **kwargs):
            raise NotImplementedError('missing simulator operation')
        with use_run_mode('eval'), patch.object(rollout, 'run_rollout', broken), patch.object(client.requests, 'post', self.post):
            with self.assertRaises(requests.HTTPError):
                self.proxy.run_tool_loop(seed=5, task_name='click_bell', instruction='Click the bell',
                                         expected_on_success='Bell visibly pressed', tool_budget=80)


if __name__ == '__main__':
    unittest.main(verbosity=2)
