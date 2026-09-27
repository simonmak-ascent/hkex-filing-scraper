"""CLI and API-parsing unit tests.

These cover the pure argument/validation logic and the HTML fallback parser, which the
integration suites never touch.
"""

from __future__ import annotations

import pytest

from hkex_scraper import api, config, main, sinks


class TestArgumentParser:
    def test_defaults(self):
        parser = main._build_parser()
        args = parser.parse_args([])
        assert args.full_history is False
        assert args.metadata_only is False
        assert args.limit == 0
        # Empty string means "fall back to the environment / .env".
        assert args.database_target == ""
        assert args.from_date == "" and args.to_date == ""

    def test_flags_are_wired(self):
        args = main._build_parser().parse_args(
            [
                "--full-history",
                "--metadata-only",
                "--limit",
                "500",
                "--database-target",
                "postgres,sqlite",
                "--parity-report",
            ]
        )
        assert args.full_history is True
        assert args.metadata_only is True
        assert args.limit == 500
        assert args.database_target == "postgres,sqlite"
        assert args.parity_report is True

    def test_version_flag_exits_cleanly(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main._build_parser().parse_args(["--version"])
        assert excinfo.value.code == 0

    def test_unknown_flag_is_rejected(self):
        with pytest.raises(SystemExit) as excinfo:
            main._build_parser().parse_args(["--nope"])
        assert excinfo.value.code != 0


class TestValidateEnv:
    def test_missing_target_exits_with_an_actionable_message(self, monkeypatch, capsys):
        monkeypatch.setattr(config, "sink_ids", lambda: [])
        with pytest.raises(SystemExit) as excinfo:
            main._validate_env()
        assert excinfo.value.code == 1
        assert "DATABASE_TARGET is not set" in capsys.readouterr().out

    def test_unknown_sink_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(config, "sink_ids", lambda: ["not-a-sink"])
        with pytest.raises(SystemExit) as excinfo:
            main._validate_env()
        assert excinfo.value.code == 1
        assert "unknown sink" in capsys.readouterr().out

    def test_unusable_sink_exits(self, monkeypatch, capsys):
        class Unusable:
            id = "sqlite"

            def available(self):
                return False

            def unavailable_reason(self):
                return "sqlite sink requires SQLITE_PATH"

        monkeypatch.setattr(config, "sink_ids", lambda: ["sqlite"])
        monkeypatch.setattr(sinks, "enabled_sinks", lambda: [Unusable()])
        with pytest.raises(SystemExit) as excinfo:
            main._validate_env()
        assert excinfo.value.code == 1
        assert "SQLITE_PATH" in capsys.readouterr().out


class TestParityReport:
    def test_single_sink_is_reported_as_not_applicable(self):
        text = main._format_parity_report({"postgres": 10})
        assert "PostgreSQL filings: 10" in text
        assert "N/A" in text

    def test_equal_counts_report_ok(self):
        text = main._format_parity_report({"postgres": 10, "sqlite": 10})
        assert "Spread:            0" in text
        assert "Parity: OK" in text

    def test_divergent_counts_warn_with_the_spread(self):
        text = main._format_parity_report({"postgres": 12, "sqlite": 9})
        assert "Spread:            3" in text
        assert "WARNING: sinks differ by 3 record(s)" in text


class TestInitialPageHtmlParsing:
    HTML = """
    <html><body>
      <div>Total records found: 1,234</div>
      <table><tbody>
        <tr>
          <td>Release Time: 11/02/2026 19:10</td>
          <td>Stock Code: 01461</td>
          <td>Stock Short Name: ZHONGTAI FUTURES</td>
          <td><a href="/listedco/listconews/sehk/2026/0211/x.pdf">Document: Announcement</a></td>
        </tr>
        <tr><td>incomplete</td></tr>
      </tbody></table>
    </body></html>
    """

    def test_extracts_filings_and_the_total(self):
        filings, total = api._parse_initial_page_html(self.HTML)
        assert total == 1234
        assert len(filings) == 1
        filing = filings[0]
        assert filing["stockCode"] == "01461"
        assert filing["stockName"] == "ZHONGTAI FUTURES"
        assert filing["title"] == "Announcement"
        assert filing["link"].startswith("http")
        assert filing["date"] == "11/02/2026"

    def test_incomplete_rows_are_skipped(self):
        filings, total = api._parse_initial_page_html(self.HTML)
        assert len(filings) == 1, "a row without enough cells must be ignored"

    def test_empty_html_is_safe(self):
        assert api._parse_initial_page_html("") == ([], 0)


class TestApiRecordParsing:
    def test_relative_link_is_absolutised(self):
        parsed = api._parse_api_record({"FILE_LINK": "/listedco/x.pdf"})
        assert parsed["link"] == f"{config.HKEX_BASE_URL}/listedco/x.pdf"

    def test_absolute_link_is_preserved(self):
        parsed = api._parse_api_record({"FILE_LINK": "https://example.test/x.pdf"})
        assert parsed["link"] == "https://example.test/x.pdf"

    def test_multi_line_name_keeps_only_the_first_line(self):
        parsed = api._parse_api_record({"STOCK_NAME": "FIRST<br/>SECOND"})
        assert parsed["stockName"] == "FIRST"

    def test_html_entities_are_decoded(self):
        parsed = api._parse_api_record({"TITLE": "A &amp; B &#x3b; C"})
        assert parsed["title"] == "A & B ; C"

    def test_missing_fields_default_to_empty_strings(self):
        parsed = api._parse_api_record({})
        assert parsed == {
            "date": "",
            "stockCode": "",
            "stockName": "",
            "title": "",
            "link": "",
            "fileType": "",
            "sizeText": "",
            "category": "",
            "newsId": "",
        }
