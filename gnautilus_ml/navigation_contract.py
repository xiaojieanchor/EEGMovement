"""Versioned, operator-attested navigation montage; original columns never move."""
CHANNELS = ['Fp1','Fp2','AF3','AF4','F7','F8','F3','F4',
            'FC5','FC3','FC1','FCz','FC2','FC4','FC6',
            'Cz','C3','C1','C5','C2','C4','C6',
            'CP5','CP3','CP1','CPz','CP2','CP4','CP6','P3','Pz','P4']
MOTOR = CHANNELS[8:]
FRONTAL = CHANNELS[:8]
RUNTIME = 'gnautilus.causal_jepa_stream.v1'
ALGORITHM = 'causal_jepa_stream'
SCOPE = 'same_wearing_internal_research'
OPENBMI = 'd1014014c14bc4ecf20352799620369e596ad7acc28acf0d06282cca03f5f582'
EOG = 'd3a0edc35ffedd236ca363b6b21b8c4d6fbe2f8a1048f66868fda21ad9a051aa'
DEFAULT_POLICY = dict(aligned_threshold=.75, conservative_threshold=.85,
                      hold_discount=.05, auxiliary_gain=.25,
                      confirm_updates=3, stop_updates=2, temperature=1.)
ELECTRICAL = dict(schema='gnautilus.electrical.v1',rail_uv=1000.,flat_ptp_uv=.1,
                  max_rms_uv=100.,max_line_ratio=.5,
                  semantics='locked exploratory gross anomaly screen; not impedance',max_ptp_uv=500.)
