"""agent-notify 的测试：python3 -m unittest discover -s tests

全部在临时 HOME 里跑，不碰真实配置；推送发到本机的假 Bark 服务器。
"""
import http.server
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "agent-notify")


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def write_jsonl(path, rows):
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in rows) + "\n")


class FakeBark(http.server.BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        FakeBark.received.append((self.path, json.loads(body)))
        out = json.dumps({"code": 200, "message": "success"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="agent-notify-test-")
        self.old_env = dict(os.environ)
        os.environ["HOME"] = self.home
        os.environ.pop("CODEX_HOME", None)
        for k in ("BARK_URL", "BARK_KEY", "BARK_SERVER"):
            os.environ.pop(k, None)
        loader = importlib.machinery.SourceFileLoader("agent_notify", SCRIPT)
        spec = importlib.util.spec_from_loader("agent_notify", loader)
        self.an = importlib.util.module_from_spec(spec)
        loader.exec_module(self.an)          # 模块常量按临时 HOME 计算
        self.conf = dict(self.an.DEFAULTS)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        shutil.rmtree(self.home, ignore_errors=True)


class TestBuild(Base):
    def claude_transcript(self, started_ago, now):
        p = os.path.join(self.home, "c.jsonl")
        write_jsonl(p, [
            {"type": "user", "timestamp": iso(now - started_ago),
             "message": {"content": [{"type": "text", "text": "帮我重构"}]}},
            {"type": "assistant", "timestamp": iso(now - started_ago + 5),
             "message": {"content": [{"type": "tool_use", "id": "t1"}]}},
            {"type": "user", "timestamp": iso(now - 10),            # 工具结果，不算新一轮
             "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}},
            {"type": "assistant", "timestamp": iso(now - 2),
             "message": {"content": [{"type": "text", "text": "重构完成，测试全过。"}]}},
        ])
        return p

    def test_claude_stop_long_turn_notifies(self):
        now = time.time()
        p = self.claude_transcript(300, now)
        res, why = self.an.build("claude", {"hook_event_name": "Stop", "transcript_path": p,
                                            "cwd": "/x/my-proj"}, self.conf, now=now)
        self.assertIsNone(why)
        self.assertEqual(res[0], "✅ Claude 干完了 · my-proj")
        self.assertEqual(res[1], "用时 5 分 0 秒")
        self.assertNotIn("重构完成", res[1])                         # 默认不带回复内容

    def test_claude_stop_short_turn_skipped(self):
        now = time.time()
        p = self.claude_transcript(20, now)
        res, why = self.an.build("claude", {"hook_event_name": "Stop", "transcript_path": p}, self.conf, now=now)
        self.assertIsNone(res)
        self.assertIn("< 60", why)

    def test_preview_and_tag(self):
        now = time.time()
        p = self.claude_transcript(120, now)
        self.conf.update(preview=True, tag="老Mac")
        res, _ = self.an.build("claude", {"hook_event_name": "Stop", "transcript_path": p}, self.conf, now=now)
        self.assertEqual(res[0], "✅ [老Mac] Claude 干完了 · 临时会话")
        self.assertIn("重构完成，测试全过。", res[1])

    def test_unknown_duration_still_notifies(self):
        res, _ = self.an.build("claude", {"hook_event_name": "Stop", "transcript_path": "/nope"}, self.conf)
        self.assertEqual(res[1], "这一轮结束了")

    def test_stop_hook_active_skipped(self):
        res, why = self.an.build("claude", {"hook_event_name": "Stop", "stop_hook_active": True}, self.conf)
        self.assertIsNone(res)

    def test_notification_types(self):
        res, _ = self.an.build("claude", {"hook_event_name": "Notification", "notification_type": "idle_prompt",
                                          "message": "Claude is waiting for your input"}, self.conf)
        self.assertIsNone(res)
        res, _ = self.an.build("claude", {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                                          "message": "Claude needs your permission to use Bash"}, self.conf)
        self.assertTrue(res[0].startswith("🔔 Claude 在等你"))

    def test_codex_stop_and_permission(self):
        now = time.time()
        p = os.path.join(self.home, "rollout.jsonl")
        write_jsonl(p, [
            {"timestamp": iso(now - 900), "type": "event_msg",
             "payload": {"type": "task_started", "turn_id": "OLD", "started_at": str(int(now - 900))}},
            {"timestamp": iso(now - 90), "type": "event_msg",
             "payload": {"type": "task_started", "turn_id": "T2", "started_at": str(int(now - 90))}},
        ])
        res, _ = self.an.build("codex", {"hook_event_name": "Stop", "transcript_path": p, "turn_id": "T2",
                                         "cwd": "/a/api"}, self.conf, now=now)
        self.assertEqual(res[:2], ("✅ Codex 干完了 · api", "用时 1 分 30 秒"))
        res, _ = self.an.build("codex", {"hook_event_name": "PermissionRequest", "tool_name": "Bash"}, self.conf)
        self.assertEqual(res[1], "要用 Bash")

    def test_scratch_dir_name_hidden(self):
        self.assertEqual(self.an.place_name("/tmp/scratch-2026-10-08-abc"), "")
        self.assertEqual(self.an.place_name("/Users/a/Code/web/"), "web")


class TestBarkConfig(Base):
    def test_parse_bark_url(self):
        self.assertEqual(self.an.parse_bark_url("https://api.day.app/AbC123/标题/正文"),
                         ("https://api.day.app", "AbC123"))
        self.assertEqual(self.an.parse_bark_url("not a url"), (None, None))

    def test_target_precedence(self):
        os.makedirs(os.path.dirname(self.an.LEGACY_BARK_CONF))
        with open(self.an.LEGACY_BARK_CONF, "w") as f:
            json.dump({"server": "https://legacy.example", "key": "LEG"}, f)
        self.assertEqual(self.an.bark_target(self.conf), ("https://legacy.example", "LEG"))
        self.conf["bark_url"] = "https://api.day.app/CONF"
        self.assertEqual(self.an.bark_target(self.conf), ("https://api.day.app", "CONF"))
        os.environ["BARK_KEY"] = "ENV"
        self.assertEqual(self.an.bark_target(self.conf), ("https://api.day.app", "ENV"))


class TestInstall(Base):
    def test_install_uninstall_keeps_other_hooks(self):
        claude = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(os.path.dirname(claude))
        other = {"type": "command", "command": "echo other"}
        with open(claude, "w") as f:
            json.dump({"model": "opus", "hooks": {"Stop": [{"hooks": [other]}]}}, f)
        self.assertEqual(self.an.add_hooks(claude, "claude", "/opt/my tools/agent-notify"), ["Stop", "Notification"])
        self.assertEqual(self.an.add_hooks(claude, "claude", "/opt/my tools/agent-notify"), [])   # 幂等
        d = json.load(open(claude))
        self.assertEqual(d["model"], "opus")
        cmds = [h["command"] for g in d["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(cmds, ["echo other", "'/opt/my tools/agent-notify' hook claude"])
        self.assertEqual(self.an.remove_hooks(claude, "claude"), ["Notification", "Stop"])
        d = json.load(open(claude))
        self.assertEqual(d["hooks"], {"Stop": [{"hooks": [other]}]})

    def test_codex_hooks_created_when_missing(self):
        path = os.path.join(self.home, ".codex", "hooks.json")
        os.makedirs(os.path.dirname(path))
        self.assertEqual(self.an.add_hooks(path, "codex", "/bin/agent-notify"), ["Stop", "PermissionRequest"])
        self.assertEqual(json.load(open(path))["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 15)

    def test_toml_set_trust(self):
        key = "/h/.codex/hooks.json:stop:1:0"
        text = '[features]\nhooks = true\n\n[hooks.state."/h/.codex/hooks.json:stop:0:0"]\ntrusted_hash = "sha256:aa"\n'
        once = self.an.toml_set_trust(text, key, "sha256:bb")
        self.assertIn('[hooks.state."%s"]\ntrusted_hash = "sha256:bb"' % key, once)
        twice = self.an.toml_set_trust(once, key, "sha256:cc")
        self.assertEqual(twice.count(key), 1)
        self.assertIn('trusted_hash = "sha256:cc"', twice)
        self.assertIn('trusted_hash = "sha256:aa"', twice)              # 别的条目不动

    def test_features_detection(self):
        os.makedirs(os.path.dirname(self.an.CODEX_CONFIG))
        with open(self.an.CODEX_CONFIG, "w") as f:
            f.write('model = "x"\n[features]\ncodex_hooks = true\n')
        self.assertTrue(self.an.codex_hooks_feature_on())
        with open(self.an.CODEX_CONFIG, "w") as f:
            f.write('hooks = true\n[features]\nother = true\n')
        self.assertFalse(self.an.codex_hooks_feature_on())


class TestHookEndToEnd(Base):
    def test_hook_returns_fast_and_pushes_in_background(self):
        FakeBark.received = []
        srv = http.server.HTTPServer(("127.0.0.1", 0), FakeBark)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            env = dict(os.environ, HOME=self.home, BARK_URL="http://127.0.0.1:%d/TESTKEY" % srv.server_port)
            env.pop("AGENT_NOTIFY_FOREGROUND", None)
            ev = {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "cwd": "/w/demo"}
            t0 = time.time()
            r = subprocess.run([sys.executable, SCRIPT, "hook", "codex"], input=json.dumps(ev),
                               capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(r.returncode, 0)
            self.assertLess(time.time() - t0, 3)
            for _ in range(50):
                if FakeBark.received:
                    break
                time.sleep(0.1)
            path, msg = FakeBark.received[0]
            self.assertEqual(path, "/push")
            self.assertEqual(msg["device_key"], "TESTKEY")
            self.assertEqual(msg["title"], "🔔 Codex 在等你批准 · demo")
            self.assertEqual(msg["group"], "Codex")

            os.makedirs(os.path.join(self.home, ".config", "agent-notify"), exist_ok=True)
            open(os.path.join(self.home, ".config", "agent-notify", "muted"), "w").close()
            FakeBark.received = []
            subprocess.run([sys.executable, SCRIPT, "hook", "codex"], input=json.dumps(ev),
                           capture_output=True, text=True, env=env, timeout=10)
            time.sleep(1)
            self.assertEqual(FakeBark.received, [])                  # 静音时不推
        finally:
            srv.shutdown()

    def test_bad_input_never_fails(self):
        r = subprocess.run([sys.executable, SCRIPT, "hook", "claude"], input="not json",
                           capture_output=True, text=True, env=dict(os.environ, HOME=self.home), timeout=10)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
