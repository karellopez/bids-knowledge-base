import os
import re
import json
import time
import hashlib
import argparse
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from openai import OpenAI
from tavily import TavilyClient
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).parent

INPUT_FILE = BASE_DIR / "knowledge.jsonl"

OUTPUT_FILE = BASE_DIR / "enriched_knowledge.jsonl"

CHECKPOINT_FILE = BASE_DIR / "enrichment_checkpoint.json"

SEARCH_CACHE_FILE = BASE_DIR / "search_cache.json"

ENV_FILE = BASE_DIR / ".env"

if load_dotenv is not None:
    load_dotenv(ENV_FILE)

ACADEMIC_CLOUD_API_KEY = os.environ.get("ACADEMIC_CLOUD_API_KEY", "")
ACADEMIC_CLOUD_API_ENDPOINT = os.environ.get(
    "ACADEMIC_CLOUD_API_ENDPOINT",
    "https://chat-ai.academiccloud.de/v1",
)
ENRICHMENT_MODEL = os.environ.get("ENRICHMENT_MODEL", "qwen3.6-35b-a3b")

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

OFFICIAL_BIDS_DOMAINS = [
    "bids-specification.readthedocs.io",
    "bids-validator.readthedocs.io",
    "bids-standard.github.io",
]

OFFICIAL_GITHUB_REPOS = [
    "github.com/bids-standard/bids-specification",
    "github.com/bids-standard/bids-validator",
]

MAX_SEARCH_RESULTS = 6

SEARCH_DELAY_SECONDS = 1.0

LLM_DELAY_SECONDS = 0.2

MAX_WEB_CONTEXT_CHARS = 12000

MAX_NEW_TOKENS = 2048

MAX_NEW_TOKENS_CAP = 8192

TEMPERATURE = 0.2

REQUEST_TIMEOUT_SECONDS = 180

MAX_LLM_ATTEMPTS = 3

MAX_SEARCH_ATTEMPTS = 3


# ============================================================
# FILE UTILITIES
# ============================================================

def iter_jsonl(path: Path):
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as f:

        for line_number, line in enumerate(f, 1):

            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[ERROR] Invalid JSON at {path.name}:{line_number}: {e}")
                continue

            if not isinstance(record, dict):
                print(f"[WARNING] {path.name}:{line_number} is not a JSON object. Skipping.")
                continue

            yield record


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    return list(iter_jsonl(path))


def append_jsonl(path: Path, record: Dict[str, Any]):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def rewrite_jsonl(path: Path, records: List[Dict[str, Any]]):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    temp_path.replace(path)


def load_json_file(path: Path, default: Any):
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARNING] Could not load {path.name}: {e}")
        return default


def save_json_file(path: Path, data: Any):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp_path.replace(path)


# ============================================================
# OUTPUT STORE (crash-safe resume against the output file)
# ============================================================

class OutputStore:

    def __init__(self, path: Path):
        self.path = path
        self.records_by_id: Dict[str, Dict[str, Any]] = {}
        self.order: List[str] = []
        self.duplicate_lines = 0
        self._load_existing()

    def _load_existing(self):
        for record in iter_jsonl(self.path):
            record_id = record.get("id")

            if not record_id:
                continue

            if record_id in self.records_by_id:
                self.duplicate_lines += 1

            else:
                self.order.append(record_id)

            self.records_by_id[record_id] = record

    def processed_ids(self) -> Set[str]:
        return set(self.records_by_id.keys())

    def append(self, enriched_record: Dict[str, Any]):
        record_id = enriched_record.get("id")

        is_new = record_id not in self.records_by_id

        append_jsonl(self.path, enriched_record)

        if is_new:
            self.order.append(record_id)

        self.records_by_id[record_id] = enriched_record

    def replace(self, enriched_record: Dict[str, Any]):
        record_id = enriched_record.get("id")

        if record_id not in self.records_by_id:
            self.append(enriched_record)
            return

        self.records_by_id[record_id] = enriched_record

        rewrite_jsonl(
            self.path,
            [self.records_by_id[rid] for rid in self.order],
        )

    def count(self) -> int:
        return len(self.records_by_id)


# ============================================================
# CHECKPOINT
# ============================================================

def load_checkpoint() -> Dict[str, Any]:
    return load_json_file(
        CHECKPOINT_FILE,
        {"processed_ids": [], "failed_ids": [], "last_index": -1},
    )


