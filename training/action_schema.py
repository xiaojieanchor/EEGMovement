"""Original five labels retain their indices; compatible combinations append."""
ACTION_LABELS=['REST','FORWARD','BACKWARD','LEFT','RIGHT','FORWARD_LEFT','FORWARD_RIGHT','BACKWARD_LEFT','BACKWARD_RIGHT']
# Per-axis classes: neutral, forward/left, backward/right.
LABEL_AXES=[(0,0),(1,0),(2,0),(0,1),(0,2),(1,1),(1,2),(2,1),(2,2)]
AXES_LABEL=dict(zip(LABEL_AXES,ACTION_LABELS))
def action_label(linear,turn):
    return AXES_LABEL[(1 if linear>0 else 2 if linear<0 else 0,1 if turn<0 else 2 if turn>0 else 0)]

def probability_summary(probabilities):
    """Marginalize an actual joint distribution; never invent combo scores."""
    import numpy as np
    p=np.asarray(probabilities,float)
    if p.shape not in [(5,),(9,)] or not np.isfinite(p).all() or (p<0).any() or not np.isclose(p.sum(),1,atol=1e-6):
        raise ValueError('Normalized five or nine class joint distribution required')
    axes=np.asarray(LABEL_AXES[:len(p)])
    linear=[float(p[axes[:,0]==v].sum()) for v in range(3)]
    turn=[float(p[axes[:,1]==v].sum()) for v in range(3)]
    active=float(1-p[0]);directions=[linear[1],linear[2],turn[1],turn[2]]
    return dict(output_schema=2 if len(p)==9 else 1,labels=ACTION_LABELS[:len(p)],prediction=ACTION_LABELS[int(p.argmax())],
        active_probability=active,direction_probabilities=[v/max(active,1e-8) for v in directions],
        axes_probabilities=dict(linear=linear,turn=turn),score_semantics='nine_class_joint_marginals' if len(p)==9 else 'five_class_joint_marginals')
