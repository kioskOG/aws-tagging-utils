from typing import Dict, Iterable, Optional
from src.governance.models import ValidationResult, ValidationViolation
from src.governance.schema_provider import SchemaProvider
from src.governance.normalizer import TagNormalizer
from src.governance.policy import PolicyEvaluator


class TagGovernanceEngine:
    """
    Central Tag Governance Engine.
    Coordinates schema loading, normalization, and policy evaluation.
    """
    def __init__(
        self,
        schema_provider: SchemaProvider,
        unknown_tags_behavior: str = "warn",
        enable_normalization: bool = True
    ):
        self.schema_provider = schema_provider
        self.unknown_tags_behavior = unknown_tags_behavior
        self.enable_normalization = enable_normalization

        self.schema = self.schema_provider.get_schema()
        self.normalizer = TagNormalizer(self.schema)
        self.policy_evaluator = PolicyEvaluator(self.schema, self.unknown_tags_behavior)

    def evaluate(
        self,
        tags: Dict[str, str],
        resource_id: Optional[str] = None,
        extra_required: Optional[Iterable[str]] = None,
        partial: bool = False,
    ) -> ValidationResult:
        """
        Evaluate a tag set against the schema.

        extra_required: additional tag keys that must be present (e.g. MANDATORY_TAGS),
            on top of the schema's `required: true` keys.
        partial: the tags are a partial update (a tag write), not the resource's full
            tag set, so required-tag checks are skipped and only the provided keys
            are validated.
        """
        result = ValidationResult(resource=resource_id)

        # 1. Normalize tags if enabled
        if self.enable_normalization:
            normalized_tags, norm_violations = self.normalizer.normalize(tags)
            for v in norm_violations:
                result.add_violation(v)
        else:
            normalized_tags = dict(tags)

        result.normalized_tags = normalized_tags

        # 2. Evaluate policies against normalized tags
        policy_violations = self.policy_evaluator.evaluate(normalized_tags)
        warn_on_unknown = self.unknown_tags_behavior.lower() == "warn"
        for v in policy_violations:
            if partial and v.type == "MISSING_REQUIRED":
                continue
            # In "warn" mode unknown tags are reported but must not fail compliance
            if warn_on_unknown and v.type == "UNKNOWN_TAG":
                result.add_warning(v)
            else:
                result.add_violation(v)

        # 3. Extra mandatory keys not already enforced by the schema
        if extra_required and not partial:
            already_missing = {v.tag for v in result.violations if v.type == "MISSING_REQUIRED"}
            for key in extra_required:
                canonical = self.normalizer._get_canonical_key(key) if self.enable_normalization else key
                if canonical in already_missing:
                    continue
                val = normalized_tags.get(canonical)
                if val is None or str(val).strip() == "":
                    result.add_violation(ValidationViolation(
                        tag=canonical,
                        type="MISSING_REQUIRED",
                        expected="Value is mandatory",
                        actual="Missing or empty",
                    ))
                    already_missing.add(canonical)

        return result