def save_checkpoint(checkpoint: Dict[str, Any]):
    save_json_file(CHECKPOINT_FILE, checkpoint)


# ============================================================
# SEARCH CACHE
# ============================================================

def load_search_cache() -> Dict[str, Any]:
    return load_json_file(SEARCH_CACHE_FILE, {})


def save_search_cache(cache: Dict[str, Any]):
    save_json_file(SEARCH_CACHE_FILE, cache)


# ============================================================
# URL HELPERS
# ============================================================

def normalize_url(url: str) -> str:
    return str(url).strip().rstrip("/")


def deduplicate_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}

    for result in results:
        url = normalize_url(result.get("url", ""))

        if not url:
            continue

        if url not in unique:
            unique[url] = result

    return list(unique.values())


def is_official_url(url: str) -> bool:
    parsed = urlparse(normalize_url(url).lower())
    host = parsed.netloc.lower()

    if any(host == d or host.endswith("." + d) for d in OFFICIAL_BIDS_DOMAINS):
        return True

    full = host + parsed.path

    return any(full.startswith(repo) for repo in OFFICIAL_GITHUB_REPOS)


# ============================================================
# SEARCH PROVIDER ABSTRACTION
# ============================================================

class SearchProvider(ABC):

    @abstractmethod
    def search(
        self,
        query: str,
        max_results: int = 5,
        domains: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        pass


# ============================================================
# TAVILY SEARCH PROVIDER
# ============================================================

class TavilySearchProvider(SearchProvider):

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError(
                "TAVILY_API_KEY is not set. "
                "Add it to .env or export it as an environment variable."
            )

        self.client = TavilyClient(api_key=api_key)

    def search(
        self,
        query: str,
        max_results: int = 5,
        domains: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:

        for attempt in range(1, MAX_SEARCH_ATTEMPTS + 1):

            try:
                response = self.client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=max_results,
                    include_answer=False,
                    include_raw_content=False,
                    include_images=False,
                    include_domains=domains,
                )

                results = []

                for item in response.get("results", []):
                    url = item.get("url", "")

                    if not url:
                        continue

                    results.append(
                        {
                            "title": item.get("title", ""),
                            "url": url,
                            "snippet": item.get("content", ""),
                            "score": item.get("score", 0),
                        }
                    )

                return results

            except Exception as e:
                print(
                    f"[SEARCH ERROR] Tavily attempt "
                    f"{attempt}/{MAX_SEARCH_ATTEMPTS}: {e}"
                )

                if attempt < MAX_SEARCH_ATTEMPTS:
                    wait_time = 2 * attempt
                    print(f"[SEARCH] Retrying in {wait_time}s...")
                    time.sleep(wait_time)

        return []


# ============================================================
# SEARCH MANAGER
# ============================================================

class SearchManager:

    def __init__(self, provider: SearchProvider, cache: Dict[str, Any]):
        self.provider = provider
        self.cache = cache

    def _cache_key(self, query: str, domains: Optional[List[str]]) -> str:
        value = json.dumps(
            {"query": query, "domains": domains},
            sort_keys=True,
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def search(
        self,
        query: str,
        max_results: int = MAX_SEARCH_RESULTS,
        domains: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:

        key = self._cache_key(query, domains)

        if key in self.cache:
            return self.cache[key]

        results = self.provider.search(
            query=query,
            max_results=max_results,
            domains=domains,
        )

        self.cache[key] = results

        save_search_cache(self.cache)

        time.sleep(SEARCH_DELAY_SECONDS)

        return results


# ============================================================
# SEARCH QUERY
# ============================================================

def build_search_query(record: Dict[str, Any]) -> str:
    title = record.get("title", "")
    summary = record.get("summary", "")
    knowledge_type = record.get("knowledge_type", "")
    retrieval_text = record.get("retrieval_text", "")

    query_parts = [title, knowledge_type, summary, retrieval_text]

    query = " ".join(str(x) for x in query_parts if x)

    query = re.sub(r"\s+", " ", query).strip()

    return query[:700]


# ============================================================
# COMBINED WEB SEARCH
# ============================================================

def search_web(
    record: Dict[str, Any],
    search_manager: SearchManager,
) -> List[Dict[str, Any]]:
    query = build_search_query(record)

    print(f"[SEARCH] Query: {query[:160]}")

    print("[SEARCH] Searching official BIDS sources...")

    official_results = search_manager.search(
        query=query,
        max_results=MAX_SEARCH_RESULTS,
        domains=list(OFFICIAL_BIDS_DOMAINS),
    )

    print("[SEARCH] Searching general web...")

    general_results = search_manager.search(
        query=query,
        max_results=MAX_SEARCH_RESULTS,
    )

    results = official_results + general_results

    for result in results:
        result["official"] = is_official_url(result.get("url", ""))

    results = deduplicate_results(results)

    results.sort(
        key=lambda x: (
            not x.get("official", False),
            -(x.get("score", 0) or 0),
        )
    )

    return results[:MAX_SEARCH_RESULTS]


# ============================================================
# WEB CONTEXT
# ============================================================

def build_web_context(search_results: List[Dict[str, Any]]) -> str:
    if not search_results:
        return "No web search results were available."

    chunks = []

    for i, result in enumerate(search_results, 1):
        source_type = (
            "OFFICIAL BIDS SOURCE"
            if result.get("official", False)
            else "GENERAL WEB SOURCE"
        )

        chunks.append(
            f"""SOURCE {i}
Type: {source_type}
Title: {result.get("title", "")}
URL: {result.get("url", "")}
Snippet: {result.get("snippet", "")}""".strip()
        )

    context = "\n\n".join(chunks)

    return context[:MAX_WEB_CONTEXT_CHARS]


# ============================================================
# LLM SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = r"""
You are a BIDS specification knowledge enrichment assistant.

You are working on the knowledge base of BIDS Manager, a local AI agent that
helps users understand BIDS datasets, validation errors, warnings, rules,
metadata, filenames, entities, modalities and related concepts.

Your task is to enrich an existing BIDS knowledge record with additional
explanatory information based on:

1. The existing structured BIDS knowledge record.
2. Search results from official BIDS specification sources.
3. Search results from official BIDS Validator sources.
4. Additional web search results.

CRITICAL RULES:

- The existing knowledge record is authoritative.

- Official BIDS sources have the highest priority.

- Do not invent BIDS rules.

- Do not invent validator error codes.

- Do not invent metadata fields.

- Do not invent entity definitions.

- Do not claim that something is required unless the supplied sources support it.

- Do not turn a recommendation into a requirement.

- Do not invent repair instructions.

- If information cannot be established from the supplied material, say so.

- Clearly distinguish source-supported facts from explanatory interpretation.

- Examples must be realistic and consistent with BIDS.

- Examples must NOT be presented as normative requirements unless the source
  explicitly makes them requirements.

For validation errors and warnings, provide practical explanations that a
normal BIDS user can understand.

The output MUST be valid JSON.

Do not output Markdown.

Do not output ```json.

Do not output explanations outside the JSON object.
"""


# ============================================================
# LLM PROMPT
# ============================================================

def build_llm_prompt(
    record: Dict[str, Any],
    search_results: List[Dict[str, Any]],
) -> str:
    web_context = build_web_context(search_results)

    record_json = json.dumps(record, ensure_ascii=False, indent=2)

    return f"""
Enrich the following BIDS knowledge record.

EXISTING KNOWLEDGE RECORD
--------------------------------
{record_json}
--------------------------------

WEB SEARCH RESULTS
--------------------------------
{web_context}
--------------------------------

Return ONLY this JSON structure:

{{
  "description": "",
  "interpretation": "",
  "why_it_matters": "",
  "example_scenarios": [],
  "common_causes": [],
  "resolution_guidance": "",
  "additional_notes": "",
  "confidence": "high | medium | low",
  "sources": []
}}

Field instructions:

description:
A clear explanation of what this BIDS concept, rule, error, or warning means.

interpretation:
Explain how the existing knowledge should be understood by a BIDS Manager user.

why_it_matters:
Explain why this information matters when organizing or validating a BIDS dataset.

example_scenarios:
Provide 0-3 concrete examples.

For errors/warnings, examples should show realistic situations that could cause
or demonstrate the issue.

common_causes:
List 0-5 likely causes only when supported by the knowledge or sources.

resolution_guidance:
Explain what the user should investigate or change.

Do NOT invent a specific fix when the correct value cannot be determined from
the available information.

additional_notes:
Useful contextual information that improves understanding.

confidence:
Your confidence in the generated enrichment: high, medium, or low.

sources:
Return a list of source objects:

[
  {{
    "title": "...",
    "url": "...",
    "relevance": "..."
  }}
]

Only include sources actually present in the supplied search results.
Never fabricate a URL.

IMPORTANT:

The enrichment must complement the existing knowledge.

Do not rewrite or replace the original knowledge.

Do not add unsupported BIDS requirements.
"""


# ============================================================
# LLM CLIENT
# ============================================================

class LLMClient:

    def __init__(self):
        if not ACADEMIC_CLOUD_API_KEY:
            raise ValueError(
                "ACADEMIC_CLOUD_API_KEY is not set. "
                "Add it to .env or export it as an environment variable."
            )

        self.model = ENRICHMENT_MODEL
        self.json_mode_ok = True

        self.client = OpenAI(
            api_key=ACADEMIC_CLOUD_API_KEY,
            base_url=ACADEMIC_CLOUD_API_ENDPOINT,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=1,
        )

        print(f"LLM model: {self.model}")
        print(f"LLM endpoint: {ACADEMIC_CLOUD_API_ENDPOINT}")

    @staticmethod
    def _is_json_mode_error(error: Exception) -> bool:
        text = str(error).lower()
        return "response_format" in text or "json_object" in text or "json mode" in text

    def generate(
        self,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
    ) -> Tuple[str, Optional[str]]:

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": max_tokens,
        }

        if self.json_mode_ok:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            if self.json_mode_ok and self._is_json_mode_error(e):
                print("[LLM] Endpoint rejected JSON response format; using plain mode.")
                self.json_mode_ok = False
                return self.generate(system_prompt, prompt, max_tokens)
            raise

        choice = response.choices[0]
        message = choice.message

        content = (
            getattr(message, "content", None)
            or getattr(message, "reasoning_content", None)
            or ""
        ).strip()

        finish_reason = getattr(choice, "finish_reason", None)

        return content, finish_reason


# ============================================================
# JSON EXTRACTION
# ============================================================

def extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()

    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        return None

    try:
        result = json.loads(text[start:end + 1])
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        return None

    return None


# ============================================================
# ENRICHMENT VALIDATION
# ============================================================

REQUIRED_ENRICHMENT_FIELDS = [
    "description",
    "interpretation",
    "why_it_matters",
    "example_scenarios",
    "common_causes",
    "resolution_guidance",
    "additional_notes",
    "confidence",
    "sources",
]

STRING_ENRICHMENT_FIELDS = [
    "description",
    "interpretation",
    "why_it_matters",
    "resolution_guidance",
    "additional_notes",
]

ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def validate_enrichment(enrichment: Dict[str, Any]) -> bool:
    for field in REQUIRED_ENRICHMENT_FIELDS:
        if field not in enrichment:
            return False

    for field in STRING_ENRICHMENT_FIELDS:
        if not isinstance(enrichment[field], str):
            return False

    if not isinstance(enrichment["example_scenarios"], list):
        return False

    if not isinstance(enrichment["common_causes"], list):
        return False

    if not isinstance(enrichment["sources"], list):
        return False

    if enrichment["confidence"] not in ALLOWED_CONFIDENCE:
        return False

    return True


def sanitize_sources(
    enrichment: Dict[str, Any],
    allowed_urls: Set[str],
) -> int:
    sanitized = []
    dropped = 0

    for source in enrichment.get("sources", []):
        if not isinstance(source, dict):
            dropped += 1
            continue

        url = normalize_url(source.get("url", ""))

        if url not in allowed_urls:
            dropped += 1
            continue

        sanitized.append(
            {
                "title": str(source.get("title", "")),
                "url": source.get("url", ""),
                "relevance": str(source.get("relevance", "")),
            }
        )

    enrichment["sources"] = sanitized

    return dropped


# ============================================================
# PROCESS ONE RECORD
# ============================================================

def enrich_record(
    index: int,
    total: int,
    record: Dict[str, Any],
    llm: LLMClient,
    search_manager: SearchManager,
) -> Optional[Dict[str, Any]]:
    record_id = record.get("id", "unknown")

    print()
    print(f"Processing {index + 1}/{total}")
    print(f"Record: {record_id}")

    # --------------------------------------------------------
    # Search
    # --------------------------------------------------------

    search_results = search_web(record, search_manager)

    print(f"[SEARCH] Found {len(search_results)} results.")

    allowed_urls = {normalize_url(r.get("url", "")) for r in search_results}

    # --------------------------------------------------------
    # LLM
    # --------------------------------------------------------

    prompt = build_llm_prompt(record, search_results)

    max_tokens = MAX_NEW_TOKENS

    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):

        try:
            print(f"[LLM] Generating enrichment... (attempt {attempt}/{MAX_LLM_ATTEMPTS})")

            raw_response, finish_reason = llm.generate(
                SYSTEM_PROMPT,
                prompt,
                max_tokens=max_tokens,
            )

        except Exception as e:
            print(f"[LLM ERROR] Attempt {attempt}: {e}")

            if attempt < MAX_LLM_ATTEMPTS:
                time.sleep(2 * attempt)

            continue

        if finish_reason == "length":
            print("[LLM] Response truncated (finish_reason=length). Not accepted.")

            max_tokens = min(max_tokens * 2, MAX_NEW_TOKENS_CAP)

            continue

        enrichment = extract_json(raw_response)

        if enrichment is None:
            print("[LLM] Could not parse valid JSON from response.")

            if attempt < MAX_LLM_ATTEMPTS:
                time.sleep(2 * attempt)

            continue

        if not validate_enrichment(enrichment):
            print("[LLM] Response does not match the required enrichment schema.")

            if attempt < MAX_LLM_ATTEMPTS:
                time.sleep(2 * attempt)

            continue

        dropped = sanitize_sources(enrichment, allowed_urls)

        if dropped:
            print(f"[LLM] Dropped {dropped} source(s) that were not in the search results.")

        print("[LLM] Valid JSON received.")

        enriched_record = dict(record)

        enriched_record["ai_enrichment"] = enrichment

        enriched_record["ai_enrichment_metadata"] = {
            "model": llm.model,
            "search_provider": "tavily",
            "search_result_count": len(search_results),
            "enriched_at": datetime.now(timezone.utc).isoformat(),
        }

        return enriched_record

    return None


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich the BIDS knowledge base with web search + LLM.",
        epilog=(
            "Examples:\n"
            "  python knowledge_base_enricher.py --limit 3\n"
            "  python knowledge_base_enricher.py --ids ver_bids_1.11.2-dev --reprocess\n"
            "  python knowledge_base_enricher.py --start-index 100\n"
            "  python knowledge_base_enricher.py --fresh\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N pending records (for smoke tests).",
    )

    parser.add_argument(
        "--ids",
        type=str,
        default=None,
        help="Comma-separated record IDs to process explicitly.",
    )

    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-enrich records even if they were already processed successfully.",
    )

    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Skip all records before this index in knowledge.jsonl.",
    )

    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Reset progress: delete checkpoint and start a new output file.",
    )

    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main():
    args = parse_args()

    print("=" * 80)
    print("BIDS Manager Knowledge Enrichment")
    print("=" * 80)

    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")

    if not ACADEMIC_CLOUD_API_KEY:
        raise RuntimeError(
            "ACADEMIC_CLOUD_API_KEY is not configured "
            "(set it in .env or the environment)."
        )

    if not TAVILY_API_KEY:
        raise RuntimeError(
            "TAVILY_API_KEY is not configured "
            "(set it in .env or the environment)."
        )

    # --------------------------------------------------------
    # Fresh run resets progress files
    # --------------------------------------------------------

    if args.fresh:
        if OUTPUT_FILE.exists():
            OUTPUT_FILE.unlink()
            print(f"[RESET] Deleted {OUTPUT_FILE.name}")

        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()
            print(f"[RESET] Deleted {CHECKPOINT_FILE.name}")

    # --------------------------------------------------------
    # Load records
    # --------------------------------------------------------

    records = load_jsonl(INPUT_FILE)
    total_records = len(records)

    print(f"Loaded {total_records} knowledge records.")

    # --------------------------------------------------------
    # Resume state: checkpoint + output file must agree
    # --------------------------------------------------------

    checkpoint = load_checkpoint()

    processed_ids: Set[str] = set(checkpoint.get("processed_ids", []))
    failed_ids: Set[str] = set(checkpoint.get("failed_ids", []))

    output_store = OutputStore(OUTPUT_FILE)

    output_ids = output_store.processed_ids()

    recovered = output_ids - processed_ids

    if recovered:
        print(
            f"[RESUME] {len(recovered)} record(s) found in the output file "
            f"but missing from the checkpoint; treating them as processed."
        )

    processed_ids |= output_ids

    if failed_ids & processed_ids:
        failed_ids -= processed_ids

    already_done = len(processed_ids & {r.get("id") for r in records})

    print(
        f"Progress: {already_done} enriched, "
        f"{len(failed_ids)} previously failed, "
        f"{total_records - already_done} pending."
    )

    if output_store.duplicate_lines:
        print(
            f"[WARNING] Output file contained {output_store.duplicate_lines} "
            f"duplicate line(s); last version of each ID is kept."
        )

    # --------------------------------------------------------
    # Explicit ID selection
    # --------------------------------------------------------

    forced_ids: Optional[Set[str]] = None

    if args.ids:
        forced_ids = {x.strip() for x in args.ids.split(",") if x.strip()}

    known_ids = {r.get("id") for r in records}

    if forced_ids:
        unknown = forced_ids - known_ids

        if unknown:
            print(f"[WARNING] Unknown ID(s) ignored: {', '.join(sorted(unknown))}")

    # --------------------------------------------------------
    # Initialize clients
    # --------------------------------------------------------

    tavily = TavilySearchProvider(TAVILY_API_KEY)

    search_manager = SearchManager(provider=tavily, cache=load_search_cache())

    print("Tavily search provider initialized.")

    llm = LLMClient()

    # --------------------------------------------------------
    # Process records
    # --------------------------------------------------------

    attempted = 0
    succeeded_now = 0
    failed_now = 0
    skipped = 0
    selected_failed_ids: Set[str] = set()

    for index, record in enumerate(records):

        if index < args.start_index:
            continue

        if not isinstance(record, dict):
            print(f"[WARNING] Record {index} is not a dictionary. Skipping.")
            continue

        record_id = record.get("id")

        if not record_id:
            print(f"[WARNING] Record at index {index} has no ID. Skipping.")
            continue

        forced_here = bool(forced_ids and record_id in forced_ids)

        if forced_ids is not None and not forced_here:
            continue

        wants_reprocess = args.reprocess and (forced_ids is None or forced_here)

        if record_id in processed_ids and not forced_here and not wants_reprocess:
            skipped += 1
            continue

        if args.limit is not None and attempted >= args.limit:
            break

        attempted += 1

        was_processed_before = record_id in processed_ids

        enriched = enrich_record(index, total_records, record, llm, search_manager)

        # ----------------------------------------------------
        # Failed
        # ----------------------------------------------------

        if enriched is None:
            print(f"[FAILED] {record_id}")

            failed_now += 1

            if not was_processed_before:
                failed_ids.add(record_id)

            if forced_here or wants_reprocess:
                selected_failed_ids.add(record_id)

            checkpoint["processed_ids"] = sorted(processed_ids)
            checkpoint["failed_ids"] = sorted(failed_ids)
            checkpoint["last_index"] = index

            save_checkpoint(checkpoint)

            continue

        # ----------------------------------------------------
        # Succeeded
        # ----------------------------------------------------

        if was_processed_before and record_id in output_store.records_by_id:
            output_store.replace(enriched)
        else:
            output_store.append(enriched)

        processed_ids.add(record_id)
        failed_ids.discard(record_id)

        checkpoint["processed_ids"] = sorted(processed_ids)
        checkpoint["failed_ids"] = sorted(failed_ids)
        checkpoint["last_index"] = index

        save_checkpoint(checkpoint)

        succeeded_now += 1

        print(f"[SUCCESS] {record_id}")

        time.sleep(LLM_DELAY_SECONDS)

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("Finished")
    print("=" * 80)
    print(f"Total records:      {total_records}")
    print(f"Attempted now:      {attempted}")
    print(f"Succeeded now:      {succeeded_now}")
    print(f"Failed now:         {failed_now}")
    print(f"Skipped (done):     {skipped}")
    print(f"Total enriched:     {output_store.count()}")

    if selected_failed_ids:
        print(f"Reprocess failures: {', '.join(sorted(selected_failed_ids))}")

    print(f"Output:             {OUTPUT_FILE}")
    print(f"Checkpoint:         {CHECKPOINT_FILE}")
    print(f"Search cache:       {SEARCH_CACHE_FILE}")

    if failed_ids:
        remaining_pending_failures = failed_ids - processed_ids

        if remaining_pending_failures:
            print(
                f"[NOTE] {len(remaining_pending_failures)} record(s) failed and will be "
                f"retried automatically on the next run."
            )


if __name__ == "__main__":
    main()
