"""The documented chat requests must pass the schema Hermes actually registers, then reach the real handler."""
import importlib
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

import jsonschema

HERE = Path(__file__).resolve().parent


def registered(package):
    tools = {}

    class Ctx:
        def register_skill(self, *a, **k): pass
        def register_tool(self, name, toolset, schema, handler): tools[name] = (schema, handler)
        def dispatch_tool(self, *a, **k): pass
    sys.path.insert(0, str(HERE.parent))
    mod = importlib.import_module(package)
    mod.register(Ctx())
    return tools


DOCUMENTED = [
    {'action': 'alternatives', 'source': 'SRC', 'title': 'hook', 'request_key': 'tg:1:1:alternatives'},
    {'action': 'finish', 'reply_to_message_id': 7, 'style': 'B', 'request_key': 'tg:1:2:finish'},
    {'action': 'revise', 'reply_to_message_id': 7, 'request_key': 'tg:1:3:revise',
     'edit': {'style': 'C', 'captions_from': 'A', 'trim_start': 1.5, 'punchier': True, 'quieter_music': True}},
    {'action': 'revise', 'reply_to_message_id': 7, 'source': 'SRC', 'request_key': 'tg:1:4:look', 'edit': {'look_from_source': True}},
    {'action': 'edit', 'reply_to_message_id': 7, 'edit': {'music_gain_db': -28}, 'request_key': 'tg:1:5:edit'},
    {'action': 'generate', 'reply_to_message_id': 7, 'query': 'blue intro', 'title': 'my-fal-video', 'request_key': 'tg:1:6:gen'},
    {'action': 'approve', 'reply_to_message_id': 8, 'query': 'YES 7F3A2C', 'request_key': 'tg:1:7:approve'},
]


class Schema(unittest.TestCase):
    def schemas(self):
        out = {}
        for pkg in (HERE.name + '.gateway', HERE.name):
            out[pkg] = registered(pkg)['studio_request'][0]['parameters']
        return out

    def test_every_documented_request_passes_both_registered_schemas(self):
        for pkg, schema in self.schemas().items():
            for req in DOCUMENTED:
                jsonschema.validate(req, schema)  # raises with the exact field on failure

    def test_schema_still_rejects_unknown_edit_keys(self):
        for pkg, schema in self.schemas().items():
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({'action': 'revise', 'request_key': 'k', 'edit': {'make_it_viral': True}}, schema)

    def test_every_edit_key_the_skills_document_is_in_the_schema(self):
        schema = self.schemas()[HERE.name + '.gateway']
        allowed = set(schema['properties']['edit']['properties'])
        for skill in (HERE / 'SKILL.md', HERE / 'gateway' / 'SKILL.md'):
            line = re.search(r'Combine any of: ([^.]+)\.', skill.read_text()).group(1)
            keys = {re.sub(r'\s*\(.*?\)', '', k).strip() for k in line.split(',')}
            self.assertTrue(keys <= allowed, f'{skill.name} documents {keys - allowed} the schema does not accept')

    def test_revise_through_the_registered_gateway_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'studio-channel'; (root / 'jobs').mkdir(parents=True)
            (root / 'config.json').write_text(json.dumps({'inbound_roots': [tmp], 'delivery_chat_id': 42, 'allowed_chat_ids': [42]}))
            parent = {'id': 'f' * 32, 'state': 'completed', 'action': 'finish', 'result': {'state': 'finished'},
                      'delivery': {'state': 'sent', 'message_id': 7, 'chat_id': 42}}
            (root / 'jobs' / ('f' * 32 + '.json')).write_text(json.dumps(parent))
            sys.modules['hermes_constants'] = types.SimpleNamespace(get_hermes_home=lambda: tmp)
            tools = registered(HERE.name + '.gateway')
            schema, handler = tools['studio_request']
            req = DOCUMENTED[2]
            jsonschema.validate(req, schema['parameters'])
            out = json.loads(handler(req))
            self.assertEqual((out.get('action'), out.get('parent'), out.get('state')), ('revise', 'f' * 32, 'queued'), out)


if __name__ == '__main__':
    unittest.main()
