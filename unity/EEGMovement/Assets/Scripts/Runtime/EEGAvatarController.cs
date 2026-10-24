using System;
using System.IO;
using EEGMovement.Core;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.XR;

namespace EEGMovement.Unity
{
    public sealed class UnityJson : IJsonCodec
    {
        public T Decode<T>(string json) { return JsonUtility.FromJson<T>(json); }
        public string Encode(object value) { return JsonUtility.ToJson(value); }
    }
    [Serializable] public sealed class BuildIdentity { public string build_id = "unbuilt-editor"; public string app_version = "2.0.0"; }
    [RequireComponent(typeof(CharacterController))]
    public sealed class EEGAvatarController : MonoBehaviour
    {
        public Transform leftLeg, rightLeg;
        public GuidedAcquisition Acquisition;
        public SelfPacedAcquisition SelfPaced;
        [Serializable] sealed class IntentControlRequest {public bool enabled;}
        [Serializable] sealed class IntentControlReply {public bool enabled;}
        [Serializable] sealed class IntentStatus {public IntentControlReply control;}
        public string ModelStatus{get;private set;}="从中枢或本页启用实验动作；可随时急停。";
        bool modelRequest;
        public async void ToggleModelMotion()
        {
            if(modelRequest)return;
            if(Acquisition&&(Acquisition.Busy||Acquisition.Active)||SelfPaced&&(SelfPaced.Busy||SelfPaced.Active)){ModelStatus="采集中不执行模型动作";return;}
            modelRequest=true;
            try
            {
                string body=await SelfPacedAcquisition.Send("/api/intent/status");var state=JsonUtility.FromJson<IntentStatus>(body);bool enable=state.control==null||!state.control.enabled;
                if(enable)
                {
                    await SelfPacedAcquisition.Send("/api/intent/start","{}");
                    ResetEmergency();
                }
                else EmergencyStop();
                await SelfPacedAcquisition.Send("/api/intent/control",JsonUtility.ToJson(new IntentControlRequest{enabled=enable}));ModelStatus=enable?"实验模型已启用：满足阈值与质量门才会执行":"模型动作已关闭";
            }
            catch(Exception){ModelStatus="模型开关未确认，请检查中枢连接";EmergencyStop();}
            finally{modelRequest=false;}
        }
        public int receivePort = 19770;
        public readonly WorkspaceState Workspace = new WorkspaceState();
        public ReceiverState State { get; private set; }
        public string Issue { get; private set; }
        public bool ReduceMotion { get; set; }
        public bool ReduceTransparency { get; set; }
        public bool LargeText { get; set; }
        public RuntimeHealth Health { get; private set; } = new RuntimeHealth();
        ReceiverClient client;
        // Internal read-only identity for the explicit CLI safety validation; never serialized or logged.
        internal string PairingNonce { get { return client == null ? null : client.Core.Nonce; } }
        double lastYaw;
        Vector3 previous;
        bool paused;
        readonly float[] frames = new float[180], sorted = new float[180];
        int frameCount, frameCursor;
        float nextP95;
        string PairingPath()
        {
            if (Application.platform == RuntimePlatform.Android) return QuestNetwork.PairingPath;
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++) if (args[i] == "--eeg-config") return Path.GetFullPath(args[i + 1]);
            var directory = new DirectoryInfo(Application.dataPath);
            for (int i = 0; i < 8 && directory != null; i++, directory = directory.Parent)
            {
                string path = Path.Combine(directory.FullName, "hub/runtime/vr_pairing.json");
                if (File.Exists(path)) return path;
            }
            return Path.Combine(Application.persistentDataPath, "eeg_pairing.json");
        }
        void Start()
        {
            if (Application.platform == RuntimePlatform.Android) { gameObject.AddComponent<QuestNetwork>(); gameObject.AddComponent<QuestMirror>(); gameObject.AddComponent<QuestUpdater>(); }
            Application.targetFrameRate = 90; Application.runInBackground = true; Time.fixedDeltaTime = .02f;
            previous = transform.position; lastYaw = transform.eulerAngles.y;
            var identity = Resources.Load<TextAsset>("build_identity");
            var build = identity ? JsonUtility.FromJson<BuildIdentity>(identity.text) : new BuildIdentity();
            Health.app_version = build.app_version; Health.build_id = build.build_id;
            ReduceMotion = PlayerPrefs.GetInt("reduce_motion", 0) != 0;
            ReduceTransparency = PlayerPrefs.GetInt("reduce_transparency", 0) != 0;
            LargeText = PlayerPrefs.GetInt("large_text", 0) != 0;
            try
            {
                var options = ReceiverOptions.Parse(Environment.GetCommandLineArgs()); receivePort = options.Port;
                if (options.AllowTests) { Workspace.Navigate("link"); Workspace.SetAllowTests(true); }
                Connect(Workspace.AllowTests);
            }
            catch (ArgumentException) { Issue = "启动端口无效：--eeg-port 需要 1024–65535。接收器保持关闭。"; }
        }
        public void SavePreferences()
        {
            PlayerPrefs.SetInt("reduce_motion", ReduceMotion ? 1 : 0); PlayerPrefs.SetInt("reduce_transparency", ReduceTransparency ? 1 : 0);
            PlayerPrefs.SetInt("large_text", LargeText ? 1 : 0); PlayerPrefs.Save();
        }
        public void Reconnect() { Workspace.Reconnected(); Connect(false); if(Application.platform==RuntimePlatform.Android)QuestNetwork.RetryConnection(); }
        void Connect(bool allowTest)
        {
            if (client != null) { client.EmergencyStop(); client.Dispose(); client = null; }
            State = null;
            try
            {
                client = new ReceiverClient(PairingPath(), receivePort, "unity", allowTest, new UnityJson());
                if(Application.platform==RuntimePlatform.Android)QuestNetwork.AttachReceiver(client);
                client.Core.X = transform.position.x; client.Core.Z = transform.position.z; client.Core.Yaw = transform.eulerAngles.y;
                if (Workspace.EmergencyLatched) client.EmergencyStop();
                client.UpdateHealth(Health); State = client.Snapshot(); Issue = null;
            }
            catch (Exception) { Issue = "接收器尚未连接。请检查中枢配对文件与本机 19770 端口，再重新连接。"; client = null; }
        }
        public void Navigate(string page)
        {
            if(page!="collection"&&Acquisition)Acquisition.Abort("page_exit");
            bool wasAllowed = Workspace.AllowTests; Workspace.Navigate(page);
            if (wasAllowed && !Workspace.AllowTests) Connect(false);
        }
        public void AllowTests(bool allow) { Workspace.SetAllowTests(allow); Connect(Workspace.AllowTests); }
        public void EmergencyStop()
        {
            var calibration=GetComponent<PlacementCalibration>();
            if(calibration&&calibration.State!=null&&calibration.State.active)calibration.Cancel();
            if (Acquisition) Acquisition.Abort("emergency");
            if (SelfPaced) SelfPaced.Abort();
            Workspace.EmergencyStop(); if (client != null) { client.EmergencyStop(); State = client.Snapshot(); client.Pulse(); }
        }
        public void ResetEmergency()
        {
            Workspace.ResetEmergency();
            // A fresh receiver nonce invalidates every pre-reset packet; TEST is revoked.
            Connect(false); if (client != null) { client.ResetEmergency(); State = client.Snapshot(); }
        }
        void Update()
        {
            if (Keyboard.current != null && Keyboard.current.escapeKey.wasPressedThisFrame) EmergencyStop();
            Health.frame_ms = Mathf.Clamp(Time.unscaledDeltaTime * 1000, 0, 10000);
            frames[frameCursor++ % frames.Length] = (float)Health.frame_ms; frameCount = Mathf.Min(frameCount + 1, frames.Length);
            if (Time.unscaledTime >= nextP95 && frameCount > 0)
            {
                Array.Copy(frames, sorted, frameCount); Array.Sort(sorted, 0, frameCount);
                Health.frame_p95_ms = sorted[Mathf.Clamp(Mathf.CeilToInt(frameCount * .95f) - 1, 0, frameCount - 1)]; nextP95 = Time.unscaledTime + .5f;
            }
            var placement=GetComponent<PlacementCalibration>();
            bool requiresPlacement=WorkspaceSafety.RequiresPlacementRegistration(Application.platform==RuntimePlatform.Android,XRSettings.isDeviceActive);
            Health.focused = Application.isFocused && SpatialWorkspaceRig.DisplayFocused && (!requiresPlacement || (placement && placement.BootRegistered)); Health.paused = paused; Health.xr_active = XRSettings.isDeviceActive;
            Health.ui_state = SelfPaced&&SelfPaced.Active ? "collection:self_paced:"+(SelfPaced.Held??"UNKNOWN") : Acquisition&&Acquisition.Immersive ? "collection:white:"+Acquisition.PhaseName : Workspace.EmergencyLatched ? "emergency" : Workspace.Page + (Workspace.AllowTests ? ":test_opt_in" : ":live");
            if (client != null) { client.UpdateHealth(Health); State = client.Snapshot(); }
        }
        public void ConfirmRenderedFrame() { Health.rendered_frames++; }
        void FixedUpdate()
        {
            if (client == null) return;
            var placement=GetComponent<PlacementCalibration>();
            bool requiresPlacement=WorkspaceSafety.RequiresPlacementRegistration(Application.platform==RuntimePlatform.Android,XRSettings.isDeviceActive);
            client.BeginFrame(Time.fixedDeltaTime,!requiresPlacement||(placement&&placement.BootRegistered));
            var core = client.Core;
            Vector3 wanted = new Vector3((float)core.X, 0, (float)core.Z);
            GetComponent<CharacterController>().Move(wanted - transform.position + Vector3.down * .02f);
            transform.rotation = Quaternion.Euler(0, (float)core.Yaw, 0);
            Vector3 delta = transform.position - previous;
            double measuredSpeed = Vector3.Dot(delta, transform.forward) / Time.fixedDeltaTime;
            double measuredYaw = Mathf.DeltaAngle((float)lastYaw, transform.eulerAngles.y) / Time.fixedDeltaTime;
            client.ConfirmFrame(transform.position.x, transform.position.z, transform.eulerAngles.y, measuredSpeed, measuredYaw);
            previous = transform.position; lastYaw = transform.eulerAngles.y;
            client.Pulse(); State = client.Snapshot();
            float swing = ReduceMotion ? 0 : Mathf.Sin(Time.time * 9) * Mathf.Clamp01(Mathf.Abs((float)measuredSpeed) / .6f) * 24;
            if (leftLeg) leftLeg.localRotation = Quaternion.Euler(swing, 0, 0);
            if (rightLeg) rightLeg.localRotation = Quaternion.Euler(-swing, 0, 0);
        }
        void OnApplicationPause(bool value) { paused = value; if (value) EmergencyStop(); }
        void OnApplicationFocus(bool focused) { if (!focused && Application.platform == RuntimePlatform.Android) EmergencyStop(); }
        void OnDestroy() { if (client != null) client.Dispose(); }
    }
}
