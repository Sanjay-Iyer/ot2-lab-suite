# Independent Raman Science Audit

## Scope and evidence

This audit evaluates the first YAML-driven implementation for initial analysis of
the Raman band near 1080 cm^-1. I inspected the analysis configuration, shared
processing path, baseline and smoothing routines, target-peak characterization,
normalization, plot generation, focused tests, and the checked-in run under
`raman/results/20260728_130541_initial_1080_peak_test`.

The checked-in `Randomized_Scan_00649.csv` is documented as a software template,
not a representative experimental Raman spectrum. It is nevertheless a valuable
negative-control challenge because its target-region trace is dominated by
strong point-to-point alternation and has no visually resolved 1080 cm^-1 band.

## Executive assessment

arPLS is a defensible default baseline family, disabling smoothing by default is
appropriately conservative, and target-peak normalization is implemented using
the baseline-corrected target height rather than a raw or off-target maximum.
The diagnostic and provenance outputs are useful.

The present implementation is **not yet scientifically safe as the default
initial-analysis workflow**, because a high, unresolved noise/cosmic-ray-like
excursion can pass `peak_valid`, be reported as a Raman band, and become the
normalization denominator. The checked-in run demonstrates this failure:
1087 cm^-1 is reported as valid at SNR 3.44 even though FWHM is explicitly
omitted as under-resolved and the target plot has no coherent band shape.
Corrections should begin with the Critical and Major findings below.

## Findings

### Critical

#### C1. An unresolved noise-like excursion can be declared a valid Raman peak and used for normalization

**Evidence.** `characterize_target_peak()` selects the highest interior local
maximum in the search window and validates it using positive height, optional
prominence, and SNR. The default prominence threshold is `null`, and failure to
obtain a resolved width does not invalidate the peak. In the checked-in run,
the 1087 cm^-1 point is marked `peak_valid: true`, while the same record warns
that FWHM is under-resolved. The corresponding target plot is visually a sequence
of narrow alternating excursions rather than a resolved Raman band. The
workflow then scales the full spectrum by this point's height.

**Scientific impact.** A noise spike, detector artifact, or cosmic ray may be
presented as the target analyte band. All normalized individual and grouped
plots then acquire a false reference, which is the most serious possible error
for the stated workflow.

**Required correction.**

- Separate `candidate_found` from `peak_valid`.
- By default, require a resolved, physically plausible shape before allowing
  target normalization: both sides of the peak must be supported, the
  half-prominence crossings must remain inside the search window, and the width
  must span a configurable minimum number of samples and a configurable minimum
  width in cm^-1. A configurable maximum width is also appropriate.
- Treat missing/under-resolved width as **invalid for quantitative normalization**
  by default. It may remain a visibly marked candidate with a warning. Provide
  an explicit YAML override only for instruments whose resolution legitimately
  leaves the band under-sampled.
- Add configurable minimum prominence and/or prominence-to-noise criteria.
  Because the algorithm selects the maximum from many candidates in a window,
  SNR 3 alone is not an adequate default false-positive guard.
- Reject or specially flag impulse-like peaks using contiguous support,
  neighboring-point coherence, or a cosmic-ray/spike test. Do not silently
  smooth an impulse into a band.
- Capture the prominence bases and invalidate or warn when either base is
  clipped by the search-window boundary.
- Make the checked-in noise-like template an explicit negative-control test and
  assert that it is not normalized under default settings.

### Major

#### M1. The default arPLS result has no convergence or baseline-quality gate

**Evidence.** arPLS with lambda 100000 is a reasonable starting family, but the
implementation returns only the estimated baseline. It does not report
iterations used, convergence status, warnings from the sparse solve, residual
flatness, or edge behavior. The checked-in baseline diagnostic shows a smooth
lower envelope, but its corrected trace remains strongly positive and highly
structured over broad regions rather than approximately centered and flat away
from bands.

**Scientific impact.** A mathematically completed baseline fit can still be
unsuitable for the specimen, acquisition, or sampling grid. Normalization and
peak metrics may then reflect baseline bias rather than Raman signal.

**Required correction.**

- Return and save arPLS convergence diagnostics (iterations, final relative
  weight change, and solver/nonfinite status).
