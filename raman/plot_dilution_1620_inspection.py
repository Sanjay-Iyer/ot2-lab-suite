"""Read-only redraw of all 93 measured dilution scans for manual inspection."""
from pathlib import Path
import csv
import textwrap
import math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import exp4_stars_report as stars

ROOT = Path(__file__).resolve().parent
BASE = ROOT/'results/dilution_1620_comprehensive'
OUT = BASE/'peak_inspection'

def read(p):
    with p.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def spectrum(r, scans):
    height=float(r['peak_height_cps'])
    if r['experiment']=='exp4':
        s=scans[int(r['scan_number'])]
        x,raw,smooth=s['x_fit'],s['y_cps'],s['smooth_cps_raw']
        left=(x>=1546)&(x<1606)
        right=(x>1632)&(x<=1692)
        xl,xr=x[left].mean(),x[right].mean()
        yl,yr=np.median(smooth[left]),np.median(smooth[right])
        baseline=yl+(yr-yl)/(xr-xl)*(x-xl)
        centre=float(r['peak_center_cm1'])
        j=np.argmin(abs(x-centre))
        assert math.isclose(smooth[j]-baseline[j],height,abs_tol=1e-6)
        return dict(x=x,raw=raw,smooth=smooth,baseline=baseline,point=x[j],centre=centre,
                    anchors=([xl,xr],[yl,yr]),method='Local shoulder baseline',
                    source='raw metadata spectrum; current configured smoothing',height_error=abs(smooth[j]-baseline[j]-height))
    paper='ow' if r['paper']=='offwhite' else 'w'
    cond='np' if r['condition']=='bp' else 'dyeonly'
    label=f"cvdil_{paper}-{cond}_{r['cv_dilution_label']}_082726"
    dirs=sorted((ROOT/'results/exp2_dilution/runs').glob(label+'__*'))
    assert len(dirs)==1, dirs
    p=dirs[0]/(label+'_processed_spectrum.csv')
    d=pd.read_csv(p)
    peak=pd.read_csv(dirs[0]/(label+'_peaks.csv')).query("name == 'cv_1620'").iloc[0]
    exposure=float(r['exposure_s'])
    x=d.wavelength_cm1.to_numpy()
    baseline=d.baseline.to_numpy()/exposure
    raw=d.raw.to_numpy()/exposure
    smooth=d.smoothed.to_numpy()/exposure+baseline
    point=float(peak.center_cm1)
    j=np.argmin(abs(x-point))
    assert math.isclose(smooth[j]-baseline[j],height,abs_tol=1e-6)
    return dict(x=x,raw=raw,smooth=smooth,baseline=baseline,point=point,
                centre=float(r['peak_center_cm1']),anchors=None,method='Legacy arPLS baseline',
                source=str(p.relative_to(ROOT)),height_error=abs(smooth[j]-baseline[j]-height))

def draw(ax,r,s,mode='corrected',limits=(1535,1700),legend=True):
    x=s['x']; sel=(x>=limits[0])&(x<=limits[1])
    ax.axvspan(1606,1632,color='#f3c46b',alpha=.16,label='Search 1606-1632')
    ax.axvspan(1614,1621,color='#369063',alpha=.10,label='Literature 1614-1621')
    if mode=='raw':
        ax.plot(x[sel],s['raw'][sel],color='#929aa5',lw=.9,label='Unsmoothed data')
        ax.plot(x[sel],s['smooth'][sel],color='#192d45',lw=1.5,label='Smoothed trace')
        ax.plot(x[sel],s['baseline'][sel],color='#b83c32',lw=1.6,ls='--',label=s['method'])
        if s['anchors']:
            ax.axvspan(1546,1606,color='#53a0d0',alpha=.10)
            ax.axvspan(1632,1692,color='#53a0d0',alpha=.10)
            ax.scatter(*s['anchors'],color='#b83c32',marker='D',s=30,zorder=5,label='Shoulder medians')
    else:
        ax.plot(x[sel],(s['raw']-s['baseline'])[sel],color='#929aa5',lw=.85,label='Unsmoothed minus baseline')
        ax.plot(x[sel],(s['smooth']-s['baseline'])[sel],color='#192d45',lw=1.6,label='Smoothed minus baseline')
        ax.axhline(0,color='#555',lw=.8)
        sigma=float(r['peak_height_cps'])/float(r['snr'])
        ax.axhspan(-sigma,sigma,color='#737b87',alpha=.10,label='Reported noise +/-1 sigma')
        ax.scatter([s['point']],[float(r['peak_height_cps'])],s=45,color='#b83c32',zorder=5,label='Height read point')
    ax.axvline(s['point'],color='#b83c32',lw=1,ls=':')
    if abs(s['point']-s['centre'])>.01:
        ax.axvline(s['centre'],color='#8c4bad',ls='--',lw=1,label='Reported fitted center')
    ax.axvline(1620,color='#4c7e62',lw=.8,ls=':')
    ax.set_xlim(*limits)
    ax.set_xlabel('Raman shift (cm$^{-1}$)')
    ax.set_ylabel('Counts per second' if mode=='raw' else 'Baseline-subtracted counts/s')
    ax.grid(alpha=.15)
    ax.ticklabel_format(axis='y',style='plain',useOffset=False)
    if legend:
        ax.legend(fontsize=7,loc='best',framealpha=.92)

