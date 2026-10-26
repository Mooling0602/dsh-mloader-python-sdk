# dsh-mloader-python-sdk

Python SDK for dsh-mloader, the multi-runtime plugin loader for DeepSeek
Harness. A Python plugin imports `dsh_mloader_sdk` to declare tools, subscribe to
host events, write logs, and register cleanup functions.

## Generated source

The package source under `src/dsh-mloader-sdk/` is generated from the
`src/runtime/python_sdk` directory of the dsh-mloader repository. That
repository is the canonical source and this repository is a mirror, so change
the runtime in dsh-mloader and copy it here. This repository does not contain
the canonical implementation.

## Use the SDK from a plugin

dsh-mloader ships this SDK and loads it for every Python plugin, so a plugin
does not install it to run inside a profile. Add the package to the plugin
project when the plugin code, an editor or the plugin's own tests need to
import `dsh_mloader_sdk` outside the loader.

Add the package from Git with uv:

```bash
uv add "dsh-mloader-sdk @ git+https://github.com/Mooling0602/dsh-mloader-python-sdk"
```

uv records the dependency as a Git source in `pyproject.toml`:

```toml
[project]
dependencies = [
  "dsh-mloader-sdk",
]

[tool.uv.sources]
dsh-mloader-sdk = { git = "https://github.com/Mooling0602/dsh-mloader-python-sdk" }
```

Pin a commit for a reproducible environment:

```bash
uv add "dsh-mloader-sdk @ git+https://github.com/Mooling0602/dsh-mloader-python-sdk@<commit>"
```

This adds `rev = "<commit>"` to the source entry in `pyproject.toml`.

To import the SDK in a one-off script without changing a project:

```bash
uv run --no-project --with "dsh-mloader-sdk @ git+https://github.com/Mooling0602/dsh-mloader-python-sdk" \
  python -c "import dsh_mloader_sdk; print(dsh_mloader_sdk.__version__)"
```

The import name is `dsh_mloader_sdk`, never the distribution name, and the
package requires Python 3.10 or later. The installed copy is a development
dependency: the loader always imports the copy that ships beside `launch.py`,
so the pinned version does not change what a running plugin imports.

## Work on this repository

The test suite uses the `unittest` framework from the standard library, so it
needs no test dependency.

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

## Minimal plugin, style A

Declare an `apply` entry point and register capabilities on the context it
receives.

```python
from dsh_mloader_sdk import plugin

@plugin
def apply(ctx):
    @ctx.tools.register(
        name="greet",
        description="Greet one person by name.",
        parameters={
            "type": "object",
            "properties": {"who": {"type": "string"}},
            "required": ["who"],
            "additionalProperties": False,
        },
    )
    def greet(args):
        return {"text": f"hello {args['who']}"}

    ctx.effect.register("greet-cleanup", lambda: ctx.logger.info("bye"))
```

## Minimal plugin, style B

Declare tools without writing an `apply` function. The loader registers the
declared tools for the plugin.

```python
from dsh_mloader_sdk import tool

@tool(
    name="add",
    description="Add two numbers.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
)
def add(args):
    return {"sum": args["a"] + args["b"]}
```

Both styles may be mixed in one plugin. A manifest next to the plugin file
supplies the plugin identity and the entry path.

## License

MIT. See [LICENSE](LICENSE).
