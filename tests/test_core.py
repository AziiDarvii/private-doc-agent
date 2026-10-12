"""Unit tests for the pieces that don't need a GPU. Run: python3 -m unittest -v"""
import unittest
from unittest import mock

from app import agent, llm, metrics, privacy


class ChunkTextTests(unittest.TestCase):
    def test_returns_empty_list_for_blank_text(self) -> None:
        self.assertEqual(agent.chunk_text("  \n\n ", 100), [])

    def test_keeps_small_paragraphs_together(self) -> None:
        self.assertEqual(agent.chunk_text("a\n\nb", 100), ["a\n\nb"])

    def test_no_chunk_exceeds_limit(self) -> None:
        text = "\n\n".join(["x" * 40] * 10 + ["y" * 250])
        chunks = agent.chunk_text(text, 100)
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        self.assertEqual("".join(chunks).count("y"), 250)


class RetrieveTests(unittest.TestCase):
    def test_ranks_relevant_excerpt_first(self) -> None:
        doc = "The pet policy forbids dogs.\n\n" + ("Rent is due monthly. " * 10)
        self.assertIn("pet", agent.retrieve(doc, "Can I have a dog as a pet?")[0])


class PrivacyTests(unittest.TestCase):
    def test_detects_and_masks_ssn_and_email(self) -> None:
        found = {p["type"]: p for p in privacy.find_pii("SSN 512-44-9087, mail a.b@example.com")}
        self.assertIn("SSN", found)
        self.assertIn("Email", found)
        self.assertNotIn("512-44-9087", found["SSN"]["examples"][0])

    def test_clean_text_has_no_pii(self) -> None:
        self.assertEqual(privacy.find_pii("The rent is due on the first."), [])


class EgressGuardTests(unittest.TestCase):
    def test_blocks_third_party_host_before_any_io(self) -> None:
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(llm.EgressBlocked):
                llm.guarded_post("https://api.openai.com/v1/chat/completions", {"x": 1})
            urlopen.assert_not_called()
        self.assertGreaterEqual(llm.stats()["blocked_attempts"], 1)
        self.assertEqual(llm.stats()["bytes_to_third_parties"], 0)


class ParseJsonTests(unittest.TestCase):
    def test_extracts_object_from_chatty_reply(self) -> None:
        self.assertEqual(llm.parse_json('Sure! {"a": 1} hope that helps'), {"a": 1})

    def test_returns_none_for_garbage(self) -> None:
        self.assertIsNone(llm.parse_json("no json here"))


class MetricsTests(unittest.TestCase):
    SAMPLE = "\n".join([
        "# HELP vllm:num_requests_running x",
        'vllm:num_requests_running{engine="0"} 3.0',
        'vllm:kv_cache_usage_perc{engine="0"} 0.25',
        'vllm:generation_tokens_total{engine="0"} 1100.0',
        'vllm:time_to_first_token_seconds_sum{engine="0"} 2.0',
        'vllm:time_to_first_token_seconds_count{engine="0"} 4.0',
    ])

    def test_snapshot_computes_rates_and_averages(self) -> None:
        parsed = metrics.parse_prometheus(self.SAMPLE)
        snap = metrics.build_snapshot(parsed, {"t": 0.0, "gen": 100.0}, 10.0)
        self.assertEqual(snap["running"], 3.0)
        self.assertEqual(snap["kv_cache_pct"], 25.0)
        self.assertEqual(snap["tokens_per_s"], 100.0)
        self.assertEqual(snap["avg_ttft_s"], 0.5)


class AnalyzeTests(unittest.TestCase):
    def test_merges_chunk_results_and_sorts_risks(self) -> None:
        replies = iter([
            ('{"summary": "s1", "risks": [{"severity": "low", "issue": "a"}], "key_facts": ["f"]}', {}),
            ('{"summary": "s2", "risks": [{"severity": "high", "issue": "b"}], "key_facts": ["F"]}', {}),
            ("merged summary", {}),
        ])
        with mock.patch.object(agent, "ANALYSIS_CHUNK_CHARS", 12), \
             mock.patch.object(agent, "MAX_PARALLEL_CHUNKS", 1), \
             mock.patch.object(llm, "chat", side_effect=lambda *a, **k: next(replies)):
            result = agent.analyze("first part\n\nsecond part")
        self.assertEqual(result["summary"], "merged summary")
        self.assertEqual([r["issue"] for r in result["risks"]], ["b", "a"])
        self.assertEqual(result["key_facts"], ["f"])

    def test_rejects_empty_document(self) -> None:
        with self.assertRaises(ValueError):
            agent.analyze("   ")


if __name__ == "__main__":
    unittest.main()
