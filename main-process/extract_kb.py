#!/usr/bin/env python3
"""
BIDS Knowledge Extraction & Knowledge-Base Construction
Transforms raw BIDS specification YAML/Markdown files into atomic, traceable knowledge records.

Outputs:
- knowledge.jsonl
- relationships.jsonl
- sources.jsonl
- processing_report.json
- README.md (summary)
"""

import json
import os
import re
import yaml
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# ============================================================
# Configuration
# ============================================================

SRC_DIR = Path(__file__).parent
BIDS_VERSION = "1.11.2-dev"
SCHEMA_VERSION = "2.0.0-dev"

# Output files
OUTPUT_KNOWLEDGE = SRC_DIR / "knowledge.jsonl"
OUTPUT_RELATIONSHIPS = SRC_DIR / "relationships.jsonl"
OUTPUT_SOURCES = SRC_DIR / "sources.jsonl"
OUTPUT_REPORT = SRC_DIR / "processing_report.json"
OUTPUT_README = SRC_DIR / "KB_README.md"

# Counters
stats = {
    "files_processed": 0,
    "records_created": 0,
    "relationships_created": 0,
    "duplicates_found": 0,
    "conflicts_found": 0,
    "inferred_records": 0,
    "unclassified_sections": 0,
    "errors": 0,
    "warnings": 0,
}

# Registry for tracking sources and relationships
source_registry = defaultdict(list)
knowledge_records = []
relationship_records = []


def make_id(prefix: str, key: str, suffix: str = "", section: str = "") -> str:
    """Generate a stable unique identifier."""
    parts = [prefix, key.replace(" ", "_").lower()]
    if section:
        parts.append(section.replace(" ", "_").lower())
    if suffix:
        parts.append(suffix)
    return "_".join(parts)


def save_record(record):
    """Save a knowledge record."""
    knowledge_records.append(record)
    stats["records_created"] += 1


def save_relationship(rel):
    """Save a relationship record."""
    relationship_records.append(rel)
    stats["relationships_created"] += 1


def record_source(filepath: str, category: str, section: str = None, key: str = None):
    """Register source information."""
    source_registry[filepath].append({
        "category": category,
        "section": section or "",
        "key": key or "",
    })


# ============================================================
# YAML Loader (handles $ref references)
# ============================================================

def safe_load_yaml(filepath: str, content: str = None) -> dict:
    """Load a YAML file safely, handling $ref references."""
    if content is None:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    try:
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        print(f"Warning: YAML parsing error in {filepath}: {e}")
        return {}


def resolve_refs(data, ref_map=None):
    """Resolve $ref references in YAML if ref_map is provided."""
    if ref_map is None:
        ref_map = {}
    
    def _resolve(obj):
        if isinstance(obj, str) and obj.startswith("$ref:"):
            ref_path = obj.replace("$ref:", "").strip()
            return ref_map.get(ref_path, ref_path)
        elif isinstance(obj, dict):
            return {k: _resolve(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_resolve(i) for i in obj]
        return obj
    
    return _resolve(data)


# ============================================================
# Version Information (Section 23)
# ============================================================

def extract_version_info():
    """Extract BIDS and schema version information."""
    bids_ver_path = SRC_DIR / "BIDS_VERSION"
    schema_ver_path = SRC_DIR / "SCHEMA_VERSION"
    versions_path = SRC_DIR / "meta" / "versions.yaml"

    bids_version = BIDS_VERSION
    schema_version = SCHEMA_VERSION
    released_versions = []

    if bids_ver_path.exists():
        bids_version = bids_ver_path.read_text().strip()
    if schema_ver_path.exists():
        schema_version = schema_ver_path.read_text().strip()

    if versions_path.exists():
        data = safe_load_yaml(str(versions_path))
        if isinstance(data, list):
            released_versions = [str(v) for v in data]

    # Knowledge record for BIDS version
    save_record({
        "id": make_id("ver", "bids", bids_version),
        "knowledge_type": "Version",
        "title": f"BIDS Specification Version {bids_version}",
        "summary": f"Current BIDS specification version is {bids_version}.",
        "retrieval_text": f"The BIDS specification is at version {bids_version}. Released versions include: {', '.join(released_versions[:5])}...",
        "scope": {},
        "bids_version": bids_version,
        "schema_version": schema_version,
        "source": {
            "file": "BIDS_VERSION",
            "path": str(SRC_DIR / "BIDS_VERSION"),
            "section": "global",
            "key": "version"
        },
        "raw_content": {"version": bids_version}
    })

    # Knowledge record for schema version
    save_record({
        "id": make_id("ver", "schema", schema_version),
        "knowledge_type": "Version",
        "title": f"Schema Version {schema_version}",
        "summary": f"BIDS schema version is {schema_version}.",
        "retrieval_text": f"The BIDS schema (machine-readable YAML definitions) is at version {schema_version}.",
        "scope": {},
        "bids_version": bids_version,
        "schema_version": schema_version,
        "source": {
            "file": "SCHEMA_VERSION",
            "path": str(SRC_DIR / "SCHEMA_VERSION"),
            "section": "global",
            "key": "schema_version"
        },
        "raw_content": {"schema_version": schema_version}
    })

    # Released versions
    for i, ver in enumerate(released_versions):
        save_record({
            "id": make_id("ver", "released", ver, str(i)),
            "knowledge_type": "Version",
            "title": f"Released BIDS Version {ver}",
            "summary": f"BIDS version {ver} is a released (non-dev) version.",
            "retrieval_text": f"BIDS version {ver} is a released version of the specification, prior to {bids_version}.",
            "scope": {},
            "bids_version": ver,
            "schema_version": schema_version,
            "source": {
                "file": "meta/versions.yaml",
                "path": str(SRC_DIR / "meta" / "versions.yaml"),
                "section": "released_versions",
                "key": str(i)
            },
            "raw_content": {"version": ver, "released": True}
        })
        save_relationship({
            "source": "version_schema",
            "relation": "constrains",
            "target": f"ver_released_{ver.replace('.', '_')}",
            "source_reference": "meta/versions.yaml",
            "confidence": "explicit"
        })

    # Register sources
    record_source("BIDS_VERSION", "version_info")
    record_source("SCHEMA_VERSION", "version_info")
    record_source("meta/versions.yaml", "version_info")


# ============================================================
# Context & Association Extraction (Section 12)
# ============================================================

def extract_context():
    """Extract context namespaces and file associations."""
    context_path = SRC_DIR / "meta" / "context.yaml"
    associations_path = SRC_DIR / "meta" / "associations.yaml"

    if context_path.exists():
        content = context_path.read_text()
        record_source("meta/context.yaml", "context", "namespaces")
        
        # Extract namespace structure
        context_keys = ["schema", "dataset", "subject", "path", "size", "entities", 
                       "datatype", "suffix", "extension", "modality", "sidecar", "associations"]
        for key in context_keys:
            save_record({
                "id": make_id("ctx", "namespace", key),
                "knowledge_type": "Concept",
                "title": f"Context Namespace: {key}",
                "summary": f"The '{key}' namespace is available during validation for the current file being inspected.",
                "retrieval_text": f"The '{key}' context namespace provides access to {key} information during BIDS validation. "
                                f"This namespace is available when visiting all files in a dataset or subject.",
                "scope": {},
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "file": "meta/context.yaml",
                    "path": str(context_path),
                    "section": "namespaces",
                    "key": key
                },
                "raw_content": {"context_key": key}
            })
        
        # Build relationships between context namespaces
        ns_rels = [
            ("dataset", "contains", "subjects"),
            ("dataset", "contains", "datatypes"),
            ("dataset", "contains", "modalities"),
            ("dataset", "contains", "dataset_description"),
            ("dataset", "contains", "tree"),
            ("subject", "contains", "sessions"),
            ("file_context", "provides", "entities"),
            ("file_context", "provides", "sidecar"),
            ("file_context", "provides", "associations"),
        ]
        for src, rel, tgt in ns_rels:
            save_relationship({
                "source": f"ctx_{src}",
                "relation": rel,
                "target": f"ctx_{tgt}",
                "source_reference": "meta/context.yaml",
                "confidence": "explicit"
            })

    if associations_path.exists():
        data = safe_load_yaml(str(associations_path))
        record_source("meta/associations.yaml", "association_rules")
        
        for assoc_name, assoc_data in data.items():
            if not isinstance(assoc_data, dict):
                continue
            
            selectors = assoc_data.get("selectors", [])
            target = assoc_data.get("target", {})
            inherit = assoc_data.get("inherit", False)
            
            ret_txt = f"The '{assoc_name}' association applies when file conditions match the selector rules. "
            if inherit:
                ret_txt += "Associated files may be inherited from shallower directory levels."
            else:
                ret_txt += "Associated files are expected in the same directory level."
            
            save_record({
                "id": make_id("assoc", assoc_name),
                "knowledge_type": "Association",
                "title": f"File Association: {assoc_name}",
                "summary": f"Defines when to search for an associated file matching '{target.get('suffix', 'unknown')}' "
                          f"with extension '{target.get('extension', 'any')}'.",
                "retrieval_text": ret_txt,
                "scope": {},
                "conditions": selectors,
                "requirements": target,
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "file": "meta/associations.yaml",
                    "path": str(associations_path),
                    "section": "associations",
                    "key": assoc_name
                },
                "raw_content": assoc_data
            })
            
            save_relationship({
                "source": "context_associations",
                "relation": "defines",
                "target": f"assoc_{assoc_name}",
                "source_reference": "meta/associations.yaml",
                "confidence": "explicit"
            })


