import ast
from pathlib import Path
import json
import unittest
from roborsi.agents.planner_contract import render_contract, system_for_namespace, namespace_contract


def actual_prompt():
    path=Path(__file__).resolve().parents[2]/'roborsi/agents/planner.py'
    tree=ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_SYSTEM_PROMPT' for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError('Planner prompt missing')


class PlannerNamespaceContractTests(unittest.TestCase):
    def test_other_namespace_prompt_is_byte_identical(self):
        original=actual_prompt()
        self.assertEqual(system_for_namespace(original,'robotwin'),original)
        self.assertEqual(namespace_contract('robotwin'),'')

    def test_libero_keeps_output_contract_without_other_robot_examples(self):
        corrected=system_for_namespace(actual_prompt(),'libero')
        self.assertIn('"sub_goals"',corrected)
        self.assertIn('You DO NOT execute anything. You only write the plan.',corrected)
        self.assertIn('plan.md markdown. Nothing else. Schema:',corrected)
        self.assertIn('one Franka arm',corrected)
        self.assertNotIn('grasp_object(arm',corrected)
        self.assertNotIn('grasp_obb(arm',corrected)
        self.assertNotIn('[0.5,-0.5,0.5,0.5]',corrected)

    def test_renderer_keeps_exact_runtime_parameters_without_mutation(self):
        function={'name':'grasp_object','description':'Declared actual tool',
                  'parameters':{'type':'object','properties':{'object':{'type':'string'},
                               'pixel':{'type':'array','items':{'type':'number'}}},'required':['object']}}
        before=json.dumps(function,sort_keys=True)
        line=render_contract(function,{'grasped':'bool'})
        value=json.loads(line.split(': ',1)[1])
        self.assertEqual(value['parameters'],function['parameters'])
        self.assertNotIn('arm',value['parameters']['properties'])
        self.assertEqual(json.dumps(function,sort_keys=True),before)


if __name__=='__main__':unittest.main()
