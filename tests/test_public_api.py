"""The published import surface and version identifiers."""

import dsh_mloader_sdk

from support import SdkTestCase


class PublicApiTest(SdkTestCase):
    """The module exports, their types and the version identifiers."""

    def test_every_exported_name_is_importable(self):
        for name in dsh_mloader_sdk.__all__:
            self.assertTrue(hasattr(dsh_mloader_sdk, name), name)

    def test_the_documented_surface_is_exported(self):
        expected = {
            "Context",
            "HostUnavailable",
            "PluginCallError",
            "PROTOCOL_VERSION",
            "__version__",
            "ctx",
            "get_context",
            "plugin",
            "tool",
        }
        self.assertEqual(set(dsh_mloader_sdk.__all__), expected)

    def test_protocol_version_is_one(self):
        self.assertEqual(dsh_mloader_sdk.PROTOCOL_VERSION, 1)

    def test_the_package_version_is_a_release_string(self):
        parts = dsh_mloader_sdk.__version__.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(part.isdigit() for part in parts))

    def test_error_types_are_runtime_errors(self):
        self.assertTrue(issubclass(dsh_mloader_sdk.PluginCallError, RuntimeError))
        self.assertTrue(issubclass(dsh_mloader_sdk.HostUnavailable, RuntimeError))

    def test_ctx_use_before_the_runtime_starts_raises_host_unavailable(self):
        with self.assertRaises(dsh_mloader_sdk.HostUnavailable):
            dsh_mloader_sdk.ctx.tools

    def test_get_context_returns_none_before_the_runtime_starts(self):
        self.assertIsNone(dsh_mloader_sdk.get_context())
