"""Static checks for scripts sent to ``execute_blender_code`` in safe mode.

Safe mode is a guardrail against accidental damage by an AI client: it rejects
scripts that touch the filesystem or network directly, start processes, load
native code, or leave code running after the script ends. It is NOT a security
sandbox; Python can always be obfuscated past a static check. Run untrusted
clients against a disposable Blender instead.
"""

import ast
from dataclasses import dataclass
from typing import List

BLOCKED_MODULES = frozenset({
    "asyncio", "builtins", "code", "codeop", "ctypes", "cffi", "ftplib", "glob", "http",
    "importlib", "io", "marshal", "multiprocessing", "os", "pathlib", "pickle", "pty",
    "requests", "httpx", "runpy", "shelve", "shutil", "signal", "smtplib", "socket",
    "socketserver", "sqlite3", "ssl", "subprocess", "sys", "tarfile", "telnetlib",
    "tempfile", "threading", "urllib", "webbrowser", "zipfile", "zipimport",
})

BLOCKED_CALLS = frozenset({
    "open", "exec", "eval", "compile", "__import__", "breakpoint", "input", "exit", "quit",
    "globals", "locals", "vars", "memoryview",
})

BLOCKED_ATTRIBUTES = frozenset({
    "__builtins__", "__class__", "__bases__", "__base__", "__closure__", "__code__",
    "__dict__", "__getattribute__", "__globals__", "__import__", "__loader__", "__mro__",
    "__spec__", "__subclasses__", "as_module", "driver_namespace",
})

# Dotted bpy paths that persist code, run text blocks, or discard the session.
BLOCKED_BPY_PATHS = (
    "bpy.app.handlers",
    "bpy.app.timers",
    "bpy.app.driver_namespace",
    "bpy.ops.preferences",
    "bpy.ops.script",
    "bpy.ops.text.run_script",
    "bpy.ops.wm.quit_blender",
    "bpy.ops.wm.read_factory_settings",
    "bpy.ops.wm.read_homefile",
    "bpy.utils.register_class",
    "bpy.utils.register_module",
    "bpy.utils.register_submodule_factory",
)

# getattr/setattr/delattr with these names would reach the blocked paths above.
BLOCKED_DYNAMIC_NAMES = frozenset({"handlers", "timers", "driver_namespace", "as_module"})


@dataclass(frozen=True)
class Violation:
    line: int
    reason: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.reason}"


class SafeModeError(ValueError):
    def __init__(self, violations: List[Violation]) -> None:
        self.violations = violations
        details = "; ".join(str(v) for v in violations)
        super().__init__(
            f"Safe mode blocked this script ({details}). Use the structured tools "
            "(import_model, export_scene, save_blend_file, render_image) for file access, "
            "or disable safe mode if this is intended."
        )


def check_script(code: str) -> List[Violation]:
    """Return the safe-mode violations in ``code`` (empty when it may run)."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return [Violation(exc.lineno or 0, f"syntax error: {exc.msg}")]
    checker = _Checker()
    checker.visit(tree)
    return checker.violations


def validate_script(code: str) -> None:
    violations = check_script(code)
    if violations:
        raise SafeModeError(violations)


def _is_blocked_module(name: str) -> bool:
    return name.split(".", 1)[0] in BLOCKED_MODULES


def _is_blocked_bpy_path(path: str) -> bool:
    return any(path == blocked or path.startswith(blocked + ".") for blocked in BLOCKED_BPY_PATHS)


class _Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: List[Violation] = []
        # Local names bound to a bpy path, e.g. "b" -> "bpy" or "app" -> "bpy.app".
        self.aliases = {"bpy": "bpy"}

    def _flag(self, node: ast.AST, reason: str) -> None:
        self.violations.append(Violation(getattr(node, "lineno", 0), reason))

    def _dotted(self, node: ast.AST):
        """Resolve ``a.b.c`` to a full bpy path using known aliases, or None."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name) and node.id in self.aliases:
            return ".".join([self.aliases[node.id]] + list(reversed(parts)))
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_blocked_module(alias.name):
                self._flag(node, f"import of '{alias.name}' is not allowed")
            elif alias.name == "bpy" or alias.name.startswith("bpy."):
                if _is_blocked_bpy_path(alias.name):
                    self._flag(node, f"'{alias.name}' is not allowed")
                bound = alias.asname or alias.name.split(".", 1)[0]
                self.aliases[bound] = alias.name if alias.asname else "bpy"
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level == 0 and _is_blocked_module(module):
            self._flag(node, f"import from '{module}' is not allowed")
        elif module == "bpy" or module.startswith("bpy."):
            for alias in node.names:
                path = f"{module}.{alias.name}"
                if _is_blocked_bpy_path(path):
                    self._flag(node, f"'{path}' is not allowed")
                self.aliases[alias.asname or alias.name] = path
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        path = self._dotted(node.value)
        if path is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.aliases[target.id] = path
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BLOCKED_ATTRIBUTES:
            self._flag(node, f"attribute '{node.attr}' is not allowed")
        else:
            path = self._dotted(node)
            if path is not None and _is_blocked_bpy_path(path):
                self._flag(node, f"'{path}' is not allowed")
                return  # report the outermost path once, not every prefix
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in BLOCKED_CALLS:
                self._flag(node, f"call to '{func.id}()' is not allowed")
            elif func.id in ("getattr", "setattr", "delattr", "hasattr") and len(node.args) >= 2:
                name = node.args[1]
                if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                    self._flag(node, f"{func.id}() needs a literal attribute name in safe mode")
                elif name.value in BLOCKED_DYNAMIC_NAMES or name.value in BLOCKED_ATTRIBUTES:
                    self._flag(node, f"{func.id}() on '{name.value}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in ("__builtins__", "__import__"):
            self._flag(node, f"name '{node.id}' is not allowed")
        self.generic_visit(node)
