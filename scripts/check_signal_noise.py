"""Synthetic QC stress and a documented counterexample; never sent as live EEG."""
import json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from hub.processing import quality_metrics

if __name__=='__main__':
    t=np.arange(500)/250;rng=np.random.default_rng(17);clean=np.tile((20*np.sin(2*np.pi*10*t))[:,None],(1,7))+rng.normal(0,1,(500,7));cases=[]
    def check(label,x,expected):
        q=quality_metrics(x);cases.append(dict(label=label,passed=q['valid']==expected,valid=q['valid'],reasons=q['reasons']))
    check('合成低噪声对照',clean,True)
    for frequency in [49,49.8,50,50.2,51]:
        for amplitude in [40,100,1000]:check('工频扰动 '+str(frequency)+'Hz / '+str(amplitude)+'uV',clean+amplitude*np.sin(2*np.pi*frequency*t)[:,None],False)
    check('全部平线',np.zeros((500,7)),False)
    flat=clean.copy();flat[:,3]=0;check('单路平线',flat,False)
    spike=clean.copy();spike[125,2]+=1500;check('单路大尖峰',spike,False)
    muscle=clean+80*np.sin(2*np.pi*75*t)[:,None];check('高频扰动对照',muscle,False)
    blink=clean+80*np.exp(-.5*((t-1)/.07)**2)[:,None];q=quality_metrics(blink)
    limitation=dict(label='模拟较轻眨眼的反例',software_gate_passed=q['valid'],interpretation='Amplitude/line/high-frequency QC is not an eye-movement classifier; a mild transient may pass. Requires worn artifact controls, head pose and/or EOG. No intention model is enabled.')
    report=dict(status='pass' if all(r['passed'] for r in cases) else 'failed',scope='synthetic software-QC stress only; no wearable physiological interference acceptance',passed=sum(r['passed'] for r in cases),cases=cases,known_limitations=[limitation])
    (ROOT/'outputs/signal_noise_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
    if report['status']!='pass':raise SystemExit(1)