- Calculate baseline quality indicators in configurable peak-free sidebands:
  residual median/slope relative to noise, excessive negative curvature, and
  edge residuals. Flag failed checks; do not claim a flat corrected baseline.
- Add a target-region baseline diagnostic or zoom so baseline intrusion through
  the 1080 cm^-1 band is visible.
- Keep arPLS as a supported/default method, but document lambda as a provisional
  starting value rather than universally conservative.

#### M2. Baseline stiffness and smoothing span are sample-index dependent

**Evidence.** The second-difference arPLS/ALS penalty is constructed on sample
index and does not use Raman-shift spacing. Savitzky-Golay window length is also
specified only in points. The loader accepts any strictly increasing axis and
does not test for approximately uniform spacing.

**Scientific impact.** The same YAML can produce materially different physical
baseline curvature and smoothing over spectra collected at 0.5, 1, or 2 cm^-1
spacing, or over irregular grids. This weakens overlay comparability and can
suppress broad/weak peaks differently across files.

**Required correction.**

- Validate and report median spacing, spacing variation, and effective
  Savitzky-Golay span in cm^-1 for every spectrum.
- Require approximately uniform spacing for the current algorithms, or
  explicitly resample all spectra to one configured uniform grid before
  smoothing/baseline processing.
- Prefer YAML parameters expressed in physical units where practical, then
  derive point windows reproducibly.
- Add sensitivity tests across sampling densities, irregular axes, weak peaks,
  narrow peaks, and broad peaks.

#### M3. The reported local peak area is positively biased and may remain populated for an invalid peak

**Evidence.** Area is the trapezoidal integral of
`clip(corrected, 0, None)` over a fixed configured window. Negative noise is
discarded while positive noise is retained. Area is calculated before final
peak validation and can remain numeric when SNR/prominence validation fails.
In addition, the detected peak may lie in the 1060--1100 cm^-1 search window
but outside the default 1065--1095 cm^-1 integration window.

**Scientific impact.** Noise alone has a positive expected clipped area.
Neighboring bands can be included, the target can be truncated or omitted, and
an invalid candidate can still receive an apparently quantitative area.

**Required correction.**

- Return area as missing whenever the target peak is invalid.
- Require the detected peak and both integration boundaries to be suitable;
  warn and omit area if the peak falls outside the integration window.
- Integrate signed baseline-corrected intensity after a documented local
  continuum treatment, or use a validated fitted-band area. If a positive-only
  area is retained as an optional descriptive metric, name it explicitly and
  quantify its noise bias.
- Report actual integration bounds, point count, and whether the window was
  truncated or likely contains another peak.

#### M4. `local_noise` is generally a full-spectrum estimate, so SNR can be misleading

**Evidence.** The default derivative-MAD noise is computed across the complete
spectrum and saved as `local_noise`. Raman noise commonly varies with
fluorescence intensity, detector response, Raman shift, and acquisition
conditions. The optional region method uses an ordinary standard deviation
without detrending or robust outlier rejection.

**Scientific impact.** Full-spectrum noise may overstate or understate the noise
near 1080 cm^-1. A high-background region elsewhere can reject a real target;
a quiet region elsewhere can validate a noisy target. Peaks or baseline drift
inside a configured region can also inflate the estimate.

**Required correction.**

- Estimate target-local noise from configurable, peak-free sidebands bracketing
  the search window, with robust detrending and outlier rejection.
- Save sideband bounds, point counts, estimator, and warnings. Omit SNR when
  there are too few valid sideband points.
- Rename the current metric to `global_derivative_noise` if it remains
  available, and do not call it local.
- Define how smoothing affects SNR. Peak height and noise must refer to a
  consistent signal domain, or the deliberate conservative mismatch must be
  documented explicitly.

#### M5. The quantity labeled FWHM is a half-prominence width and lacks sufficient validity metadata

**Evidence.** SciPy `peak_widths(..., rel_height=0.5)` measures width at half
prominence relative to the prominence contour. That is not necessarily the
full width at half maximum relative to a zero/local baseline. The implementation
does not save evaluation height, prominence bases, uncertainty, or instrumental
resolution.

**Scientific impact.** On residual slope, overlapping peaks, or a clipped
search window, the reported value may not be the conventional Raman FWHM.

**Required correction.**

