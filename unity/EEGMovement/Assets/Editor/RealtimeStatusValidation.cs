using System;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.UI;
using EEGMovement.Unity;

// Explicit batch-mode check of the real JSON parser and panel text. No production API calls.
public static class RealtimeStatusValidation
{
    public static void Validate()
    {
        var root = new GameObject("Isolated status contract test");
        try
        {
            var overlay = root.AddComponent<QuestStatusOverlay>();
            var kind = typeof(QuestStatusOverlay);
            var fields = new[] { "freshness", "model", "probabilities", "fusion", "signal", "compute", "execution", "training" };
            foreach (string field in fields)
            {
                var child = new GameObject(field, typeof(RectTransform), typeof(Text));
                child.transform.SetParent(root.transform);
                kind.GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(overlay, child.GetComponent<Text>());
            }
            kind.GetField("panel", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(overlay, root);
            string json = "{\"version\":1,\"source\":{\"fresh\":true,\"sample_rate\":250,\"quality_valid\":true,\"good_channels\":6,\"channels\":[{\"name\":\"CH1\",\"color\":\"green\",\"quality_label\":\"良好\",\"training_eligible\":true},{\"name\":\"CH6\",\"color\":\"red\",\"quality_label\":\"禁用\",\"training_eligible\":false}]},\"model\":{\"name\":\"vr_fit80-20261017-033136-seed17\",\"status\":\"experimental_loaded\",\"inference_enabled\":true,\"inference_state\":\"running\",\"parameters\":70087769,\"candidate_kind\":\"normalized_nonlinear_hierarchical_jepa\",\"training_accuracy\":0.96085,\"development_accuracy\":0.1511,\"compute_ms\":12.3,\"roundtrip_ms\":42.5,\"evidence_ms\":1187.5,\"hop_ms\":100,\"result_fresh\":true,\"result_age_ms\":10,\"active_probability\":0.82,\"idle_probability\":0.18,\"direction_probabilities\":[0.46,0.04,0.46,0.04],\"usable_channels\":[0,1,2,3,4,6]},\"control\":{\"enabled\":true,\"fresh\":true,\"active_threshold\":0.7,\"direction_threshold\":0.6,\"decision\":{\"active\":true,\"action_label\":\"FORWARD_LEFT\",\"p_active\":0.82}},\"server\":{\"connected\":true,\"device\":\"fixture GPU\"},\"execution\":{\"known\":true,\"state\":\"STOP\",\"mode\":\"LIVE\"},\"training\":{},\"acquisition\":{}}";
            var status = JsonUtility.FromJson<QuestStatusOverlay.Status>(json);
            kind.GetField("latest", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(overlay, status);
            var received = kind.GetField("receivedAt", BindingFlags.Instance | BindingFlags.NonPublic);
            var update = kind.GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic);
            Action refresh = () => { received.SetValue(overlay, Time.unscaledTime); update.Invoke(overlay, null); };
            Func<string> text = () => { string s=""; foreach (var label in root.GetComponentsInChildren<Text>()) s+=label.text+"\n"; return s; };
            Action<bool,string> check = (valid,message) => { if(!valid)throw new Exception(message+"\n"+text()); };
            refresh();
            check(text().Contains("70.09M") && text().Contains("运行中"), "New model identity and inference state absent");
            check(text().Contains("96.1%") && text().Contains("15.1%") && text().Contains("跨记录"), "Training fit and development caveat absent");
            check(text().Contains("CH6") && text().Contains("屏蔽") && text().Contains("CH1"), "Per-channel quality or mask absent");
            check(text().Contains("82.0%") && text().Contains("前进 + 左转") && text().Contains("42.5"), "Live probability/fusion/latency contract absent");
            status.model.result_age_ms=600;refresh();check(!text().Contains("82.0%") && !text().Contains("前进 + 左转"), "Expired scores reused");
            status.model.result_age_ms=10;
            var enabled=status.model.GetType().GetField("inference_enabled");enabled.SetValue(status.model,false);refresh();
            check(!text().Contains("82.0%"), "Stopped inference reused old scores");
            received.SetValue(overlay, Time.unscaledTime-2);update.Invoke(overlay,null);
            check(text().Contains("旧数据已失效") && !text().Contains("82.0%"), "Transport stale values reused");
            string output=Path.GetFullPath(Path.Combine(Application.dataPath,"../../../outputs/realtime_status_editor_validation.json"));
            File.WriteAllText(output,"{\"success\":true,\"json_parser\":true,\"model_identity\":true,\"quality_mask\":true,\"fit_vs_development\":true,\"latency\":true,\"expired_hidden\":true,\"inference_stopped_hidden\":true,\"transport_stale_hidden\":true}");
            Debug.Log("EEG_REALTIME_STATUS_VALIDATED");
        }
        finally { UnityEngine.Object.DestroyImmediate(root); }
    }
}
