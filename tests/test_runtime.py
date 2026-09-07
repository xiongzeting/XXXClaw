from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import shutil

from MiniClaw.llm.types import ToolInvocation
from MiniClaw.coding_agent.runtime import (
    DockerCommandExecutor,
    RuntimeSettings,
    RunStateStore,
    WorkspaceGuard,
    create_tool_runtime,
    is_safe_snapshot_path,
    load_runtime_settings,
    discover_execution_capabilities,
)
from MiniClaw.coding_agent.tools import ToolExecutor
from MiniClaw.coding_agent.tools.factory import create_coding_tools


class RuntimeConfigTests(unittest.TestCase):
    def test_docker_direct_is_default_and_miniclaw_resource_settings_are_supported(self) -> None:
        defaults = load_runtime_settings({})
        self.assertEqual(defaults.sandbox, "docker:miniclaw-runtime:py311")
        self.assertEqual(defaults.workspace_mode, "direct")
        settings = load_runtime_settings(
            {
                "MINICLAW_DOCKER_CPUS": "2",
                "MINICLAW_DOCKER_MEMORY_MB": "768",
                "MINICLAW_DOCKER_PIDS_LIMIT": "128",
                "MINICLAW_DOCKER_NETWORK": "bridge",
                "MINICLAW_DOCKER_TMPFS_MB": "64",
            },
            sandbox="docker:python:3.12-slim",
            workspace_mode="snapshot",
        )
        self.assertEqual(settings.backend, "docker")
        self.assertEqual(settings.docker_image, "python:3.12-slim")
        self.assertEqual(settings.docker_cpus, 2)
        self.assertEqual(settings.docker_network, "bridge")
        self.assertEqual(settings.workspace_mode, "snapshot")
        self.assertEqual(load_runtime_settings({}, sandbox="host").backend, "host")

    def test_invalid_limits_fail_before_runtime_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 64"):
            load_runtime_settings(
                {"MINICLAW_DOCKER_MEMORY_MB": "32"},
                sandbox="docker",
            )


class RunStateTests(unittest.TestCase):
    def test_inflight_run_is_marked_interrupted_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStateStore(directory, "session")
            store.begin("run-1", "repair parser")
            store.transition("waiting_model", turn=2)
            store.tool_started("call-1", "bash")
            store.transition("executing_tool", owner_pid=999_999_999)

            recovered = RunStateStore(directory, "session").recover_interrupted()

            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual(recovered.status, "interrupted")
            self.assertIn("tool=bash/call-1", recovered.recovery_note or "")
            self.assertIsNone(recovered.active_tool_call_id)

    def test_completed_run_is_not_reclassified_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStateStore(directory, "session")
            store.begin("run-1", "done")
            store.transition("completed", stop_reason="stop")
            recovered = RunStateStore(directory, "session").recover_interrupted()
            self.assertEqual(recovered.status if recovered else None, "completed")


class WorkspaceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_paths_and_protected_files_share_one_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "workspace"
            source.mkdir()
            public = source / "src" / "app.py"
            public.parent.mkdir()
            public.write_text("print('ok')\n", encoding="utf-8")
            secret = source / ".env"
            secret.write_text("SECRET=value\n", encoding="utf-8")
            guard = WorkspaceGuard(source, "/workspace", [secret])

            self.assertEqual(guard.resolve("/workspace/src/app.py"), public.resolve())
            self.assertEqual(guard.to_execution_path(public), "/workspace/src/app.py")
            with self.assertRaisesRegex(PermissionError, "protected"):
                guard.resolve(".env", must_exist=True)
            with self.assertRaisesRegex(PermissionError, "outside"):
                guard.resolve("/workspace/../outside.txt")

    async def test_snapshot_is_session_local_and_filters_secrets_and_heavy_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            (source / ".env").write_text("SECRET=value\n", encoding="utf-8")
            (source / ".ssh").mkdir()
            (source / ".ssh" / "id_rsa").write_text("private\n", encoding="utf-8")
            (source / ".venv").mkdir()
            (source / ".venv" / "ignored.py").write_text("x\n", encoding="utf-8")
            session = root / "session"
            runtime = create_tool_runtime(
                source,
                session,
                "task-1",
                RuntimeSettings(backend="host", workspace_mode="snapshot"),
            )

            self.assertNotEqual(runtime.host_workspace, source)
            self.assertTrue((runtime.host_workspace / "app.py").is_file())
            self.assertFalse((runtime.host_workspace / ".env").exists())
            self.assertFalse((runtime.host_workspace / ".ssh").exists())
            self.assertFalse((runtime.host_workspace / ".venv").exists())
            (runtime.host_workspace / "task-only.txt").write_text("kept\n", encoding="utf-8")

            reopened = create_tool_runtime(
                source,
                session,
                "task-1",
                RuntimeSettings(backend="host", workspace_mode="snapshot"),
            )
            self.assertTrue((reopened.host_workspace / "task-only.txt").is_file())
            self.assertTrue((session / "sandbox" / "manifest.json").is_file())

    async def test_direct_runtime_protects_secret_file_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("SECRET=value\n", encoding="utf-8")
            runtime = create_tool_runtime(
                root,
                root / ".aster" / "session",
                "direct",
                RuntimeSettings(backend="host"),
            )
            with self.assertRaisesRegex(PermissionError, "protected"):
                runtime.workspace.resolve(".env", must_exist=True)
            with self.assertRaisesRegex(PermissionError, "protected"):
                runtime.workspace.resolve(".aster/session", must_exist=True)
            output_dir = root / ".aster" / "tool-output"
            output_dir.mkdir()
            self.assertEqual(runtime.workspace.resolve(".aster/tool-output"), output_dir.resolve())
            self.assertEqual(runtime.execution_workspace, str(root.resolve()))

    async def test_paths_created_after_startup_use_operation_aware_protection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guard = WorkspaceGuard(root)

            goal = root / ".aster" / "goal.json"
            goal.parent.mkdir()
            goal.write_text("{}\n", encoding="utf-8")
            secret = root / ".env"
            secret.write_text("TOKEN=value\n", encoding="utf-8")
            artifact = root / ".aster" / "tool-output" / "result.log"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("safe output\n", encoding="utf-8")
            git_head = root / ".git" / "HEAD"
            git_head.parent.mkdir()
            git_head.write_text("ref: refs/heads/main\n", encoding="utf-8")
            git_config = root / ".git" / "config"
            git_config.write_text("[remote \"origin\"]\n", encoding="utf-8")
            ssh_key = root / "nested" / ".ssh" / "id_rsa"
            ssh_key.parent.mkdir(parents=True)
            ssh_key.write_text("private\n", encoding="utf-8")
            credentials = root / "config" / "credentials"
            credentials.parent.mkdir()
            credentials.write_text("token=value\n", encoding="utf-8")

            for path in (goal, secret, git_config, ssh_key, credentials):
                with self.assertRaisesRegex(PermissionError, "protected"):
                    guard.resolve(path, access="read", must_exist=True)
                with self.assertRaisesRegex(PermissionError, "protected"):
                    guard.resolve(path, access="write", must_exist=True)

            self.assertEqual(
                guard.resolve(artifact, access="read", must_exist=True),
                artifact.resolve(),
            )
            with self.assertRaisesRegex(PermissionError, "protected"):
                guard.resolve(artifact, access="write", must_exist=True)
            self.assertEqual(
                guard.resolve(git_head, access="read", must_exist=True),
                git_head.resolve(),
            )
            with self.assertRaisesRegex(PermissionError, "protected"):
                guard.resolve(git_head, access="write", must_exist=True)

    async def test_factory_routes_bash_through_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = create_tool_runtime(
                root,
                root / ".aster" / "session",
                "factory",
                RuntimeSettings(backend="host", default_command_timeout_seconds=5),
            )
            executor = ToolExecutor()
            for tool in create_coding_tools(runtime):
                executor.register(tool)
            result = await executor.execute(
                ToolInvocation("bash-1", "bash", {"command": "echo runtime-ok"})
            )
            self.assertFalse(result.is_error)
            self.assertIn("runtime-ok", result.content)
            self.assertEqual(result.details["runtime"], "host")

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is required")
    async def test_grep_does_not_cross_protected_file_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "public.txt").write_text("needle public\n", encoding="utf-8")
            (root / ".env").write_text("needle secret\n", encoding="utf-8")
            runtime = create_tool_runtime(
                root,
                root / ".aster" / "session",
                "grep",
                RuntimeSettings(backend="host"),
            )
            executor = ToolExecutor()
            for tool in create_coding_tools(runtime):
                executor.register(tool)
            result = await executor.execute(
                ToolInvocation("grep-1", "grep", {"pattern": "needle"})
            )
            self.assertIn("public.txt", result.content)
            self.assertNotIn(".env", result.content)
            self.assertNotIn("secret", result.content)