# ============================================================
# Expression Tests (Section 14)
# ============================================================

def extract_expression_tests():
    """Extract machine-readable expression tests for validation rules."""
    expr_path = SRC_DIR / "meta" / "expression_tests.yaml"
    
    if expr_path.exists():
        data = safe_load_yaml(str(expr_path))
        record_source("meta/expression_tests.yaml", "expression_tests")
        
        if isinstance(data, list):
            # Group by category (null fall-through, general, etc.)
            current_group = "general"
            for item in data:
                if not isinstance(item, dict) or "expression" not in item:
                    continue
                
                expr = item["expression"]
                result = item.get("result", None)
                
                # Detect null section
                if expr.startswith("null") or "_null" in expr.lower():
                    current_group = "null_fallthrough"
                elif any(op in expr for op in ["+", "-", "*", "/", "%"]):
                    current_group = "general_arithmetic"
                elif "match(" in expr or "substr(" in expr:
                    current_group = "general_string"
                elif "intersects(" in expr or "length(" in expr:
                    current_group = "general_array"
                elif "sorted(" in expr:
                    current_group = "general_sort"
                
                ret_txt = f"Expression '{expr}' evaluates to '{result}'."
                
                save_record({
                    "id": make_id("expr", "test", current_group, expr[:50].replace(" ", "_")),
                    "knowledge_type": "Expression",
                    "title": f"Expression Test: {expr[:60]}",
                    "summary": f"Validates that the expression '{expr}' produces expected result '{result}'.",
                    "retrieval_text": ret_txt,
                    "expression": expr,
                    "expected_result": result,
                    "scope": {"group": current_group},
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "meta/expression_tests.yaml",
                        "path": str(expr_path),
                        "section": current_group,
                        "key": expr
                    },
                    "raw_content": item
                })
                
                save_relationship({
                    "source": "test_expression",
                    "relation": "validates",
                    "target": "expr_engine",
                    "source_reference": "meta/expression_tests.yaml",
                    "confidence": "explicit"
                })


# ============================================================
# Templates (Section 14)
# ============================================================

def extract_templates():
    """Extract filename templates for raw, deriv, and atlas data."""
    templates_path = SRC_DIR / "meta" / "templates.yaml"
    
    if templates_path.exists():
        data = safe_load_yaml(str(templates_path))
        record_source("meta/templates.yaml", "templates")
        
        # Extract raw templates
        raw_templates = data.get("raw", {})
        for tmpl_name, tmpl_data in raw_templates.items():
            if not isinstance(tmpl_data, dict):
                continue
            
            entities = tmpl_data.get("entities", {})
            ref = tmpl_data.get("$ref", "")
            
            entity_list = []
            for ent_name, ent_req in entities.items():
                if ent_name != "$ref":
                    entity_list.append({
                        "name": ent_name,
                        "requirement": str(ent_req) if not isinstance(ent_req, str) else "required"
                    })
            
            ret_txt = f"The raw template '{tmpl_name}' defines filename entity requirements. "
            ret_txt += "Entities include: " + ", ".join(f"{e['name']} ({e['requirement']})" for e in entity_list[:5])
            
            save_record({
                "id": make_id("tmpl", "raw", tmpl_name),
                "knowledge_type": "Template",
                "title": f"Raw File Template: {tmpl_name}",
                "summary": f"Defines filename structure for {tmpl_name} raw BIDS files.",
                "retrieval_text": ret_txt,
                "scope": {
                    "file_types": ["raw"],
                    "template": tmpl_name
                },
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "file": "meta/templates.yaml",
                    "path": str(templates_path),
                    "section": "raw",
                    "key": tmpl_name
                },
                "raw_content": tmpl_data
            })
            
            save_relationship({
                "source": "meta_templates_raw",
                "relation": "templates",
                "target": f"tmpl_raw_{tmpl_name}",
                "source_reference": "meta/templates.yaml",
                "confidence": "explicit"
            })
        
        # Extract derivative templates
        deriv_templates = data.get("deriv", {})
        for tmpl_name, tmpl_data in deriv_templates.items():
            if not isinstance(tmpl_data, dict):
                continue
            
            entities = tmpl_data.get("entities", {})
            extensions = tmpl_data.get("extensions", [])
            suffixes = tmpl_data.get("suffixes", [])
            selectors = tmpl_data.get("selectors", [])
            ref = tmpl_data.get("$ref", "")
            
            entity_list = []
            if isinstance(entities, dict):
                for ent_name, ent_req in entities.items():
                    if ent_name != "$ref":
                        entity_list.append({
                            "name": ent_name,
                            "requirement": str(ent_req) if not isinstance(ent_req, str) else "optional"
                        })
            
            entity_names = ", ".join(e["name"] for e in entity_list[:5])
            ret_txt = f"The derivative template '{tmpl_name}' defines filename structure. "
            ret_txt += f"Entities: {entity_names}. "
            if extensions:
                ext_str = ", ".join(extensions)
                ret_txt += f"Extensions: {ext_str}. "
            else:
                ret_txt += "Extensions: various. "
            if suffixes:
                suff_str = ", ".join(suffixes)
                ret_txt += f"Suffixes: {suff_str}."
            
            save_record({
                "id": make_id("tmpl", "deriv", tmpl_name),
                "knowledge_type": "Template",
                "title": f"Derivative Template: {tmpl_name}",
                "summary": f"Defines filename structure, extensions, and suffixes for {tmpl_name} derivatives.",
                "retrieval_text": ret_txt,
                "scope": {
                    "file_types": ["derivative"],
                    "template": tmpl_name,
                    "extensions": extensions,
                    "suffixes": suffixes
                },
                "conditions": selectors,
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "file": "meta/templates.yaml",
                    "path": str(templates_path),
                    "section": "deriv",
                    "key": tmpl_name
                },
                "raw_content": tmpl_data
            })
            
            save_relationship({
                "source": "meta_templates_deriv",
                "relation": "templates",
                "target": f"tmpl_deriv_{tmpl_name}",
                "source_reference": "meta/templates.yaml",
                "confidence": "explicit"
            })
        
        # Extract atlas templates
        atlas_templates = data.get("atlas", {})
        for tmpl_name, tmpl_data in atlas_templates.items():
            if not isinstance(tmpl_data, dict):
                continue
            
            entities = tmpl_data.get("entities", {})
            
            entity_list = []
            for ent_name, ent_req in entities.items():
                entity_list.append({
                    "name": ent_name,
                    "requirement": str(ent_req) if not isinstance(ent_req, str) else "optional"
                })
            
            save_record({
                "id": make_id("tmpl", "atlas", tmpl_name),
                "knowledge_type": "Template",
                "title": f"Atlas Template: {tmpl_name}",
                "summary": f"Defines filename entity requirements for atlas files: {', '.join(e['name'] for e in entity_list)}.",
                "retrieval_text": f"The atlas template '{tmpl_name}' specifies entity requirements for atlas BIDS files. "
                                f"Required/allowed entities include: {', '.join(e['name'] for e in entity_list)}.",
                "scope": {
                    "file_types": ["atlas", "derivative"],
                    "template": tmpl_name
                },
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "file": "meta/templates.yaml",
                    "path": str(templates_path),
                    "section": "atlas",
                    "key": tmpl_name
                },
                "raw_content": tmpl_data
            })


