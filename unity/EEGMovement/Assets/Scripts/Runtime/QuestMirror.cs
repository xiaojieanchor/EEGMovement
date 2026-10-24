using System;
using System.Collections;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Rendering;

namespace EEGMovement.Unity
{
    // App-only center-eye spectator camera. No passthrough, OS UI or microphone capture.
    public sealed class QuestMirror : MonoBehaviour
    {
        Camera spectator;
        RenderTexture target;
        Texture2D pixels;
        readonly byte[] raw = new byte[960 * 540 * 3];
        volatile bool busy;
        Task<byte[]> encoding;
        Task<string> sending;
        string lastIssue;
        bool sentLogged;
        public static string Issue { get; private set; }
        IEnumerator Start()
        {
            target = new RenderTexture(960, 540, 16, RenderTextureFormat.ARGB32) { antiAliasing = 1 };
            target.Create(); pixels = new Texture2D(960, 540, TextureFormat.RGB24, false);
            var cameraObject = new GameObject("Quest LAN spectator · 960x540 bounded preview");
            spectator = cameraObject.AddComponent<Camera>(); spectator.enabled = false;
            while (true)
            {
                yield return new WaitForSecondsRealtime(QuestNetwork.IsPublic?1f:.167f);
                if (!QuestNetwork.Ready || busy || !UnityEngine.XR.XRSettings.isDeviceActive) continue;
                Camera source = null;
                foreach (var candidate in Camera.allCameras)
                    if (candidate != spectator && candidate.enabled && candidate.stereoTargetEye != StereoTargetEyeMask.None) { source = candidate; break; }
                if (!source) { ReportIssue("camera_unavailable"); continue; }
                yield return new WaitForEndOfFrame();
                busy = true;
                spectator.CopyFrom(source); spectator.enabled = false; spectator.stereoTargetEye = StereoTargetEyeMask.None;
                spectator.targetTexture = target; spectator.transform.SetPositionAndRotation(source.transform.position, source.transform.rotation);
                spectator.ResetProjectionMatrix(); spectator.aspect = 960f / 540; spectator.fieldOfView = 75;
                spectator.Render();
                if (SystemInfo.supportsAsyncGPUReadback)
                    AsyncGPUReadback.Request(target, 0, TextureFormat.RGB24, result => {
                        if (!this) return;
                        if (result.hasError) { busy = false; ReportIssue("gpu_readback_failed"); return; }
                        result.GetData<byte>().CopyTo(raw); SendFrame();
                    });
                else
                {
                    var previous = RenderTexture.active; RenderTexture.active = target;
                    pixels.ReadPixels(new Rect(0, 0, 960, 540), 0, 0, false); RenderTexture.active = previous;
                    pixels.GetRawTextureData<byte>().CopyTo(raw); SendFrame();
                }
            }
        }
        void SendFrame()
        {
            // Only JPEG encoding runs on the pool. Sending crosses the Android
            // Java bridge and must begin from Unity's attached main thread.
            encoding = Task.Run(() =>
            {
                byte[] jpeg = ImageConversion.EncodeArrayToJPG(raw, UnityEngine.Experimental.Rendering.GraphicsFormat.R8G8B8_UNorm, 960, 540, 0, 75);
                if (jpeg.Length > 262144) throw new InvalidOperationException("Preview frame exceeded bound");
                return jpeg;
            });
        }
        void Update()
        {
            if (encoding != null && encoding.IsCompleted)
            {
                var completed = encoding; encoding = null;
                if (completed.IsFaulted || completed.IsCanceled)
                {
                    ReportIssue("jpeg_" + (completed.Exception?.GetBaseException().GetType().Name ?? "cancelled")); busy = false;
                }
                else
                {
                    try { sending = QuestNetwork.Send("/v1/mirror", completed.Result, "image/jpeg"); }
                    catch (Exception e) { ReportIssue("send_" + e.GetType().Name); busy = false; }
                }
            }
            if (sending != null && sending.IsCompleted)
            {
                var completed = sending; sending = null; busy = false;
                if (completed.IsFaulted || completed.IsCanceled) ReportIssue("send_" + (completed.Exception?.GetBaseException().GetType().Name ?? "cancelled"));
                else
                {
                    Issue = null; lastIssue = null;
                    if (!sentLogged) { sentLogged = true; Debug.Log("EEG_MIRROR stage=transport_accepted width=960 height=540"); }
                }
            }
        }
        void ReportIssue(string stage)
        {
            Issue = "画面回传未确认：" + stage;
            if (lastIssue != stage) { lastIssue = stage; Debug.Log("EEG_MIRROR stage=" + stage); }
        }
        void OnDestroy()
        {
            AsyncGPUReadback.WaitAllRequests();
            if (spectator) Destroy(spectator.gameObject);
            if (target) { target.Release(); Destroy(target); }
            if (pixels) Destroy(pixels);
        }
    }
}
