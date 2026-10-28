"""Joint SPD/JEPA readout with the retained conservative navigation policy."""
from .eye_evidence import EventHybridModel,EventSession
from .covariance_spectrum import CovarianceEvidence
RUNTIME='gnautilus.hybrid_navigation.v7'
class CovarianceHybridModel(EventHybridModel):
    def __init__(self,arrays,details,device='cpu'):
        super().__init__(arrays,details,device);self.window_s=int(details['spectral_view_mode'].rsplit('_',1)[1])/250
    def new_session(self):return CovarianceSession(self)
class CovarianceSession(EventSession):
    def __init__(self,model):
        super().__init__(model);self.nav=CovarianceEvidence(self.nav,model.details['spectral_view_mode'])
