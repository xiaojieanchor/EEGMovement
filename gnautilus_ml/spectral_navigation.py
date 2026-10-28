"""Versioned spectral-view adaptation with the retained v3 navigation policy."""
from .eye_evidence import EventHybridModel,EventSession
from .spectral_views import SpectrumViewEvidence
RUNTIME='gnautilus.hybrid_navigation.v5'

class SpectralHybridModel(EventHybridModel):
    def new_session(self):return SpectralSession(self)

class SpectralSession(EventSession):
    def __init__(self,model):
        super().__init__(model);self.nav=SpectrumViewEvidence(self.nav,model.details['spectral_view_mode'])
