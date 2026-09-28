"""Tests for app/tools/utils.py.

Replaces the old test_tool_decorator.py: `app.tools.tool_decorator` was removed
and its schema-extraction / handler-wrapping logic now lives in app/tools/utils.py
(the @builtin_tool decorator itself is covered in test_builtins.py).
"""

from __future__ import annotations

import os
import subprocess
import sys
import typing
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, TypedDict, Union
from unittest.mock import MagicMock, patch

import pytest

from app.common.errors import AppError
from app.llm.types import InputSchema
from app.tools import utils as U
from app.tools.types import CallContext


def _ctx(working_dir: str = "") -> CallContext:
    return CallContext(session_id="s", agent_id="a", working_dir=working_dir)


# ── cwd / path resolution ─────────────────────────────────────────────────────

class TestResolveCwd:
    def test_none_ctx(self):
        assert U._resolve_cwd(None) is None

    def test_empty_working_dir_is_none(self):
        assert U._resolve_cwd(_ctx("")) is None

    def test_working_dir_returned(self):
        assert U._resolve_cwd(_ctx("/tmp/x")) == "/tmp/x"


class TestResolvePath:
    def test_absolute_path_unchanged(self, tmp_path):
        assert U._resolve_path(str(tmp_path), _ctx("/other")) == Path(str(tmp_path))

    def test_relative_joined_with_cwd(self):
        assert U._resolve_path("a/b.txt", _ctx("/base")) == Path("/base") / "a/b.txt"

    def test_relative_without_cwd_is_left_relative(self):
        assert U._resolve_path("a.txt", None) == Path("a.txt")


class TestBashBlacklist:
    def test_is_a_list_of_patterns(self):
        assert isinstance(U.BASH_BLACKLIST, list) and len(U.BASH_BLACKLIST) == 10

    def test_patterns_compile(self):
        import re
        for p in U.BASH_BLACKLIST:
            re.compile(p)


# ── venv env ──────────────────────────────────────────────────────────────────