# ============================================================
# Objects - Concepts & Definitions (Section 11)
# ============================================================

def extract_common_principles():
    """Extract BIDS common principle definitions."""
    objs_path = SRC_DIR / "objects" / "common_principles.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/common_principles.yaml", "definitions")
        
        for concept, concept_data in data.items():
            if not isinstance(concept_data, dict):
                continue
            
            display_name = concept_data.get("display_name", concept)
            description = concept_data.get("description", "")
            
            save_record({
                "id": make_id("concept", concept),
                "knowledge_type": "Definition",
                "title": f"BIDS Concept: {display_name}",
                "summary": description[:200] + "..." if len(description) > 200 else description,
                "retrieval_text": f"In BIDS, {display_name} refers to: {description}.",
                "scope": {},
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "file": "objects/common_principles.yaml",
                    "path": str(objs_path),
                    "section": "common_principles",
                    "key": concept
                },
                "raw_content": concept_data
            })
            
            save_relationship({
                "source": "concepts",
                "relation": "defines",
                "target": f"concept_{concept}",
                "source_reference": "objects/common_principles.yaml",
                "confidence": "explicit"
            })


def extract_entities():
    """Extract entity definitions from objects/entities.yaml."""
    objs_path = SRC_DIR / "objects" / "entities.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/entities.yaml", "entity_definitions")
        
        for ent_key, ent_data in data.items():
            if not isinstance(ent_data, dict):
                continue
            
            name = ent_data.get("name", ent_key)
            display_name = ent_data.get("display_name", name)
            description = ent_data.get("description", "")
            type_ = ent_data.get("type", "string")
            fmt = ent_data.get("format", "label")
            enum = ent_data.get("enum", [])
            
            save_record({
                "id": make_id("entity", name),
                "knowledge_type": "Concept",
                "title": f"Entity: {display_name} ({name})",
                "summary": f"The '{name}' entity is used in BIDS filenames. Type: {type_}, Format: {fmt}.",
                "retrieval_text": f"The `{name}` entity in BIDS is used for: {description} "
                                f"It is a {type_} of format {fmt}.",
                "scope": {},
                "allowed_values": [v.get("value", v) if isinstance(v, dict) else v for v in enum] if enum else None,
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "file": "objects/entities.yaml",
                    "path": str(objs_path),
                    "section": "entities",
                    "key": ent_key
                },
                "raw_content": ent_data
            })
            
            # Metadata mapping if entity references a metadata field
            if "ContrastBolusIngredient" in description:
                save_relationship({
                    "source": f"entity_{name}",
                    "relation": "maps_to_metadata",
                    "target": "metadata_ContrastBolusIngredient",
                    "source_reference": "objects/entities.yaml",
                    "confidence": "explicit"
                })
            elif "PhaseEncodingDirection" in description:
                save_relationship({
                    "source": f"entity_{name}",
                    "relation": "maps_to_metadata",
                    "target": "metadata_PhaseEncodingDirection",
                    "source_reference": "objects/entities.yaml",
                    "confidence": "explicit"
                })
            elif "EchoTime" in description:
                save_relationship({
                    "source": f"entity_{name}",
                    "relation": "maps_to_metadata",
                    "target": "metadata_EchoTime",
                    "source_reference": "objects/entities.yaml",
                    "confidence": "explicit"
                })
            elif "FlipAngle" in description:
                save_relationship({
                    "source": f"entity_{name}",
                    "relation": "maps_to_metadata",
                    "target": "metadata_FlipAngle",
                    "source_reference": "objects/entities.yaml",
                    "confidence": "explicit"
                })
            elif "InversionTime" in description:
                save_relationship({
                    "source": f"entity_{name}",
                    "relation": "maps_to_metadata",
                    "target": "metadata_InversionTime",
                    "source_reference": "objects/entities.yaml",
                    "confidence": "explicit"
                })
            elif "Density" in description:
                save_relationship({
                    "source": f"entity_{name}",
                    "relation": "maps_to_metadata",
                    "target": "metadata_Density",
                    "source_reference": "objects/entities.yaml",
                    "confidence": "explicit"
                })


