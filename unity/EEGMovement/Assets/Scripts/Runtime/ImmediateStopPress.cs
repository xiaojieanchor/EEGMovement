using UnityEngine;
using UnityEngine.EventSystems;
namespace EEGMovement.Unity
{
    // E-stop is applied on the press edge, before Button's release/click feedback.
    public sealed class ImmediateStopPress : MonoBehaviour, IPointerDownHandler, ISubmitHandler
    {
        public EEGAvatarController controller;
        public void OnPointerDown(PointerEventData data) { if (controller) controller.EmergencyStop(); }
        public void OnSubmit(BaseEventData data) { if (controller) controller.EmergencyStop(); }
    }
}
