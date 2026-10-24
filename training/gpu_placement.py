"""Physical single-GPU policy. Importing this module cannot initialize CUDA."""
import re

SECOND_A100_UUID='GPU-68bca3c5-cd6e-066a-b543-7466ac87ad62'
FIRST_A100_UUID='GPU-ec371e26-ed3e-9461-ca94-94924005c427'


def project_gpu_environment(config):
    uuid=config.get('gpu_uuid')
    if config.get('gpu_index')!=1 or uuid!=SECOND_A100_UUID or not re.fullmatch(r'GPU-[0-9a-f-]{36}',str(uuid)):
        raise ValueError('EEGMovement is restricted to the verified physical second A100 UUID')
    return dict(CUDA_VISIBLE_DEVICES=uuid,CUDA_DEVICE_ORDER='PCI_BUS_ID',EEGMOVEMENT_PHYSICAL_GPU_UUID=uuid)
