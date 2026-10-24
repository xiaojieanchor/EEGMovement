using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
namespace EEGMovement.Unity
{
    public sealed class HoldLabelPress : MonoBehaviour, IPointerDownHandler, IPointerUpHandler, IPointerEnterHandler, IPointerExitHandler, ICancelHandler
    {
        public SelfPacedAcquisition Owner; public string Label; bool held; int pointer;
        readonly System.Collections.Generic.HashSet<int> hovering=new System.Collections.Generic.HashSet<int>();
        public void OnPointerEnter(PointerEventData e){hovering.Add(e.pointerId);}
        public void OnPointerDown(PointerEventData e) { if(held||!Owner||!Owner.Active||!Owner.Press(Label,e.pointerId))return;held=true;pointer=e.pointerId; }
        public void OnPointerUp(PointerEventData e) { if(held&&e.pointerId==pointer)Release(); }
        public void OnPointerExit(PointerEventData e) { hovering.Remove(e.pointerId);if(held&&e.pointerId==pointer)Release(); }
        public void OnCancel(BaseEventData e) { Release(); }
        void Release(){if(!held)return;held=false;if(Owner)Owner.Release(Label,pointer);}
        void OnDisable(){hovering.Clear();Release();}
        void Update(){var image=GetComponent<Image>();if(image)image.color=held?new Color(.72f,.85f,.98f):hovering.Count>0?new Color(.84f,.91f,.98f):new Color(.94f,.96f,.98f);}
    }
}
