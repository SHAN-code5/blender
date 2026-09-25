"""Prompt templates and a transparent, provider-independent enhancer."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

from ..core.errors import ValidationError


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    name: str
    text: str
    description: str = ""


@dataclass(frozen=True)
class PromptEnhancement:
    original: str
    enhanced: str
    template_id: str
    generated_by: str = "local-template"


class PromptTemplates:
    """Small replaceable template registry; no network/LLM claim is made."""

    @staticmethod
    def defaults() -> Dict[str, PromptTemplate]:
        return {
            "realistic_prop": PromptTemplate("realistic_prop", "Realistic Prop", "Detailed realistic {prompt}, coherent proportions, physically based materials, production-ready asset, clean readable silhouette"),
            "stylized_game": PromptTemplate("stylized_game", "Stylized Game Asset", "Stylized game-ready {prompt}, clear readable silhouette, durable materials, balanced proportions, production asset"),
            "low_poly": PromptTemplate("low_poly", "Low Poly Asset", "Low-poly {prompt}, intentional faceted geometry, compact topology, readable silhouette, game-ready proportions"),
            "fantasy_environment": PromptTemplate("fantasy_environment", "Fantasy Environment", "Fantasy environment asset featuring {prompt}, coherent environmental design, production-ready materials, atmospheric detail"),
            "indian_village": PromptTemplate("indian_village", "Indian Village Asset", "Culturally grounded Indian village asset: {prompt}, believable local materials, coherent construction, respectful design, production-ready proportions"),
        }

    def get(self, template_id: str) -> PromptTemplate:
        try:
            return self.defaults()[template_id]
        except KeyError as exc:
            raise ValidationError(f"Unknown prompt template: {template_id}") from exc

    def render(self, template_id: str, prompt: str) -> str:
        text = str(prompt or "").strip()
        if not text:
            raise ValidationError("Prompt cannot be empty.")
        return self.get(template_id).text.format(prompt=text)

    def custom(self, template_id: str, name: str, text: str) -> PromptTemplate:
        key = str(template_id or "").strip()
        value = str(text or "").strip()
        if not key or not value:
            raise ValidationError("Custom prompt templates require an ID and text.")
        return PromptTemplate(key, str(name or key), value)


class PromptEnhancer:
    """Apply a user-selected local template without pretending to call an LLM."""

    def __init__(self, template_id: str = "realistic_prop", templates: Mapping[str, PromptTemplate] | None = None) -> None:
        self.template_id = template_id
        self.templates = dict(templates or PromptTemplates.defaults())

    def enhance(self, prompt: str) -> PromptEnhancement:
        original = str(prompt or "").strip()
        if not original:
            raise ValidationError("Prompt cannot be empty.")
        try:
            template = self.templates[self.template_id]
        except KeyError as exc:
            raise ValidationError(f"Unknown prompt template: {self.template_id}") from exc
        try:
            enhanced = template.text.format(prompt=original)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValidationError("Prompt template is invalid.") from exc
        return PromptEnhancement(original=original, enhanced=enhanced, template_id=template.id)


def effective_prompt(original: str, enhanced: str, use_enhanced: bool) -> str:
    """Choose the user-edited prompt without overwriting the original input."""
    if use_enhanced and str(enhanced or "").strip():
        return str(enhanced).strip()
    return str(original or "").strip()
