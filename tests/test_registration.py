"""Decorator based plugin declarations."""

from dsh_mloader_sdk import plugin, tool
from dsh_mloader_sdk.context import state

from support import SdkTestCase


class RegistrationTest(SdkTestCase):
    """The plugin and tool decorators and the records they leave behind."""

    def test_bare_plugin_decorator_records_the_entry_function(self):
        @plugin
        def apply(ctx):
            return None

        self.assertIs(state.apply_fn, apply)
        self.assertEqual(apply.__name__, "apply")

    def test_plugin_decorator_returns_the_function_unchanged(self):
        def apply(ctx):
            return None

        self.assertIs(plugin(apply), apply)
        self.assertIs(plugin()(apply), apply)

    def test_plugin_decorator_accepts_a_name_keyword(self):
        @plugin(name="named-entry")
        def apply(ctx):
            return None

        self.assertIs(state.apply_fn, apply)

    def test_declared_tools_keep_their_full_contract(self):
        parameters = {"type": "object", "properties": {"a": {"type": "number"}}}
        output = {"type": "object", "properties": {"sum": {"type": "number"}}}

        @tool(
            name="add",
            description="Add two numbers.",
            parameters=parameters,
            output=output,
            timeout_ms=2500,
        )
        def add(args):
            return {"sum": args["a"]}

        entry = state.declared_tools[0]
        self.assertEqual(entry["name"], "add")
        self.assertEqual(entry["description"], "Add two numbers.")
        self.assertIs(entry["parameters"], parameters)
        self.assertIs(entry["output"], output)
        self.assertEqual(entry["timeoutMs"], 2500)
        self.assertIs(entry["handler"], add)

    def test_declared_tools_omit_unset_optional_fields(self):
        @tool(name="ping", description="Ping.")
        def ping(args):
            return {}

        self.assertEqual(set(state.declared_tools[0]), {"name", "description", "handler"})
