import gzip
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "s3_fastq.py"
SPEC = importlib.util.spec_from_file_location("s3_fastq", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class S3FastqTests(unittest.TestCase):
    def test_unique_accessions_normalizes_and_deduplicates(self):
        self.assertEqual(
            MODULE.unique_accessions("err1, SRR2\nERR1;DRR3"),
            ["ERR1", "SRR2", "DRR3"],
        )

    def test_unique_accessions_rejects_invalid(self):
        with self.assertRaisesRegex(ValueError, "Invalid"):
            MODULE.unique_accessions("ERP123")

    def test_unique_accessions_rejects_empty(self):
        with self.assertRaisesRegex(ValueError, "No SRA"):
            MODULE.unique_accessions(" , \n")

    def test_load_accessions_from_utf8_bom_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.txt"
            path.write_text("\ufeffERR1\nERR2\n", encoding="utf-8")
            self.assertEqual(
                MODULE.load_accessions({"input_type": "list_file", "accession_file": str(path)}),
                ["ERR1", "ERR2"],
            )

    def test_parse_int_and_bool(self):
        self.assertEqual(MODULE.parse_int("x", "", 4, 1, 16), 4)
        self.assertTrue(MODULE.parse_bool("true", False))
        self.assertFalse(MODULE.parse_bool("0", True))

    def test_parse_int_enforces_bounds(self):
        with self.assertRaisesRegex(ValueError, "between"):
            MODULE.parse_int("x", "17", 4, 1, 16)

    def test_format_size_uses_binary_units(self):
        self.assertEqual(MODULE.format_size(8 * 1024 * 1024), "8.0 MiB")

    def test_fetch_sra_spec_uses_full_quality_aws_object(self):
        response = mock.MagicMock()
        response.headers = {
            "Content-Length": "123456",
            "Accept-Ranges": "bytes",
            "ETag": '"abc"',
        }
        response.geturl.return_value = f"{MODULE.S3_ROOT}/ERR1/ERR1"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch.object(MODULE, "direct_opener") as opener:
            opener.return_value.open.return_value = response
            result = MODULE.fetch_sra_spec("ERR1", attempts=1)
        self.assertEqual(result.size, 123456)
        self.assertEqual(result.etag, "abc")
        self.assertEqual(result.url, f"{MODULE.S3_ROOT}/ERR1/ERR1")
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.method, "HEAD")

    def test_make_segments_covers_object_without_overlap(self):
        segments = MODULE.make_segments(25, segment_size=10)
        self.assertEqual([(item.start, item.end) for item in segments], [(0, 9), (10, 19), (20, 24)])
        self.assertEqual(sum(item.size for item in segments), 25)

    def test_prepare_parts_keeps_matching_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            spec = MODULE.SraSpec("ERR1", f"{MODULE.S3_ROOT}/ERR1/ERR1", 25, "etag")
            with mock.patch.object(MODULE, "SEGMENT_SIZE_BYTES", 10):
                segments = MODULE.prepare_parts(spec, cache)
                first = MODULE.segment_path(cache, spec.accession, segments[0])
                first.write_bytes(b"x" * 5)
                MODULE.prepare_parts(spec, cache)
            self.assertEqual(first.stat().st_size, 5)
            self.assertFalse(any(path.is_dir() for path in cache.iterdir()))

    def test_prepare_parts_removes_stale_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            cache.mkdir()
            (cache / ".ERR1.download.json").write_text("{}", encoding="utf-8")
            stale = cache / ".ERR1.part-stale"
            stale.write_bytes(b"stale")
            spec = MODULE.SraSpec("ERR1", f"{MODULE.S3_ROOT}/ERR1/ERR1", 25, "new-etag")
            with mock.patch.object(MODULE, "SEGMENT_SIZE_BYTES", 10):
                MODULE.prepare_parts(spec, cache)
            self.assertFalse(stale.exists())

    def test_download_segment_resumes_from_existing_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            segment = MODULE.Segment(0, 0, 9)
            path = MODULE.segment_path(cache, "ERR1", segment)
            path.write_bytes(b"abc")
            response = io.BytesIO(b"defghij")
            response.status = 206
            response.headers = {"Content-Range": "bytes 3-9/10"}
            spec = MODULE.SraSpec("ERR1", f"{MODULE.S3_ROOT}/ERR1/ERR1", 10, "etag")
            with mock.patch.object(MODULE, "direct_opener") as opener:
                opener.return_value.open.return_value = response
                MODULE.download_one_segment(spec, segment, cache, 2, MODULE.threading.Event())
            self.assertEqual(path.read_bytes(), b"abcdefghij")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("Range"), "bytes=3-9")

    def test_download_sra_retries_and_accepts_valid_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            spec = MODULE.SraSpec("ERR1", f"{MODULE.S3_ROOT}/ERR1/ERR1", 12, "etag")
            calls = 0

            def fake_download(_spec, segment, cache_root, _retries, _cancel_event):
                nonlocal calls
                calls += 1
                MODULE.segment_path(cache_root, _spec.accession, segment).write_bytes(
                    b"x" * segment.size
                )

            with mock.patch.object(MODULE, "SEGMENT_SIZE_BYTES", 5), \
                 mock.patch.object(MODULE, "download_one_segment", side_effect=fake_download), \
                 mock.patch.object(MODULE, "validate_sra", return_value=True):
                result = MODULE.download_sra(spec, cache, 2, 3)
            self.assertEqual(result, cache / "ERR1.sra")
            self.assertEqual(result.read_bytes(), b"x" * spec.size)
            self.assertEqual(calls, 3)
            self.assertFalse(any(path.is_dir() for path in cache.iterdir()))

    def test_gzip_and_md5_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ERR1_1.fastq.gz"
            with gzip.open(path, "wb") as handle:
                handle.write(b"@x\nAC\n+\nII\n")
            records = MODULE.verify_fastqs("ERR1", [path])
            self.assertEqual(records[0]["md5"], hashlib.md5(path.read_bytes()).hexdigest())

    def test_detect_fastq_layout_accepts_single(self):
        self.assertEqual(
            MODULE.detect_fastq_layout("ERR1", [Path("ERR1.fastq")]),
            "SINGLE",
        )
        self.assertEqual(
            MODULE.detect_fastq_layout("ERR1", [Path("ERR1_4.fastq")]),
            "SINGLE",
        )

    def test_detect_fastq_layout_accepts_exact_pair(self):
        self.assertEqual(
            MODULE.detect_fastq_layout(
                "ERR1", [Path("ERR1_1.fastq"), Path("ERR1_2.fastq")]
            ),
            "PAIRED",
        )
        self.assertEqual(
            MODULE.detect_fastq_layout(
                "ERR1", [Path("ERR1_1.fastq"), Path("ERR1_3.fastq")]
            ),
            "PAIRED",
        )

    def test_detect_fastq_layout_rejects_missing_or_extra_reads(self):
        with self.assertRaisesRegex(MODULE.WorkflowError, "unexpected FASTQ set"):
            MODULE.detect_fastq_layout(
                "ERR1",
                [Path("ERR1.fastq"), Path("ERR1_1.fastq"), Path("ERR1_2.fastq")],
            )

    def test_normalize_fastq_names_uses_standard_single_and_pair_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            single = root / "ERR1_4.fastq"
            single.write_bytes(b"single")
            normalized = MODULE.normalize_fastq_names("ERR1", "SINGLE", [single])
            self.assertEqual([path.name for path in normalized], ["ERR1.fastq"])
            self.assertEqual(normalized[0].read_bytes(), b"single")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "ERR1_1.fastq"
            second = root / "ERR1_3.fastq"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            normalized = MODULE.normalize_fastq_names("ERR1", "PAIRED", [second, first])
            self.assertEqual([path.name for path in normalized], ["ERR1_1.fastq", "ERR1_2.fastq"])
            self.assertEqual(normalized[0].read_bytes(), b"first")
            self.assertEqual(normalized[1].read_bytes(), b"second")

    def test_verify_completed_accepts_exact_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_root = root / "s3_full_quality_fastq"
            manifest_root = root / ".s3_fastq_manifests"
            final_root.mkdir()
            manifest_root.mkdir()
            records = []
            for suffix in ("1", "2"):
                path = final_root / f"ERR1_{suffix}.fastq.gz"
                with gzip.open(path, "wb") as handle:
                    handle.write(f"@x/{suffix}\nAC\n+\nII\n".encode())
                records.append(
                    {"name": path.name, "size": path.stat().st_size, "md5": MODULE.md5_file(path)}
                )
            spec = MODULE.SraSpec("ERR1", "https://example/ERR1", 1, "etag")
            manifest_path = manifest_root / "ERR1.json"
            manifest_path.write_text(
                json.dumps(MODULE.manifest_payload(spec, "PAIRED", records)), encoding="utf-8"
            )
            layout, paths = MODULE.verify_completed(final_root, manifest_path, "ERR1")
            self.assertEqual(layout, "PAIRED")
            self.assertEqual(len(paths), 2)
            self.assertEqual({path.name for path in final_root.iterdir()}, {"ERR1_1.fastq.gz", "ERR1_2.fastq.gz"})

    def test_verify_completed_accepts_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_root = root / "s3_full_quality_fastq"
            manifest_root = root / ".s3_fastq_manifests"
            final_root.mkdir()
            manifest_root.mkdir()
            path = final_root / "ERR1.fastq.gz"
            with gzip.open(path, "wb") as handle:
                handle.write(b"@x\nAC\n+\nII\n")
            records = [{"name": path.name, "size": path.stat().st_size, "md5": MODULE.md5_file(path)}]
            spec = MODULE.SraSpec("ERR1", "https://example/ERR1", 1, "etag")
            manifest_path = manifest_root / "ERR1.json"
            manifest_path.write_text(
                json.dumps(MODULE.manifest_payload(spec, "SINGLE", records)), encoding="utf-8"
            )
            layout, paths = MODULE.verify_completed(final_root, manifest_path, "ERR1")
            self.assertEqual(layout, "SINGLE")
            self.assertEqual([item.name for item in paths], ["ERR1.fastq.gz"])

    def test_verify_completed_rejects_layout_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_root = root / "s3_full_quality_fastq"
            manifest_root = root / ".s3_fastq_manifests"
            final_root.mkdir()
            manifest_root.mkdir()
            path = final_root / "ERR1.fastq.gz"
            with gzip.open(path, "wb") as handle:
                handle.write(b"@x\nAC\n+\nII\n")
            records = [{"name": path.name, "size": path.stat().st_size, "md5": MODULE.md5_file(path)}]
            spec = MODULE.SraSpec("ERR1", "https://example/ERR1", 1, "etag")
            manifest_path = manifest_root / "ERR1.json"
            manifest_path.write_text(
                json.dumps(MODULE.manifest_payload(spec, "PAIRED", records)), encoding="utf-8"
            )
            with self.assertRaisesRegex(MODULE.WorkflowError, "does not match"):
                MODULE.verify_completed(final_root, manifest_path, "ERR1")

    def test_quarantine_files_preserves_flat_invalid_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_root = root / "s3_full_quality_fastq"
            final_root.mkdir()
            invalid = final_root / "ERR1_1.fastq.gz"
            invalid.write_bytes(b"bad")
            MODULE.quarantine_files([invalid], root / ".s3_fastq_rejected", "ERR1", "test")
            self.assertFalse(invalid.exists())
            self.assertEqual(len(list((root / ".s3_fastq_rejected").rglob("ERR1_1.fastq.gz"))), 1)

    def test_write_summary_sanitizes_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.tsv"
            MODULE.write_summary(path, [("ERR1", "", "FAILED", "", "bad\tvalue\nnext")])
            self.assertIn("bad value next", path.read_text(encoding="utf-8"))
            self.assertTrue(path.read_text(encoding="utf-8").startswith("accession\tlayout\tstatus"))

    def test_write_run_status_records_terminal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".s3_fastq_status.json"
            MODULE.write_run_status(path, "SUCCESS", 54, 54, 0, "all accessions completed")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "SUCCESS")
            self.assertEqual(payload["completed_accessions"], 54)
            self.assertEqual(payload["failed_accessions"], 0)
            self.assertEqual(payload["plugin_id"], MODULE.PLUGIN_ID)

    def test_useful_diagnostics_prefers_errors(self):
        lines = ["normal status", "download timeout", "another line"]
        self.assertEqual(MODULE.useful_diagnostics(lines), ["download timeout"])


if __name__ == "__main__":
    unittest.main()