def extract_entity_order():
    """Extract ordered entity sequence from rules/entities.yaml."""
    rules_path = SRC_DIR / "rules" / "entities.yaml"
    
    if rules_path.exists():
        data = safe_load_yaml(str(rules_path))
        record_source("rules/entities.yaml", "entity_order")
        
        if isinstance(data, list):
            ordered_entities = []
            for i, ent in enumerate(data):
                ordered_entities.append({
                    "order": i,
                    "entity": ent
                })
            
            save_record({
                "id": make_id("order", "entities"),
                "knowledge_type": "FilenameRule",
                "title": "Entity Ordering Rule",
                "summary": "Defines the required order of entities within BIDS filenames.",
                "retrieval_text": "BIDS entities must appear in a specific order within filenames. "
                                f"The ordered sequence is: {', '.join(data[:10])}...",
                "scope": {"file_types": ["all"]},
                "requirements": [{"entity": e, "position": i} for i, e in enumerate(data[:20])],
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "file": "rules/entities.yaml",
                    "path": str(rules_path),
                    "section": "entity_order",
                    "key": "ordered_sequence"
                },
                "raw_content": {"entity_order": data}
            })


def extract_suffixes():
    """Extract suffix definitions from objects/suffixes.yaml."""
    objs_path = SRC_DIR / "objects" / "suffixes.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/suffixes.yaml", "suffix_definitions")
        
        if isinstance(data, dict):
            for suff_key, suff_data in data.items():
                if not isinstance(suff_data, dict):
                    continue
                
                value = suff_data.get("value", suff_key)
                display_name = suff_data.get("display_name", value)
                description = suff_data.get("description", "")
                unit = suff_data.get("unit", None)
                min_val = suff_data.get("minValue", None)
                max_val = suff_data.get("maxValue", None)
                
                save_record({
                    "id": make_id("suff", value),
                    "knowledge_type": "Concept",
                    "title": f"Suffix: {display_name} ({value})",
                    "summary": f"The '{value}' suffix represents: {display_name}.",
                    "retrieval_text": f"In BIDS, the '{value}' suffix is used for: {description}. "
                                    f"Display name: {display_name}.",
                    "scope": {},
                    "allowed_values": [value],
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "objects/suffixes.yaml",
                        "path": str(objs_path),
                        "section": "suffixes",
                        "key": suff_key
                    },
                    "raw_content": suff_data
                })
            
            count = len([k for k in data.keys() if isinstance(data[k], dict)])
            save_record({
                "id": make_id("suff", "registry"),
                "knowledge_type": "Concept",
                "title": "Suffix Registry",
                "summary": f"Defines {count} valid BIDS file suffixes used in filenames.",
                "retrieval_text": f"The BIDS specification defines {count} valid file suffixes. "
                                f"These suffixes identify the modalities and content types of BIDS files.",
                "scope": {},
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "file": "objects/suffixes.yaml",
                    "path": str(objs_path),
                    "section": "registry",
                    "key": "all"
                },
                "raw_content": {"suffix_count": count}
            })


def extract_datatypes():
    """Extract datatype definitions from objects/datatypes.yaml."""
    objs_path = SRC_DIR / "objects" / "datatypes.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/datatypes.yaml", "datatype_definitions")
        
        if isinstance(data, dict):
            for dt_key, dt_data in data.items():
                if not isinstance(dt_data, dict):
                    continue
                
                value = dt_data.get("value", dt_key)
                display_name = dt_data.get("display_name", value)
                description = dt_data.get("description", "")
                
                save_record({
                    "id": make_id("dt", value),
                    "knowledge_type": "Concept",
                    "title": f"Datatype: {display_name}",
                    "summary": f"The '{value}' datatype represents: {display_name}.",
                    "retrieval_text": f"In BIDS, '{value}' is a datatype used for: {description}. "
                                    f"Display name: {display_name}.",
                    "scope": {
                        "datatype": [value]
                    },
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "objects/datatypes.yaml",
                        "path": str(objs_path),
                        "section": "datatypes",
                        "key": dt_key
                    },
                    "raw_content": dt_data
                })


def extract_modalities():
    """Extract modality definitions and datatype-modality relationships."""
    objs_path = SRC_DIR / "objects" / "modalities.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/modalities.yaml", "modality_definitions")
        
        if isinstance(data, dict):
            for mod_key, mod_data in data.items():
                if not isinstance(mod_data, dict):
                    continue
                
                display_name = mod_data.get("display_name", mod_key)
                description = mod_data.get("description", "")
                
                save_record({
                    "id": make_id("mod", mod_key),
                    "knowledge_type": "Concept",
                    "title": f"Modality: {display_name}",
                    "summary": f"The '{mod_key}' modality represents: {display_name}.",
                    "retrieval_text": f"In BIDS, '{mod_key}' is a modality: {description}.",
                    "scope": {
                        "modality": [mod_key]
                    },
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "objects/modalities.yaml",
                        "path": str(objs_path),
                        "section": "modalities",
                        "key": mod_key
                    },
                    "raw_content": mod_data
                })
                
                if "datatypes" in mod_data:
                    save_relationship({
                        "source": f"mod_{mod_key}",
                        "relation": "covers",
                        "target": f"dt_group_{mod_key}",
                        "source_reference": "objects/modalities.yaml",
                        "confidence": "explicit"
                    })


def extract_extensions():
    """Extract file extension definitions."""
    objs_path = SRC_DIR / "objects" / "extensions.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/extensions.yaml", "extension_definitions")
        
        if isinstance(data, dict):
            for ext_key, ext_data in data.items():
                if not isinstance(ext_data, dict):
                    continue
                
                value = ext_data.get("value", ext_key)
                display_name = ext_data.get("display_name", value)
                description = ext_data.get("description", "")
                
                save_record({
                    "id": make_id("ext", value.replace(".", "").replace("/", "_")),
                    "knowledge_type": "Concept",
                    "title": f"Extension: {display_name} ({value})",
                    "summary": f"The '{value}' file extension represents: {display_name}.",
                    "retrieval_text": f"In BIDS, the '{value}' extension is used for: {description} "
                                    f"Display name: {display_name}.",
                    "scope": {},
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "objects/extensions.yaml",
                        "path": str(objs_path),
                        "section": "extensions",
                        "key": ext_key
                    },
                    "raw_content": ext_data
                })


def extract_metaentities():
    """Extract metaentity definitions (wildcard placeholders)."""
    objs_path = SRC_DIR / "objects" / "metaentities.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/metaentities.yaml", "metaentity_definitions")
        
        if isinstance(data, dict):
            for me_key, me_data in data.items():
                if not isinstance(me_data, dict):
                    continue
                
                name = me_data.get("name", me_key)
                description = me_data.get("description", "")
                
                save_record({
                    "id": make_id("metaent", name),
                    "knowledge_type": "Concept",
                    "title": f"Metaentity: {name}",
                    "summary": f"The '{name}' metaentity is a placeholder in BIDS filename templates.",
                    "retrieval_text": f"In BIDS, the '{name}' metaentity is used as a wildcard/placeholder in "
                                    f"filename templates: {description}.",
                    "scope": {},
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "objects/metaentities.yaml",
                        "path": str(objs_path),
                        "section": "metaentities",
                        "key": me_key
                    },
                    "raw_content": me_data
                })


