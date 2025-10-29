"""
QA Rules Module
Defines the baseline rules for a perfect inspection report.
Single source of truth for all QA checks.
"""

from typing import Dict, List, Any

class QABaseline:
    """Defines the clean baseline for a perfect inspection report."""
    
    # Required sections in order
    REQUIRED_SECTIONS = [
        "sammendrag",
        "observasjoner",
        "årsak",
        "konsekvenser",
        "anbefalinger",
        "kostnadsestimat"
    ]
    
    # Forbidden words that indicate poor quality
    FORBIDDEN_WORDS = [
        "kanskje",
        "tror",
        "antagelig", 
        "sannsynligvis",
        "litt",
        "noe",
        "ganske",
        "veldig"
    ]
    
    # Professional tone indicators (should be present)
    TONE_INDICATORS = {
        "professional": [
            "observert",
            "konstatert",
            "anbefaler",
            "vurdert",
            "dokumentert"
        ],
        "technical": [
            "fuktmåling",
            "konstruksjon",
            "vedlikehold",
            "utbedring",
            "tilstand"
        ]
    }
    
    # Quantification requirements
    QUANTIFICATION_RULES = {
        "measurements": {
            "required_units": ["m²", "cm", "mm", "%", "kr"],
            "avoid_vague": ["litt", "noe", "mye", "stor", "liten"]
        },
        "time_frames": {
            "required": ["umiddelbart", "innen", "måneder", "år"],
            "avoid": ["snart", "etterhvert", "når som helst"]
        }
    }
    
    # Structure requirements
    STRUCTURE_RULES = {
        "min_section_length": 50,  # characters
        "max_section_length": 500,
        "total_min_length": 400,
        "total_max_length": 2000,
        "requires_bullet_points": ["anbefalinger", "observasjoner"],
        "requires_numbers": ["kostnadsestimat"]
    }
    
    # Logical consistency rules
    CONSISTENCY_RULES = {
        "severity_alignment": {
            "alvorlig": ["umiddelbart", "kritisk", "omfattende"],
            "moderat": ["innen 6 måneder", "betydelig", "anbefalt"],
            "mindre": ["ved neste vedlikehold", "begrenset", "vurderes"]
        },
        "cost_alignment": {
            "high": (50000, float('inf')),
            "medium": (10000, 50000),
            "low": (0, 10000)
        }
    }
    
    @classmethod
    def get_baseline(cls) -> Dict[str, Any]:
        """Returns the complete baseline as a dictionary."""
        return {
            "required_sections": cls.REQUIRED_SECTIONS,
            "forbidden_words": cls.FORBIDDEN_WORDS,
            "tone_indicators": cls.TONE_INDICATORS,
            "quantification_rules": cls.QUANTIFICATION_RULES,
            "structure_rules": cls.STRUCTURE_RULES,
            "consistency_rules": cls.CONSISTENCY_RULES
        }
    
    @classmethod
    def validate_section_order(cls, sections: List[str]) -> bool:
        """Validates if sections appear in the correct order."""
        report_sections = [s.lower() for s in sections]
        baseline_sections = [s.lower() for s in cls.REQUIRED_SECTIONS]
        
        # Check if all required sections are present
        for required in baseline_sections:
            if required not in report_sections:
                return False
        
        # Check order
        last_idx = -1
        for required in baseline_sections:
            try:
                current_idx = report_sections.index(required)
                if current_idx <= last_idx:
                    return False
                last_idx = current_idx
            except ValueError:
                return False
        
        return True
    
    @classmethod
    def check_forbidden_words(cls, text: str) -> List[Dict[str, str]]:
        """Returns list of forbidden words found in text."""
        issues = []
        text_lower = text.lower()
        
        for word in cls.FORBIDDEN_WORDS:
            if word in text_lower:
                # Find the position
                start = text_lower.find(word)
                issues.append({
                    "word": word,
                    "span": f"{start}:{start + len(word)}",
                    "context": text[max(0, start-20):min(len(text), start+len(word)+20)]
                })
        
        return issues
    
    @classmethod
    def check_quantification(cls, text: str) -> List[Dict[str, str]]:
        """Checks if measurements are properly quantified."""
        issues = []
        text_lower = text.lower()
        
        # Check for vague quantifiers
        for vague in cls.QUANTIFICATION_RULES["measurements"]["avoid_vague"]:
            if vague in text_lower:
                start = text_lower.find(vague)
                issues.append({
                    "type": "vague_quantifier",
                    "word": vague,
                    "span": f"{start}:{start + len(vague)}",
                    "suggestion": "Use specific measurements with units"
                })
        
        # Check if any measurement units are present
        has_units = any(unit in text for unit in cls.QUANTIFICATION_RULES["measurements"]["required_units"])
        if not has_units and len(text) > 200:  # Only flag if text is substantial
            issues.append({
                "type": "missing_units",
                "span": "0:0",
                "suggestion": "Add specific measurements with units (m², %, kr, etc.)"
            })
        
        return issues