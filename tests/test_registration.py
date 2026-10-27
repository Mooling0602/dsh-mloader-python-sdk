"""Decorator based plugin declarations."""

from typing import Any, Optional, Union

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


class InferredOutputTest(SdkTestCase):
    """Result schemas derived from a handler's return annotation."""

    def _spec(self, handler):
        from dsh_mloader_sdk.runtime import _declared_tool_spec

        return _declared_tool_spec({"name": "t", "description": "d", "handler": handler})

    def test_scalar_annotations_map_to_their_type(self):
        cases = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            dict: "object",
            list: "array",
        }
        for annotation, expected in cases.items():
            handler = _handler_returning(annotation)
            with self.subTest(annotation=annotation):
                self.assertEqual(self._spec(handler)["output"], {"type": expected})

    def test_a_parametrised_list_describes_its_elements(self):
        def handler(args) -> list[str]:
            return []

        self.assertEqual(self._spec(handler)["output"], {"type": "array", "items": {"type": "string"}})

    def test_optional_drops_the_null_member(self):
        def handler(args) -> Optional[str]:
            return None

        self.assertEqual(self._spec(handler)["output"], {"type": "string"})

    def test_a_union_becomes_a_one_of(self):
        def handler(args) -> Union[str, int]:
            return 1

        self.assertEqual(
            self._spec(handler)["output"],
            {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        )

    def test_a_missing_annotation_states_no_schema(self):
        def handler(args):
            return {}

        self.assertNotIn("output", self._spec(handler))

    def test_any_states_no_schema(self):
        def handler(args) -> Any:
            return {}

        self.assertNotIn("output", self._spec(handler))

    def test_an_unresolvable_annotation_states_no_schema(self):
        def handler(args) -> "Undefined":  # noqa: F821 - the name must stay undefined
            return {}

        self.assertNotIn("output", self._spec(handler))

    def test_an_explicit_output_wins(self):
        declared = {"type": "string", "enum": ["a"]}

        @tool(name="t", description="d", output=declared)
        def handler(args) -> int:
            return 1

        from dsh_mloader_sdk.runtime import _declared_tool_spec

        self.assertIs(_declared_tool_spec(state.declared_tools[0])["output"], declared)


def _handler_returning(annotation):
    """Build a handler whose return annotation is ``annotation``.

    :param annotation: Annotation object to attach.
    :returns: A function carrying the requested return annotation.
    :rtype: collections.abc.Callable
    """

    def handler(args):
        return None

    handler.__annotations__["return"] = annotation
    return handler
