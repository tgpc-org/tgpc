"""CLI dispatch tests for `python3 -m tgpc`.

`__main__.py` is the entry point the runbook and the Makefile actually call, so
what matters here is the wiring: which Manager method each subcommand reaches,
what happens when a destination fails, and the guards that protect production
state (`fetch-dg --no-resume` must not wipe a checkpoint by accident).

Nothing here touches the network, the Keychain, WARP or Supabase: Manager,
warp-cli, the keychain helpers and `run_fetch` are all patched.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import tgpc.__main__ as cli


def run_main(argv, **patches):
    """Run cli.main() with argv and return the patched Manager instance.

    `write_run_status`, `egress_ip` and `send_alert` are stubbed for every
    test: the CLI records the outcome of each run and alerts on failure, and
    tests must neither touch `data/last_run.json` nor the network.
    """
    mgr = MagicMock()
    defaults = {
        "Manager": MagicMock(return_value=mgr),
        "_warp_connect": MagicMock(return_value=False),
        "Phase": lambda *a, **kw: contextlib.nullcontext(),
        "write_run_status": MagicMock(return_value={"status": "recorded"}),
        "egress_ip": MagicMock(return_value="49.37.155.92"),
        "send_alert": MagicMock(return_value=True),
    }
    defaults.update(patches)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(sys, "argv", ["tgpc", *argv]))
        for name, value in defaults.items():
            stack.enter_context(patch.object(cli, name, value))
        cli.main()
    return mgr


class DispatchTests(unittest.TestCase):
    def test_no_command_prints_help_and_never_builds_a_manager(self):
        manager_cls = MagicMock()
        with (
            patch.object(sys, "argv", ["tgpc"]),
            patch.object(cli, "Manager", manager_cls),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertIsNone(cli.main())
        manager_cls.assert_not_called()

    def test_sync_reaches_every_destination(self):
        mgr = run_main(["sync"])
        for name in (
            "sync_to_supabase",
            "sync_to_supabase_storage",
            "sync_to_r2",
            "sync_to_gdrive",
            "sync_to_release",
            "sync_to_email",
        ):
            getattr(mgr, name).assert_called_once()

    def test_sync_fails_the_run_when_any_destination_fails(self):
        mgr = MagicMock()
        for name in (
            "sync_to_supabase",
            "sync_to_supabase_storage",
            "sync_to_r2",
            "sync_to_gdrive",
            "sync_to_release",
            "sync_to_email",
        ):
            setattr(mgr, name, MagicMock(return_value=True))
        mgr.sync_to_r2 = MagicMock(return_value=False)

        with self.assertRaises(SystemExit) as ctx:
            run_main(["sync"], Manager=MagicMock(return_value=mgr))
        self.assertEqual(ctx.exception.code, 1)

    def test_enrich_and_retry_photos_dispatch(self):
        mgr = run_main(["enrich", "--force"])
        mgr.enrich_new_records.assert_called_once_with(force=True)

        mgr = run_main(["retry-photos"])
        mgr.retry_photos.assert_called_once()

    def test_quota_dispatches(self):
        with (
            patch.object(sys, "argv", ["tgpc", "quota"]),
            patch.object(cli, "show_quotas") as show,
            patch.object(cli, "Phase", lambda *a, **kw: contextlib.nullcontext()),
        ):
            cli.main()
        show.assert_called_once()

    def test_dispatch_passes_its_flags_through(self):
        import tgpc.dispatch as dispatch_mod

        report = dispatch_mod.SyncReport(
            published=242, already_present=220, missing=22, uploaded=22, newest="DL16102026.pdf"
        )
        with (
            patch.object(sys, "argv", ["tgpc", "dispatch", "--dry-run", "--force", "--limit", "5", "--write-fallback"]),
            patch.object(dispatch_mod, "sync_dispatch", return_value=report) as sync,
            patch.object(cli, "Phase", lambda *a, **kw: contextlib.nullcontext()),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            cli.main()

        sync.assert_called_once_with(dry_run=True, force=True, limit=5, write_fallback=True)
        self.assertIn("242 published", out.getvalue())
        self.assertIn("DL16102026.pdf", out.getvalue())

    def test_dispatch_exits_non_zero_when_a_file_failed(self):
        import tgpc.dispatch as dispatch_mod

        report = dispatch_mod.SyncReport(published=2, missing=2, uploaded=1, failed=1, failed_names=["DL16102026.pdf"])
        with (
            patch.object(sys, "argv", ["tgpc", "dispatch"]),
            patch.object(dispatch_mod, "sync_dispatch", return_value=report),
            patch.object(cli, "Phase", lambda *a, **kw: contextlib.nullcontext()),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.main()
        self.assertEqual(ctx.exception.code, 1)


class UpdateFlowTests(unittest.TestCase):
    def _manager(self, status="updated", new=(), modified=(), removed=(), records=()):
        mgr = MagicMock()
        mgr.run_daily_update.return_value = status
        mgr._last_new_regs = set(new)
        mgr._last_modified_regs = set(modified)
        mgr._last_removed_regs = set(removed)
        mgr.file_manager.load.return_value = [SimpleNamespace(registration_number=r) for r in records]
        for name in (
            "sync_to_supabase",
            "sync_to_supabase_storage",
            "sync_to_r2",
            "sync_to_gdrive",
            "sync_to_release",
            "sync_to_email",
            "delete_removed_from_supabase",
        ):
            setattr(mgr, name, MagicMock(return_value=True))
        mgr.refresh_merged_columns.return_value = {
            "dor_filled": 1,
            "home_filled": 2,
            "validity_updated": 3,
            "review_skipped": 4,
        }
        return mgr

    def test_source_unavailable_skips_every_sync(self):
        mgr = self._manager(status="source_unavailable")
        with self.assertRaises(SystemExit) as ctx:
            run_main(["update"], Manager=MagicMock(return_value=mgr))
        self.assertEqual(ctx.exception.code, 2)

        mgr.run_daily_update.assert_called_once_with(force=False)
        mgr.sync_to_supabase.assert_not_called()
        mgr.enrich_new_records.assert_not_called()

    def test_blocked_run_is_recorded_and_alerted(self):
        # The whole point: a blocked source used to exit 0 with one log line.
        mgr = self._manager(status="blocked")
        record = MagicMock()
        write = MagicMock(return_value=record)
        alert = MagicMock(return_value=True)

        with self.assertRaises(SystemExit) as ctx:
            run_main(
                ["update"],
                Manager=MagicMock(return_value=mgr),
                write_run_status=write,
                send_alert=alert,
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(write.call_args.args[0], "blocked")
        self.assertIn("preflight", write.call_args.kwargs["error"])
        alert.assert_called_once_with(record)
        mgr.sync_to_supabase.assert_not_called()

    def test_successful_run_records_status_without_alerting(self):
        mgr = self._manager(records=["R1"])
        write = MagicMock(return_value={})
        alert = MagicMock()

        run_main(
            ["update"],
            Manager=MagicMock(return_value=mgr),
            write_run_status=write,
            send_alert=alert,
        )

        self.assertEqual(write.call_args.args[0], "updated")
        self.assertEqual(write.call_args.kwargs["error"], "")
        alert.assert_not_called()

    def test_failed_destination_is_recorded_before_the_run_exits(self):
        mgr = self._manager(new=["R1"], records=["R1"])
        mgr.sync_to_gdrive = MagicMock(return_value=False)
        write = MagicMock(return_value={})
        alert = MagicMock()

        with self.assertRaises(SystemExit) as ctx:
            run_main(
                ["update"],
                Manager=MagicMock(return_value=mgr),
                write_run_status=write,
                send_alert=alert,
            )
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(write.call_args.args[0], "sync_failed")
        alert.assert_called_once()

    def test_no_sync_skips_every_cloud_step_including_enrichment(self):
        # --no-sync means "do not touch the cloud at all": enrichment uploads
        # photos to R2, so it is gated with the destinations, not just them.
        mgr = self._manager(new=["R1"], records=["R1"])
        run_main(["update", "--no-sync"], Manager=MagicMock(return_value=mgr))

        mgr.enrich_new_records.assert_not_called()
        mgr.sync_to_supabase.assert_not_called()
        mgr.sync_to_r2.assert_not_called()
        mgr.refresh_merged_columns.assert_not_called()

    def test_delta_sync_sends_new_and_modified_rows_only(self):
        mgr = self._manager(new=["R1"], modified=["R2"], records=["R1", "R2", "R3"])
        run_main(["update"], Manager=MagicMock(return_value=mgr))

        sent = mgr.sync_to_supabase.call_args.kwargs["delta_records"]
        self.assertEqual([r.registration_number for r in sent], ["R1", "R2"])
        mgr.sync_to_r2.assert_called_once()
        mgr.refresh_merged_columns.assert_called_once()
        mgr.enrich_new_records.assert_called_once_with(force=False)

    def test_removals_only_update_still_propagates_everywhere(self):
        mgr = self._manager(removed=["R9"], records=["R1"])
        run_main(["update"], Manager=MagicMock(return_value=mgr))

        self.assertEqual(mgr.sync_to_supabase.call_args.kwargs["delta_records"], [])
        mgr.delete_removed_from_supabase.assert_called_once()
        mgr.sync_to_r2.assert_called_once()
        mgr.sync_to_email.assert_called_once()

    def test_no_changes_skips_the_file_destinations(self):
        mgr = self._manager(records=["R1"])
        run_main(["update"], Manager=MagicMock(return_value=mgr))

        mgr.sync_to_supabase.assert_called_once()
        mgr.sync_to_r2.assert_not_called()
        mgr.sync_to_gdrive.assert_not_called()

    def test_a_failed_destination_fails_the_update(self):
        mgr = self._manager(new=["R1"], records=["R1"])
        mgr.sync_to_gdrive = MagicMock(return_value=False)

        with self.assertRaises(SystemExit) as ctx:
            run_main(["update"], Manager=MagicMock(return_value=mgr))
        self.assertEqual(ctx.exception.code, 1)


class ScrapeFlowTests(unittest.TestCase):
    """`scrape` runs update AND dispatch in one process.

    That is the whole point of the command: `make scrape` used to invoke
    `tgpc update && tgpc dispatch` as two processes, so the first process's
    atexit handler disconnected WARP before dispatch started. One process =
    one tunnel for the entire run.
    """

    def _manager(self, new=(), records=()):
        mgr = MagicMock()
        mgr.run_daily_update.return_value = "updated"
        mgr._last_new_regs = set(new)
        mgr._last_modified_regs = set()
        mgr._last_removed_regs = set()
        mgr.file_manager.load.return_value = [SimpleNamespace(registration_number=r) for r in records]
        for name in (
            "sync_to_supabase",
            "sync_to_supabase_storage",
            "sync_to_r2",
            "sync_to_gdrive",
            "sync_to_release",
            "sync_to_email",
            "delete_removed_from_supabase",
        ):
            setattr(mgr, name, MagicMock(return_value=True))
        mgr.refresh_merged_columns.return_value = {
            "dor_filled": 0,
            "home_filled": 0,
            "validity_updated": 0,
            "review_skipped": 0,
        }
        return mgr

    def test_scrape_runs_update_then_dispatch(self):
        import tgpc.dispatch as dispatch_mod

        mgr = self._manager(new=["R1"], records=["R1"])
        calls = []
        mgr.run_daily_update.side_effect = lambda **kw: (calls.append("update"), "updated")[1]
        report = dispatch_mod.SyncReport(published=241, already_present=234)

        with patch.object(
            dispatch_mod,
            "sync_dispatch",
            side_effect=lambda **kw: (calls.append("dispatch"), report)[1],
        ) as sync:
            run_main(["scrape"], Manager=MagicMock(return_value=mgr))

        self.assertEqual(calls, ["update", "dispatch"])
        mgr.sync_to_r2.assert_called_once()
        sync.assert_called_once_with(dry_run=False, force=False, limit=None, write_fallback=False)

    def test_scrape_keeps_warp_up_until_the_run_ends(self):
        import tgpc.dispatch as dispatch_mod

        mgr = self._manager()
        connect = MagicMock(return_value=True)
        disconnect = MagicMock()
        report = dispatch_mod.SyncReport(published=241, already_present=241)

        try:
            with (
                patch.object(cli, "_warp_disconnect", disconnect),
                patch.object(dispatch_mod, "sync_dispatch", return_value=report),
            ):
                run_main(
                    ["scrape"],
                    Manager=MagicMock(return_value=mgr),
                    _warp_connect=connect,
                )
                connect.assert_called_once()
                disconnect.assert_not_called()
                cli._warp_ensure_disconnected()
                disconnect.assert_called_once()
        finally:
            cli._warp_connected_by_us = False

    def test_scrape_does_not_dispatch_when_update_sync_fails(self):
        import tgpc.dispatch as dispatch_mod

        mgr = self._manager(new=["R1"], records=["R1"])
        mgr.sync_to_gdrive = MagicMock(return_value=False)

        with patch.object(dispatch_mod, "sync_dispatch") as sync:
            with self.assertRaises(SystemExit) as ctx:
                run_main(["scrape"], Manager=MagicMock(return_value=mgr))
        self.assertEqual(ctx.exception.code, 1)
        sync.assert_not_called()
        mgr.refresh_merged_columns.assert_not_called()


class FetchDgGuardTests(unittest.TestCase):
    def test_no_ids_is_a_usage_error(self):
        with (
            patch.object(sys, "argv", ["tgpc", "fetch-dg"]),
            patch.object(cli, "Manager"),
            patch.object(cli, "_warp_connect", MagicMock(return_value=False)),
            patch("tgpc.utils.load_credentials"),
            patch("tgpc.details_dg.run_fetch") as run_fetch,
        ):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 2)
        run_fetch.assert_not_called()

    def test_no_resume_refuses_to_wipe_an_existing_checkpoint(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        checkpoint = tmp / "dg_fetch_checkpoint.json"
        checkpoint.write_text('{"done": ["TS1"]}', encoding="utf-8")

        with (
            patch.object(
                sys, "argv", ["tgpc", "fetch-dg", "--ids", "TS1", "--no-resume", "--checkpoint", str(checkpoint)]
            ),
            patch.object(cli, "Manager"),
            patch.object(cli, "_warp_connect", MagicMock(return_value=False)),
            patch("tgpc.utils.load_credentials"),
            patch("tgpc.details_dg.run_fetch") as run_fetch,
        ):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 2)
        run_fetch.assert_not_called()
        self.assertEqual(checkpoint.read_text(encoding="utf-8"), '{"done": ["TS1"]}', "resume state must be intact")

    def test_ids_file_and_reference_are_loaded(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        ids_file = tmp / "ids.txt"
        ids_file.write_text("TS1\n\nTS2\n", encoding="utf-8")
        reference = tmp / "rph.json"
        reference.write_text(
            json.dumps([{"registration_number": "ts1", "serial_number": 5}, {"no_reg": True}]),
            encoding="utf-8",
        )

        argv = [
            "tgpc",
            "fetch-dg",
            "--ids-file",
            str(ids_file),
            "--reference",
            str(reference),
            "--checkpoint",
            str(tmp / "cp.json"),
            "--out",
            str(tmp / "out.jsonl"),
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(cli, "Manager"),
            patch.object(cli, "_warp_connect", MagicMock(return_value=False)),
            patch("tgpc.utils.load_credentials"),
            patch("tgpc.details_dg.run_fetch", return_value={"processed": 2}) as run_fetch,
            patch.object(cli, "Phase", lambda *a, **kw: contextlib.nullcontext()),
        ):
            cli.main()

        kwargs = run_fetch.call_args
        self.assertEqual(kwargs.args[0], ["TS1", "TS2"], "blank lines are dropped")
        self.assertEqual(kwargs.kwargs["reference"], {"TS1": {"registration_number": "ts1", "serial_number": 5}})
        self.assertTrue(kwargs.kwargs["resume"])

    def test_validate_dg_passes_parsed_paths(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with (
            patch.object(sys, "argv", ["tgpc", "validate-dg", "--out", str(tmp / "c.jsonl"), "--max-list", "3"]),
            patch.object(cli, "Manager"),
            patch("tgpc.details_dg.validate_l1", return_value={"flagged": 0}) as validate,
        ):
            cli.main()

        args, kwargs = validate.call_args
        self.assertEqual(args[0], tmp / "c.jsonl")
        self.assertEqual(kwargs["max_list"], 3)


class CredsCommandTests(unittest.TestCase):
    def test_set_with_key_value_stores_without_prompting(self):
        with (
            patch.object(sys, "argv", ["tgpc", "creds", "set", "SUPABASE_URL=https://x"]),
            patch.object(cli, "_set_keychain") as set_kc,
        ):
            cli.main()
        set_kc.assert_called_once_with("SUPABASE_URL", "https://x")

    def test_delete_removes_an_existing_key_and_reports_a_missing_one(self):
        with (
            patch.object(sys, "argv", ["tgpc", "creds", "delete", "SUPABASE_URL"]),
            patch.object(cli, "_get_keychain", return_value="secret"),
            patch.object(cli.subprocess, "run") as run,
        ):
            cli.main()
        run.assert_called_once()
        self.assertIn("delete-generic-password", run.call_args.args[0])

        with (
            patch.object(sys, "argv", ["tgpc", "creds", "delete", "SUPABASE_URL"]),
            patch.object(cli, "_get_keychain", return_value=None),
            patch.object(cli.subprocess, "run") as run,
        ):
            cli.main()
        run.assert_not_called()

    def test_list_reports_env_keychain_and_unset(self):
        env = {"SUPABASE_URL": "https://from-env"}
        keychain = {"SUPABASE_SECRET_KEY": "sk-from-keychain"}
        with (
            patch.object(sys, "argv", ["tgpc", "creds", "list"]),
            patch.dict(cli.os.environ, env, clear=True),
            patch.object(cli, "_get_keychain", side_effect=lambda k: keychain.get(k)),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            cli.main()
        text = out.getvalue()
        self.assertIn("SUPABASE_URL  env:", text)
        self.assertIn("SUPABASE_SECRET_KEY  keychain:", text)
        self.assertIn("not set", text)

    def test_creds_without_a_subcommand_prints_help_only(self):
        manager_cls = MagicMock()
        with (
            patch.object(sys, "argv", ["tgpc", "creds"]),
            patch.object(cli, "Manager", manager_cls),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.main()
        manager_cls.assert_not_called()


class WarpTests(unittest.TestCase):
    """WARP is connected for network commands — but never torn down if the user
    already had it up, which is the invariant `_warp_connect`'s return value
    carries."""

    def test_unavailable_warp_is_a_no_op(self):
        with patch.object(cli, "_warp_available", return_value=False):
            self.assertFalse(cli._warp_connect())
            self.assertIsNone(cli._warp_disconnect())

    def test_already_connected_is_left_alone(self):
        status = SimpleNamespace(returncode=0, stdout="Status: Connected", stderr="")
        with (
            patch.object(cli, "_warp_available", return_value=True),
            patch.object(cli, "warp_cli_run", return_value=status) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(cli._warp_connect(), "a pre-existing connection is not ours to own")
        self.assertEqual([c.args[0] for c in run.call_args_list], ["status"])

    def test_connect_reports_ownership_only_on_success(self):
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        status = SimpleNamespace(returncode=0, stdout="Status: Disconnected", stderr="")
        with (
            patch.object(cli, "_warp_available", return_value=True),
            patch.object(cli, "warp_cli_run", side_effect=[status, ok]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(cli._warp_connect())

        failed = SimpleNamespace(returncode=1, stdout="", stderr="nope")
        with (
            patch.object(cli, "_warp_available", return_value=True),
            patch.object(cli, "warp_cli_run", side_effect=[status, failed]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(cli._warp_connect())

    def test_connect_error_is_reported_not_raised(self):
        with (
            patch.object(cli, "_warp_available", return_value=True),
            patch.object(cli, "warp_cli_run", side_effect=RuntimeError("boom")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(cli._warp_connect())

    def test_atexit_disconnects_only_what_this_process_connected(self):
        with patch.object(cli, "_warp_disconnect") as disconnect:
            cli._warp_connected_by_us = False
            cli._warp_ensure_disconnected()
            disconnect.assert_not_called()

            cli._warp_connected_by_us = True
            cli._warp_ensure_disconnected()
            disconnect.assert_called_once()
            self.assertFalse(cli._warp_connected_by_us)


class CredsPromptTests(unittest.TestCase):
    def test_interactive_set_skips_existing_keys_unless_forced(self):
        with (
            patch.object(sys, "argv", ["tgpc", "creds", "set"]),
            patch.object(cli, "CREDENTIAL_KEYS", ["A", "B"]),
            patch.object(cli, "_get_keychain", side_effect=lambda k: "existing-value" if k == "A" else None),
            patch.object(cli, "_set_keychain") as set_kc,
            patch.object(cli.getpass, "getpass", return_value="new-value"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.main()
        set_kc.assert_called_once_with("B", "new-value")

        set_kc.reset_mock()
        with (
            patch.object(sys, "argv", ["tgpc", "creds", "set", "--force"]),
            patch.object(cli, "CREDENTIAL_KEYS", ["A"]),
            patch.object(cli, "_get_keychain", return_value="existing-value"),
            patch.object(cli, "_set_keychain") as set_kc,
            patch.object(cli.getpass, "getpass", return_value="new-value"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.main()
        set_kc.assert_called_once_with("A", "new-value")

    def test_blank_input_skips_the_key(self):
        with (
            patch.object(sys, "argv", ["tgpc", "creds", "set"]),
            patch.object(cli, "CREDENTIAL_KEYS", ["A"]),
            patch.object(cli, "_get_keychain", return_value=None),
            patch.object(cli, "_set_keychain") as set_kc,
            patch.object(cli.getpass, "getpass", return_value=""),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.main()
        set_kc.assert_not_called()


class FetchDgOptionTests(unittest.TestCase):
    def _run(self, tmp: Path, extra_argv):
        argv = [
            "tgpc",
            "fetch-dg",
            "--ids",
            "TS1",
            "--checkpoint",
            str(tmp / "cp.json"),
            "--out",
            str(tmp / "out.jsonl"),
            "--raw-dir",
            str(tmp / "raw"),
            "--stats",
            str(tmp / "stats.json"),
            *extra_argv,
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(cli, "Manager"),
            patch.object(cli, "_warp_connect", MagicMock(return_value=False)),
            patch("tgpc.utils.load_credentials"),
            patch("tgpc.details_dg.run_fetch", return_value={}) as run_fetch,
            patch.object(cli, "Phase", lambda *a, **kw: contextlib.nullcontext()),
        ):
            cli.main()
        return run_fetch

    def test_worker_override_is_clamped_to_the_documented_maximum(self):
        import tgpc.details_dg as dg

        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with patch.object(dg, "DG_WORKERS", 4):
            self._run(tmp, ["--workers", "99"])
            self.assertEqual(dg.DG_WORKERS, 16)

            self._run(tmp, ["--workers", "0"])
            self.assertEqual(dg.DG_WORKERS, 1)

    def test_unreadable_reference_is_ignored_not_fatal(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        reference = tmp / "rph.json"
        reference.write_text("{not json", encoding="utf-8")

        run_fetch = self._run(tmp, ["--reference", str(reference)])
        self.assertIsNone(run_fetch.call_args.kwargs["reference"])

    def test_manual_captcha_solver_normalizes_input_and_fails_without_a_tty(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        run_fetch = self._run(tmp, ["--captcha", "manual"])
        solver = run_fetch.call_args.kwargs["captcha_solver"]
        self.assertIsNotNone(solver, "manual mode must install a solver")

        with (
            patch("builtins.input", return_value=" a b C "),
            patch.object(Path, "write_bytes"),
            patch.object(Path, "mkdir"),
        ):
            self.assertEqual(solver(b"img"), "ABC")

        with (
            patch("builtins.input", side_effect=EOFError),
            patch.object(Path, "write_bytes"),
            patch.object(Path, "mkdir"),
        ):
            from tgpc.details_dg import CaptchaNeeded

            with self.assertRaises(CaptchaNeeded):
                solver(b"img")

        with (
            patch("builtins.input", return_value="   "),
            patch.object(Path, "write_bytes"),
            patch.object(Path, "mkdir"),
        ):
            from tgpc.details_dg import CaptchaNeeded

            with self.assertRaises(CaptchaNeeded):
                solver(b"img")


if __name__ == "__main__":
    unittest.main()