- Either name the metric `width_at_half_prominence_cm1`, or calculate true FWHM
  after a defensible local-baseline treatment or constrained peak fit.
- Save evaluation height, left/right crossing positions, base positions,
  samples across width, and resolution/sampling warnings.
- Do not report width when the band is unresolved, the bases/crossings touch the
  search boundary, or a neighboring peak makes the measurement ambiguous.
- Use a width-resolution guard in `peak_valid` as specified in C1.

#### M6. Plot autoscaling includes out-of-window data and can compress the target view

**Evidence.** The plotting functions draw the full arrays and then set the
x-axis range. Matplotlib's automatic y limits therefore reflect values outside
the displayed target window. In the checked-in target plot, the normalized
target is near 1, yet the y-axis extends above 2 because off-screen values
influence autoscaling.

**Scientific impact.** The main plot can make the 1080 cm^-1 region appear
flatter or smaller than it is, contrary to the principal visualization goal.
The same issue can affect overlays and obscure between-spectrum shape
differences.

**Required correction.**

- When `y_range` is null, calculate y limits only from finite points inside the
  configured visible x range, with robust/configurable padding.
- Apply the same visible-window rule to overlays and grouped plots.
- Add a plot-level regression test that an out-of-window extreme does not alter
  the target-panel y limits.

#### M7. Invalid candidates are still plotted with a positive "detected peak" convention

**Evidence.** A candidate that has a position but fails SNR or prominence
validation retains its detected index. The individual plot falls back to
unscaled corrected intensity and can still mark that position with a red
triangle labeled "detected peak"; the title does not state that normalization
failed.

**Scientific impact.** A user reviewing plots without the CSV/log may interpret
an invalid candidate as a confirmed band.

**Required correction.**

- Plot valid and invalid candidates with distinct symbols and labels
  (`validated target peak` versus `unvalidated candidate`).
- Put a concise status such as `target not validated; not normalized` on the
  individual plot when appropriate.
- Never use the success marker/legend wording for an invalid candidate.

#### M8. Configuration validation does not enforce scientifically coherent target and integration windows

**Evidence.** Each range is checked for ordering, but the expected position need
not lie inside the search window, and the integration window need not contain
the expected or detected peak. Minimum width in physical units, maximum
plausible shift, and adequate sideband coverage are not validated.

**Scientific impact.** A YAML typo can cause detection and normalization to use
an unrelated band while area is calculated elsewhere.

**Required correction.**

- Require the expected position to lie inside the search window.
- Require a fixed integration window to contain the expected position and,
  at runtime, the detected position; otherwise omit area with a warning.
- Add configurable maximum allowed shift from expected for peak validation.
- Validate that the spectrum covers the target plot, search, integration, and
  noise-sideband ranges with adequate sampling before processing.

#### M9. Tests assert metric existence more strongly than scientific correctness

**Evidence.** The synthetic baseline test uses one uniform 1 cm^-1 grid and one
well-separated Gaussian band. Metric tests largely assert positivity or
non-missing values. The no-target test raises SNR to 10 rather than demonstrating
that default settings reject a noise-only spectrum. There are no focused tests
for spikes, broad/weak/overlapping peaks, heteroscedastic noise, irregular
sampling, integration bias, boundary clipping, or visible-window plot scaling.

**Scientific impact.** The current suite passes while the checked-in noise-like
template is validated and normalized.

**Required correction.**

- Add negative controls containing noise only, a single-point spike/cosmic ray,
  residual slope, and a band outside the search window.
- Add randomized ensembles with acceptance/false-positive tolerances rather
  than one exact trace.
- Test peak-height recovery, position error, area bias, and width accuracy
  against known values over multiple widths, SNRs, backgrounds, and grid
  spacings.
- Assert that invalid peaks have missing area/width/normalized values and
  visibly invalid plot status.

### Minor

#### m1. Peak position precision is limited to the sampled x coordinate but is presented without a resolution qualifier

**Evidence.** The detected position is the x coordinate of the maximum sample;
no sub-sample estimate or position uncertainty is provided.

**Scientific impact.** Small reported shifts may reflect sampling phase rather
than chemistry.

**Correction.** Save Raman-shift spacing and report position precision
accordingly. Optionally provide a constrained local parabolic or peak-model
center only after shape validation, retaining the observed-bin position.