def title(r):
    return f"Scan {r['scan_number']} | {r['sample']} | {r['paper']} | CV {r['cv_dilution_label'] or 'unknown'} | {r['exposure_s']} s"

def plot_filename(r):
    particle = {'stars': 'star', 'stars5x': 'star_water1to5', 'bp': 'bipyramid', 'dyeonly': 'cv_only', '': 'unassigned'}[r['condition']]
    paper = r['paper'] if r['paper'] not in ('?', '') else 'unknownpaper'
    dilution = r['cv_dilution_label'] or 'unknownCV'
    return f"{paper}_{particle}_{dilution}_scan{r['scan_number']}.png"

def comparison_filename(label, pair, t, c):
    return f"{plot_filename(t)[:-4]}_vs_{plot_filename(c)[:-4]}_{label.lower()}_rank{int(pair['rank']):02d}.png"

def contact_filename(panel):
    return {
        'ow-10': 'offwhite_star_and_cv_all_dilutions_10s',
        'w-10': 'white_star_and_cv_all_dilutions_10s',
        'w-6': 'white_star_and_cv_all_dilutions_6s',
        'bp-ow-10': 'offwhite_bipyramid_and_cv_all_dilutions_10s',
        'bp-w-10': 'white_bipyramid_all_dilutions_10s',
        'bp-w-6': 'white_bipyramid_and_cv_all_dilutions_6s',
        'exp2-offwhite': 'offwhite_bipyramid_and_cv_all_dilutions_exp2',
        'exp2-white': 'white_bipyramid_and_cv_all_dilutions_exp2',
        'unassigned': 'unknownpaper_unassigned_unknownCV'
    }[panel] + '.png'

