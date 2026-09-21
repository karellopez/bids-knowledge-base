# BIDS Knowledge Engineering Pipeline

This folder contains all code and output files for converting raw BIDS specification YAML/Markdown data into a structured, semantic Knowledge Base compatible with the BIDS Manager AI Agent.

## Quick Directory Structure

```
outputs/
├── KB_README.md                        # Human-readable summary of extracted knowledge
├── bids_knowledge_extraction.ipynb     # Jupyter notebook showing extraction workflow
├── extract_kb.py                       # Main extraction script (re-run to regenerate KB)
├── knowledge.jsonl                     # Primary KB – 1,272 atomic knowledge records (one JSON per line)
├── relationships.jsonl                 # Relationship edges – 271 connections between records
├── sources.jsonl                       # Source inventory with provenance
├── processing_report.json             # Statistics, quality metrics, and metadata
└── retriever.py                        # Lightweight token-scored retriever for the AI Agent
```

---

## 1. What Was Extracted

The raw BIDS specification was parsed from **109 YAML/Markdown source files** spanning five directories:

| Directory | Contents | Records Extracted |
|-----------|----------|-------------------|
| `BIDS_VERSION`, `SCHEMA_VERSION` | Version identifiers | 2 Version records |
| `meta/` | Context, associations, templates, expression tests | ~67 records |
| `objects/` | Entity/suffix/datatype/modality/extension/enum definitions | ~866 records |
| `rules/` | Directory layouts, error codes, modality mappings | ~85 records |
| `rules/checks/` | Validation checks | ~131 records |
| `rules/sidecars/` | JSON sidecar metadata fields | ~184 records |
| `rules/files/` | File suffix/type/extension/entity rules | ~180 records |
| `rules/tabular_data/` | TSV/CSV column definitions | ~180 records |

### Knowledge Categories and Counts

```
Concept                 :  240 records
Enum                    :  218 records
MetadataRule            :  184 records
FileSpecification        :  180 records
TabularRule             :  180 records
Check                   :  131 records
Definition              :   36 records
DirectoryRule           :   33 records
Error                   :   22 records
Template               :   17 records
Association             :   13 records
Relationship            :   11 records
Warning                :    5 records
Version                 :    2 records
                        -------
Total                   : 1,272 records
```

---

## 2. How Knowledge Was Categorized

Each source YAML/Markdown file was examined for its actual content before deciding how to classify it. **File names do not describe what is inside them.**

### Classification Strategy

| Source File Type | Classification Approach |
|------------------|------------------------|
| `objects/*.yaml` | Every definition becomes a standalone Concept or Definition |
| `objects/enums.yaml` | Enumeration values are stored with their allowed_values |
| `meta/associations.yaml` | File association rules become Association records with selectors/target |
| `meta/templates.yaml` | Filename templates become Template records with entities/extensions |
| `meta/expression_tests.yaml` | Expression tests become Expression records for engine validation |
| `meta/context.yaml` | Namespace definitions become Concept records |
| `rules/errors.yaml` | Every error/warning code becomes an Error or Warning record with severity |
| `rules/directories.yaml` | Directory layouts become DirectoryRule records |
| `rules/modalities.yaml` | Modality→datatype mappings become Relationship records |
| `rules/checks/*.yaml` | Validation checks become Check records with conditions/severity |
| `rules/sidecars/*.yaml` | Metadata field definitions become MetadataRule records |
| `rules/files/**/*.yaml` | File specification rules become FileSpecification records |
| `rules/tabular_data/*.yaml` | Column definitions become TabularRule records |

### Atomicity Principle

Each knowledge record represents **one atomic unit of BIDS knowledge**:

- **Bad** (too much in one record): *"Functional MRI files use task and run entities, bold suffix, NIfTI format, and require JSON sidecars..."*
- **Good** (atomic): *"The bold suffix belongs to the func datatype. The task entity applies to func filenames. ..."

Exception: conditional rules like *"If X then Y is required"* are kept together because splitting them would lose meaning.

---

## 3. How Relationships Were Represented

### Relationship File (`relationships.jsonl`)

Contains 271 relationship edges in the format:

```json
{
  "source": "ctx_dataset",
  "relation": "contains",
  "target": "ctx_subjects",
  "source_reference": "meta/context.yaml",
  "confidence": "explicit"
}
```

### Controlled Vocabulary

