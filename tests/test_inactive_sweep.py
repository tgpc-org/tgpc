"""Inactive-sweep helper tests (CODE_REVIEW.md T3 remainder).

Covers the JSONL/checkpoint plumbing and the resume-skip decision path of
cmd_sweep — no scraping, no Supabase. All file paths are redirected to a
temp directory via patch.object on the module constants.
"""

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tgpc import inactive_sweep as sweep


def make_args(**kw):
    defaults = dict(command="sweep", batches=None, limit=None, min_delay=3.0, workers=1)
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class SweepHelperTests(unittest.TestCase):
    def _tmp(self):
        return self.enterContext(tempfile.TemporaryDirectory())

    def test_existing_active_set_parses_good_lines_and_skips_bad(self):
        tmp = self._tmp()
        active = Path(tmp) / "active.jsonl"
        active.write_text(
            json.dumps({"registration_number": "R1"})
            + "\n"
            + "not-json\n"
            + json.dumps({"registration_number": "R2"})
            + "\n",
            encoding="utf-8",
        )
        with patch.object(sweep, "ACTIVE_FILE", active):
            self.assertEqual(sweep._existing_active_set(), {"R1", "R2"})

    def test_load_records_exits_when_input_missing(self):
        with patch.object(sweep, "INACTIVE_FILE", Path(self._tmp()) / "nope.jsonl"):
            with self.assertRaises(SystemExit):
                sweep._load_records()

    def test_checkpoint_roundtrip(self):
        tmp = Path(self._tmp())
        cp = tmp / "cp.json"
        with patch.object(sweep, "CHECKPOINT_FILE", cp):
            self.assertEqual(sweep._load_checkpoint(), {"done_batches": []})
            sweep._save_checkpoint({"done_batches": [0, 2]})
            self.assertEqual(sweep._load_checkpoint(), {"done_batches": [0, 2]})

    def test_write_active_emits_full_record(self):
        class FH:
            def __init__(self):
                self.chunks = []

            def write(self, s):
                self.chunks.append(s)

            def flush(self):
                pass

        fh = FH()
        sweep._write_active(fh, "R9", {"name": "Basic Name", "serial_number": 7}, sweep.__dict__ and _detail())
        record = json.loads("".join(fh.chunks))
        self.assertEqual(record["registration_number"], "R9")
        self.assertEqual(record["status"], "Active")
        self.assertEqual(record["name"], "Basic Name")
        self.assertEqual(record["validity_date"], "31-Dec-2026")


def _detail():
    from tgpc.scraper import PharmacistRecord

    return PharmacistRecord(
        registration_number="IGNORED",
        name="Scraped",
        father_name="F",
        category="BPharm",
        status="Active",
        validity_date="31-Dec-2026",
    )