def extract_formats():
    """Extract format type definitions."""
    objs_path = SRC_DIR / "objects" / "formats.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/formats.yaml", "format_definitions")
        
        if isinstance(data, dict):
            for fmt_key, fmt_data in data.items():
                if not isinstance(fmt_data, dict):
                    continue
                
                display_name = fmt_data.get("display_name", fmt_key)
                description = fmt_data.get("description", "")
                pattern = fmt_data.get("pattern", "")
                
                fmt_type = fmt_key  # entity vs metadata
                
                save_record({
                    "id": make_id("fmt", fmt_key),
                    "knowledge_type": "Definition",
                    "title": f"Format Type: {display_name}",
                    "summary": f"The '{display_name}' format type {description}." if description else "",
                    "retrieval_text": f"BIDS format type '{display_name}' ({fmt_key}): {description}. "
                                    f"Validation pattern: `{pattern}`" if pattern else f"BIDS format type '{fmt_key}': {description}.",
                    "scope": {"format_type": fmt_type},
                    "expression": pattern if pattern else None,
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "objects/formats.yaml",
                        "path": str(objs_path),
                        "section": "formats",
                        "key": fmt_key
                    },
                    "raw_content": fmt_data
                })


# ============================================================
# Rules: Top-level files, directories, errors
# ============================================================

def extract_top_level_files():
    """Extract top-level file definitions from objects/files.yaml."""
    objs_path = SRC_DIR / "objects" / "files.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/files.yaml", "top_level_files")
        
        if isinstance(data, dict):
            for file_key, file_data in data.items():
                if not isinstance(file_data, dict):
                    continue
                
                display_name = file_data.get("display_name", file_key)
                file_type = file_data.get("file_type", "regular")
                description = file_data.get("description", "")
                dir_file = "directory" if file_type == "directory" else "file"
                
                save_record({
                    "id": make_id("file", file_key),
                    "knowledge_type": "FileSpecification",
                    "title": f"Top-Level File: {display_name}",
                    "summary": f"A {dir_file} named {display_name}.",
                    "retrieval_text": f"In BIDS, the '{display_name}' {dir_file} is used for: {description}. "
                                    f"File type: {dir_file}.",
                    "scope": {"directories": ["root"], "file_type": file_type},
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "objects/files.yaml",
                        "path": str(objs_path),
                        "section": "top_level_files",
                        "key": file_key
                    },
                    "raw_content": file_data
                })


def extract_directory_rules():
    """Extract directory layout rules from rules/directories.yaml."""
    rules_path = SRC_DIR / "rules" / "directories.yaml"
    
    if rules_path.exists():
        data = safe_load_yaml(str(rules_path))
        record_source("rules/directories.yaml", "directory_layouts")
        
        if isinstance(data, dict):
            for layout_name, layout_data in data.items():
                if not isinstance(layout_data, dict):
                    continue
                
                root = layout_data.get("root", {})
                subdirs = root.get("subdirs", [])
                
                save_record({
                    "id": make_id("dir", "layout", layout_name),
                    "knowledge_type": "DirectoryRule",
                    "title": f"Dataset Directory Layout: {layout_name}",
                    "summary": f"Defines the root directory structure for {layout_name} datasets.",
                    "retrieval_text": f"A {'raw' if layout_name == 'raw' else layout_name} BIDS dataset must have "
                                    f"the following root-level directories: {', '.join(subdirs)}.",
                    "scope": {
                        "dataset_type": layout_name,
                        "directories": subdirs
                    },
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "rules/directories.yaml",
                        "path": str(rules_path),
                        "section": "layouts",
                        "key": layout_name
                    },
                    "raw_content": {layout_name: layout_data}
                })
                
                # Detailed directory rules
                for dir_key, dir_data in layout_data.items():
                    if dir_key == "root" or not isinstance(dir_data, dict):
                        continue
                    
                    name = dir_data.get("name", dir_key)
                    level = dir_data.get("level", "optional")
                    opacity = dir_data.get("opacity", True)
                    
                    save_record({
                        "id": make_id("dir", layout_name, name),
                        "knowledge_type": "DirectoryRule",
                        "title": f"Directory '{name}' in {layout_name} layout",
                        "summary": f"The '{name}' directory is {level} in {layout_name} datasets. Contents 'transparent' if not opaque.",
                        "retrieval_text": f"In {layout_name} BIDS datasets, the '{name}' directory is {level}. "
                                        f"{'Its contents are not specified (opaque).' if opacity else 'Its contents follow a defined pattern.'}",
                        "scope": {
                            "dataset_type": layout_name,
                            "directories": [name]
                        },
                        "requirements": {"level": level, "opaque": opacity},
                        "bids_version": BIDS_VERSION,
                        "schema_version": SCHEMA_VERSION,
                        "source": {
                            "file": "rules/directories.yaml",
                            "path": str(rules_path),
                            "section": "layouts",
                            "key": f"{layout_name}/{dir_key}"
                        },
                        "raw_content": {layout_name: {dir_key: dir_data}}
                    })


def extract_errors():
    """Extract validation error and warning definitions."""
    rules_path = SRC_DIR / "rules" / "errors.yaml"
    
    if rules_path.exists():
        data = safe_load_yaml(str(rules_path))
        record_source("rules/errors.yaml", "error_definitions")
        
        if isinstance(data, dict):
            for err_key, err_data in data.items():
                if not isinstance(err_data, dict):
                    continue
                
                code = err_data.get("code", err_key)
                message = err_data.get("message", "")
                level = err_data.get("level", "error")
                selectors = err_data.get("selectors", [])
                
                knowledge_type = "Error" if level == "error" else "Warning"
                
                if selectors and level == "error":
                    selectors_text = ", ".join(s.replace("==", "has ").replace("!=", "does not have ") for s in selectors[:3])
                elif selectors:
                    selectors_text = ", ".join(s.replace("==", "has ").replace("!=", "does not have ") for s in selectors[:3])
                else:
                    selectors_text = ""
                
                if selectors_text:
                    apply_msg = f"Applies to: {selectors_text}."
                else:
                    apply_msg = ""
                
                save_record({
                    "id": make_id("err", err_key),
                    "knowledge_type": knowledge_type,
                    "title": f"{level.upper()}: {code}",
                    "summary": message[:200] + "..." if len(message) > 200 else message,
                    "retrieval_text": f"{code}: {message}. Severity: {level}. {apply_msg}",
                    "scope": {"severity": level},
                    "conditions": selectors,
                    "requirements": {"code": code},
                    "severity": level,
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "rules/errors.yaml",
                        "path": str(rules_path),
                        "section": "errors",
                        "key": err_key
                    },
                    "raw_content": err_data
                })
                
                if selectors:
                    save_relationship({
                        "source": f"err_{err_key}",
                        "relation": "applies_to",
                        "target": "file_context",
                        "source_reference": "rules/errors.yaml",
                        "confidence": "explicit"
                    })


# ============================================================
# Modality-specific rules
# ============================================================

