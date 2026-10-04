"""Run each hook as Claude Code would: JSON on stdin, inspect exit code and stdout/stderr."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")

CLAUDE_MD = textwrap.dedent("""\
    # Demo

    <!-- ## Backend in a comment must be ignored
    - paths: nope -->

    ## Backend
    - paths: src/api, tests/backend
    - test: {backend_test}
    - typecheck: <e.g. mypy src>

    ## Frontend
    - paths: src/app/
    - test: true
    - typecheck:

    ## Do not
    - paths: should/not/count
""")


def run_hook(name, payload):
    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, name)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


def denied(stdout):
    if not stdout.strip():
        return False
    return json.loads(stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


class RepoCase(unittest.TestCase):
    backend_test = "true"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.realpath(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.write("CLAUDE.md", CLAUDE_MD.format(backend_test=self.backend_test))
        self.git("add", "CLAUDE.md")
        self.git("commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def write(self, rel, content="x"):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path


class ConfigTest(RepoCase):
    def test_parses_layers_ignoring_comments_placeholders_and_other_sections(self):
        sys.path.insert(0, HOOKS)
        from factory_config import load_layers
        layers = load_layers(self.repo)
        self.assertEqual(layers["backend"]["paths"], ["src/api", "tests/backend"])
        self.assertEqual(layers["backend"]["typecheck"], "")
        self.assertEqual(layers["frontend"]["paths"], ["src/app"])
        self.assertEqual(set(layers), {"backend", "frontend"})


class PathScopeTest(RepoCase):
    def edit(self, agent_type, rel):
        payload = {"tool_name": "Write", "cwd": self.repo,
                   "tool_input": {"file_path": os.path.join(self.repo, rel)}}
        if agent_type:
            payload["agent_type"] = agent_type
        return run_hook("path_scope.py", payload)

    def test_main_session_unaffected(self):
        code, out, _ = self.edit(None, "anything/at/all.py")
        self.assertEqual(code, 0)
        self.assertFalse(denied(out))

    def test_backend_inside_own_paths_allowed(self):
        for rel in ("src/api/users.py", "tests/backend/test_users.py"):
            code, out, _ = self.edit("software-factory:backend-builder", rel)
            self.assertFalse(denied(out), rel)

    def test_backend_into_frontend_denied(self):
        _, out, _ = self.edit("software-factory:backend-builder", "src/app/page.tsx")
        self.assertTrue(denied(out))
        self.assertIn("OUT_OF_SCOPE", out)

    def test_prefix_is_not_a_match(self):
        _, out, _ = self.edit("software-factory:backend-builder", "src/apiary/x.py")
        self.assertTrue(denied(out))

    def test_frontend_into_backend_denied(self):
        _, out, _ = self.edit("software-factory:frontend-builder", "src/api/users.py")
        self.assertTrue(denied(out))

    def test_contract_folder_always_allowed(self):
        _, out, _ = self.edit("software-factory:backend-builder", ".factory/tickets/3/contract.md")
        self.assertFalse(denied(out))

    def test_validator_cannot_write(self):
        _, out, _ = self.edit("software-factory:validator", "src/api/users.py")
        self.assertTrue(denied(out))

    def test_unconfigured_layer_denied(self):
        self.write("CLAUDE.md", "# no layers\n")
        _, out, _ = self.edit("software-factory:frontend-builder", "src/app/page.tsx")
        self.assertTrue(denied(out))
        self.assertIn("not configured", out)

    def test_worktree_uses_its_own_root(self):
        wt = os.path.join(self.repo, ".factory", "worktrees", "1")
        self.git("worktree", "add", "-q", wt, "-b", "t1")
        payload = {"tool_name": "Edit", "cwd": self.repo, "agent_type": "software-factory:backend-builder",
                   "tool_input": {"file_path": os.path.join(wt, "src/api/new.py")}}
        _, out, _ = run_hook("path_scope.py", payload)
        self.assertFalse(denied(out))
        payload["tool_input"]["file_path"] = os.path.join(wt, "src/app/new.tsx")
        _, out, _ = run_hook("path_scope.py", payload)
        self.assertTrue(denied(out))


class BlockSecretsTest(RepoCase):
    def bash(self, command):
        return run_hook("block_secrets.py", {"tool_name": "Bash", "cwd": self.repo,
                                             "tool_input": {"command": command}})

    def test_git_add_all_with_env_blocked(self):
        self.write(".env", "KEY=1")
        self.write("app.py")
        code, _, err = self.bash("git add -A && git commit -m 'wip'")
        self.assertEqual(code, 2)
        self.assertIn(".env", err)

    def test_staged_key_blocked_on_commit(self):
        self.write("certs/server.pem")
        self.git("add", "certs/server.pem")
        code, _, err = self.bash('git commit -m "add cert"')
        self.assertEqual(code, 2)
        self.assertIn("server.pem", err)

    def test_env_example_and_normal_files_allowed(self):
        self.write(".env.example")
        self.write("src/api/x.py")
        code, _, _ = self.bash("git add . && git commit -m ok")
        self.assertEqual(code, 0)

    def test_gitignored_env_allowed(self):
        self.write(".gitignore", ".env\n")
        self.write(".env", "KEY=1")
        code, _, _ = self.bash("git add .")
        self.assertEqual(code, 0)

    def test_commit_all_flag_catches_tracked_secret(self):
        path = self.write("secrets.json", "{}")
        self.git("add", "-f", "secrets.json")
        self.git("commit", "-qm", "oops")
        with open(path, "w") as f:
            f.write('{"k": 1}')
        code, _, _ = self.bash("git commit -am 'update'")
        self.assertEqual(code, 2)

    def test_non_git_command_ignored(self):
        self.write(".env")
        code, _, _ = self.bash("ls -la && cat README.md")
        self.assertEqual(code, 0)


class StopGateTest(RepoCase):
    backend_test = "exit 1"

    def stop(self, agent_type, message, agent_id=None):
        return run_hook("stop_gate.py", {
            "hook_event_name": "SubagentStop", "cwd": "/",
            "agent_type": agent_type, "agent_id": agent_id or f"t-{id(self)}-{message[:5]}",
            "last_assistant_message": message,
        })

    def test_failing_tests_block_builder(self):
        _, out, _ = self.stop("software-factory:backend-builder", f"WORKTREE: {self.repo}\nSTATUS: DONE")
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_passing_layer_lets_builder_stop(self):
        _, out, _ = self.stop("software-factory:frontend-builder", f"WORKTREE: {self.repo}\nSTATUS: DONE")
        self.assertEqual(out.strip(), "")

    def test_blocked_report_skips_gate(self):
        _, out, _ = self.stop("software-factory:backend-builder",
                              f"WORKTREE: {self.repo}\nSTATUS: BLOCKED\nBLOCKERS:\n- OUT_OF_SCOPE: src/app/x")
        self.assertEqual(out.strip(), "")

    def test_other_agents_ignored(self):
        _, out, _ = self.stop("Explore", f"WORKTREE: {self.repo}")
        self.assertEqual(out.strip(), "")

    def test_gives_up_after_three_blocks(self):
        aid = f"cap-{os.getpid()}-{id(self)}"
        msg = f"WORKTREE: {self.repo}\nSTATUS: DONE"
        outs = [self.stop("software-factory:backend-builder", msg, aid)[1] for _ in range(4)]
        self.assertTrue(all(o.strip() for o in outs[:3]))
        self.assertEqual(outs[3].strip(), "")
        os.remove(os.path.join(tempfile.gettempdir(), f"software-factory-stopgate-{aid}"))


if __name__ == "__main__":
    unittest.main()
