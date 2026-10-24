using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.EventSystems;

namespace EEGMovement.Unity
{
    // Visual changes never move the raycast rectangle. Both hands can hover independently.
    public sealed class SpatialButtonFeedback : MonoBehaviour, IPointerEnterHandler, IPointerExitHandler, IPointerDownHandler, IPointerUpHandler, IPointerClickHandler
    {
        public bool Selected;
        public Color BaseColor = new Color(.88f, .93f, .95f);
        public static int Clicks;
        readonly HashSet<int> hovering = new HashSet<int>(), pressing = new HashSet<int>();
        Image image; Button button; Outline border;
        void Awake() { image = GetComponent<Image>(); button = GetComponent<Button>(); button.transition = Selectable.Transition.None; border = gameObject.AddComponent<Outline>(); border.effectDistance = new Vector2(2, -2); }
        public void OnPointerEnter(PointerEventData e) { hovering.Add(e.pointerId); }
        public void OnPointerExit(PointerEventData e) { hovering.Remove(e.pointerId); pressing.Remove(e.pointerId); }
        public void OnPointerDown(PointerEventData e) { if (button.IsInteractable()) pressing.Add(e.pointerId); }
        public void OnPointerUp(PointerEventData e) { pressing.Remove(e.pointerId); }
        public void OnPointerClick(PointerEventData e) { if (button.IsInteractable()) { Clicks++; Debug.Log("EEG_UI_CLICK " + GetComponentInChildren<Text>().text); } }
        void OnDisable() { hovering.Clear(); pressing.Clear(); }
        void Update()
        {
            bool enabled = button.IsInteractable(), hover = hovering.Count > 0, down = pressing.Count > 0;
            Color target = !enabled ? new Color(.76f, .79f, .81f) : down ? new Color(.32f, .70f, .79f) : hover ? new Color(.69f, .89f, .96f) : Selected ? new Color(.64f, .83f, .90f) : BaseColor;
            image.color = Color.Lerp(image.color, target, 1 - Mathf.Exp(-Time.unscaledDeltaTime * 24));
            border.effectColor = enabled && (hover || Selected) ? new Color(.02f, .36f, .51f, hover ? .95f : .5f) : Color.clear;
        }
    }
}
