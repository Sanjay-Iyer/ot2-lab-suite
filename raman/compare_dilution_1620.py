"""Consolidate existing exp2/exp4 results; never alter source measurements.

Run with bundled Python. Outputs are flat CSVs plus a methods/results note.
"""
from pathlib import Path
import csv
import math
import re
import json

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results' / 'dilution_1620_comprehensive'

def read(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def num(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def yes(v):
    return str(v).lower() in ('true', 'yes')

def write(name, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with (OUT / name).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    assert len(read(OUT / name)) == len(rows)

def raw_index(subdir):
    result = {}
    for p in sorted((ROOT / 'raw' / subdir).rglob('*.csv')):
        m = re.search(r'Scan (\d+)', p.name)
        if m:
            result.setdefault(int(m[1]), []).append(p)
    return result

def metadata(paths):
    for p in paths:
        with p.open(encoding='utf-8-sig', newline='') as f:
            lines = list(csv.reader(f))
        if lines and lines[0][0] == 'Name':
            return {r[0]: r[1] for r in lines if len(r) == 2}
    return {}

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    e4 = ROOT / 'results' / 'exp4_stars'
    e2 = ROOT / 'results' / 'exp2_dilution' / 'report'
    screens = {int(r['scan_number']): r for r in read(e4 / 'scan_screen.csv')}
    keys = {int(r['scan_number']): r for r in read(e4 / 'scan_key.csv')}
    raws = {'exp2': raw_index('082726_dyedilutioncomparison'),
            'exp4': raw_index('083126_Raman_TimeSeries')}
    floor = {r['paper']: float(r['noise_floor_cps']) for r in read(e2 / 'combined/sers_gain_1620.csv')}
    measurements = []
    labels = {'stars': 'Stock nanostars + CV', 'stars5x': 'Nanostars:water 1:5 + CV',
              'bp': 'Bipyramids + CV', 'dyeonly': 'CV alone', '': 'Unassigned probe'}
    for exp, source in [('exp4', e4 / 'band_metrics.csv'),
                        ('exp2', e2 / 'offwhite/per_spectrum_bands.csv'),
                        ('exp2', e2 / 'white/per_spectrum_bands.csv')]:
        for r in read(source):
            if r['peak_name'] != 'cv_1620':
                continue
            scan = int(r['scan_number'])
            s = screens.get(scan, {}) if exp == 'exp4' else {}
            cond = r.get('condition', r.get('series', ''))
            if cond == 'np':
                cond = 'bp'
            factor = num(r['factor'])
            paths = raws[exp].get(scan, [])
            assert paths, (exp, scan, 'missing raw files')
            meta = metadata(paths)
            peak = float(r['height_cps'])
            snr = float(r['snr'])
            flags = []
            if exp == 'exp2':
                flags.append('legacy_peak_method_no_exp4_saturation_screen')
            if s.get('verdict') in ('warn', 'fail'):
                flags.append('band_QC_' + s['verdict'])
            if yes(s.get('band_above_fit_ceiling')):
                flags.append('above_fit_ceiling_possible_nonlinearity')
            if snr < 3:
                flags.append('SNR_below_3')
            inlit = yes(r.get('in_lit_window', r.get('in_window')))
            if not inlit:
                flags.append('outside_literature_window')
            atfloor = exp == 'exp2' and cond == 'dyeonly' and peak <= floor[r['paper']]
            if atfloor:
                flags.append('control_at_legacy_noise_floor')
            if not cond:
                flags.append('condition_and_dilution_unassigned')
            measurements.append(dict(
                record_id=f'{exp}_{scan}', experiment=exp, scan_number=scan,
                acquisition_date=meta.get('CreatedDate', s.get('created', '')),
                paper=r['paper'], panel=r.get('key', 'exp2-' + r['paper']),
                condition=cond, sample=labels[cond], cv_dilution_label=r['dilution'],
                cv_dilution_factor=factor, cv_fraction_of_common_stock=1/factor if factor else None,
                nanoparticle_stock_fraction_before_CV=(1 if cond == 'stars' else 1/6 if cond == 'stars5x' else 0 if cond == 'dyeonly' else None),
                nanoparticle_preparation=('40 uL stars + 200 uL water; source code stars5x' if cond == 'stars5x' else '5 uL nanoparticles + CV; stock strength unspecified' if cond == 'bp' else labels[cond]),
                peak_nominal_cm1=1620, peak_center_cm1=num(r.get('center_cm1', r.get('observed_cm1'))),
                peak_height_cps=peak, exposure_s=num(r.get('int_time_s_x', r.get('int_time_s'))),
                peak_height_counts=peak*float(r.get('int_time_s_x', r.get('int_time_s'))),
                snr=snr, source_detected=yes(r['detected']), in_literature_window=inlit,
                band_quality=s.get('verdict', 'not_screened'),
                whole_scan_quality=s.get('verdict_scan', 'not_screened'),
                above_fit_ceiling=yes(s.get('band_above_fit_ceiling')) if exp == 'exp4' else None,
                control_at_legacy_noise_floor=atfloor,
                peak_method='local_shoulders_60cm1' if exp == 'exp4' else 'legacy_arPLS_peak',
                wavelength_nm=meta.get('Wavelength', ''), laser_power=meta.get('LaserPower', ''),
                averages=meta.get('Averages', ''), observed_spots_per_cell=1,
                concentration_basis='same CV stock and deposited volume confirmed by user',
                flags='; '.join(flags), source_csv=str(source.relative_to(ROOT)),
                source_row_scan=scan, raw_files=' | '.join(str(p.relative_to(ROOT)) for p in paths)))
    assert len(measurements) == 93
    assert len({r['record_id'] for r in measurements}) == 93
    # Corrected white assignment is authoritative in the existing plate map.
    assert all(r['condition'] == 'dyeonly' for r in measurements if 763 <= r['scan_number'] <= 770)
    assert all(r['condition'] == 'bp' for r in measurements if 771 <= r['scan_number'] <= 778)
    missing = []
    for scan in range(823, 828):
        k = keys[scan]
        missing.append(dict(record_id=f'exp4_{scan}', experiment='exp4', scan_number=scan,
                            paper='white', panel='w-10', condition='stars', sample=labels['stars'],
                            cv_dilution_label=k['cv_dilution'], cv_dilution_factor=float(k['cv_dilution'][:-1]),
                            flags='missing_raw_scan; not_measured_in_available_dump',
                            source_csv='configs/stars_exp4.yaml', observed_spots_per_cell=0))
    write('all_results_1620.csv', sorted(measurements + missing, key=lambda r:r['scan_number']))
    controls = [r for r in measurements if r['condition'] == 'dyeonly']
    pairs = []
    for t in measurements:
        if t['condition'] not in ('stars', 'stars5x', 'bp'):
            continue
        for c in controls:
            # Do not compare different papers, dates or peak methods.
            if (t['experiment'], t['paper']) != (c['experiment'], c['paper']):
                continue
            ratio = t['peak_height_cps']/c['peak_height_cps'] if c['peak_height_cps'] > 0 else None
            correction = t['cv_dilution_factor']/c['cv_dilution_factor']
            adjusted = ratio*correction if ratio is not None else None
            same_panel = t['panel'] == c['panel']
            reasons = []
            if not same_panel:
                reasons.append('different_control_panel_or_print')
            if t['experiment'] != 'exp4':
                reasons.append('legacy_method_not_saturation_screened')
            if any(r['band_quality'] == 'fail' for r in (t,c)):
                reasons.append('failed_band_QC')
            if any(r['above_fit_ceiling'] for r in (t,c)):
                reasons.append('possible_detector_nonlinearity')
            if any(r['snr'] < 3 or r['peak_height_cps'] <= 0 for r in (t,c)):
                reasons.append('low_or_nonpositive_signal')
            if c['control_at_legacy_noise_floor']:
                reasons.append('CV_at_noise_floor')
            pairs.append(dict(
                test_record_id=t['record_id'], control_record_id=c['record_id'],
                experiment=t['experiment'], paper=t['paper'], sample=t['sample'],
                test_panel=t['panel'], control_panel=c['panel'],
                nanoparticle_preparation=t['nanoparticle_preparation'],
                test_cv_dilution=t['cv_dilution_label'], control_cv_dilution=c['cv_dilution_label'],
                test_cv_dilution_factor=t['cv_dilution_factor'], control_cv_dilution_factor=c['cv_dilution_factor'],
                test_scan=t['scan_number'], control_scan=c['scan_number'],
                test_height_cps=t['peak_height_cps'], control_height_cps=c['peak_height_cps'],
                raw_signal_ratio_x=ratio, concentration_correction_x=correction,
                concentration_adjusted_boost_x=adjusted,
                test_exposure_s=t['exposure_s'], control_exposure_s=c['exposure_s'],
                test_snr=t['snr'], control_snr=c['snr'],
                same_panel=same_panel, same_cv_dilution=t['cv_dilution_factor']==c['cv_dilution_factor'],
                screened_rank_eligible=not reasons, exclusion_reasons='; '.join(reasons),
                test_flags=t['flags'], control_flags=c['flags'],
                peak_method=t['peak_method'], concentration_basis=t['concentration_basis']))
    pairs.sort(key=lambda r: r['concentration_adjusted_boost_x'] if r['concentration_adjusted_boost_x'] is not None else -math.inf, reverse=True)
    for i,r in enumerate(pairs,1):
        r['numerical_rank_all_same_paper_pairs'] = i
    write('all_comparisons_1620.csv', pairs)
    screened = [r for r in pairs if r['screened_rank_eligible']]
    top = [dict(rank=i, **r) for i,r in enumerate(screened[:10],1)]
    # A numerical top 10 for the complete dataset, using each panel's own controls.
    provisional = [dict(rank=i, **r) for i,r in enumerate([p for p in pairs if p['same_panel']][:10],1)]
    matched = [dict(rank=i, **r) for i,r in enumerate(sorted([p for p in pairs if p['same_panel'] and p['same_cv_dilution']], key=lambda p:p['raw_signal_ratio_x'], reverse=True),1)]
    write('top10_screened_adjusted_boost.csv', top)
    write('top10_numerical_adjusted_boost_with_flags.csv', provisional)
    write('same_dilution_comparisons_1620.csv', matched)
    assert len(top) == 10
    for p in pairs:
        expected=(p['test_height_cps']*p['test_cv_dilution_factor'])/(p['control_height_cps']*p['control_cv_dilution_factor'])
        assert math.isclose(p['concentration_adjusted_boost_x'], expected, rel_tol=1e-12)
    # Same-dilution gains should reproduce the canonical exp4 gain output.
    existing={(int(r['test_scan']),int(r['control_scan'])):float(r['gain_x']) for r in read(e4/'sers_gain.csv') if r['peak_name']=='cv_1620'}
    checked=0
    for p in matched:
        if (p['test_scan'],p['control_scan']) in existing:
            assert math.isclose(p['raw_signal_ratio_x'],existing[p['test_scan'],p['control_scan']],rel_tol=1e-9)
            checked+=1
    lines=['# Comprehensive 1620 cm-1 dilution comparison', '',
           'Scope: exp2 (2026-08-27) original bipyramid print and CV controls, plus exp4 (2026-08-31) nanostars, diluted nanostars, CV controls and bipyramid reruns. Existing canonical peak results were consolidated; spectra were not re-fitted.', '',
           f'Inventory: 93 measured scans (32 exp2, 61 exp4), including one unassigned probe; five missing exp4 scans retained as placeholders. {len(pairs)} same-experiment/same-paper candidate comparisons; {len(screened)} eligible for screened ranking. Two exports of a scan are metadata plus spectrum, not independent replicates.', '',
           '## Calculation', '',
           'Adjusted boost = (test peak cps / CV-only peak cps) * (test CV dilution factor / control CV dilution factor). User confirmed identical CV stock and CV volume deposited for treatments and controls. Absolute stock concentration is unspecified; absolute molarity is therefore not invented. Example: 2x measured signal at CV 20x vs control CV 2x gives 20x adjusted boost.', '',
           'The CV dilution ladder is distinct from nanoparticle preparation. stars5x is the historical condition code for 40 uL stars + 200 uL water: 1:5 stars:water, or 1/6 of stock before adding CV. No additional factor of five or six is applied to CV-normalized gain. Bipyramid stock concentration is unspecified.', '',
           '## Ranking rules and limits', '',
           '- Primary top 10: each panel uses its own CV controls, same paper and exposure; exp4 band QC must not fail, both SNR >= 3 and heights positive, and neither scan above the fit ceiling. QC warnings and peak-center flags remain visible. This is a screened point-estimate ranking, not proof of enhancement or statistical significance.',
           '- Numerical top 10 includes legacy exp2 estimates and flagged measurements, but still uses each panel\'s own controls. Inspect flags before using these values. All comparisons also retain cross-panel candidates, explicitly excluded from the screened ranking; controls from different printed panels vary materially.',
           '- Exp2 peak heights use its legacy arPLS analysis; exp4 heights use each spectrum\'s local shoulder baseline. Ratios only pair the same experiment/method. The combined ranking is descriptive; reprocessing exp2 with the same method and QC would be needed for a method-harmonized winner across experiments.',
           '- Exposure is normalized by using cps. Exp2 white AutoInt varied; those results lack the exp4 saturation screen. The authoritative white scan assignment is CV-only 763-770 and bipyramids 771-778. Exp4 bipyramid rerun CV dilutions are corrected to 2x, 5x, 10x.',
           '- One measured spot per cell; instrument averages and reruns are not independent sample replicates. No fabricated error bars. Missing white stock-star scans 823-827 cannot be reconstructed.',
           '- Concentration-normalized ratios across different dilutions are apparent signal per nominal CV amount, not a molecular SERS enhancement factor. CV-only signal is not proportional to concentration throughout these data. A high dilution-adjusted score is not the same as a large measured intensity gain. The same-dilution CSV provides the direct paired comparison.', '',
           '## Top 10 screened concentration-adjusted comparisons', '',
           '| Rank | Sample | Paper / panel | CV in NP sample | CV-only dilution | Measured ratio | Adjusted boost | Scans NP / CV |',
           '|---|---|---|---|---|---:|---:|---|']
    for p in top:
        lines.append(f"| {p['rank']} | {p['sample']} | {p['paper']} / {p['test_panel']} | {p['test_cv_dilution']} | {p['control_cv_dilution']} | {p['raw_signal_ratio_x']:.3f}x | {p['concentration_adjusted_boost_x']:.3f}x | {p['test_scan']} / {p['control_scan']} |")
    lines += ['', '## Files', '',
              '- all_results_1620.csv: 98 rows; source paths, metadata, CV ladder, nanoparticle preparation, peak heights, detection and QC.',
              '- all_comparisons_1620.csv: all candidate pairs with raw ratio, concentration factor, adjusted boost, eligibility and reasons.',
              '- top10_screened_adjusted_boost.csv: recommended screened point estimates.',
              '- top10_numerical_adjusted_boost_with_flags.csv: highest numerical own-panel estimates across both experiments.',
              '- same_dilution_comparisons_1620.csv: direct own-panel comparisons across all measured matching CV dilutions.', '',
              f'Validation: unique scan IDs and complete source coverage checked; every adjusted ratio independently recomputed algebraically; {checked} same-dilution exp4 ratios agree with existing sers_gain.csv within 1e-9 relative tolerance; all saved CSV row counts verified.', '',
              'Source definitions: configs/stars_exp4.yaml, plates/exp2_dilution.yaml, results/exp4_stars/{band_metrics,scan_screen,scan_key,sers_gain}.csv, results/exp2_dilution/report/{offwhite,white}/per_spectrum_bands.csv, and report/combined/sers_gain_1620.csv. Run raman/compare_dilution_1620.py to regenerate.']
    (OUT/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'measured_scans':len(measurements), 'missing':len(missing), 'pairs':len(pairs), 'screened':len(screened), 'matched_verified':checked, 'top10':top, 'numerical_best':provisional[0]},indent=2))

if __name__ == '__main__':
    main()