#### m2. Normalization and overlay labels do not fully state the comparison being made

**Evidence.** Overlay y labels say only `Normalized intensity`; they do not state
the normalization reference and target value. If normalization is set to
`none`, the overlay code can still label the y axis as normalized.

**Scientific impact.** Target normalization intentionally removes absolute
1080-band amplitude differences. A reader may mistakenly infer quantitative
intensity equivalence.

**Correction.** Use labels such as
`Intensity normalized to validated 1080 cm^-1 peak (target = 1)` and use
`Baseline-corrected intensity (a.u.)` for `none`. State in documentation and
plot captions that target-normalized overlays compare shape/position, not
absolute target-band intensity.

#### m3. Metric provenance should distinguish raw and smoothed signal domains

**Evidence.** With smoothing enabled, baseline and corrected height come from
the smoothed signal, while `raw_peak_intensity` comes from raw data and default
noise is evaluated from raw minus the smoothed-signal baseline.

**Scientific impact.** The metrics are not wrong by definition, but their signal
domains are easy to misinterpret.

**Correction.** Add explicit names/metadata such as
`corrected_smoothed_peak_height`, `raw_intensity_at_detected_bin`, and
`noise_signal_domain`, and record the effective smoothing span.

### Recommendations

#### R1. Preserve an unnormalized comparison alongside target-normalized overlays

Target normalization is correctly implemented and useful for comparing spectral
shape around 1080 cm^-1, but it removes the most obvious view of absolute band
strength. Consider an optional baseline-corrected, unnormalized overlay or a
companion peak-height/area plot. Do not enable vertical offsets by default.

#### R2. Add replicate-aware quality summaries

For plate rows/columns, provide group summaries of validation rate, peak
position, corrected height, SNR, and width with missing values retained. Do not
average invalid normalized traces into a scientific summary.

#### R3. Document acquisition prerequisites

State that quantitative comparison assumes compatible laser power, integration
time, accumulations, objective/focus, cosmic-ray treatment, and instrument
calibration. Capture these fields in metadata when known.

## Strengths and no-finding areas

- **Baseline algorithm choice:** arPLS is an established and reasonable Raman
  fluorescence-baseline method. No replacement is required solely because of
  method family; the needed improvements are quality control, grid handling,
  and validation of defaults.
- **Smoothing conservatism:** smoothing is disabled by default, which avoids
  silently broadening or suppressing weak/narrow bands. Savitzky-Golay with a
  short window is a reasonable optional method once the physical span is
  controlled.
- **Normalization formula:** target-peak normalization divides the complete
  baseline-corrected signal by the validated target peak height and preserves
  corrected and scaled columns. It does not normalize to the raw global
  maximum or an off-target peak.
- **Invalid normalized data:** when a target fails validation and
  `allow_invalid_target` is false, scaled values become missing rather than a
  fabricated normalized spectrum.
- **Overlay offset:** vertical offset defaults to zero, preserving direct
  target-normalized shape comparability.
- **Shared processing path:** individual, ordinary overlay, and grouped outputs
  use the same `SpectrumResult`; there is no evidence of different scientific
  preprocessing being applied during overlay plotting.
- **Units and axes:** Raman shift is explicitly identified as cm^-1, plots use
  the conventional increasing left-to-right axis, corrected intensity is
  labeled in arbitrary units, and normalized intensity is unitless.
- **Diagnostic/provenance outputs:** raw signal, baseline, corrected signal,
  processed columns, configuration snapshots, metrics, logs, and warnings are
  retained. These are appropriate foundations for traceable initial analysis.
- **No default artificial vertical shifting:** individual spectra are not
  independently shifted upward, and small negative corrected values remain
  visible.

## Correction priority

1. Implement C1 and make the checked-in noise-like spectrum fail target
   validation and normalization by default.
2. Correct area, local-noise/SNR, FWHM terminology/validity, and invalid-candidate
   plot status (M3--M5 and M7).
3. Add baseline convergence/quality and sampling-grid safeguards (M1--M2).
4. Fix target-window autoscaling and scientific config validation (M6 and M8).
5. Expand the test suite with realistic negative controls and randomized
   scientific acceptance criteria (M9).

