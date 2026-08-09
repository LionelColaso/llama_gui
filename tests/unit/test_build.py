from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from llamagui.backends.build import (
    BUILDABLE_BACKENDS,
    ToolchainMissing,
    _configure_args,
    build_backend,
    build_llama_swap,
    detect_cmake,
    detect_go,
    detect_msvc,
    get_toolchain_info,
    has_toolchain,
)


class TestToolchainDetection:
    def test_detect_msvc_not_windows(self) -> None:
        with patch("platform.system", return_value="Linux"):
            assert detect_msvc() is None

    def test_detect_cmake_missing(self) -> None:
        with patch(
            "llamagui.backends.build._run", side_effect=ToolchainMissing("cmake")
        ):
            assert detect_cmake() is None

    def test_detect_cmake_present(self) -> None:
        with patch("llamagui.backends.build._run", return_value="cmake version 3.30.0"):
            assert detect_cmake() == "3.30.0"

    def test_detect_go_missing(self) -> None:
        with patch("llamagui.backends.build._run", side_effect=ToolchainMissing("go")):
            assert detect_go() is None

    def test_detect_go_present(self) -> None:
        with patch(
            "llamagui.backends.build._run",
            return_value="go version go1.23.0 windows/amd64",
        ):
            assert detect_go() == "1.23.0"

    def test_has_toolchain_unknown_backend(self) -> None:
        assert has_toolchain("nonexistent") is False

    def test_has_toolchain_no_cmake(self) -> None:
        with patch("llamagui.backends.build.detect_cmake", return_value=None):
            assert has_toolchain("vulkan") is False

    def test_get_toolchain_info_structure(self) -> None:
        info = get_toolchain_info()
        assert "c_compiler" in info
        assert "cmake" in info
        assert "go" in info
        assert "ninja_or_make" in info


class TestToolchainMissing:
    def test_exception_message(self) -> None:
        exc = ToolchainMissing("go", "Install from https://go.dev")
        assert "go" in str(exc)
        assert "https://go.dev" in str(exc)

    def test_exception_no_hint(self) -> None:
        exc = ToolchainMissing("cmake")
        assert "cmake" in str(exc)


class TestBuildBackend:
    def test_missing_toolchain_cmake(self, tmp_path: Path) -> None:
        with (
            patch("llamagui.backends.build.detect_cmake", return_value=None),
            pytest.raises(ToolchainMissing, match="compiler"),
        ):
            build_backend("vulkan", tmp_path, tmp_path, tmp_path)

    def test_missing_toolchain_msvc(self, tmp_path: Path) -> None:
        with (
            patch("llamagui.backends.build.detect_cmake", return_value="3.30"),
            patch("llamagui.backends.build.detect_c_compiler", return_value=None),
            pytest.raises(ToolchainMissing, match="compiler"),
        ):
            build_backend("vulkan", tmp_path, tmp_path, tmp_path)

    def test_cuda_backend_requires_nvcc(self, tmp_path: Path) -> None:
        with (
            patch("llamagui.backends.build.detect_cmake", return_value="3.30"),
            patch("llamagui.backends.build.detect_c_compiler", return_value="cl"),
            patch("llamagui.backends.build.detect_nvcc", return_value=None),
        ):
            assert has_toolchain("cuda12") is False

    def test_cuda_arch_never_pascal(self, tmp_path: Path) -> None:
        """CUDA >= 13 dropped Pascal; compute_61 must never be emitted."""
        args = _configure_args("cuda13", tmp_path, tmp_path, "Release", "75")
        assert "-DCMAKE_CUDA_ARCHITECTURES=75" in args
        assert not any("61" in arg for arg in args)


class TestBuildLlamaSwap:
    def test_missing_go(self, tmp_path: Path) -> None:
        with (
            patch("llamagui.backends.build.detect_go", return_value=None),
            pytest.raises(ToolchainMissing, match="go"),
        ):
            build_llama_swap(tmp_path, tmp_path, tmp_path)


class TestBuildableBackends:
    def test_contains_expected(self) -> None:
        assert "vulkan" in BUILDABLE_BACKENDS
        assert "cuda12" in BUILDABLE_BACKENDS
        assert "cuda13" in BUILDABLE_BACKENDS

    def test_does_not_contain_unknown(self) -> None:
        assert "nonexistent" not in BUILDABLE_BACKENDS


class TestOrchestratorBuild:
    def test_build_no_submodules(self, tmp_path: Path) -> None:
        from llamagui.config import AppConfig
        from llamagui.orchestrator import Orchestrator

        cfg = AppConfig(root=str(tmp_path))
        orch = Orchestrator(cfg)
        results = orch._do_build(["vulkan"])
        assert len(results) >= 1
        assert results[0].name == "vulkan"
        assert results[0].status == "skipped"

    def test_build_submodule_no_toolchain(self, tmp_path: Path) -> None:
        """With sources but no compiler, `build` fails with the toolchain error.

        The CLI turns this into exit 7 so the caller can offer the prebuilt
        download instead of leaving the user with a cryptic build failure.
        """
        from llamagui.config import AppConfig
        from llamagui.orchestrator import Orchestrator

        vendor = tmp_path.parent / "vendor"
        (vendor / "llama.cpp").mkdir(parents=True, exist_ok=True)

        cfg = AppConfig(root=str(tmp_path))
        orch = Orchestrator(cfg)
        with (
            patch.object(orch, "_vendor_root", return_value=vendor),
            patch("llamagui.orchestrator.has_toolchain", return_value=False),
            pytest.raises(ToolchainMissing, match="prebuilt"),
        ):
            orch._do_build(["vulkan"])

    def test_build_action_in_description(self) -> None:
        from llamagui.config import AppConfig
        from llamagui.orchestrator import Orchestrator

        orch = Orchestrator(AppConfig())
        desc = orch.describe()
        assert "build" in desc.available_actions
