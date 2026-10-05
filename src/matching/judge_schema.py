"""Prompt système et JSON Schema de la réponse structurée du juge (grille v3)."""
from __future__ import annotations

from typing import Any

from src.config import PROJECT_ROOT
from src.constants import (
    CONTRACT_TYPES,
    HARD_CAP_RULES,
    STRUCTURE_TYPES,
)


def get_system_prompt() -> str:
    prompt_path = PROJECT_ROOT / "data" / "prompt_rerank.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "Tu es un évaluateur d'offres de stage."


def get_response_schema() -> dict[str, Any]:
    """Retourne le JSON Schema imposant la structure et les énumérations exactes v3."""
    return {
        "type": "OBJECT",
        "properties": {
            "information_level": {
                "type": "STRING",
                "enum": ["COMPLET", "PARTIEL", "INSUFFISANT"],
            },
            "contract_type": {
                "type": "STRING",
                "enum": list(CONTRACT_TYPES),
            },
            "contract_evidence": {"type": "STRING"},
            "duration_months": {"type": "INTEGER", "nullable": True},
            "is_cesure": {"type": "BOOLEAN"},
            "evidence": {
                "type": "OBJECT",
                "properties": {
                    "structure": {"type": "STRING"},
                    "technical_depth": {"type": "STRING"},
                    "target_alignment": {"type": "STRING"},
                    "learning_environment": {"type": "STRING"},
                    "logistics": {"type": "STRING"},
                    "rd_nature": {"type": "STRING"},
                },
            },
            "signals": {
                "type": "OBJECT",
                "properties": {
                    "encadrant_explicite": {
                        "type": "OBJECT",
                        "properties": {"present": {"type": "BOOLEAN"}, "evidence": {"type": "STRING"}},
                        "required": ["present", "evidence"],
                    },
                    "donnees_reelles_explicites": {
                        "type": "OBJECT",
                        "properties": {"present": {"type": "BOOLEAN"}, "evidence": {"type": "STRING"}},
                        "required": ["present", "evidence"],
                    },
                    "suite_explicite": {
                        "type": "OBJECT",
                        "properties": {"present": {"type": "BOOLEAN"}, "evidence": {"type": "STRING"}},
                        "required": ["present", "evidence"],
                    },
                    "donnees_benchmark_seulement": {
                        "type": "OBJECT",
                        "properties": {"present": {"type": "BOOLEAN"}, "evidence": {"type": "STRING"}},
                        "required": ["present", "evidence"],
                    },
                },
            },
            "reasoning": {"type": "STRING"},
            "structure_type": {
                "type": "STRING",
                "enum": list(STRUCTURE_TYPES),
            },
            "category_confidence": {
                "type": "STRING",
                "enum": ["HAUTE", "MOYENNE", "FAIBLE"],
            },
            "rd_nature": {"type": "BOOLEAN"},
            "hard_cap_triggered": {
                "type": "STRING",
                "enum": ["NONE"] + list(HARD_CAP_RULES.keys()),
            },
            "hard_cap_evidence": {"type": "STRING", "nullable": True},
            "sub_scores": {
                "type": "OBJECT",
                "nullable": True,
                "properties": {
                    "technical_depth": {"type": "INTEGER"},
                    "target_alignment": {"type": "INTEGER"},
                    "learning_environment": {"type": "INTEGER"},
                    "logistics": {"type": "INTEGER"},
                },
            },
            "flags": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "company_note": {
                "type": "OBJECT",
                "properties": {
                    "known": {"type": "BOOLEAN"},
                    "note": {"type": "STRING"},
                    "confidence": {"type": "STRING", "enum": ["HAUTE", "MOYENNE", "FAIBLE"]},
                },
            },
            "match_reasons": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "red_flags": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "tech_stack_detected": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "questions_entretien": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
        },
        "required": [
            "information_level",
            "contract_type",
            "structure_type",
            "category_confidence",
            "rd_nature",
            "hard_cap_triggered",
            "reasoning",
        ],
    }
