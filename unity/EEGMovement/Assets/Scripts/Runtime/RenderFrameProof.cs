using UnityEngine;
namespace EEGMovement.Unity {
    public sealed class RenderFrameProof : MonoBehaviour
    {
        public EEGAvatarController controller;
        void OnPostRender() { if (controller) controller.ConfirmRenderedFrame(); }
    }
}
