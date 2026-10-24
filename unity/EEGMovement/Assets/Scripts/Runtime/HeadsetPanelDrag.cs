using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.XR;
using UnityEngine.XR.Interaction.Toolkit.Interactors;
using UnityEngine.XR.Interaction.Toolkit.UI;

namespace EEGMovement.Unity
{
    public sealed class HeadsetPanelDrag : MonoBehaviour, IPointerDownHandler, IPointerUpHandler, IBeginDragHandler, IDragHandler, IEndDragHandler
    {
        public Transform Panel, Head;
        static HeadsetPanelDrag current;
        XRRayInteractor ray;
        Vector3 grabbedLocal;
        float distance;
        int pointer;
        bool dragging;
        public static void CancelCurrent() { if (current) current.Cancel(); }
        public void OnPointerDown(PointerEventData data)
        {
            var tracked = data as TrackedDeviceEventData;
            var interactor = tracked == null ? null : tracked.interactor as XRRayInteractor;
            if (!interactor || !Panel || !Head) return;
            CancelCurrent(); current = this; ray = interactor; pointer = data.pointerId;
            Vector3 point = data.pointerCurrentRaycast.worldPosition;
            grabbedLocal = Panel.InverseTransformPoint(point);
            distance = Mathf.Clamp(Vector3.Distance(ray.rayOriginTransform.position, point), .65f, 2.8f);
            dragging = true; Debug.Log("EEG_PANEL_DRAG begin");
        }
        public void OnBeginDrag(PointerEventData data) { data.useDragThreshold = false; }
        public void OnDrag(PointerEventData data) { }
        public void OnPointerUp(PointerEventData data) { if (data.pointerId == pointer) Cancel(); }
        public void OnEndDrag(PointerEventData data) { if (data.pointerId == pointer) Cancel(); }
        void OnDisable() { Cancel(); }
        void Cancel() { if (dragging) Debug.Log("EEG_PANEL_DRAG placed"); dragging = false; ray = null; if (current == this) current = null; }
        void Update()
        {
            if (!dragging || !ray || !ray.isActiveAndEnabled) { if (dragging) Cancel(); return; }
            var device = InputDevices.GetDeviceAtXRNode(ray.name.StartsWith("Left") ? XRNode.LeftHand : XRNode.RightHand);
            Vector2 stick; if (device.TryGetFeatureValue(CommonUsages.primary2DAxis, out stick) && Mathf.Abs(stick.y) > .2f)
                distance = Mathf.Clamp(distance + stick.y * Time.unscaledDeltaTime * .7f, .65f, 2.8f);
            var origin = ray.rayOriginTransform;
            Vector3 point = origin.position + origin.forward * distance;
            Vector3 estimatedCenter = point - Panel.TransformVector(grabbedLocal);
            Vector3 forward = estimatedCenter - Head.position; if (forward.sqrMagnitude < .1f) return;
            Quaternion rotation = Quaternion.LookRotation(forward.normalized, Vector3.up);
            Vector3 target = point - rotation * Vector3.Scale(grabbedLocal, Panel.lossyScale);
            Vector3 offset = target - Head.position;
            if (offset.magnitude < .7f) target = Head.position + offset.normalized * .7f;
            float blend = 1 - Mathf.Exp(-Time.unscaledDeltaTime * 28);
            Panel.SetPositionAndRotation(Vector3.Lerp(Panel.position, target, blend), Quaternion.Slerp(Panel.rotation, rotation, blend));
        }
    }
}
