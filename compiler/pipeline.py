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
    # Phase-separated layered transitions (show_builder) + the landing fix reduce
    # dense shows to a SPARSE residual on which deconflict already converges, so
    # the heavy spline-verified layering loop is OFF by default (redundant + slower
    # on current shows). Turn it on for a denser show where deconflict diverges.
    verified_layering: bool = False
    deconflict:        bool = True    # primary residual resolver (converges on the sparse field)
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

        # Stage 1 — compile waypoints → polynomial trajectories. With verified
        # layering on (default), the convergent spline-verified planner is the
        # PRIMARY collision-avoidance mechanism (deconflict below becomes polish).
        if cfg.verified_layering:
            from compiler.verified_layering import plan as _verified_plan
            show, _vphi = _verified_plan(builder, cfg.validation.min_sep_m)
            if _vphi > 0:
                print(f"[pipeline] verified-layering: {_vphi} residual breach-sample(s) "
                      f"after layering — deconfliction/validation will arbitrate.")
        else:
            show = builder.compile()
        n = len(show.trajectories)

        # Stage 1.5 — resolve nominal separation violations before envelope computation
        deconflicted        = False
        deconflict_resolved = True
        dc_cfg              = cfg.deconflict_cfg
        if cfg.deconflict:
            # Adaptive iteration budget: larger fleets pack more crossings, so
            # give them more correction passes (capped to keep compile bounded).
            adaptive_iters = min(50, max(dc_cfg.max_iters, n))
            if adaptive_iters != dc_cfg.max_iters:
                dc_cfg = dataclasses.replace(dc_cfg, max_iters=adaptive_iters)
            dc = _deconflict(show, dc_cfg)
            show = dc.show
            deconflicted        = True
            deconflict_resolved = dc.resolved
            if dc.conflicts_found:
                status = "resolved" if dc.resolved else f"UNRESOLVED after {dc.iters_run} iters"
                print(
                    f"[pipeline] deconfliction: {dc.conflicts_found} conflict window(s), "
                    f"status={status}"
                )
            if not dc.resolved:
                # Known-unsafe: deconfliction couldn't clear all conflicts. Don't burn
                # O(n²·T·segments) computing envelopes / validating the now heavily-knotted
                # trajectories — fail fast. The runtime gate rejects it anyway (status
                # stays "unvalidated").
                print(
                    "[pipeline] WARNING: deconfliction did NOT resolve all conflicts — "
                    "show is UNSAFE; failing fast without envelope/validation."
                )
                meta = dataclasses.replace(
                    show.metadata,
                    compile_min_sep_m     = cfg.validation.min_sep_m,
                    compile_deconflict_hz = dc_cfg.sample_hz,
                    compile_validate_hz   = max(cfg.validation.sample_hz, dc_cfg.sample_hz),
                    deconflicted          = True,
                    deconflict_resolved   = False,
                )
                show = dataclasses.replace(show, metadata=meta)
                validation = ValidationResult(
                    passed=False,
                    errors=[
                        f"deconfliction did not resolve all separation conflicts after "
                        f"{dc.iters_run} iters — show is UNSAFE and was not validated"
                    ],
                )
                if cfg.fail_on_error:
                    raise RuntimeError(f"Show validation failed:\n{validation}")
                return CompileResult(show=show, validation=validation)

        # Stage 2 — replace placeholder envelopes with computed ones
        if cfg.compute_envelopes:
            envelopes = compute_envelopes(show, cfg.envelope)
            show = dataclasses.replace(show, envelopes=envelopes)

        # The validator must sample at least as finely as deconfliction, else it
        # can pass a show that still has sub-sample-interval conflicts.
        val_cfg = cfg.validation
        if cfg.deconflict and val_cfg.sample_hz < dc_cfg.sample_hz:
            print(
                f"[pipeline] WARNING: validate_hz ({val_cfg.sample_hz}) < deconflict_hz "
                f"({dc_cfg.sample_hz}); raising validate_hz to match."
            )
            val_cfg = dataclasses.replace(val_cfg, sample_hz=dc_cfg.sample_hz)

        # Stamp the compile-time safety contract into metadata so the runtime can
        # verify it is flying a show planned under compatible assumptions.
        meta = dataclasses.replace(
            show.metadata,
            compile_min_sep_m     = val_cfg.min_sep_m,
            compile_deconflict_hz = dc_cfg.sample_hz if cfg.deconflict else 0.0,
            compile_validate_hz   = val_cfg.sample_hz,
            deconflicted          = deconflicted,
            deconflict_resolved   = deconflict_resolved,
        )
        show = dataclasses.replace(show, metadata=meta)

        # Stage 3 — validate
        validation: Optional[ValidationResult] = None
        if cfg.validate:
            validation = validate(show, val_cfg)
            if validation.passed:
                meta = dataclasses.replace(show.metadata, validation_status="validated")
                show = dataclasses.replace(show, metadata=meta)
            elif cfg.fail_on_error:
                raise RuntimeError(
                    f"Show validation failed:\n{validation}"
                )

        return CompileResult(show=show, validation=validation)
