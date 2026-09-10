from pathlib import Path
import tempfile
import unittest

from workbench.project_store import ProjectStore


class ProjectLabelTests(unittest.TestCase):
    def test_default_project_persists_without_rebinding_existing_projects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / 'workbench', root / 'erp'
            for path in (first, second):
                (path / '.git').mkdir(parents=True)
            store = ProjectStore(root / 'registry.db')
            old = store.create('课程参考', first, ['python', 'check.py'])
            new = store.create('FlowERP', second, ['python', 'check.py'])
            self.assertIsNone(store.default())
            store.set_default(new['id'])
            restored = ProjectStore(store.path)
            self.assertEqual(new, restored.default())
            self.assertEqual(old, restored.get(old['id']))
            with self.assertRaises(KeyError):
                store.set_default('PROJECT-MISSING')
            self.assertEqual(new, restored.default())

    def test_rename_preserves_identity_root_and_eval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.git').mkdir()
            store = ProjectStore(root / 'workbench.db')
            original = store.create('FlowERP', root, ['python', '-m', 'eval.harness'])
            changed = store.rename(original['id'], '个人研发工作台')
            self.assertEqual('个人研发工作台', changed['name'])
            for key in ('id', 'root_path', 'eval_command'):
                self.assertEqual(original[key], changed[key])
            self.assertEqual(1, len(store.list()))
            with self.assertRaises(ValueError):
                store.rename(original['id'], ' ')
            self.assertEqual('个人研发工作台', store.get(original['id'])['name'])
            with self.assertRaises(KeyError):
                store.rename('PROJECT-MISSING', 'new name')


if __name__ == '__main__':
    unittest.main()
