"""
Compilation pipeline  (Phase 2).

CompilePipeline ties together ShowBuilder → envelope computation → validation
into a single, configurable operation.  Each stage is independently togglable
so the same pipeline can be used for fast iteration (envelopes off) or
production (all stages on).

Usage:
    pipeline = CompilePipeline()                  # default config
    result   = pipeline.run(builder)
    if result.ok:
        to_json(result.show, "my_show.skyforge.json")
    else:
        print(result.validation)
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Optional

from compiler.deconflict import DeconflictConfig, deconflict as _deconflict
from compiler.envelope  import EnvelopeConfig,   compute_envelopes
from compiler.validator import ValidationConfig,  validate, ValidationResult
from compiler.show_builder import ShowBuilder
from core.show_format.schema import ShowFile


# ── Config & result value objects ─────────────────────────────────────────────

@dataclass
class CompileConfig:
    envelope:          EnvelopeConfig   = field(default_factory=EnvelopeConfig)
    deconflict_cfg:    DeconflictConfig = field(default_factory=DeconflictConfig)
    validation:        ValidationConfig = field(default_factory=ValidationConfig)
    compute_envelopes: bool = True    # False → keep placeholder radius_m=0 envelopes
    deconflict:        bool = True    # False → skip trajectory deconfliction
    validate:          bool = True    # False → skip validation entirely
    fail_on_error:     bool = True    # True  → raise RuntimeError when validation fails


@dataclass
class CompileResult:
    show:       ShowFile
    validation: Optional[ValidationResult] = None

    @property
    def ok(self) -> bool:
        return self.validation is None or self.validation.passed


# ── Pipeline ──────────────────────────────────────────────────────────────────

class CompilePipeline:
    """
    Stateless compilation pipeline.  Create once, call run() as many times as
    needed (e.g. to compile multiple shows with the same config).
    """

    def __init__(self, config: CompileConfig | None = None) -> None:
        self.config = config or CompileConfig()

    def run(self, builder: ShowBuilder) -> CompileResult:
        """
        Compile *builder* into a ShowFile, optionally computing envelopes and
        running validation.  Returns a CompileResult.

        Raises RuntimeError if validation fails and config.fail_on_error is True.
        """
        cfg = self.config

        # Stage 1 — compile waypoints → polynomial trajectories
        show: ShowFile = builder.compile()

        # Stage 1.5 — resolve nominal separation violations before envelope computation
        if cfg.deconflict:
            dc = _deconflict(show, cfg.deconflict_cfg)
            show = dc.show
            if dc.conflicts_found:
                status = "resolved" if dc.resolved else f"UNRESOLVED after {dc.iters_run} iters"
                print(
                    f"[pipeline] deconfliction: {dc.conflicts_found} conflict window(s), "
                    f"status={status}"
                )

        # Stage 2 — replace placeholder envelopes with computed ones

        if cfg.compute_envelopes:
            envelopes = compute_envelopes(show, cfg.envelope)
            show = dataclasses.replace(show, envelopes=envelopes)

        # Stage 3 — validate
        validation: Optional[ValidationResult] = None
        if cfg.validate:
            validation = validate(show, cfg.validation)
            if validation.passed:
                meta = dataclasses.replace(show.metadata, validation_status="validated")
                show = dataclasses.replace(show, metadata=meta)
            elif cfg.fail_on_error:
                raise RuntimeError(
                    f"Show validation failed:\n{validation}"
                )

        return CompileResult(show=show, validation=validation)
