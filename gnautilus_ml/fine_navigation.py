"""Fine-spectrum joint readout; continuous v3 eye and navigation policy."""
from .eye_evidence import EventHybridModel,EventSession
from .fine_spectrum import FineSpectrumEvidence
RUNTIME='gnautilus.hybrid_navigation.v6'

class FineHybridModel(EventHybridModel):
    def __init__(self,arrays,details,device='cpu'):
        super().__init__(arrays,details,device);self.window_s=2.
    def new_session(self):return FineSession(self)

class FineSession(EventSession):
    def __init__(self,model):
        super().__init__(model);self.nav=FineSpectrumEvidence(self.nav,model.details['spectral_view_mode'])