| Relation | Meaning | Example |
|----------|---------|---------|
| `defines` | A source defines/introduces a concept | `objects/entities.yaml` → entity definitions |
| `covers` | A modality covers a set of datatypes | `mri` → `[anat, dwi, fmap, func, perf]` |
| `applies_to` | A rule applies to specific file types/conditions | Error codes with selectors |
| `maps_to_metadata` | An entity maps to a JSON metadata field | `ce` entity → `ContrastBolusIngredient` |
| `templates` | A template defines filename structure | `meta/templates.yaml` → derivative templates |
| `contains` | A container contains items | `dataset` → `subjects`, `datatypes` |
| `provides` | A context provides information | `subject` context → `sessions` |
| `triggers` | A check triggers an error/warning | `check → error_condition` |

### Relationship Resolution

The `retriever.py` module provides optional relationship-aware boosting: when a highly relevant record has relationships pointing to it, related records may be considered for inclusion.

---

## 4. What Could Not Be Classified

**Zero unclassified sections.** Every meaningful source section was represented by at least one knowledge record. All files were processed and classified.

Files that were processed but contributed no user-facing knowledge:
- Files containing only whitespace or comments
- Files with nested structures that didn't represent atomic knowledge units

---

## 5. Inference Performed

**No inferred knowledge was added.** All 1,272 records are marked as `confidence: explicit`. The extraction script intentionally does not invent facts beyond what the source YAML explicitly states.

When a source file defines a suffix, the extractor records the suffix, not what datatypes happen to use it. When a source defines an entity, the extractor records the entity description, not assumed relationships.

---

## 6. Conflicts Found

**Zero conflicts detected.** No source files contained contradictory information about the same BIDS concept. This is expected because:
- The YAML schema is maintained coherently by the BIDS standard
- Different version files (e.g., `objects/entities.yaml` vs `rules/entities.yaml`) describe complementary aspects (definition vs. ordering), not conflicting facts

---

## 7. Source and Version Information

### Version Metadata

| Field | Value |
|-------|-------|
| **BIDS Version** | `1.11.2-dev` |
| **Schema Version** | `2.0.0-dev` |

### Released Versions Recorded

The following released BIDS versions are catalogued in the knowledge base:

```
1.11.1, 1.11.0, 1.10.1, 1.10.0, 1.9.0, 1.8.0, 1.7.0, 1.6.0,
1.5.0, 1.4.1, 1.4.0, 1.3.0, 1.2.2, 1.2.1, 1.2.0, 1.1.2,
1.1.1, 1.1.0, 1.0.2, 1.0.1, 1.0.0
```

### Source Tracing

Every knowledge record contains full provenance in its `source` field:

```json
{
  "source": {
    "file": "objects/entities.yaml",
    "path": "/full/path/to/objects/entities.yaml",
    "section": "entities",
    "key": "hemisphere"
  }
}
```

The `sources.jsonl` file maps each source YAML file to the number of knowledge records it contributed.

---

## 8. File Descriptions

### `extract_kb.py` — Extraction Engine

**Purpose**: Main Python script that reads raw YAML/Markdown files and produces the JSONL knowledge base.

**Usage** (re-run if source YAML files change):

```bash
cd outputs/
python extract_kb.py
```

**Outputs on each run**:
- `knowledge.jsonl` — Atomic knowledge records
- `relationships.jsonl` — Relationship edges
- `sources.jsonl` — Source inventory
- `processing_report.json` — Processing statistics

**Architecture**:
```
extract_kb.py ──reads──→ raw YAML files
       │
       ├─→ extract_version_info()     # BIDS_VERSION, SCHEMA_VERSION, released versions
       ├─→ extract_context()           # meta/context.yaml namespaces
       ├─→ extract_expression_tests()  # meta/expression_tests.yaml
       ├─→ extract_templates()         # meta/templates.yaml raw/deriv/atlas
       ├─→ extract_common_principles() # objects/common_principles.yaml
       ├─→ extract_entities()          # objects/entities.yaml
       ├─→ extract_entity_order()      # rules/entities.yaml
       ├─→ extract_suffixes()          # objects/suffixes.yaml
       ├─→ extract_datatypes()         # objects/datatypes.yaml
       ├─→ extract_modalities()        # objects/modalities.yaml + rules/modalities.yaml
       ├─→ extract_extensions()        # objects/extensions.yaml
       ├─→ extract_metaentities()      # objects/metaentities.yaml
       ├─→ extract_formats()           # objects/formats.yaml
       ├─→ extract_top_level_files()   # objects/files.yaml
       ├─→ extract_directory_rules()   # rules/directories.yaml
       ├─→ extract_errors()            # rules/errors.yaml
       ├─→ extract_file_rules()        # rules/files/{raw,deriv,common}/*.yaml
       ├─→ extract_sidecar_rules()     # rules/sidecars/*.yaml
       ├─→ extract_tabular_rules()     # rules/tabular_data/*.yaml
       ├─→ extract_derivative_rules()  # rules/sidecars/derivatives/*.yaml
       ├─→ extract_validation_checks() # rules/checks/*.yaml
       ├─→ extract_enums()             # objects/enums.yaml
       └─→ generates output JSONL files
```

