#!/usr/bin/env python3
"""
Tests for the BIDS Knowledge Base Retriever (retriever.py).

Test scenarios are derived from real BIDS validation warnings/errors
produced for a dataset_description.json, e.g.:

  {"severity": "err",  "rule_id": "JSON_SCHEMA_VALIDATION_ERROR",
   "message": "Authors must be array", "field": "Authors"}
  {"severity": "warn", "rule_id": "JSON_KEY_RECOMMENDED",
   "message": "missing recommended field 'HEDVersion'", "field": "HEDVersion"}
  {"severity": "warn", "rule_id": "bidsmgr.todo_placeholder",
   "message": "field 'License' contains a TODO placeholder", "field": "License"}

For each warning/error the retriever must return the relevant BIDS
knowledge item(s) (by their knowledge.jsonl id), and for fields that have
no knowledge-base record it must degrade gracefully (clean "no relevant
knowledge" message) instead of returning unrelated noise or crashing.

Run with:
    python test_retriever.py
or:
    python -m unittest test_retriever -v
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from retriever import Retriever

KB_DIR = Path(__file__).parent
KB = Retriever(str(KB_DIR))

NO_MATCH_PREFIX = "No relevant BIDS knowledge found"


def item_ids(result: str) -> list:
    """Extract the knowledge item IDs present in a formatted result."""
    return re.findall(r"^ID: (\S+)", result, flags=re.MULTILINE)


def num_items(result: str) -> int:
    return len(re.findall(r"^=== Knowledge Item ", result, flags=re.MULTILINE))


# ---------------------------------------------------------------------------
# Validation report entries (subset actually produced for dataset_description.json)
# ---------------------------------------------------------------------------

# rule_id / field / short query -> expected knowledge record id (if the KB covers it)
EXPECTED_KNOWLEDGE = [
    # JSON_SCHEMA_VALIDATION_ERROR: field must be an array
    ("JSON_SCHEMA_VALIDATION_ERROR", "Authors", "Authors must be array",
     "check_hints_TooFewAuthors"),
    ("JSON_SCHEMA_VALIDATION_ERROR", "ReferencesAndLinks", "ReferencesAndLinks must be array",
     "check_dataset_SingleSourceCitationFields"),
    # JSON_KEY_RECOMMENDED: recommended field missing
    ("JSON_KEY_RECOMMENDED", "HEDVersion", "missing recommended field HEDVersion",
     "err_hedversionnotdefined"),
    # bidsmgr.todo_placeholder: field contains a TODO placeholder
    ("bidsmgr.todo_placeholder", "License", "field License contains a TODO placeholder",
     "file_license"),
    ("bidsmgr.todo_placeholder", "Authors", "field Authors contains a TODO placeholder",
     "check_hints_TooFewAuthors"),
    ("bidsmgr.todo_placeholder", "HowToAcknowledge",
     "field HowToAcknowledge contains a TODO placeholder",
     "check_dataset_SingleSourceCitationFields"),
]

# Fields with NO knowledge-base record -> clean "no relevant knowledge" fallback
UNCOVERED_FIELDS = [
    ("Funding", "Funding must be array"),
    ("EthicsApprovals", "EthicsApprovals must be array"),
    ("Acknowledgements", "field Acknowledgements contains a TODO placeholder"),
]


class RetrieverKnowledgeCoverageTest(unittest.TestCase):
    """Each warning/error must surface the matching BIDS knowledge item."""

    def test_covered_warnings_and_errors(self):
        for rule_id, field, query, expected_id in EXPECTED_KNOWLEDGE:
            with self.subTest(rule_id=rule_id, field=field):
                result = KB.retrieve(query, top_k=3)
                self.assertIn(
                    f"ID: {expected_id}",
                    result,
                    f"query {query!r} should retrieve {expected_id}",
                )

    def test_uncovered_fields_return_clean_fallback(self):
        for field, query in UNCOVERED_FIELDS:
            with self.subTest(field=field):
                result = KB.retrieve(query, top_k=3)
                self.assertTrue(
                    result.startswith(NO_MATCH_PREFIX),
                    f"query {query!r} should return the no-match fallback, got: "
                    f"{item_ids(result)}",
                )

    def test_json_schema_validation_error_rule(self):
        result = KB.retrieve("JSON_SCHEMA_VALIDATION_ERROR", top_k=2)
        self.assertIn("ID: err_jsonschemavalidationerror", result)

    def test_hed_version_rule_code(self):
        result = KB.retrieve("HED_VERSION_NOT_DEFINED", top_k=2)
        self.assertIn("ID: err_hedversionnotdefined", result)


# The 10 demonstration queries, one per validation warning/error, phrased
# the way a Planner would ask the retriever. `None` means the knowledge
# base has no record for that field, so the retriever must return the
# clean "no relevant knowledge" fallback.
DEMO_QUERIES = [
    ("warn JSON_KEY_RECOMMENDED HEDVersion",
     "What is the HEDVersion field used for?",
     ["err_hedversionnotdefined"]),
    ("warn JSON_KEY_RECOMMENDED SourceDatasets",
     "What is SourceDatasets?",
     None),
    ("err JSON_SCHEMA_VALIDATION_ERROR Authors",
     "What is the Authors field?",
     ["check_dataset_SingleSourceAuthors", "check_hints_TooFewAuthors"]),
    ("err JSON_SCHEMA_VALIDATION_ERROR Funding",
     "What is the Funding field?",
     None),
    ("err JSON_SCHEMA_VALIDATION_ERROR EthicsApprovals",
     "What is EthicsApprovals?",
     None),
    ("err JSON_SCHEMA_VALIDATION_ERROR ReferencesAndLinks",
     "What is ReferencesAndLinks?",
     ["check_dataset_SingleSourceCitationFields"]),
    ("warn bidsmgr.todo_placeholder License",
     "What is the License field?",
     ["file_license"]),
    ("warn bidsmgr.todo_placeholder Authors",
     "What should the Authors field contain?",
     ["check_dataset_SingleSourceAuthors", "check_hints_TooFewAuthors"]),
    ("warn bidsmgr.todo_placeholder Acknowledgements",
     "What is Acknowledgements?",
     None),
    ("warn bidsmgr.todo_placeholder HowToAcknowledge",
     "What is HowToAcknowledge?",
     ["check_dataset_SingleSourceCitationFields"]),
]


class DemoQueriesTest(unittest.TestCase):
    """The 10 queries derived from the dataset_description.json report."""

    def test_ten_queries_from_validation_report(self):
        for label, query, expected_ids in DEMO_QUERIES:
            with self.subTest(label=label, query=query):
                result = KB.retrieve(query, top_k=3)
                if expected_ids is None:
                    self.assertTrue(
                        result.startswith(NO_MATCH_PREFIX),
                        f"query {query!r} should fall back, got: {item_ids(result)}",
                    )
                else:
                    for expected_id in expected_ids:
                        self.assertIn(
                            f"ID: {expected_id}",
                            result,
                            f"query {query!r} should retrieve {expected_id}, got: {item_ids(result)}",
                        )


class RetrieverBehaviourTest(unittest.TestCase):
    """General behaviour of the public retrieve() API."""

    def test_public_api_signature(self):
        self.assertIsInstance(KB.retrieve("task entity"), str)
        self.assertIsInstance(KB.retrieve("task entity", top_k=4), str)

    def test_top_k_respected(self):
        result = KB.retrieve("What is the task entity?", top_k=3)
        self.assertEqual(num_items(result), 3)
        result = KB.retrieve("What is the task entity?", top_k=1)
        self.assertEqual(num_items(result), 1)

    def test_top_k_default_is_two(self):
        result = KB.retrieve("What is the task entity?")
        self.assertEqual(num_items(result), 2)

    def test_empty_query(self):
        result = KB.retrieve("   ")
        self.assertEqual(result, "An empty query was provided.")

    def test_caching_across_calls(self):
        first = KB.retrieve("What is the task entity?", top_k=2)
        second = KB.retrieve("What is the task entity?", top_k=2)
        self.assertEqual(first, second)
        self.assertEqual(KB.get_record_count(), len(KB.records))

    def test_relationship_aware_boost(self):
        # The 'covers' edges (mod_mri -> dt_*) should surface the MRI
        # modality record for this query.
        result = KB.retrieve("what datatypes does MRI cover?", top_k=3)
        self.assertIn("ID: rule_modality_mri", result)

    def test_repeated_calls_do_not_reload(self):
        # Construction should be the only place files are read; repeated
        # calls must not re-read/re-parse anything.
        before = KB.get_record_count()
        for _ in range(5):
            KB.retrieve("bold", top_k=1)
        self.assertEqual(KB.get_record_count(), before)


class RetrieverErrorHandlingTest(unittest.TestCase):
    """Graceful handling of missing/malformed knowledge base files."""

    def _write(self, directory: str, filename: str, lines) -> Path:
        path = Path(directory) / filename
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_missing_knowledge_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            retriever = Retriever(tmp)
            result = retriever.retrieve("bold")
            self.assertIn("not found in", result)
            self.assertIn("knowledge.jsonl", result)
            self.assertEqual(retriever.get_record_count(), 0)

    def test_processing_report_alone_is_not_knowledge(self):
        # A directory containing only processing_report.json must NOT be
        # treated as BIDS knowledge.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "processing_report.json",
                        [json.dumps({"records_created": 5})])
            retriever = Retriever(tmp)
            result = retriever.retrieve("bold")
            self.assertIn("not found in", result)
            self.assertIn("knowledge.jsonl", result)
            self.assertEqual(retriever.get_record_count(), 0)

    def test_empty_knowledge_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "knowledge.jsonl", [""])
            retriever = Retriever(tmp)
            result = retriever.retrieve("bold")
            self.assertEqual(result, "The BIDS knowledge base is empty or could not be loaded.")

    def test_malformed_lines_are_skipped(self):
        valid = {
            "id": "test_bold",
            "knowledge_type": "Concept",
            "title": "Test Bold Record",
            "summary": "About the bold suffix.",
            "retrieval_text": "The bold suffix is used in func files.",
            "scope": {},
            "source": {"file": "test.yaml"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "knowledge.jsonl", [
                "this is not valid json {",
                json.dumps(valid),
                "",
                "[1, 2, 3]",
            ])
            retriever = Retriever(tmp)
            self.assertEqual(retriever.get_record_count(), 1)
            result = retriever.retrieve("bold")
            self.assertIsInstance(result, str)
            self.assertNotIn("Traceback", result)

    def test_missing_relationships_and_sources_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "knowledge.jsonl", [json.dumps({
                "id": "test_bold",
                "knowledge_type": "Concept",
                "title": "Test Bold Record",
                "summary": "About the bold suffix.",
                "retrieval_text": "The bold suffix is used in func files.",
                "scope": {},
                "source": {"file": "test.yaml"},
            })])
            retriever = Retriever(tmp)  # no relationships/sources files
            result = retriever.retrieve("bold")
            self.assertIn("ID: test_bold", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
