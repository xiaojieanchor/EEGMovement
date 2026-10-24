import numpy as np
from scipy import signal

class Notch50Filter:
    """Stateful causal 50 Hz biquad; same notch stage as model preprocessing."""
    def __init__(self,fs=250,channels=7):
        self.channels=channels;self.sos=signal.tf2sos(*signal.iirnotch(50,30,fs=fs));self.reset()
    def reset(self):
        self.zi=np.zeros((len(self.sos),2,self.channels));self.anchor=None;self.warm_samples=0
    def process(self,samples):
        x=np.asarray(samples,dtype=np.float64)
        if x.ndim!=2 or x.shape[1]!=self.channels or not np.isfinite(x).all():raise ValueError('Finite time-by-channel signal required')
        if not len(x):return x.copy()
        if self.anchor is None:self.anchor=x[0].copy()
        y,self.zi=signal.sosfilt(self.sos,x-self.anchor,axis=0,zi=self.zi);self.warm_samples+=len(x)
        return y

class CausalFilter:
    def __init__(self,fs=250):
        self.sos=signal.butter(4,[1,45],fs=fs,btype='bandpass',output='sos')
        self.notch=signal.tf2sos(*signal.iirnotch(50,30,fs=fs));self.sos=np.vstack([self.notch,self.sos]);self.reset()
    def reset(self):self.zi=np.zeros((len(self.sos),2,7));self.warm_samples=0
    def process(self,x):
        y,self.zi=signal.sosfilt(self.sos,np.asarray(x,dtype=np.float64),axis=0,zi=self.zi)
        self.warm_samples+=len(x);return y

def quality_metrics(raw,fs=250,max_peak=500,max_rms=200,line_threshold=.45):
    x=np.asarray(raw,dtype=np.float64)
    if x.ndim!=2 or x.shape[1]!=7 or len(x)<fs:
        return {'valid':False,'state':'warming','label':'等待完整窗口','channels':[],'good_channels':0,'reasons':['窗口不足']}
    centered=x-np.median(x,axis=0);rms=np.sqrt(np.mean(centered**2,axis=0));peak=np.max(np.abs(centered),axis=0);ptp=np.ptp(x,axis=0)
    f,p=signal.welch(centered,fs=fs,nperseg=min(len(x),500),axis=0)
    total=np.sum(p[(f>=1)&(f<=100)],axis=0)+1e-20
    line=np.sum(p[(f>=48)&(f<=52)],axis=0)/total
    high=np.sum(p[(f>=35)&(f<=100)],axis=0)/total
    channels=[]
    for i in range(7):
        reasons=[]
        if peak[i]>max_peak or rms[i]>max_rms:reasons.append('幅度过高')
        if ptp[i]<.05:reasons.append('平线')
        if line[i]>line_threshold:reasons.append('50Hz干扰')
        if high[i]>.65 and line[i]<.25:reasons.append('高频伪迹')
        channels.append(dict(name=f'Ch{i+1}',valid=not reasons,rms_uv=round(float(rms[i]),2),peak_uv=round(float(peak[i]),2),line_ratio=round(float(line[i]),4),high_ratio=round(float(high[i]),4),line_power_uv2=float(np.sum(p[(f>=48)&(f<=52),i])*(f[1]-f[0])),reasons=reasons))
    good=sum(c['valid'] for c in channels);valid=good==7
    return dict(valid=valid,state='usable' if valid else 'invalid',label='软件质量检查通过' if valid else '信号无效 / 检查佩戴',channels=channels,good_channels=good,reasons=sorted(set(r for c in channels for r in c['reasons'])),spectrum={'hz':f[(f>=1)&(f<=75)].tolist(),'power':np.mean(p[(f>=1)&(f<=75)],axis=1).tolist()},thresholds={'max_peak_uv':max_peak,'max_rms_uv':max_rms,'line_ratio':line_threshold},method='software estimate; not official contact-impedance state')