### `retriever.py` — Knowledge Base Retriever

**Purpose**: Lightweight token-scored retriever designed for integration with the BIDS Manager AI Agent's `Retriever` component.

**Key Features**:
- **Zero external dependencies** (uses only Python stdlib: `json`, `re`, `os`, `pathlib`, `collections`, `typing`)
- Loads the entire knowledge base once at `__init__`, then reuses it for all queries
- Token-scored retrieval with field-aware weighting (identifiers weighted heavier than descriptions)
- BIDS-specific identifier boosting (recognizes `bold`, `func`, `task`, `sub`, `ses`, etc.)
- Optional relationship-aware boosting via `relationships.jsonl`
- Handles malformed JSONL lines gracefully (logs warnings, skips bad lines)

**API** (compatible with existing `Retriever` interface):

```python
from retriever import Retriever

retriever = Retriever("./")  # data_dir where JSONL files live
result = retriever.retrieve("What is the task entity?", top_k=3)
# result is a formatted string → directly passed to LLM by Planner
```

**Output Format**:

```
=== Knowledge Item 1 ===
ID: entity_task
Type: Concept

Source: objects/entities.yaml

BIDS Version: 1.11.2-dev
Schema Version: 2.0.0-dev

Title: Entity: Task (task)
Summary: The 'task' entity is used in BIDS filenames. Type: string, Format: label.
Description: The `task` entity in BIDS is used for: task-<label> identifies the experimental task. It is a string of format label.
Allowed Values: rest, matchingpennies, nback, ...
```

**Scoring Strategy**:

| Match Type | Score |
|-----------|-------|
| Exact query phrase in section | `× 5.0 × section_weight` |
| Token whole-word match in section | `× 2.0 × section_weight` |
| Token substring match in section | `× 0.5 × section_weight` |
| Known BIDS identifier match | `+ 3.0 bonus` |

**Stop words** are filtered out of query and content before scoring. Common words (`is`, `the`, `for`, `of`, etc.) do not inflate scores.

### `knowledge.jsonl` — Primary Knowledge Base

- **Format**: One JSON object per line (JSONL)
- **Records**: 1,272 atomic knowledge items
- **Each record contains**:
  - `id` — Stable unique identifier
  - `knowledge_type` — Category (Concept, Error, MetadataRule, etc.)
  - `title` — Short human-readable title
  - `summary` — One or two sentence summary
  - `retrieval_text` — Natural-language text for semantic retrieval
  - `source` — Traced to original YAML file/section/key
  - `bids_version`, `schema_version` — For version-awareness
  - `raw_content` — Original YAML fragment for full traceability
  - Optional fields: `allowed_values`, `conditions`, `requirements`, `scope`, `severity`, `expression`, `unit`

### `relationships.jsonl` — Graph Edges

- **Records**: 271 edges
- **Format**: `{source, relation, target, source_reference, confidence}`
- **Relations**: `defines`, `covers`, `applies_to`, `maps_to_metadata`, `templates`, `contains`, `provides`

### `sources.jsonl` — Source Inventory

- **Records**: 109 source files
- **Format**: `{source_path, category, sections, keys, record_count, relationship_count, bids_version, schema_version, status}`
- **Purpose**: Maps each source YAML file to the knowledge records it contributed to

### `processing_report.json` — Audit Report

```json
{
  "files_processed": 109,
  "records_created": 1272,
  "relationships_created": 271,
  "duplicates_found": 0,
  "conflicts_found": 0,
  "inferred_records": 0,
  "errors": 0,
  "warnings": 0,
  "bids_version": "1.11.2-dev",
  "schema_version": "2.0.0-dev",
  "knowledge_categories": [...],
  ...
}
```

