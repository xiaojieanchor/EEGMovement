using UnityEngine;
using UnityEngine.UI;
using UnityEngine.XR;
using UnityEngine.XR.Interaction.Toolkit;
using UnityEngine.XR.Interaction.Toolkit.Interactors;
using UnityEngine.XR.Interaction.Toolkit.Inputs.Readers;
using Unity.XR.CoreUtils;
using InputAction = UnityEngine.InputSystem.InputAction;
using InputActionType = UnityEngine.InputSystem.InputActionType;
using InputActionProperty = UnityEngine.InputSystem.InputActionProperty;
using TrackedPoseDriver = UnityEngine.InputSystem.XR.TrackedPoseDriver;

namespace EEGMovement.Unity
{
    // Device poses are applied to a visual rig only. Avatar/core coordinates are never recentered.
    [DefaultExecutionOrder(-30000)] // Pose/trigger update precedes XRI manager (-105) and UI sampling.
    public sealed class SpatialWorkspaceRig : MonoBehaviour
    {
        public bool Active { get; private set; }
        Canvas canvas;
        Camera desktop, headset;
        EEGAvatarController controller;
        GameObject originObject;
        WorkspaceUI workspaceUI;
        bool pendingRecenter;
        readonly Transform[] hands = new Transform[2];
        readonly XRRayInteractor[] rays = new XRRayInteractor[2];
        readonly LineRenderer[] lines = new LineRenderer[2];
        readonly LineRenderer[] reticles = new LineRenderer[2];
        readonly string[] lastHits = new string[2];
        readonly float[] cursorLogAt = new float[2];
        readonly bool[] wasPressed = new bool[2], wasMenu = new bool[2];
        bool wasToggle, wasSummon;
        bool wasRightA, sessionSeen, heightCalibrated;
        QuestStatusOverlay statusOverlay;
        float trackingSince = -1, nextPoseLog;
        XROrigin origin;
        Transform trackingSpace;
        XRDisplaySubsystem display;
        XRInputSubsystem inputSubsystem;
        bool pendingSceneRecenter;
        float originChangedAt, recenterTrackingSince = -1;
        public static bool DisplayFocused { get; private set; } = true;
        bool InteractionFocused { get { return Application.isFocused && DisplayFocused; } }
        readonly bool[] suppressPress = new bool[2];
        public Transform Head { get { return headset ? headset.transform : null; } }
        public bool PanelVisible { get { return canvas && canvas.gameObject.activeSelf; } }
        public void CalibrateViewHeight()
        {
            bool tracked; var device = InputDevices.GetDeviceAtXRNode(XRNode.Head);
            if (!Active || !InteractionFocused || !device.TryGetFeatureValue(CommonUsages.isTracked, out tracked) || !tracked) return;
            // Translate only the visual origin once. The tracked camera pose and motion core
            // remain untouched; subsequent leaning, crouching and walking remain 6DoF.
            bool initial=!heightCalibrated;
            Vector3 position = originObject.transform.position; position.y += 1.65f - headset.transform.position.y;
            originObject.transform.position = position; heightCalibrated = true;
            if(initial)
            {
                Vector3 forward=Vector3.ProjectOnPlane(headset.transform.forward,Vector3.up),toward=Vector3.ProjectOnPlane(controller.transform.position-headset.transform.position,Vector3.up);
                if(forward.sqrMagnitude>.1f&&toward.sqrMagnitude>.1f)originObject.transform.RotateAround(headset.transform.position,Vector3.up,Vector3.SignedAngle(forward,toward,Vector3.up));
            }
            if (PanelVisible) RecenterPanel();
            Debug.Log("EEG_XR_HEIGHT calibrated_eye=1.65 seated_reference=true");
        }
        public void SetPanelVisible(bool visible)
        {
            if (!Active) return;
            HeadsetPanelDrag.CancelCurrent();
            if (!visible) { if (controller.Acquisition) controller.Acquisition.Abort("panel_hidden"); if (controller.Workspace.AllowTests) controller.AllowTests(false); }
            canvas.gameObject.SetActive(visible);
            for (int i = 0; i < 2; i++) { rays[i].enableUIInteraction = visible; suppressPress[i] = true; wasPressed[i] = false; }
            if (visible) { RecenterPanel(); canvas.enabled = true; pendingRecenter = false; }
            Debug.Log(visible ? "EEG_PANEL summoned" : "EEG_PANEL hidden");
        }
        public void Initialize(Canvas target, Camera screen, EEGAvatarController avatar, WorkspaceUI ui)
        {
            canvas = target; desktop = screen; controller = avatar; workspaceUI = ui;
            new GameObject("XR Interaction Manager", typeof(XRInteractionManager)).transform.SetParent(transform, false);
            originObject = new GameObject("XR visual origin · independent of motion core");
            // Awake must see the real XR camera and offset. An active AddComponent would instead
            // let XROrigin.Awake capture Camera.main (the independent desktop camera).
            originObject.SetActive(false); originObject.transform.SetParent(transform, false); originObject.transform.position = new Vector3(2.2f, 0, -3.2f);
            originObject.transform.rotation = Quaternion.LookRotation(new Vector3(-2.2f, 0, 3.2f));
            var offset = new GameObject("Tracking space"); offset.transform.SetParent(originObject.transform, false);
            var head = new GameObject("XR camera"); head.transform.SetParent(offset.transform, false); headset = head.AddComponent<Camera>();
            headset.nearClipPlane = .05f; headset.farClipPlane = 100; headset.clearFlags = CameraClearFlags.SolidColor; headset.backgroundColor = desktop.backgroundColor;
            headset.stereoTargetEye = StereoTargetEyeMask.Both; headset.enabled = false;
            desktop.stereoTargetEye = StereoTargetEyeMask.None;
#if UNITY_ANDROID && !UNITY_EDITOR
            // System overlays remove application focus while XR view tracking must continue.
            // Controller interaction is separately gated by focus below.
            UnityEngine.InputSystem.InputSystem.settings.backgroundBehavior = UnityEngine.InputSystem.InputSettings.BackgroundBehavior.IgnoreFocus;
#endif
            var pose = head.AddComponent<TrackedPoseDriver>();
            pose.positionInput = new InputActionProperty(new InputAction("HMD position", InputActionType.Value, "<XRHMD>/centerEyePosition", expectedControlType: "Vector3"));
            pose.rotationInput = new InputActionProperty(new InputAction("HMD rotation", InputActionType.Value, "<XRHMD>/centerEyeRotation", expectedControlType: "Quaternion"));
            pose.trackingStateInput = new InputActionProperty(new InputAction("HMD tracking state", InputActionType.Value, "<XRHMD>/trackingState", expectedControlType: "Integer"));
            pose.trackingType = TrackedPoseDriver.TrackingType.RotationAndPosition;
            pose.updateType = TrackedPoseDriver.UpdateType.UpdateAndBeforeRender; pose.ignoreTrackingState = false;
            head.AddComponent<RenderFrameProof>().controller = avatar;
            origin = originObject.AddComponent<XROrigin>(); origin.Origin = originObject; origin.Camera = headset; origin.CameraFloorOffsetObject = offset; origin.CameraYOffset = 1.65f;
            trackingSpace = offset.transform;
            origin.RequestedTrackingOriginMode = XROrigin.TrackingOriginMode.Device;
            for (int i = 0; i < 2; i++)
            {
                var hand = new GameObject(i == 0 ? "Left UI ray · trigger to select" : "Right UI ray · trigger to select"); hand.transform.SetParent(offset.transform, false); hands[i] = hand.transform;
                hand.SetActive(false); var ray = hand.AddComponent<XRRayInteractor>(); ray.enableUIInteraction = true; ray.maxRaycastDistance = 5;
                ray.rayOriginTransform = hand.transform; ray.referenceFrame = originObject.transform;
                ray.uiPressInput.inputSourceMode = XRInputButtonReader.InputSourceMode.ManualValue; rays[i] = ray;
                var line = hand.AddComponent<LineRenderer>(); line.positionCount = 2; line.startWidth = .003f; line.endWidth = .0015f; line.sortingOrder = 100;
                var preserved = Resources.Load<Material>("WorkspaceLine");
                line.material = preserved ? preserved : new Material(Shader.Find("Sprites/Default")); line.startColor = new Color(.1f, .6f, .7f, .8f); line.endColor = new Color(.1f, .6f, .7f, .25f); lines[i] = line;
                var point = new GameObject(i == 0 ? "Left exact UI landing ring" : "Right exact UI landing ring"); point.transform.SetParent(transform, false);
                var ring = point.AddComponent<LineRenderer>(); ring.loop = true; ring.useWorldSpace = true; ring.positionCount = 32; ring.startWidth = ring.endWidth = .003f; ring.sortingOrder = 110;
                ring.material = line.material; reticles[i] = ring; ring.enabled = false;
            }
            originObject.SetActive(true);
            statusOverlay=gameObject.AddComponent<QuestStatusOverlay>();statusOverlay.Controller=controller;statusOverlay.Initialize(headset.transform);
        }
        void Update()
        {
            var general = UnityEngine.XR.Management.XRGeneralSettings.Instance;
            bool loader = general && general.Manager && general.Manager.isInitializationComplete && general.Manager.activeLoader;
            var currentDisplay = loader ? general.Manager.activeLoader.GetLoadedSubsystem<XRDisplaySubsystem>() : null;
            var currentInput = loader ? general.Manager.activeLoader.GetLoadedSubsystem<XRInputSubsystem>() : null;
            if (currentInput != inputSubsystem)
            {
                if (inputSubsystem != null) inputSubsystem.trackingOriginUpdated -= OnTrackingOriginUpdated;
                inputSubsystem = currentInput;
                if (inputSubsystem != null) inputSubsystem.trackingOriginUpdated += OnTrackingOriginUpdated;
            }
            if (currentDisplay != display)
            {
                if (display != null) display.displayFocusChanged -= OnDisplayFocusChanged;
                display = currentDisplay;
                if (display != null) display.displayFocusChanged += OnDisplayFocusChanged;
            }
            bool active = XRSettings.isDeviceActive || loader;
            if (active) sessionSeen = true;
            // A Quest system overlay is not a request to render the desktop camera in XR.
            if (Application.platform == RuntimePlatform.Android && sessionSeen) active = true;
            if (active != Active)
            {
                canvas.gameObject.SetActive(true);
                Active = active; desktop.enabled = !active; headset.enabled = active;
                Debug.Log("EEG_XR_MODE visual_xr=" + active + " device_active=" + XRSettings.isDeviceActive + " loader=" + loader);
                if (active)
                {
                    canvas.renderMode = RenderMode.WorldSpace; canvas.worldCamera = headset;
                    var rect = canvas.GetComponent<RectTransform>(); rect.sizeDelta = new Vector2(1040, 760); rect.localScale = Vector3.one * .00135f;
                    var scaler = canvas.GetComponent<CanvasScaler>(); scaler.dynamicPixelsPerUnit = 2; scaler.enabled = true;
                    QualitySettings.antiAliasing = 4; QualitySettings.shadows = ShadowQuality.Disable;
                    XRSettings.eyeTextureResolutionScale = 1.1f; XRSettings.renderViewportScale = 1;
                    Application.targetFrameRate = 72;
                    workspaceUI.SetSpatial(true); pendingRecenter = true; canvas.enabled = false;
                    canvas.GetComponent<GraphicRaycaster>().enabled = false;
                    var inputModule = UnityEngine.EventSystems.EventSystem.current.currentInputModule as UnityEngine.XR.Interaction.Toolkit.UI.XRUIInputModule;
                    if (inputModule) { inputModule.enableMouseInput = false; inputModule.enableGamepadInput = false; }
                    Debug.Log("EEG_XR_CLARITY eye=" + XRSettings.eyeTextureWidth + "x" + XRSettings.eyeTextureHeight + " scale=1.1 MSAA=4 panel=1040x760@1.55m glyphDensity=2");
                }
                else
                {
                    canvas.renderMode = RenderMode.ScreenSpaceOverlay; canvas.worldCamera = null; canvas.transform.localScale = Vector3.one;
                    canvas.GetComponent<CanvasScaler>().enabled = true; controller.EmergencyStop();
                    workspaceUI.SetSpatial(false);
                    canvas.GetComponent<GraphicRaycaster>().enabled = true;
                    var inputModule = UnityEngine.EventSystems.EventSystem.current.currentInputModule as UnityEngine.XR.Interaction.Toolkit.UI.XRUIInputModule;
                    if (inputModule) { inputModule.enableMouseInput = true; inputModule.enableGamepadInput = true; }
                    for (int i = 0; i < 2; i++) { hands[i].gameObject.SetActive(false); reticles[i].enabled = false; }
                    canvas.enabled = true; pendingRecenter = false;
                }
            }
            if (!active) { if (statusOverlay) statusOverlay.SetVisible(false); return; }
            bool manual=controller.SelfPaced&&controller.SelfPaced.Active;
            // Put the collection plane in place BEFORE XRI samples UI hits. A
            // second LateUpdate move would separate visible and clickable points.
            if(manual)controller.SelfPaced.SynchronizeInputSurface();
            var calibration=controller.GetComponent<PlacementCalibration>();
            bool calibrating=calibration&&calibration.State!=null&&calibration.State.active;
            bool collecting=(controller.Acquisition&&controller.Acquisition.Immersive)||manual||calibrating;
            var left = InputDevices.GetDeviceAtXRNode(XRNode.LeftHand); bool toggle, summon;
            left.TryGetFeatureValue(CommonUsages.primaryButton, out toggle); left.TryGetFeatureValue(CommonUsages.secondaryButton, out summon);
            if(collecting&&!manual&&!calibrating&&controller.Acquisition.Active&&InteractionFocused&&toggle&&!wasToggle)controller.Acquisition.TogglePause();
            if (!collecting && InteractionFocused && toggle && !wasToggle) SetPanelVisible(!PanelVisible);
            if (!collecting && InteractionFocused && summon && !wasSummon) SetPanelVisible(true);
            wasToggle = toggle; wasSummon = summon;
            var right = InputDevices.GetDeviceAtXRNode(XRNode.RightHand); bool rightA, rightB, rightTracked;
            right.TryGetFeatureValue(CommonUsages.primaryButton, out rightA); right.TryGetFeatureValue(CommonUsages.secondaryButton, out rightB);
            right.TryGetFeatureValue(CommonUsages.isTracked, out rightTracked);
            if (InteractionFocused && rightTracked && rightA && !wasRightA)
            {
                if(calibrating)calibration.Cancel();else if(manual)controller.SelfPaced.Finish();else if(collecting)controller.Acquisition.Abort("operator_stop");
                else {
                bool show = !PanelVisible;
                if (show) controller.Navigate("settings");
                SetPanelVisible(show);
                Debug.Log("EEG_SETTINGS_BUTTON key=A visible=" + show);
                }
            }
            // Momentary visibility: no hold timer and no latched toggle.
            statusOverlay.SetVisible(!collecting && InteractionFocused && right.isValid && rightTracked && rightB);
            wasRightA = rightA;
            // The official driver owns the HMD transform, including its before-render update.
            for (int i = 0; i < 2; i++)
            {
                var device = InputDevices.GetDeviceAtXRNode(i == 0 ? XRNode.LeftHand : XRNode.RightHand); bool tracked;
                device.TryGetFeatureValue(CommonUsages.isTracked, out tracked); hands[i].gameObject.SetActive(device.isValid && tracked);
                if (!device.isValid || !tracked) { wasPressed[i] = false; continue; }
                // OpenXR devicePosition/deviceRotation are grip poses. A pointing ray must
                // use pointerPosition/pointerRotation (aimPose), without arbitrary angle offsets.
                var input = UnityEngine.InputSystem.InputSystem.GetDevice<UnityEngine.InputSystem.XR.XRController>(i == 0 ? UnityEngine.InputSystem.CommonUsages.LeftHand : UnityEngine.InputSystem.CommonUsages.RightHand);
                var aimPosition = input == null ? null : input.TryGetChildControl<UnityEngine.InputSystem.Controls.Vector3Control>("pointerPosition");
                var aimRotation = input == null ? null : input.TryGetChildControl<UnityEngine.InputSystem.Controls.QuaternionControl>("pointerRotation");
                if (aimPosition == null || aimRotation == null) { hands[i].gameObject.SetActive(false); reticles[i].enabled = false; continue; }
                hands[i].localPosition = aimPosition.ReadValue(); hands[i].localRotation = aimRotation.ReadValue();
                bool pressed, menu; device.TryGetFeatureValue(CommonUsages.triggerButton, out pressed); device.TryGetFeatureValue(CommonUsages.menuButton, out menu);
                if (suppressPress[i]) { if (!pressed) suppressPress[i] = false; pressed = false; }
                if ((collecting&&!manual) || (!PanelVisible&&!manual) || !InteractionFocused) pressed = false;
                rays[i].enableUIInteraction = (!collecting||manual) && (PanelVisible||manual) && InteractionFocused;
                if (menu && !wasMenu[i]) controller.EmergencyStop(); wasMenu[i] = menu;
                var reader = rays[i].uiPressInput; reader.manualPerformed = pressed; reader.manualValue = pressed ? 1 : 0;
                if (pressed && !wasPressed[i]) reader.manualFramePerformed = Time.frameCount;
                if (!pressed && wasPressed[i]) reader.manualFrameCompleted = Time.frameCount;
                wasPressed[i] = pressed;
            }
        }
        void LateUpdate()
        {
            if (!Active) return;
            bool headTracked, present;
            var headDevice = InputDevices.GetDeviceAtXRNode(XRNode.Head);
            bool validHead = headDevice.TryGetFeatureValue(CommonUsages.isTracked, out headTracked) && headTracked;
            bool userPresent = !headDevice.TryGetFeatureValue(CommonUsages.userPresence, out present) || present;
            if (!heightCalibrated)
            {
                if (validHead && userPresent && InteractionFocused)
                { if (trackingSince < 0) trackingSince = Time.unscaledTime; if (Time.unscaledTime - trackingSince >= .5f) CalibrateViewHeight(); }
                else trackingSince = -1;
            }
            if (pendingSceneRecenter && heightCalibrated)
            {
                if (validHead && userPresent && InteractionFocused)
                {
                    if (recenterTrackingSince < 0) recenterTrackingSince = Time.unscaledTime;
                    // OpenXR may report several reference-space changes for one Meta gesture.
                    // Coalesce them and wait for the new tracked pose, never on focus alone.
                    if (Time.unscaledTime - originChangedAt >= .2f && Time.unscaledTime - recenterTrackingSince >= .15f)
                        RecenterSceneView();
                }
                else recenterTrackingSince = -1;
            }
            if (Time.unscaledTime >= nextPoseLog)
            {
                nextPoseLog = Time.unscaledTime + 1;
                Debug.Log("EEG_XR_POSE focused=" + InteractionFocused + " app_focus=" + Application.isFocused + " display_focus=" + DisplayFocused + " device_active=" + XRSettings.isDeviceActive + " tracked=" + validHead
                    + " desktop_camera=" + desktop.enabled + " eye_y=" + headset.transform.position.y.ToString("F3")
                    + " local=" + headset.transform.localPosition.ToString("F3") + " rotation=" + headset.transform.eulerAngles.ToString("F1")
                    + " offset_y=" + trackingSpace.localPosition.y.ToString("F3") + " origin_y=" + originObject.transform.position.y.ToString("F3")
                    + " mode=" + origin.CurrentTrackingOriginMode);
            }
            if (pendingRecenter)
            {
                bool tracked; var head = InputDevices.GetDeviceAtXRNode(XRNode.Head);
                if (heightCalibrated && head.isValid && head.TryGetFeatureValue(CommonUsages.isTracked, out tracked) && tracked)
                { RecenterPanel(); canvas.enabled = true; pendingRecenter = false; }
            }
            bool manualInput=controller.SelfPaced&&controller.SelfPaced.Active&&controller.SelfPaced.InputCanvas;
            Canvas pointerCanvas=manualInput?controller.SelfPaced.InputCanvas:canvas;
            for (int i = 0; i < 2; i++)
            {
                // The collection canvas renders at 32750; the old 100/110
                // renderers were behind its opaque white field, including at
                // empty points. Keep both rays above the active canvas.
                PointerSurface.Configure(lines[i],reticles[i],pointerCanvas,manualInput);
                if (!hands[i].gameObject.activeInHierarchy || (!PanelVisible&&!manualInput) || !InteractionFocused) { reticles[i].enabled = false; lines[i].enabled = false; continue; }
                lines[i].enabled = true;
                UnityEngine.EventSystems.RaycastResult hit; bool hasHit = rays[i].TryGetCurrentUIRaycastResult(out hit);
                var ray = new Ray(hands[i].position, hands[i].forward);
                Vector3 end;bool onPanel=PointerSurface.Intersect(pointerCanvas,ray,manualInput,out end);
                // Cursor position is a continuous geometric intersection, never a selected
                // widget's cached hit position. UI hits affect feedback only.
                var button = hasHit && hit.gameObject ? hit.gameObject.GetComponentInParent<Button>() : null;
                var dragHandle = hasHit && hit.gameObject ? hit.gameObject.GetComponentInParent<HeadsetPanelDrag>() : null;
                bool clickable = (button && button.IsInteractable()) || dragHandle;
                Color color = clickable ? new Color(.02f, .48f, .66f, 1) : new Color(.035f, .16f, .24f, 1);
                if (wasPressed[i] && clickable) color = new Color(.02f, .28f, .42f, 1);
                lines[i].startColor = lines[i].endColor = color; lines[i].SetPosition(0, hands[i].position); lines[i].SetPosition(1, end);
                var ring = reticles[i]; ring.enabled = onPanel; ring.startColor = ring.endColor = color;
                Vector3 center = end + (headset.transform.position-end).normalized * .003f; float radius = wasPressed[i] ? .008f : clickable ? .012f : .009f;
                for (int n = 0; n < 32; n++) { float angle = n * Mathf.PI * 2 / 32; ring.SetPosition(n, center + (pointerCanvas.transform.right * Mathf.Cos(angle) + pointerCanvas.transform.up * Mathf.Sin(angle)) * radius); }
                string name = button ? button.GetComponentInChildren<Text>().text : dragHandle ? "title_drag" : "none";
                if (name != lastHits[i]) { lastHits[i] = name; Debug.Log("EEG_XR_POINTER hand=" + i + " source=openxr_aimPose hit=" + name); }
                if (onPanel && Time.unscaledTime >= cursorLogAt[i])
                { cursorLogAt[i] = Time.unscaledTime + 1; Vector3 local = pointerCanvas.transform.InverseTransformPoint(end); Debug.Log("EEG_XR_CURSOR hand=" + i + " x=" + local.x.ToString("F1") + " y=" + local.y.ToString("F1") + " target=" + name + " sorting="+ring.sortingOrder+" collection="+manualInput+" visible="+ring.isVisible+" continuous_plane=true"); }
            }
        }
        static void UpdatePose(InputDevice device, Transform target, bool head)
        {
            Vector3 position; Quaternion rotation;
            if (device.TryGetFeatureValue(CommonUsages.devicePosition, out position)) target.localPosition = position;
            else if (head) target.localPosition = Vector3.up * 1.65f;
            if (device.TryGetFeatureValue(CommonUsages.deviceRotation, out rotation)) target.localRotation = rotation;
        }
        public void RecenterPanel()
        {
            HeadsetPanelDrag.CancelCurrent();
            var forward = Vector3.ProjectOnPlane(headset.transform.forward, Vector3.up).normalized;
            if (forward.sqrMagnitude < .1f) forward = originObject.transform.forward;
            canvas.transform.position = headset.transform.position + forward * 1.55f;
            canvas.transform.rotation = Quaternion.LookRotation(forward, Vector3.up);
        }
        void OnTrackingOriginUpdated(XRInputSubsystem subsystem)
        {
            if (!Active || !heightCalibrated) return; // Initial mode negotiation is not a user recenter.
            pendingSceneRecenter = true; originChangedAt = Time.unscaledTime; recenterTrackingSince = -1;
            HeadsetPanelDrag.CancelCurrent();
            controller.EmergencyStop();
            if (controller.Acquisition) controller.Acquisition.Abort("tracking_origin_changed");
            Debug.Log("EEG_SCENE_RECENTER queued source=runtime_origin_changed");
        }
        void RecenterSceneView()
        {
            pendingSceneRecenter = false;
            if (!origin || origin.Origin != originObject || origin.Camera != headset)
            { Debug.LogError("EEG_SCENE_RECENTER rejected reason=visual_rig_reference"); return; }
            Vector3 avatarPosition = controller.transform.position;
            Quaternion avatarRotation = controller.transform.rotation;
            Vector3 viewingOffset = Quaternion.Euler(0, avatarRotation.eulerAngles.y, 0) * new Vector3(2.2f, 0, -3.2f);
            Vector3 desiredEye = avatarPosition + viewingOffset + Vector3.up * 1.65f;
            if (!origin.MatchOriginUpCameraForward(Vector3.up, -viewingOffset.normalized) || !origin.MoveCameraToWorldLocation(desiredEye))
            { Debug.LogError("EEG_SCENE_RECENTER rejected reason=pose_alignment"); return; }
            if (PanelVisible) RecenterPanel();
            if (statusOverlay && statusOverlay.Visible) statusOverlay.Recenter();
            float error = Vector3.Angle(Vector3.ProjectOnPlane(headset.transform.forward, Vector3.up), -viewingOffset);
            bool unchanged = controller.transform.position == avatarPosition && controller.transform.rotation == avatarRotation;
            Debug.Log("EEG_SCENE_RECENTER applied source=runtime_origin_changed authority_unchanged=" + unchanged
                + " facing_error_deg=" + error.ToString("F2") + " eye_y=" + headset.transform.position.y.ToString("F3"));
        }
        void OnApplicationFocus(bool focused)
        {
            Debug.Log("EEG_XR_FOCUS source=application focused=" + focused);
            RefreshInteractionFocus();
        }
        void OnDisplayFocusChanged(bool focused)
        {
            DisplayFocused = focused;
            Debug.Log("EEG_XR_FOCUS source=display focused=" + focused);
            if (!focused && controller) controller.EmergencyStop();
            RefreshInteractionFocus();
        }
        void RefreshInteractionFocus()
        {
            if (statusOverlay) statusOverlay.SetVisible(false);
            HeadsetPanelDrag.CancelCurrent();
            for (int i = 0; i < 2; i++)
            {
                suppressPress[i] = true;
                if (rays[i]) rays[i].enableUIInteraction = InteractionFocused && PanelVisible;
            }
            // Pose driver, camera, origin and placed panel are deliberately preserved.
        }
        void OnDestroy()
        {
            if (display != null) display.displayFocusChanged -= OnDisplayFocusChanged;
            if (inputSubsystem != null) inputSubsystem.trackingOriginUpdated -= OnTrackingOriginUpdated;
            DisplayFocused = true;
        }
    }
}