def main():
    for d in ['individual','comparisons','contact_sheets']:
        (OUT/d).mkdir(parents=True,exist_ok=True)
    rows=[r for r in read(BASE/'all_results_1620.csv') if r['peak_height_cps']]
    scans={s['scan_number']:s for s in stars.load_scans(stars.load_config())}
    data={r['record_id']:spectrum(r,scans) for r in rows}
    byid={r['record_id']:r for r in rows}
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'white'})
    index=[]
    for r in rows:
        s=data[r['record_id']]
        fig,axs=plt.subplots(1,3,figsize=(17,6))
        draw(axs[0],r,s,'raw'); axs[0].set_title('Raw signal and baseline',loc='left')
        draw(axs[1],r,s); axs[1].set_title('Baseline-subtracted context',loc='left')
        draw(axs[2],r,s,limits=(1598,1642)); axs[2].set_title('Tight peak / noise inspection',loc='left')
        fig.suptitle(title(r),x=.055,ha='left',fontsize=14)
        note=f"Recorded height {float(r['peak_height_cps']):.3f} cps | SNR {float(r['snr']):.2f} | height point {s['point']:.2f} | reported center {s['centre']:.2f} cm-1 | {s['method']}"
        flags=r['flags'] or 'No recorded quality flags'
        if r['experiment']=='exp4':
            flags+=' | Left shoulder 1546-1606 includes the neighboring 1587 band; inspect baseline placement.'
        fig.text(.055,.115,note,fontsize=9)
        fig.text(.055,.075,textwrap.fill(flags,175),fontsize=8,color='#744328')
        fig.text(.055,.027,'One measured spot. Shading is a search/reference window, not evidence of a peak. Noise band is the existing SNR denominator, not a confidence interval.',fontsize=8)
        fig.subplots_adjust(left=.055,right=.98,top=.86,bottom=.25,wspace=.28)
        p=OUT/'individual'/plot_filename(r)
        fig.savefig(p,dpi=150); plt.close(fig)
        index.append(dict(record_id=r['record_id'],scan_number=r['scan_number'],sample=r['sample'],paper=r['paper'],cv_dilution=r['cv_dilution_label'],peak_height_cps=r['peak_height_cps'],snr=r['snr'],height_read_cm1=s['point'],reported_center_cm1=s['centre'],flags=r['flags'],height_redraw_error_cps=s['height_error'],plot_path=str(p),source_spectrum=s['source']))
    with (OUT/'inspection_index.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(index[0])); w.writeheader(); w.writerows(index)
    print(f'Created {len(index)} individual plots; heights match saved CSVs.',flush=True)
    md=['# Inspect the 1620 peaks','','93 individual scan plots, plus both top-10 comparison lists. All plotted heights are verified against the comprehensive results CSV to within 1e-6 counts/s.','','## How to read these plots','','Gray is the unsmoothed measured trace; blue is the smoothed trace. Red marks the baseline/height read point. Inspect the width and consistency of the feature in the gray data, not just a maximum in the blue line. The raw panel shows whether background curvature or a neighboring band is driving the reported height. Peaks shifted outside the reference window are flagged, but a flag alone does not prove noise.','','Exp4: local shoulder baseline, with 1546-1606 and 1632-1692 anchors. The left shoulder can include the 1587 band. Exp2: its saved arPLS baseline; purple marks the fitted center when it differs from the point where height was measured. Methods are preserved, not re-fitted.','','[Inspection CSV with absolute plot paths](inspection_index.csv)','','## Top-10 comparisons','']
    for label,file in [('Screened','top10_screened_adjusted_boost.csv'),('Numerical_with_flags','top10_numerical_adjusted_boost_with_flags.csv')]:
        md += [f'### {label.replace("_"," ")}', '']
        for pair in read(BASE/file):
            t,c=byid[pair['test_record_id']],byid[pair['control_record_id']]
            fig,axs=plt.subplots(2,2,figsize=(13,9))
            for col,r in enumerate([t,c]):
                s=data[r['record_id']]
                draw(axs[0,col],r,s,'raw')
                draw(axs[1,col],r,s,limits=(1580,1655))
                axs[0,col].set_title(f"{'NP sample' if col==0 else 'CV-only control'}: scan {r['scan_number']} | CV {r['cv_dilution_label']}",loc='left')
                axs[1,col].set_title(f"Height {float(r['peak_height_cps']):.2f} cps | SNR {float(r['snr']):.1f}",loc='left')
            # Common corrected y-scale ensures the apparent size comparison is honest.
            lim=[a.get_ylim() for a in axs[1]]
            for a in axs[1]: a.set_ylim(min(v[0] for v in lim),max(v[1] for v in lim))
            fig.suptitle(f"{label.replace('_',' ')} #{pair['rank']} | {t['sample']} | {t['paper']}\nMeasured ratio {float(pair['raw_signal_ratio_x']):.3f}x  x  CV correction {float(pair['concentration_correction_x']):g}  =  {float(pair['concentration_adjusted_boost_x']):.2f}x",fontsize=13)
            warning='NP: '+(t['flags'] or 'no flags')+' | CV: '+(c['flags'] or 'no flags')
            fig.text(.06,.035,textwrap.fill(warning,150),fontsize=8,color='#744328')
            fig.subplots_adjust(left=.08,right=.98,top=.85,bottom=.13,hspace=.42,wspace=.25)
            name=comparison_filename(label,pair,t,c)
            fig.savefig(OUT/'comparisons'/name,dpi=145); plt.close(fig)
            md += [f"{pair['rank']}. **{float(pair['concentration_adjusted_boost_x']):.2f}x adjusted** — {t['sample']}, {t['paper']}, CV {t['cv_dilution_label']} versus {c['cv_dilution_label']}: [side-by-side plot](comparisons/{name}) · [NP scan {t['scan_number']}](individual/{plot_filename(t)}) · [CV scan {c['scan_number']}](individual/{plot_filename(c)})", '']
    md += ['## Every measured scan, grouped by panel','']
    panels=sorted({r['panel'] for r in rows})
    for panel in panels:
        group=[r for r in rows if r['panel']==panel]
        nrow=math.ceil(len(group)/4)
        fig,axs=plt.subplots(nrow,4,figsize=(16,3*nrow),squeeze=False)
        for ax,r in zip(axs.flat,group):
            draw(ax,r,data[r['record_id']],limits=(1585,1650),legend=False)
            ax.set_title(f"{r['scan_number']} {r['condition'] or 'unknown'} CV {r['cv_dilution_label']}\nH {float(r['peak_height_cps']):.1f} | SNR {float(r['snr']):.1f}",fontsize=9)
            ax.tick_params(labelsize=8); ax.set_xlabel('cm$^{-1}$',fontsize=8); ax.set_ylabel('cps',fontsize=8)
        for ax in list(axs.flat)[len(group):]: ax.set_visible(False)
        fig.suptitle(f'{panel} — 1620 inspection overview (individual axes autoscale)',fontsize=14)
        fig.tight_layout(rect=(0,0,1,.96)); fig.savefig(OUT/'contact_sheets'/contact_filename(panel),dpi=125); plt.close(fig)
        md += [f'### {panel}', '',f'[Open panel overview](contact_sheets/{contact_filename(panel)})', '', '| Scan | Sample | CV dilution | Height cps | SNR | Flags |','|---|---|---|---:|---:|---|']
        for r in group:
            md.append(f"| [{r['scan_number']}](individual/{plot_filename(r)}) | {r['sample']} | {r['cv_dilution_label']} | {float(r['peak_height_cps']):.2f} | {float(r['snr']):.1f} | {r['flags'] or 'none'} |")
        md.append('')
    md += ['Missing scans 823-827 have no spectra and therefore no plots. Scan 847 is an unassigned probe and is included for completeness.','']
    (OUT/'README.md').write_text('\n'.join(md),encoding='utf-8')
    assert len(list((OUT/'individual').glob('*.png')))==93
    assert len(list((OUT/'comparisons').glob('*.png')))==20
    print(f'Created 20 paired figures, {len(panels)} contact sheets and README.md gallery.',flush=True)

if __name__=='__main__': main()