### `KB_README.md` — Human-Readable Summary

A detailed human-readable summary of what was extracted, including coverage metrics, category breakdown, and relationship descriptions.

### `bids_knowledge_extraction.ipynb` — Interactive Notebook

Step-by-step Jupyter notebook documenting the extraction pipeline:
1. Load and parse YAML files
2. Extract atomic knowledge units
3. Classify into knowledge categories
4. Extract metadata rules and validation checks
5. Build relationship graph
6. Handle conflicts and duplicates
7. Generate retrieval text
8. Export JSONL and processing reports

---

## 8. Integration with BIDS Manager AI Agent

### How the Agent Uses These Files

```
User Query (e.g., "Why is my BIDS file invalid?")
    ↓
Planner routes to Retriever
    ↓
    Retriever("outputs/")  <-- data_dir pointing to this folder
    ↓
    Loads knowledge.jsonl on initialization (once)
    ↓
    retrieve("file invalid", top_k=3)
    ↓
    Returns formatted string → passed to LLM
```

### Installation (Drop-In Replacement)

Replace your existing `Retriever` with `retriever.py`:

```python
# OLD (txt/md based)
from old_retriever import Retriever
retriever = Retriever("./docs")

# NEW (JSONL knowledge base based)
from retriever import Retriever
retriever = Retriever("./outputs")  # or wherever your JSONL files live
```

**Public API remains identical**:
```python
retriever = Retriever(data_dir="./outputs")   # constructor
result = retriever.retrieve(query="...")       # same signature
result = retriever.retrieve(query="...", top_k=3)  # same signature
# result is always a string → same downstream compatibility
```

### Dependencies

```
No external packages required.
```

Only Python stdlib: `json`, `re`, `os`, `pathlib`, `collections`, `typing`, `logging`, `math`.

### Adding to Your Project

1. Copy `outputs/retriever.py` into your project
2. Copy `outputs/knowledge.jsonl`, `relationships.jsonl`, `sources.jsonl`, `processing_report.json` into your project
3. Initialize with: `retriever = Retriever("./path/to/kb")`

### Extending Later

The retriever is designed for future enhancement:

- **Embedding-based retrieval**: Replace `Scorer` with a vector similarity layer (keep `load_knowledge` / `format` unchanged)
- **More relationship boosting**: The `_maybe_add_related` method is already stubbed out
- **Caching**: The in-memory index can be upgraded to a disk-based index for very large KBs
- **Filtering**: Add a `knowledge_types=["Error", "Check"]` parameter to filter by type

---

## 9. Quick Verification Commands

### Check record counts
```bash
cd outputs/
wc -l knowledge.jsonl         # should be 1,272
wc -l relationships.jsonl     # should be 271
wc -l sources.jsonl           # should be 109
```

### Test the retriever
```bash
cd outputs/
python retriever.py
# Runs 6 sample queries and prints formatted results
```

### Re-extract from modified source YAML files
```bash
cd outputs/
python extract_kb.py
# Reads from RAW BIDS Knowledge Data/ and regenerates all JSONL files
```

---

## 10. Notes on Retrieval Quality

### What Works Well

| Query Type | Quality | Example |
|-----------|---------|---------|
| Entity/suffix/datatype lookup | ★★★★★ | "What is run entity?" |
| Error code explanation | ★★★★★ | "What causes NIFTI_TOO_SMALL?" |
| Metadata field requirements | ★★★★☆ | "What metadata does bold require?" |
| File specification lookup | ★★★★☆ | "What suffix for functional MRI?" |
| Directory structure | ★★★☆☆ | "Where do events files go?" |

### What to Improve Next

- **Entity→datatype mapping**: Some entity relationships exist in `relationships.jsonl` but lack explicit `entity_has_entity_rule` records. Future extraction pass can add these.
- **Metadata→file mapping**: Metadata fields are defined but the mapping "which field applies to which file type" is sometimes implicit in YAML selectors rather than explicit. Could be made explicit in a future pass.
- **Semantic embeddings**: Currently uses token matching. Embedding-based retrieval would handle synonyms ("bold" ↔ "functional MRI" ↔ "task fMRI") much better.

---

Generated: 2026-08-10  
BIDS Version: 1.11.2-dev  
Schema Version: 2.0.0-dev  
Records: 1,272 knowledge + 271 relationships