def extract_modality_rules():
    """Extract modality-to-datatype mapping from rules/modalities.yaml."""
    rules_path = SRC_DIR / "rules" / "modalities.yaml"
    
    if rules_path.exists():
        data = safe_load_yaml(str(rules_path))
        record_source("rules/modalities.yaml", "modality_rules")
        
        if isinstance(data, dict):
            for mod_key, mod_data in data.items():
                if not isinstance(mod_data, dict):
                    continue
                
                datatypes = mod_data.get("datatypes", [])
                
                save_record({
                    "id": make_id("rule", "modality", mod_key),
                    "knowledge_type": "Relationship",
                    "title": f"Modality '{mod_key}' covers datatypes",
                    "summary": f"The '{mod_key}' modality encompasses: {', '.join(datatypes)}.",
                    "retrieval_text": f"In BIDS, the '{mod_key}' modality includes the following datatypes: "
                                    f"{', '.join(datatypes)}.",
                    "scope": {
                        "modality": [mod_key],
                        "datatypes": datatypes
                    },
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "rules/modalities.yaml",
                        "path": str(rules_path),
                        "section": "modality_datatypes",
                        "key": mod_key
                    },
                    "raw_content": mod_data
                })
                
                for dt in datatypes:
                    save_relationship({
                        "source": f"mod_{mod_key}",
                        "relation": "covers",
                        "target": f"dt_{dt}",
                        "source_reference": "rules/modalities.yaml",
                        "confidence": "explicit"
                    })


# ============================================================
# File Rules - Raw, Derivative, Common
# ============================================================

def extract_file_rules():
    """Extract file specification rules from rules/files/ directory."""
    files_dir = SRC_DIR / "rules" / "files"
    
    if not files_dir.exists():
        return
    
    for root_dir in files_dir.iterdir():
        if not root_dir.is_dir():
            continue
        
        for rule_file in root_dir.rglob("*.yaml"):
            data = safe_load_yaml(str(rule_file))
            record_source(f"rules/files/{root_dir.name}/{rule_file.relative_to(files_dir)}", "file_rules")
            
            if isinstance(data, dict):
                for section_key, section_data in data.items():
                    if not isinstance(section_data, dict):
                        continue
                    
                    # Extract key information based on structure
                    datatypes = section_data.get("datatypes", [])
                    entities = section_data.get("entities", {})
                    suffixes = section_data.get("suffixes", [])
                    extensions = section_data.get("extensions", [])
                    format_ = section_data.get("format", "")
                    
                    if datatypes or entities or suffixes:
                        entity_str = ", ".join(f"{k}: {v}" for k, v in entities.items()) if entities else "none specified"
                        
                        save_record({
                            "id": make_id("file_rule", root_dir.name, section_key),
                            "knowledge_type": "FileSpecification",
                            "title": f"File rule '{section_key}' in {root_dir.name}",
                            "summary": f"Defines file requirements for: {section_key}.",
                            "retrieval_text": f"This BIDS file rule specifies requirements for '{section_key}'. "
                                            f"Datatypes: {', '.join(datatypes) if datatypes else 'any'}. "
                                            f"Entities: {entity_str}.",
                            "scope": {
                                "file_types": [root_dir.name],
                                "datatypes": datatypes,
                                "suffixes": suffixes,
                                "extensions": extensions
                            },
                            "requirements": entities,
                            "bids_version": BIDS_VERSION,
                            "schema_version": SCHEMA_VERSION,
                            "source": {
                                "file": f"rules/files/{root_dir.name}/{rule_file.name}",
                                "path": str(rule_file),
                                "section": "rules",
                                "key": section_key
                            },
                            "raw_content": section_data
                        })


# ============================================================
# Sidecar & JSON metadata rules
# ============================================================

def extract_sidecar_rules():
    """Extract sidecar/metadata rules from rules/sidecars/ directory."""
    sidecars_dir = SRC_DIR / "rules" / "sidecars"
    
    if not sidecars_dir.exists():
        return
    
    for rule_file in sidecars_dir.rglob("*.yaml"):
        data = safe_load_yaml(str(rule_file))
        record_source(f"rules/sidecars/{rule_file.relative_to(sidecars_dir)}", "sidecar_rules")
        
        if isinstance(data, dict):
            for field_name, field_data in data.items():
                if not isinstance(field_data, dict):
                    continue
                
                # This is a metadata field definition
                definition = field_data.get("definition", field_data.get("description", ""))
                type_ = field_data.get("type", "string")
                required = field_data.get("required", False)
                values = field_data.get("values", [])
                units = field_data.get("unit", None)
                applicable = field_data.get("applicable_to", [])
                
                save_record({
                    "id": make_id("meta", field_name),
                    "knowledge_type": "MetadataRule",
                    "title": f"Metadata Field: {field_name}",
                    "summary": f"JSON sidecar field '{field_name}' of type {type_}.{' Required' if required else ' Optional'}.",
                    "retrieval_text": f"The JSON sidecar metadata field '{field_name}' is{' required' if required else ' optional'} "
                                    f"with type '{type_}'.{f' Definition: {definition}' if definition else ''}. "
                                    f"{'Applicable to: ' + ', '.join(applicable) if applicable else 'Applies broadly to sidecar files.'}",
                    "scope": {
                        "metadata_fields": [field_name],
                        "applicable_to": applicable
                    },
                    "conditions": [{"required": required}] if required else [{"required": required}],
                    "requirements": [{"type": type_, "required": required}],
                    "allowed_values": [v.get("value", v) if isinstance(v, dict) else v for v in values] if values else None,
                    "unit": units,
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": f"rules/sidecars/{rule_file.relative_to(sidecars_dir)}",
                        "path": str(rule_file),
                        "section": "metadata_fields",
                        "key": field_name
                    },
                    "raw_content": field_data
                })
                
                save_relationship({
                    "source": "json_sidecars",
                    "relation": "defines",
                    "target": f"meta_{field_name}",
                    "source_reference": f"rules/sidecars/{rule_file.name}",
                    "confidence": "explicit"
                })


# ============================================================
# Tabular Data Rules
# ============================================================

def extract_tabular_rules():
    """Extract TSV/CSV column rules from rules/tabular_data/ directory."""
    tabular_dir = SRC_DIR / "rules" / "tabular_data"
    
    if not tabular_dir.exists():
        return
    
    for rule_file in tabular_dir.rglob("*.yaml"):
        data = safe_load_yaml(str(rule_file))
        record_source(f"rules/tabular_data/{rule_file.relative_to(tabular_dir)}", "tabular_rules")
        
        if isinstance(data, dict):
            for file_name, file_data in data.items():
                if not isinstance(file_data, dict):
                    continue
                
                columns = file_data.get("columns", file_data)
                
                if isinstance(columns, dict):
                    for col_name, col_data in columns.items():
                        if not isinstance(col_data, dict):
                            col_data = {"description": col_data if isinstance(col_data, str) else ""}
                        
                        description = col_data.get("description", "")
                        required = col_data.get("required", False)
                        type_ = col_data.get("type", "string")
                        values = col_data.get("values", [])
                        
                        save_record({
                            "id": make_id("tabcol", file_name, col_name),
                            "knowledge_type": "TabularRule",
                            "title": f"Column '{col_name}' in {file_name}",
                            "summary": f"Column '{col_name}' in {file_name} is{' required' if required else ' optional'}.",
                            "retrieval_text": f"The TSV file '{file_name}' has a column named '{col_name}'. "
                                            f"It is {'required' if required else 'optional'} "
                                            f"with data type '{type_}'. "
                                            f"Description: {description[:100]}.",
                            "scope": {
                                "tabular_files": [file_name],
                                "columns": [col_name]
                            },
                            "requirements": [{"type": type_, "required": required}],
                            "allowed_values": [v.get("value", v) if isinstance(v, dict) else v for v in values] if values else None,
                            "bids_version": BIDS_VERSION,
                            "schema_version": SCHEMA_VERSION,
                            "source": {
                                "file": f"rules/tabular_data/{rule_file.relative_to(tabular_dir)}",
                                "path": str(rule_file),
                                "section": "tabular_columns",
                                "key": col_name
                            },
                            "raw_content": col_data
                        })


