using System;
using System.IO;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using Unity.XR.CoreUtils;
using UnityEngine.InputSystem.XR;

namespace EEGMovement.Unity
{
    // This finite validation runner exists only after an explicit operator command-line flag.
    // Normal mode creates no instance, control endpoint, background test or synthetic motion.
    public sealed class RuntimeSafetyValidation : MonoBehaviour
    {
        [Serializable] sealed class Case { public string name; public bool passed; public string detail; }
        [Serializable] sealed class Result
        {
            public int version = 1;
            public string build_id, checked_at;
            public bool success;
            public long rendered_frames;
            public List<Case> cases;
        }
        string destination;
        EEGAvatarController controller;
        readonly List<Case> cases = new List<Case>();
        int bindingWarnings;
        bool finished;
        float started;
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        static void EnableIfRequested()
        {
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length; i++) if (args[i] == "--eeg-validate-safety")
            {
                if (i + 1 >= args.Length || args[i + 1].StartsWith("--", StringComparison.Ordinal) || args[i + 1].Length > 1024)
                { Debug.LogError("EEG safety validation requires an explicit output JSON path."); Application.Quit(2); return; }
                var go = new GameObject("Explicit operator safety validation");
                var validation = go.AddComponent<RuntimeSafetyValidation>();
                validation.destination = Path.GetFullPath(args[i + 1]); return;
            }
        }
        void OnEnable() { started = Time.realtimeSinceStartup; Application.logMessageReceived += InspectWarning; }
        void OnDisable() { Application.logMessageReceived -= InspectWarning; }
        void InspectWarning(string condition, string stackTrace, LogType type)
        {
            if (condition.Contains("No Camera Floor Offset GameObject") || condition.Contains("does not use a Tracked Pose Driver")) bindingWarnings++;
        }
        void Add(string name, bool passed, string detail) { cases.Add(new Case { name = name, passed = passed, detail = detail }); }
        IEnumerator Start()
        {
            yield return null;
            for (int i = 0; i < 3; i++) yield return new WaitForFixedUpdate();
            controller = FindFirstObjectByType<EEGAvatarController>();
            Add("controller_available", controller != null, "Actual scene controller, without pointer or keyboard input.");
            if (!controller) { Finish(); yield break; }
            var workspaceCanvas = FindFirstObjectByType<UnityEngine.Canvas>();
            var scaler = workspaceCanvas ? workspaceCanvas.GetComponent<UnityEngine.UI.CanvasScaler>() : null;
            Add("layout_preserves_safe_logical_bounds", scaler && scaler.screenMatchMode == UnityEngine.UI.CanvasScaler.ScreenMatchMode.Expand && scaler.referenceResolution.x >= 1440 && scaler.referenceResolution.y >= 900, "Expand reserves the full reference layout for narrow and wide windows; controls scale without overlapping.");
            string priorNonce = controller.PairingNonce;
            Add("receiver_initialized", !String.IsNullOrEmpty(priorNonce), "Private pairing data is never included in this report.");
            var origin = FindFirstObjectByType<XROrigin>();
            var driver = origin && origin.Camera ? origin.Camera.GetComponent<TrackedPoseDriver>() : null;
            Add("xr_camera_bound_before_awake", origin && origin.Camera && origin.Camera.name == "XR camera" && origin.CameraFloorOffsetObject && origin.CameraFloorOffsetObject != origin.gameObject && origin.Camera.transform.IsChildOf(origin.CameraFloorOffsetObject.transform), "Independent headset camera and floor-offset hierarchy.");
            Add("xr_before_render_tracking", driver && driver.positionInput.action != null && driver.rotationInput.action != null && driver.trackingStateInput.action != null && driver.updateType == TrackedPoseDriver.UpdateType.UpdateAndBeforeRender && !driver.ignoreTrackingState, "Official Input System driver owns headset pose and respects tracking validity.");
            Add("xr_startup_binding_warnings_absent", bindingWarnings == 0, "No floor-offset or missing-pose-driver startup warnings.");
            controller.EmergencyStop();
            Add("emergency_immediate", controller.Workspace.EmergencyLatched && !controller.Workspace.AllowTests, "Actual controller method immediately locks movement and revokes TEST.");
            for (int i = 0; i < 3; i++) yield return new WaitForFixedUpdate();
            var stopped = controller.State;
            Add("emergency_actual_stop", stopped != null && Math.Abs(stopped.actual_speed) < .0001 && Math.Abs(stopped.actual_yaw_rate) < .0001 && stopped.target_speed == 0 && stopped.target_yaw_rate == 0, "Applied-pose telemetry confirms zero after fixed frames.");
            controller.ResetEmergency();
            string resetNonce = controller.PairingNonce;
            Add("reset_fresh_identity", !String.IsNullOrEmpty(priorNonce) && !String.IsNullOrEmpty(resetNonce) && resetNonce != priorNonce, "Only the identity-change boolean is reported.");
            Add("reset_no_opt_in", !controller.Workspace.EmergencyLatched && !controller.Workspace.AllowTests, "Actual reset keeps TEST permission disabled.");
            for (int i = 0; i < 3; i++) yield return new WaitForFixedUpdate();
            var reset = controller.State;
            Add("reset_actual_stop", reset != null && Math.Abs(reset.actual_speed) < .0001 && Math.Abs(reset.actual_yaw_rate) < .0001 && reset.target_speed == 0 && reset.target_yaw_rate == 0, "Reset alone does not start applied movement.");
            Finish();
        }
        void Update()
        {
            if (!finished && Time.realtimeSinceStartup - started > 15)
            { Add("bounded_completion", false, "Validation exceeded 15 seconds."); Finish(); }
        }
        void Finish()
        {
            if (finished) return; finished = true;
            if (controller) controller.EmergencyStop();
            bool success = cases.Count >= 10; foreach (var item in cases) success &= item.passed;
            var result = new Result { build_id = controller ? controller.Health.build_id : "", checked_at = DateTime.UtcNow.ToString("o"), success = success, rendered_frames = controller ? controller.Health.rendered_frames : 0, cases = cases };
            try
            {
                string json = JsonUtility.ToJson(result, true);
                if (json.Length > 16384) throw new InvalidOperationException();
                Directory.CreateDirectory(Path.GetDirectoryName(destination)); File.WriteAllText(destination, json);
                Debug.Log(success ? "EEG_SAFETY_VALIDATION_PASS" : "EEG_SAFETY_VALIDATION_FAIL");
            }
            catch (Exception) { success = false; Debug.LogError("EEG safety validation report could not be written."); }
            Application.Quit(success ? 0 : 2);
        }
    }
}
