# dsh-mloader-python-sdk

[English](README.md) | 简体中文

[dsh-mloader](https://github.com/Mooling0602/dsh-mloader) 的 Python SDK。dsh-mloader 是
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的多运行时插件加载器。
Python 插件导入 `dsh_mloader_sdk` 来声明工具、订阅宿主事件、写日志并注册清理函数。

[![](https://img.shields.io/badge/powered_by-dsh--mloader-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/Mooling0602/dsh-mloader)
[![](https://img.shields.io/github/license/Mooling0602/dsh-mloader-python-sdk?style=flat-square)](https://github.com/Mooling0602/dsh-mloader-python-sdk/blob/main/LICENSE)

## 生成来源

`src/dsh-mloader-sdk/` 下的包源码由 dsh-mloader 仓库的
[`src/runtime/python_sdk`](https://github.com/Mooling0602/dsh-mloader/tree/main/src/runtime/python_sdk)
目录生成。那个仓库是权威来源，本仓库是镜像，所以改运行时请改 dsh-mloader 然后复制过来。
本仓库不包含权威实现。

## 在插件中使用 SDK

dsh-mloader 自带这个 SDK，并为每个 Python 插件加载它，所以插件在 profile 里运行时不需要安装它。
当插件代码、编辑器或插件自己的测试需要在加载器之外导入 `dsh_mloader_sdk` 时，再把包加进插件项目。

用 [uv](https://docs.astral.sh/uv/) 从 Git 添加：

```bash
uv add "dsh-mloader-sdk @ git+https://github.com/Mooling0602/dsh-mloader-python-sdk"
```

uv 会把依赖记录成 `pyproject.toml` 里的 Git 源：

```toml
[project]
dependencies = [
  "dsh-mloader-sdk",
]

[tool.uv.sources]
dsh-mloader-sdk = { git = "https://github.com/Mooling0602/dsh-mloader-python-sdk" }
```

固定到某个提交，环境即可复现：

```bash
uv add "dsh-mloader-sdk @ git+https://github.com/Mooling0602/dsh-mloader-python-sdk@<commit>"
```

这会在 `pyproject.toml` 的源条目里加上 `rev = "<commit>"`。

如果只想临时导入 SDK，不改动项目：

```bash
uv run --no-project --with "dsh-mloader-sdk @ git+https://github.com/Mooling0602/dsh-mloader-python-sdk" \
  python -c "import dsh_mloader_sdk; print(dsh_mloader_sdk.__version__)"
```

导入名是 `dsh_mloader_sdk`，不是分发名；包要求 Python 3.10 或更高版本。
安装进来的副本属于开发依赖：加载器始终导入 `launch.py` 旁边自带的那份，
所以固定的版本不影响正在运行的插件实际导入的代码。

## 在本仓库开发

测试套件使用标准库的 `unittest`，不需要额外的测试依赖。

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

## 最小插件，风格 A

声明一个 `apply` 入口，在它收到的上下文上注册能力。

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

## 最小插件，风格 B

只声明工具，不写 `apply` 函数。加载器会为插件注册这些已声明的工具。

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

两种风格可以在一个插件里混用。插件文件旁边的清单文件提供插件身份和入口路径。

## 许可证

MIT。见 [LICENSE](LICENSE)。
