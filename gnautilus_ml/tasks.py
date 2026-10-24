"""Versioned instructed-task semantics; never inferred eye-position truth."""
from .data import MAPPING,MODES,NAVIGATION_MODES

RESEARCH_OBJECTIVE=dict(schema='gnautilus.navigation_mi_objective.v1',primary_source='motor_imagery',auxiliary_source='gaze',
    imagined_task='first_person_walking_and_turning',prescribed_limb_mapping=False,
    evaluation_required=['motor_imagery_only','gaze_only','combined','uncommanded_gaze_and_controller_interference'],
    source_contribution_verified=False)


def task_spec(label,mode):
    if label not in MAPPING or mode not in MODES:raise ValueError('Unknown instructed task or experiment mode')
    eye,limb=MAPPING[label]
    target='REST' if mode in ['no_control','interference'] else label
    if mode=='motor_only':eye='CENTER'
    if mode in ['gaze_only','no_control','interference']:limb='NO_CONTROL'
    if mode=='no_control':eye='CENTER'
    eyes={'UP':'向上看','DOWN':'向下看','LEFT':'向左看','RIGHT':'向右看','CENTER':'自然中央注视'}
    if mode in NAVIGATION_MODES:
        if mode=='navigation_mi_only':eye='CENTER'
        motors={'FORWARD':('IMAGINE_WALK_FORWARD','向前行走'),'BACKWARD':('IMAGINE_WALK_BACKWARD','向后行走'),
                'LEFT':('IMAGINE_TURN_LEFT','向左转向'),'RIGHT':('IMAGINE_TURN_RIGHT','向右转向'),'REST':('NO_NAVIGATION_IMAGERY','停止导航想象')}
        motor,action=motors[label]
        instruction=('自然中央注视，停止导航运动想象，不发出导航意图。' if label=='REST' else
            '以第一人称想象自己'+action+'，体会身体运动感觉，不只是观看移动画面；'+
            (eyes[eye]+'，眼动仅作辅助。' if mode=='navigation_mi_gaze' else '自然中央注视，不用定向眼动辅助。'))
        instruction+='不指定某只手或腿的想象映射；无需真实迈步或转身，可正常操作手柄，手柄和头动单独记录。'
        return dict(label=label,experiment_mode=mode,gaze_instruction=eye,limb_instruction='NOT_PRESCRIBED',motor_instruction=motor,
                    primary_source='motor_imagery',auxiliary_source='gaze' if mode=='navigation_mi_gaze' else 'none',
                    instruction=instruction,task_binding_version='navigation_mi_gaze_v3')
    limbs={'LEFT_LEG_MI':'想象左腿运动','RIGHT_LEG_MI':'想象右腿运动','LEFT_HAND_MI':'想象左手运动','RIGHT_HAND_MI':'想象右手运动','NO_CONTROL':'无需肢体想象'}
    if mode=='gaze_only':
        instruction=eyes[eye]+('，不发出导航意图' if label=='REST' else '，保持此方向注视')+'；无需肢体想象，可正常操作手柄。不要用转头替代眼动；任务结束回中停止。'
    elif mode=='no_control':instruction='自然中央注视，不发出导航意图；无需肢体想象，可正常操作手柄。'
    elif mode=='interference':instruction=eyes[eye]+'；这是无导航意图的干扰对照，无需肢体想象，可正常操作手柄。'
    else:instruction=eyes[eye]+'；'+limbs[limb]+'。不要实际移动手腿。'
    return dict(label=target,experiment_mode=mode,gaze_instruction=eye,limb_instruction=limb,instruction=instruction,
                task_binding_version='gaze_navigation_v2' if mode in ['gaze_only','no_control','interference'] else 'gaze_four_limb_v1')