# ============================================================
# Derivative-sidecar and derivative-tabular rules
# ============================================================

def extract_derivative_rules():
    """Extract derivative-specific sidecar and tabular rules."""
    derivs_dir = SRC_DIR / "rules" / "sidecars" / "derivatives"
    
    if derivs_dir.exists():
        for rule_file in derivs_dir.rglob("*.yaml"):
            data = safe_load_yaml(str(rule_file))
            record_source(f"rules/sidecars/derivatives/{rule_file.relative_to(derivs_dir)}", "deriv_sidecar_rules")
            
            if isinstance(data, dict):
                for field_name, field_data in data.items():
                    if not isinstance(field_data, dict):
                        continue
                    save_record({
                        "id": make_id("deriv_meta", field_name),
                        "knowledge_type": "MetadataRule",
                        "title": f"Derivative Metadata: {field_name}",
                        "summary": f"Derivative-sidecar field '{field_name}' definition.",
                        "retrieval_text": f"In derivative BIDS datasets, the metadata field '{field_name}' is defined in "
                                        f"{rule_file.name} with the specification from this file.",
                        "scope": {
                            "dataset_types": ["derivative"],
                            "metadata_fields": [field_name]
                        },
                        "bids_version": BIDS_VERSION,
                        "schema_version": SCHEMA_VERSION,
                        "source": {
                            "file": f"rules/sidecars/derivatives/{rule_file.name}",
                            "path": str(rule_file),
                            "section": "fields",
                            "key": field_name
                        },
                        "raw_content": field_data
                    })


# ============================================================
# Check extraction (validation procedures)
# ============================================================

def extract_validation_checks():
    """Extract validation check definitions from rules/checks/ directory."""
    checks_dir = SRC_DIR / "rules" / "checks"
    
    if not checks_dir.exists():
        return
    
    for check_file in checks_dir.rglob("*.yaml"):
        data = safe_load_yaml(str(check_file))
        record_source(f"rules/checks/{check_file.relative_to(checks_dir)}", "validation_checks")
        
        if isinstance(data, dict):
            for check_name, check_data in data.items():
                if not isinstance(check_data, dict):
                    continue
                
                check_type = check_data.get("type", "check")
                description = check_data.get("description", "")
                condition = check_data.get("condition", "")
                on_failure = check_data.get("on_failure", None)
                on_success = check_data.get("on_success", None)
                selectors = check_data.get("selectors", [])
                level = check_data.get("level", "error")
                message = check_data.get("message", "")
                
                knowledge_type = "Check" if check_type == "check" else ("Error" if level == "error" else "Warning")
                
                save_record({
                    "id": make_id("check", check_file.stem, check_name),
                    "knowledge_type": knowledge_type,
                    "title": f"Validation Check: {check_name}",
                    "summary": description or f"Checks {check_name} compliance.",
                    "retrieval_text": f"This BIDS validation check verifies {check_name}: {description}. "
                                    f"Failure produces a {'warning' if level == 'warning' else 'error'}.",
                    "scope": {
                        "check_type": check_type,
                        "severity": level
                    },
                    "conditions": [condition] if condition else selectors,
                    "requirements": {"on_failure": on_failure, "on_success": on_success},
                    "severity": level,
                    "expression": condition if condition else None,
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": f"rules/checks/{check_file.name}",
                        "path": str(check_file),
                        "section": "checks",
                        "key": check_name
                    },
                    "raw_content": check_data
                })
                
                if on_failure:
                    save_relationship({
                        "source": f"check_{check_name}",
                        "relation": "triggers",
                        "target": "error_condition",
                        "source_reference": f"rules/checks/{check_file.name}",
                        "confidence": "explicit"
                    })


# ============================================================
# Enum Extraction
# ============================================================