class DockerArgumentTests(unittest.TestCase):
    def test_capability_discovery_reports_real_paths_and_runtime(self) -> None:
        result = discover_execution_capabilities(runtime="host")
        self.assertEqual(result["runtime"], "host")
        self.assertIsInstance(result["registered"], list)
        self.assertIn("edge", result["environment"])
        self.assertIn("playwright", result["environment"])

    def test_docker_args_apply_resource_security_mount_and_environment_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            secret = workspace / ".env"
            secret.write_text("SECRET=value\n", encoding="utf-8")
            guard = WorkspaceGuard(workspace, "/workspace", [secret])
            settings = RuntimeSettings(
                backend="docker",
                docker_image="python:3.11-slim",
                docker_network="none",
            )
            executor = DockerCommandExecutor(settings, guard, root / "session", "task")
            args = executor.build_run_args("miniclaw-test", "python -V")
            rendered = " ".join(args)

            self.assertIn("--read-only", args)
            self.assertIn("--cap-drop ALL", rendered)
            self.assertIn("--security-opt no-new-privileges", rendered)
            self.assertIn("--network none", rendered)
            self.assertIn("--memory-swap 1024m", rendered)
            self.assertIn("--pids-limit 256", rendered)
            self.assertIn("target=/workspace", rendered)
            self.assertIn("target=/workspace/.env,readonly", rendered)
            self.assertIn("HOME=/tmp", args)
            self.assertNotIn("SECRET=value", rendered)
            self.assertEqual(args[-3:], ["sh", "-c", "python -V"])

    def test_snapshot_path_policy_reuses_the_canonical_protection_rules(self) -> None:
        self.assertTrue(is_safe_snapshot_path("src/app.py"))
        self.assertTrue(is_safe_snapshot_path(".git/HEAD"))
        self.assertFalse(is_safe_snapshot_path(".env.production"))
        self.assertFalse(is_safe_snapshot_path("nested/.ssh/id_rsa"))
        self.assertFalse(is_safe_snapshot_path(".git/config"))
        self.assertFalse(is_safe_snapshot_path("config/credentials"))

    def test_docker_masks_sensitive_files_created_after_executor_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            guard = WorkspaceGuard(workspace, "/workspace")
            settings = RuntimeSettings(
                backend="docker",
                docker_image="python:3.11-slim",
            )
            executor = DockerCommandExecutor(settings, guard, root / "session", "task")

            (workspace / ".env").write_text("TOKEN=value\n", encoding="utf-8")
            aster = workspace / ".aster"
            aster.mkdir()
            (aster / "goal.json").write_text("{}\n", encoding="utf-8")

            rendered = " ".join(executor.build_run_args("miniclaw-test", "pwd"))
            self.assertIn("target=/workspace/.env,readonly", rendered)
            self.assertIn("target=/workspace/.aster,readonly", rendered)


if __name__ == "__main__":
    unittest.main()