class SweepResumeTests(unittest.TestCase):
    def _seed(self, tmp: Path, n=4):
        inactive = Path(tmp) / "inactive.jsonl"
        inactive.write_text(
            "".join(json.dumps({"registration_number": f"R{i}", "name": f"N{i}"}) + "\n" for i in range(1, n + 1)),
            encoding="utf-8",
        )
        return inactive

    def test_completed_batches_are_skipped(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        inactive = self._seed(Path(tmp))
        checkpoint = Path(tmp) / "cp.json"
        checkpoint.write_text(json.dumps({"done_batches": [0]}), encoding="utf-8")

        with (
            patch.object(sweep, "INACTIVE_FILE", inactive),
            patch.object(sweep, "CHECKPOINT_FILE", checkpoint),
            patch.object(sweep, "_sweep_regs") as regs_mock,
        ):
            sweep.cmd_sweep(make_args())

        regs_mock.assert_not_called()

    def test_partial_run_processes_only_requested_slice(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        inactive = self._seed(Path(tmp))
        checkpoint = Path(tmp) / "cp.json"

        captured = {}

        def fake_regs(regs, records, offset=0, global_total=None, min_delay=3.0, workers=1):
            captured["regs"] = list(regs)
            return {"active": 0, "inactive": 0, "other": 0, "not_found": 0, "error": 0}, 1.0

        with (
            patch.object(sweep, "INACTIVE_FILE", inactive),
            patch.object(sweep, "CHECKPOINT_FILE", checkpoint),
            patch.object(sweep, "_sweep_regs", side_effect=fake_regs),
        ):
            sweep.cmd_sweep(make_args(limit=2))

        self.assertEqual(captured["regs"], ["R1", "R2"])


class FakeBar:
    """Stand-in for ProgressBar: records updates instead of drawing."""

    def __init__(self, total=None, label="", cadence=1, **kw):
        self.total = total
        self.n = 0
        self.updates = 0
        self.details = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, n=1, detail=None):
        self.n += n
        self.updates += n
        if detail is not None:
            self.details.append(detail)

    def substep(self, text):
        pass


def _record(reg, status):
    from tgpc.scraper import PharmacistRecord

    return PharmacistRecord(
        registration_number=reg,
        name=f"Scraped {reg}",
        father_name="F",
        category="BPharm",
        status=status,
        validity_date="31-Dec-2026",
    )


class SweepRegsTests(unittest.TestCase):
    """_sweep_regs is the sweep's correctness core: it decides what gets written."""

    def _run(self, regs, results, records=None, existing=(), **kwargs):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        active_file = tmp / "active.jsonl"
        if existing:
            active_file.write_text(
                "".join(json.dumps({"registration_number": r}) + "\n" for r in existing), encoding="utf-8"
            )
        else:
            active_file.touch()
        calls = []

        def factory():
            class FakeScraper:
                def __init__(self):
                    from types import SimpleNamespace

                    self.rate_limiter = SimpleNamespace(min_delay=3.0, current_delay=3.0)

                def extract_detailed_info(self, reg, img_dir=None):
                    calls.append(reg)
                    outcome = results[reg]
                    if isinstance(outcome, Exception):
                        raise outcome
                    return outcome

            return FakeScraper()

        if records is None:
            records = [{"registration_number": r, "name": f"Basic {r}", "serial_number": i} for i, r in enumerate(regs)]

        bar = FakeBar()

        def make_bar(**kw):
            bar.total = kw.get("total")
            bar.label = kw.get("label", "")
            return bar

        with (
            patch.object(sweep, "ACTIVE_FILE", active_file),
            patch("tgpc.scraper.Scraper", factory),
            patch("tgpc.progress.ProgressBar", make_bar),
            patch("tgpc.progress.step") as step_mock,
        ):
            counts, elapsed = sweep._sweep_regs(regs, records, **kwargs)

        return counts, elapsed, active_file, calls, bar, step_mock

    def test_counts_each_outcome_and_writes_only_new_actives(self):
        results = {
            "R1": _record("R1", "Active"),
            "R2": _record("R2", "Inactive"),
            "R3": _record("R3", "Active"),  # already known-active: must not be appended twice
            "R4": _record("R4", "Expired"),
            "R5": None,  # genuinely absent from the source
        }
        counts, _, active_file, calls, bar, step_mock = self._run(list(results), results, existing=["R3"])

        self.assertEqual(counts, {"active": 2, "inactive": 1, "other": 1, "not_found": 1, "error": 0})
        self.assertEqual(sorted(calls), sorted(results))

        lines = [json.loads(ln) for ln in active_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual([ln["registration_number"] for ln in lines], ["R3", "R1"])
        written = lines[1]
        self.assertEqual(written["name"], "Basic R1", "identity fields from rph.json must win")
        self.assertEqual(written["serial_number"], 0)
        self.assertEqual(written["status"], "Active")
        self.assertEqual(written["validity_date"], "31-Dec-2026")

        self.assertEqual(bar.updates, len(results))
        reported = [c.args[0] for c in step_mock.call_args_list]
        self.assertIn("R2: inactive", reported)
        self.assertIn("R5: NOT FOUND", reported)

    def test_one_failing_record_does_not_abort_the_batch(self):
        results = {
            "R1": RuntimeError("connection reset"),
            "R2": _record("R2", "Active"),
            "R3": _record("R3", "Inactive"),
        }
        counts, _, active_file, calls, bar, _ = self._run(list(results), results)

        self.assertEqual(counts["error"], 1)
        self.assertEqual(counts["active"], 1)
        self.assertEqual(counts["inactive"], 1)
        self.assertEqual(len(calls), 3, "the batch must keep going after a failure")
        self.assertEqual(bar.updates, 3)
        self.assertEqual(active_file.read_text(encoding="utf-8").count("\n"), 1)

    def test_parallel_workers_cover_every_record_exactly_once(self):
        regs = [f"R{i}" for i in range(1, 7)]
        results = {r: _record(r, "Inactive") for r in regs}
        counts, _, _, calls, bar, _ = self._run(regs, results, workers=3)

        self.assertEqual(sorted(calls), regs)
        self.assertEqual(counts["inactive"], 6)
        self.assertEqual(bar.updates, 6)

    def test_bar_starts_at_offset_so_one_bar_spans_the_whole_sweep(self):
        regs = ["R1", "R2"]
        results = {r: _record(r, "Inactive") for r in regs}
        _, _, _, _, bar, _ = self._run(regs, results, offset=7, global_total=9)

        self.assertEqual(bar.total, 9)
        self.assertEqual(bar.n, 9, "offset 7 + 2 processed")


class InactivePhaseTests(unittest.TestCase):
    def test_cmd_inactive_joins_identity_fields_and_counts_missing(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        out = tmp / "inactive.jsonl"

        class FakeQuery:
            def select(self, *a):
                return self

            def eq(self, *a):
                return self

            def limit(self, *a):
                return self

            def execute(self):
                # Deliberately unsorted: cmd_inactive must sort before writing.
                return type("R", (), {"data": [{"registration_number": "R2"}, {"registration_number": "R1"}]})()

        class FakeSb:
            def table(self, name):
                return FakeQuery()

        rph = {"R1": {"name": "N1", "father_name": "F1", "category": "BPharm", "serial_number": 7}}

        with (
            patch.object(sweep, "DATA_DIR", tmp),
            patch.object(sweep, "INACTIVE_FILE", out),
            patch.object(sweep, "_sb_client", return_value=FakeSb()),
            patch.object(sweep, "_load_rph", return_value=rph),
            patch("tgpc.progress.ProgressBar", lambda **kw: FakeBar()),
        ):
            sweep.cmd_inactive(None)

        lines = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([ln["registration_number"] for ln in lines], ["R1", "R2"])
        self.assertEqual(
            lines[0],
            {"registration_number": "R1", "name": "N1", "father_name": "F1", "category": "BPharm", "serial_number": 7},
        )
        self.assertIsNone(lines[1]["name"], "a record missing from rph.json keeps the key, with no value")

    def test_load_rph_returns_empty_when_rph_json_is_missing(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with patch.object(sweep, "DATA_DIR", tmp):
            self.assertEqual(sweep._load_rph(), {})

    def test_load_rph_indexes_by_registration_number(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "rph.json").write_text(
            json.dumps([{"registration_number": "R1", "name": "N1"}, {"registration_number": "R2", "name": "N2"}]),
            encoding="utf-8",
        )
        with patch.object(sweep, "DATA_DIR", tmp):
            self.assertEqual(
                sweep._load_rph(),
                {"R1": {"registration_number": "R1", "name": "N1"}, "R2": {"registration_number": "R2", "name": "N2"}},
            )

    def test_corrupt_checkpoint_falls_back_instead_of_aborting_a_resume(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cp = tmp / "cp.json"
        cp.write_text('{"done_batches": [0,', encoding="utf-8")
        with patch.object(sweep, "CHECKPOINT_FILE", cp):
            self.assertEqual(sweep._load_checkpoint(), {"done_batches": []})


class SweepBatchLoopTests(unittest.TestCase):
    def _seed(self, tmp: Path, n: int) -> Path:
        inactive = tmp / "inactive.jsonl"
        inactive.write_text(
            "".join(json.dumps({"registration_number": f"R{i}"}) + "\n" for i in range(1, n + 1)), encoding="utf-8"
        )
        return inactive

    def _run(self, n, done_batches=None, batch_size=2):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        inactive = self._seed(tmp, n)
        checkpoint = tmp / "cp.json"
        if done_batches is not None:
            checkpoint.write_text(json.dumps({"done_batches": done_batches}), encoding="utf-8")

        calls = []

        def fake_regs(regs, records, offset=0, global_total=None, min_delay=3.0, workers=1):
            calls.append({"regs": list(regs), "offset": offset, "global_total": global_total})
            return {"active": 1, "inactive": len(regs) - 1, "other": 0, "not_found": 0, "error": 0}, 1.0

        with (
            patch.object(sweep, "INACTIVE_FILE", inactive),
            patch.object(sweep, "CHECKPOINT_FILE", checkpoint),
            patch.object(sweep, "BATCH_SIZE", batch_size),
            patch.object(sweep, "_sweep_regs", side_effect=fake_regs),
        ):
            sweep.cmd_sweep(make_args())

        return calls, json.loads(checkpoint.read_text(encoding="utf-8"))

    def test_each_batch_is_swept_with_a_running_offset_and_checkpointed(self):
        calls, checkpoint = self._run(5)

        self.assertEqual([c["offset"] for c in calls], [0, 2, 4])
        self.assertEqual([c["regs"] for c in calls], [["R1", "R2"], ["R3", "R4"], ["R5"]])
        self.assertTrue(all(c["global_total"] == 5 for c in calls), "one bar spans the whole sweep")
        self.assertEqual(checkpoint, {"done_batches": [0, 1, 2]})

    def test_resume_after_a_crash_starts_at_the_first_unfinished_batch(self):
        calls, checkpoint = self._run(5, done_batches=[0])

        self.assertEqual([c["offset"] for c in calls], [2, 4], "batch 0 must not be swept again")
        self.assertEqual(checkpoint, {"done_batches": [0, 1, 2]}, "the checkpoint accumulates, never resets")


class SweepCliTests(unittest.TestCase):
    """The module docstring tells operators to run these two commands."""

    def test_sweep_flags_reach_the_command(self):
        captured = {}

        def fake_cmd(args):
            captured.update(vars(args))
            return 0

        argv = ["tgpc.inactive_sweep", "sweep", "--limit", "5", "--workers", "2", "--min-delay", "0.5"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(sweep, "cmd_sweep", fake_cmd),
        ):
            with self.assertRaises(SystemExit) as ctx:
                sweep.main()

        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(captured["command"], "sweep")
        self.assertEqual((captured["limit"], captured["workers"], captured["min_delay"]), (5, 2, 0.5))
        self.assertIsNone(captured["batches"])

    def test_inactive_subcommand_dispatches(self):
        calls = []

        with (
            patch.object(sys, "argv", ["tgpc.inactive_sweep", "inactive"]),
            patch.object(sweep, "cmd_inactive", lambda args: calls.append(args) or 0),
        ):
            with self.assertRaises(SystemExit) as ctx:
                sweep.main()

        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(len(calls), 1)

    def test_a_subcommand_is_required(self):
        with patch.object(sys, "argv", ["tgpc.inactive_sweep"]):
            with self.assertRaises(SystemExit) as ctx:
                sweep.main()

        self.assertEqual(ctx.exception.code, 2, "argparse usage error")


if __name__ == "__main__":
    unittest.main()