class TestPythonForVenv:
    def test_unfrozen_returns_sys_executable(self):
        assert U._python_for_venv() == sys.executable

    def test_frozen_finds_python_on_path(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/python3" if n == "python3" else None)
        ok = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=ok):
            assert U._python_for_venv() == "/usr/bin/python3"

    def test_frozen_skips_windows_store_stub(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        paths = {"python3": r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python3.exe",
                 "python": r"C:\Python311\python.exe"}
        monkeypatch.setattr("shutil.which", lambda n: paths.get(n))
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            assert U._python_for_venv() == r"C:\Python311\python.exe"

    def test_frozen_skips_nonzero_version_check(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            with pytest.raises(AppError) as e:
                U._python_for_venv()
        assert e.value.code == "PYTHON_NOT_FOUND"

    def test_frozen_skips_when_subprocess_raises(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
        with patch("subprocess.run", side_effect=OSError("boom")):
            with pytest.raises(AppError):
                U._python_for_venv()

    def test_frozen_no_python_at_all(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr("shutil.which", lambda n: None)
        with pytest.raises(AppError) as e:
            U._python_for_venv()
        assert e.value.code == "PYTHON_NOT_FOUND"


class TestBuildVenvEnv:
    def test_no_cwd_returns_none(self):
        assert U.build_venv_env(None) is None
        assert U.build_venv_env("") is None

    def _fake_venv(self, cwd: Path) -> Path:
        if sys.platform == "win32":
            d = cwd / ".venv" / "Scripts"
            exe = d / "python.exe"
        else:
            d = cwd / ".venv" / "bin"
            exe = d / "python"
        d.mkdir(parents=True)
        exe.write_text("", encoding="utf-8")
        return d

    def test_existing_venv_is_activated(self, tmp_path):
        scripts = self._fake_venv(tmp_path)
        env = U.build_venv_env(str(tmp_path))
        assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")
        assert env["PATH"].startswith(str(scripts) + os.pathsep)
        assert "PYTHONHOME" not in env

    def test_pythonhome_is_stripped(self, tmp_path, monkeypatch):
        self._fake_venv(tmp_path)
        monkeypatch.setenv("PYTHONHOME", "/somewhere")
        assert "PYTHONHOME" not in U.build_venv_env(str(tmp_path))

    def test_missing_venv_is_created(self, tmp_path):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as run:
            env = U.build_venv_env(str(tmp_path))
        assert run.call_args.args[0][1:3] == ["-m", "venv"]
        assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")

    def test_venv_creation_failure_raises(self, tmp_path):
        failed = MagicMock(returncode=1, stdout="out", stderr="err")
        with patch("subprocess.run", return_value=failed):
            with pytest.raises(AppError) as e:
                U.build_venv_env(str(tmp_path))
        assert e.value.code == "VENV_CREATE_FAILED"
        assert "outerr" in e.value.message


# ── JSON schema mapping ───────────────────────────────────────────────────────

class TestPyTypeToJsonSchema:
    @pytest.mark.parametrize("py,expected", [
        (bool, {"type": "boolean"}),
        (int, {"type": "integer"}),
        (float, {"type": "number"}),
        (str, {"type": "string"}),
        (dict, {"type": "object"}),
        (object, {"type": "string"}),      # unknown falls back to string
    ])
    def test_scalars(self, py, expected):
        assert U._py_type_to_json_schema(py) == expected

    def test_bare_list(self):
        assert U._py_type_to_json_schema(list) == {"type": "array"}

    def test_typed_list(self):
        assert U._py_type_to_json_schema(list[str]) == {
            "type": "array", "items": {"type": "string"}}

    def test_typed_dict_origin(self):
        assert U._py_type_to_json_schema(dict[str, int]) == {"type": "object"}

    def test_annotated_adds_description(self):
        assert U._py_type_to_json_schema(Annotated[int, "count"]) == {
            "type": "integer", "description": "count"}

    def test_annotated_non_string_metadata_ignored(self):
        assert U._py_type_to_json_schema(Annotated[int, 42]) == {"type": "integer"}

    def test_optional_unwrapped(self):
        assert U._py_type_to_json_schema(Optional[int]) == {"type": "integer"}

    def test_pep604_optional_unwrapped(self):
        assert U._py_type_to_json_schema(int | None) == {"type": "integer"}

    def test_multi_member_union_falls_back_to_string(self):
        assert U._py_type_to_json_schema(Union[int, str]) == {"type": "string"}

    def test_literal_of_strings(self):
        assert U._py_type_to_json_schema(Literal["a", "b"]) == {
            "type": "string", "enum": ["a", "b"]}

    def test_literal_of_ints(self):
        assert U._py_type_to_json_schema(Literal[1, 2]) == {
            "type": "integer", "enum": [1, 2]}

    def test_literal_of_bools_stringified(self):
        out = U._py_type_to_json_schema(Literal[True, False])
        assert out == {"type": "string", "enum": ["True", "False"]}

    def test_empty_literal(self):
        assert U._py_type_to_json_schema(Literal[()]) == {"type": "string", "enum": []}

    def test_pydantic_model_uses_model_json_schema(self):
        class M:
            @staticmethod
            def model_json_schema():
                return {"type": "object", "title": "M"}

        assert U._py_type_to_json_schema(M) == {"type": "object", "title": "M"}

    def test_typed_dict_expanded(self):
        class Row(TypedDict):
            a: str
            b: int

        out = U._py_type_to_json_schema(Row)
        assert out["type"] == "object"
        assert out["properties"] == {"a": {"type": "string"}, "b": {"type": "integer"}}
        assert set(out["required"]) == {"a", "b"}

    def test_total_false_typed_dict_has_no_required(self):
        class Opt(TypedDict, total=False):
            a: str

        out = U._py_type_to_json_schema(Opt)
        assert "required" not in out

    def test_typed_dict_with_unresolvable_hints(self, monkeypatch):
        class Row(TypedDict):
            a: str

        def boom(*a, **k):
            raise NameError("nope")

        monkeypatch.setattr(typing, "get_type_hints", boom)
        out = U._py_type_to_json_schema(Row)
        assert out["type"] == "object" and "a" in out["properties"]


# ── hint resolution ───────────────────────────────────────────────────────────

class TestResolveHints:
    def test_resolves_normally(self):
        def fn(a: int, b: str = "x") -> None: ...
        hints = U._resolve_hints(fn)
        assert hints["a"] is int and hints["b"] is str

    def test_falls_back_per_annotation_when_get_type_hints_fails(self, monkeypatch):
        def fn(a: int, b: "Undefined") -> None: ...  # noqa: F821
        fn.__annotations__ = {"a": "int", "b": "NotARealName"}
        fn.__globals__["int"] = int
        monkeypatch.setattr(typing, "get_type_hints",
                            lambda *a, **k: (_ for _ in ()).throw(NameError("x")))
        hints = U._resolve_hints(fn)
        assert hints["a"] is int
        assert "b" not in hints            # unresolvable name is skipped, not fatal

    def test_non_string_annotation_kept_on_fallback(self, monkeypatch):
        def fn(a) -> None: ...
        fn.__annotations__ = {"a": int}
        monkeypatch.setattr(typing, "get_type_hints",
                            lambda *a, **k: (_ for _ in ()).throw(NameError("x")))
        assert U._resolve_hints(fn)["a"] is int

    def test_object_without_annotations(self, monkeypatch):
        monkeypatch.setattr(typing, "get_type_hints",
                            lambda *a, **k: (_ for _ in ()).throw(TypeError("x")))
        assert U._resolve_hints(object()) == {}


# ── extract_input_schema ──────────────────────────────────────────────────────

class TestExtractInputSchema:
    def test_required_vs_optional(self):
        def fn(a: Annotated[str, "the a"], b: Annotated[int, "the b"] = 3) -> None: ...
        s = U.extract_input_schema(fn)
        assert isinstance(s, InputSchema)
        assert s.type == "object"
        assert s.properties["a"] == {"type": "string", "description": "the a"}
        assert s.properties["b"] == {"type": "integer", "description": "the b"}
        assert s.require == ["a"]

    def test_exclude_drops_params(self):
        def fn(a: str, ctx=None) -> None: ...
        s = U.extract_input_schema(fn, exclude={"ctx"})
        assert list(s.properties) == ["a"]

    def test_exclude_none_keeps_everything(self):
        def fn(a: str, ctx=None) -> None: ...
        assert set(U.extract_input_schema(fn).properties) == {"a", "ctx"}

    def test_unannotated_param_defaults_to_string(self):
        def fn(a) -> None: ...
        assert U.extract_input_schema(fn).properties["a"] == {"type": "string"}

    def test_plain_annotation_without_description(self):
        def fn(a: int) -> None: ...
        assert U.extract_input_schema(fn).properties["a"] == {"type": "integer"}

    def test_annotated_without_description_metadata(self):
        def fn(a: Annotated[int, 99]) -> None: ...
        assert U.extract_input_schema(fn).properties["a"] == {"type": "integer"}

    def test_no_params(self):
        def fn() -> None: ...
        s = U.extract_input_schema(fn)
        assert s.properties == {} and s.require == []


# ── make_tool_handler ─────────────────────────────────────────────────────────

class TestMakeToolHandler:
    def test_maps_arguments_by_name(self):
        def fn(a: str, b: int, *, ctx=None):
            return (a, b, ctx)

        h = U.make_tool_handler(fn)
        assert h({"a": "x", "b": 2}, "CTX") == ("x", 2, "CTX")

    def test_defaults_used_for_missing_args(self):
        def fn(a: str, b: int = 7, *, ctx=None):
            return (a, b)

        assert U.make_tool_handler(fn)({"a": "x"}) == ("x", 7)

    def test_ctx_defaults_to_none(self):
        def fn(*, ctx=None):
            return ctx

        assert U.make_tool_handler(fn)({}) is None

    def test_extra_arguments_ignored(self):
        def fn(a: str, *, ctx=None):
            return a

        assert U.make_tool_handler(fn)({"a": "x", "unexpected": 1}) == "x"

    def test_missing_required_argument_raises_type_error(self):
        def fn(a: str, *, ctx=None):
            return a

        with pytest.raises(TypeError):
            U.make_tool_handler(fn)({})

    def test_ctx_positional_in_signature_is_still_skipped(self):
        def fn(a: str, ctx=None):
            return (a, ctx)

        assert U.make_tool_handler(fn)({"a": "v"}, "C") == ("v", "C")
