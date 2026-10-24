using System;
using System.Collections;
using System.IO;
using UnityEngine;

namespace EEGMovement.Unity
{
    // Explicit CLI integration run of the real VR collection component, without pointer/keyboard input.
    public sealed class AcquisitionValidation : MonoBehaviour
    {
        [Serializable] sealed class Result
        {
            public bool success; public string checked_at, build_id, recording, status;
            public int confirmed_events; public long rendered_frames;
            public bool actual_motion_stopped, no_live_model;
        }
        string destination;
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        static void EnableIfRequested()
        {
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i + 1 < args.Length; i++) if (args[i] == "--eeg-validate-acquisition")
            { var runner = new GameObject("Explicit VR acquisition validation").AddComponent<AcquisitionValidation>(); runner.destination = Path.GetFullPath(args[i + 1]); return; }
        }
        IEnumerator Start()
        {
            yield return null; yield return null;
            var controller = FindFirstObjectByType<EEGAvatarController>();
            var ui = FindFirstObjectByType<WorkspaceUI>(); ui.Show("collection");
            var collection = FindFirstObjectByType<GuidedAcquisition>();
            collection.Begin(1);
            float deadline = Time.realtimeSinceStartup + 100;
            bool stopped = true;
            while (collection.Busy && Time.realtimeSinceStartup < deadline)
            {
                var state = controller.State;
                stopped &= state == null || (Math.Abs(state.actual_speed) < .005 && Math.Abs(state.actual_yaw_rate) < .005);
                yield return null;
            }
            if (collection.Busy) collection.Abort("transport_error");
            var result = new Result { success = !collection.Busy && collection.Saved == 20 && collection.Progress >= .99f && stopped,
                checked_at = DateTime.UtcNow.ToString("o"), build_id = controller.Health.build_id, recording = collection.Recording,
                status = collection.Status, confirmed_events = collection.Saved, rendered_frames = controller.Health.rendered_frames,
                actual_motion_stopped = stopped, no_live_model = true };
            Directory.CreateDirectory(Path.GetDirectoryName(destination)); File.WriteAllText(destination, JsonUtility.ToJson(result, true));
            Debug.Log(result.success ? "EEG_ACQUISITION_VALIDATION_PASS" : "EEG_ACQUISITION_VALIDATION_FAIL"); Application.Quit(result.success ? 0 : 2);
        }
    }
}