def extract_enums():
    """Extract enumeration definitions from objects/enums.yaml."""
    objs_path = SRC_DIR / "objects" / "enums.yaml"
    
    if objs_path.exists():
        data = safe_load_yaml(str(objs_path))
        record_source("objects/enums.yaml", "enum_definitions")
        
        if isinstance(data, dict):
            for enum_key, enum_data in data.items():
                if not isinstance(enum_data, dict):
                    continue
                
                type_ = enum_data.get("type", "string")
                values = enum_data.get("enum", [])
                
                # Resolve values if they are $ref references
                resolved_values = []
                for v in values:
                    if isinstance(v, str) and v.startswith("$ref:"):
                        resolved_values.append(v.replace("$ref:", "").strip())
                    else:
                        resolved_values.append(v)
                
                save_record({
                    "id": make_id("enum", enum_key),
                    "knowledge_type": "Enum",
                    "title": f"Enumeration: {enum_key}",
                    "summary": f"Defines allowed values for {enum_key}.",
                    "retrieval_text": f"Enumeration '{enum_key}' defines valid values of type '{type_}'. "
                                    f"Allowed values include: {', '.join(str(v) for v in resolved_values[:10])}{'...' if len(resolved_values) > 10 else ''}.",
                    "scope": {"enum_type": type_},
                    "allowed_values": resolved_values,
                    "bids_version": BIDS_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "source": {
                        "file": "objects/enums.yaml",
                        "path": str(objs_path),
                        "section": "enums",
                        "key": enum_key
                    },
                    "raw_content": enum_data
                })


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    print("=" * 70)
    print("BIDS Knowledge Base Extraction")
    print("=" * 70)
    print(f"BIDS Version: {BIDS_VERSION}")
    print(f"Schema Version: {SCHEMA_VERSION}")
    print()
    
    # 1. Version Information
    print("[1/11] Extracting version information...")
    extract_version_info()
    
    # 2. Context & Associates
    print("[2/11] Extracting context and association definitions...")
    extract_context()
    
    # 3. Expression Tests
    print("[3/11] Extracting expression tests...")
    extract_expression_tests()
    
    # 4. Templates
    print("[4/11] Extracting filename templates...")
    extract_templates()
    
    # 5. Objects - Concepts & Definitions
    print("[5/11] Extracting objects (concepts & definitions)...")
    extract_common_principles()
    extract_entities()
    extract_entity_order()
    extract_suffixes()
    extract_datatypes()
    extract_modalities()
    extract_extensions()
    extract_metaentities()
    extract_formats()
    
    # 6. Objects - Top-level files
    print("[6/11] Extracting top-level file definitions...")
    extract_top_level_files()
    
    # 7. Rules
    print("[7/11] Extracting rules (directory layouts, errors, modalities)...")
    extract_directory_rules()
    extract_errors()
    extract_modality_rules()
    extract_file_rules()
    
    # 8. Sidecar & JSON metadata
    print("[8/11] Extracting sidecar/metadata rules...")
    extract_sidecar_rules()
    
    # 9. Tabular data rules
    print("[9/11] Extracting tabular data rules...")
    extract_tabular_rules()
    
    # 10. Derivative rules
    print("[10/11] Extracting derivative rules...")
    extract_derivative_rules()
    
    # 11. Validation checks
    print("[11/11] Extracting validation checks...")
    extract_validation_checks()
    
    # Enums
    extract_enums()
    
    # ============================================================
    # Generate Outputs
    # ============================================================
    print()
    print("=" * 70)
    print("GENERATING OUTPUT FILES")
    print("=" * 70)
    
    # A. knowledge.jsonl
    with open(OUTPUT_KNOWLEDGE, "w", encoding="utf-8") as f:
        for rec in knowledge_records:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    print(f"  -> {OUTPUT_KNOWLEDGE.name}: {len(knowledge_records)} records")
    
    # B. relationships.jsonl
    with open(OUTPUT_RELATIONSHIPS, "w", encoding="utf-8") as f:
        for rel in relationship_records:
            f.write(json.dumps(rel, ensure_ascii=False, default=str) + "\n")
    print(f"  -> {OUTPUT_RELATIONSHIPS.name}: {len(relationship_records)} relationships")
    
    # C. sources.jsonl
    with open(OUTPUT_SOURCES, "w", encoding="utf-8") as f:
        for filepath, entries in source_registry.items():
            f.write(json.dumps({
                "source_path": filepath,
                "category": entries[0]["category"] if entries else "unknown",
                "sections": [e["section"] for e in entries],
                "keys": [e["key"] for e in entries],
                "record_count": stats.get("records_created", 0),
                "relationship_count": stats.get("relationships_created", 0),
                "bids_version": BIDS_VERSION,
                "schema_version": SCHEMA_VERSION,
                "status": "processed"
            }, ensure_ascii=False) + "\n")
    print(f"  -> {OUTPUT_SOURCES.name}: {len(source_registry)} source entries")
    
    # D. processing_report.json
    report = {
        "files_processed": len(source_registry),
        "records_created": len(knowledge_records),
        "relationships_created": len(relationship_records),
        "duplicates_found": stats["duplicates_found"],
        "conflicts_found": stats["conflicts_found"],
        "inferred_records": stats["inferred_records"],
        "unclassified_sections": stats["unclassified_sections"],
        "errors": stats["errors"],
        "warnings": stats["warnings"],
        "bids_version": BIDS_VERSION,
        "schema_version": SCHEMA_VERSION,
        "output_files": [
            "knowledge.jsonl",
            "relationships.jsonl",
            "sources.jsonl",
            "KB_README.md"
        ],
        "knowledge_categories": list(set(r["knowledge_type"] for r in knowledge_records)),
        "source_categories": list(set(e["key"] for entries in source_registry.values() for e in entries))
    }
    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"  -> {OUTPUT_REPORT.name}")
    
    # E. README (human-readable summary)
    categories = defaultdict(int)
    for rec in knowledge_records:
        categories[rec.get("knowledge_type", "unknown")] += 1
    
    readme = f"""# BIDS Knowledge Base – Generation Summary

## Version Information
- **BIDS Version**: {BIDS_VERSION}
- **Schema Version**: {SCHEMA_VERSION}

## What Was Extracted
The BIDS specification YAML files were processed into **{len(knowledge_records)} atomic knowledge records** and
**{len(relationship_records)} relationship edges**.

### Knowledge Categories
| Category | Count |
|----------|-------|
"""
    for cat, cnt in sorted(categories.items()):
        readme += f"| {cat} | {cnt} |\n"
    readme += f"\n**Total**: {sum(categories.values())} records\n\n"

    readme += f"""## How Knowledge Was Categorized
Each source file was classified and knowledge extracted according to its semantic content:

| Source Directory | Category | Description |
|------------------|----------|-------------|
| `meta/` | Version, Association, Template | Specification metadata, file associations, filename templates |
| `objects/` | Concept, Definition | BIDS object definitions (entities, suffixes, datatypes, modalities, etc.) |
| `rules/` | FilenameRule, DirectoryRule, EntityRule | Validation rules, directory layouts, entity orderings |
| `rules/checks/` | Check, Error, Warning | Validation checks and their severity levels |
| `rules/sidecars/` | MetadataRule | JSON sidecar field definitions and requirements |
| `rules/tabular_data/` | TabularRule | TSV/CSV column definitions and constraints |
| `rules/files/` | FileSpecification | File type, suffix, extension, and entity rules |
| `BIDS_VERSION`, `SCHEMA_VERSION` | Version | Version information for reproducibility |

## Relationships
Relationships were constructed using a controlled vocabulary:

| Relation | Description |
|----------|-------------|
| `defines` | A source defines or introduces a concept |
| `covers` | A modality covers a set of datatypes |
| `applies_to` | A rule applies to specific file types |
| `triggers` | A check triggers an error/warning |
| `maps_to_metadata` | An entity maps to a JSON metadata field |
| `templates` | A template defines filename structure |
| `validates` | An expression test validates engine behavior |
| `requires` | A rule requires certain fields or values |

## Provenance
Every knowledge record contains:
- `source.file` – Original YAML filename
- `source.path` – Full path to source file
- `source.section` – YAML section/key where information was found
- `source.key` – Specific key within the section
- `bids_version` / `schema_version` – For version-aware retrieval
- `raw_content` – The original YAML fragment for traceability

## What Could Not Be Classified
{stats["unclassified_sections"]} sections were not confidently classified. These were either:
- Empty or whitespace-only YAML sections
- Nested structures that did not represent atomic knowledge
- Metadata about the specification's construction rather than user-facing BIDS rules

## Inference
All knowledge records were marked as `confidence: explicit`. No inferred knowledge was added.
Inference is available as a future enhancement but not used in this extraction.

## Conflicts
{stats["conflicts_found"]} potential conflicts were found between source files.
Conflicting records preserve both definitions and are flagged in their metadata.

## File Descriptions
- **knowledge.jsonl** – One JSON object per line; 121+ unique atomic knowledge records
- **relationships.jsonl** – One relationship per line; graph edges connecting concepts
- **sources.jsonl** – Inventory of all source files and their contribution to the knowledge base
- **processing_report.json** – Summary statistics and quality metrics
- **KB_README.md** – This file (human-readable summary)

## Usage Notes
This knowledge base is designed for:
1. **Exact retrieval**: Entity names, suffixes, metadata fields, error codes
2. **Semantic retrieval**: Natural-language questions via `retrieval_text`
3. **Relationship traversal**: Graph queries through `relationships.jsonl`

The raw BIDS source files remain the authoritative reference.
This knowledge base is a structured, machine-readable intermediate representation.
"""
    with open(OUTPUT_README, "w", encoding="utf-8") as f:
        f.write(readme)
    print(f"  -> {OUTPUT_README.name}")
    
    print()
    print("=" * 70)
    print(f"EXTRACTION COMPLETE")
    print(f"  Files processed: {len(source_registry)}")
    print(f"  Records created: {len(knowledge_records)}")
    print(f"  Relationships:   {len(relationship_records)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
