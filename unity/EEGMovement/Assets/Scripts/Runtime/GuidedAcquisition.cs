using System;
using System.Collections;
using UnityEngine;
using System.Net.Http;
using System.Threading.Tasks;
using UnityEngine.XR;

namespace EEGMovement.Unity
{
    // Explicit operator-run cue collection. No automatic recording or motion is started.
    public sealed class GuidedAcquisition : MonoBehaviour
    {
        [Serializable] public sealed class Trial { public int trial, block, prepare_ms, cue_ms, task_ms, rest_ms; public string label,instruction,experiment_mode,gaze_instruction,limb_instruction; public bool neutral_task_display; }
        [Serializable] sealed class Session { public bool active; public string session_id, recording; public Trial[] trials; }
        [Serializable] sealed class Sync { public double hub_monotonic_ms; }
        [Serializable] sealed class StartRequest { public string build_id, paradigm = "guided_motor_imagery", presentation_protocol="gnautilus_hybrid_neutral_v1"; public int blocks = 4; public int target_seconds=780; }
        [Serializable] sealed class StopRequest { public string session_id, reason; }
        [Serializable] sealed class Ack { public bool ok; public int ack_seq; }
        [Serializable] sealed class Marker
        {
            public string session_id, label, phase;
            public int seq, trial, sender_frame;
            public double sender_monotonic_ms, clock_offset_ms, sync_rtt_ms;
            public float[] head_pose;
            public bool xr_active;
        }
        const string Base = "http://127.0.0.1:8765";
        public EEGAvatarController Controller;
        public bool Busy { get; private set; }
        public bool Active { get; private set; }
        public bool Paused { get; private set; }
        public string Status { get; private set; } = "未开始 · 原始脑电与事件将保存在电脑中枢";
        public string Prompt { get; private set; } = "开始后显示白底居中指令，按A结束并保存。";
        public string Fixation { get; private set; } = "";
        public bool Immersive { get { return (Busy || Active) && !cancelled; } }
        public string PhaseName { get; private set; } = "idle";
        public int Saved { get; private set; }
        public float Progress { get; private set; }
        public string Recording { get; private set; }
        public float PhaseRemainingSeconds { get; private set; }
        public float TotalRemainingSeconds { get { return Mathf.Max(0,(float)((plannedMilliseconds-heldMilliseconds)/1000)); } }
        public int CurrentTrial {get;private set;}
        public int TrialCount {get;private set;}
        public string PhaseTitle {get { return Paused?"已暂停 · 计时停止":PhaseName=="prepare"?"准备下一动作":PhaseName=="cue"?"确认动作":PhaseName=="task"?"保持当前动作":PhaseName=="rest"?"休息 · 不计动作标签":"正在准备"; }}
        public string TimingText {get { return "当前剩余 "+Clock(PhaseRemainingSeconds)+"    总计还需 "+Clock(TotalRemainingSeconds); }}
        public static string Clock(float seconds){int n=Mathf.CeilToInt(Mathf.Max(0,seconds));return (n/60).ToString("00")+":"+(n%60).ToString("00");}
        double plannedMilliseconds,heldMilliseconds;
        bool cancelled;
        string stopReason = "operator_stop", error, response, sessionId;
        double offset, rtt;
        int sequence;
        static double Now { get { return Time.realtimeSinceStartupAsDouble * 1000; } }
        public void Begin(int blocks = 2)
        {
            if (Busy || Active) return;
            if(!GetComponent<AcquisitionPresentation>())gameObject.AddComponent<AcquisitionPresentation>().Owner=this;
            cancelled = false; Paused = false; Saved = sequence = 0; sessionId = null;heldMilliseconds=plannedMilliseconds=0;PhaseRemainingSeconds=0;Progress=0;
            Controller.AllowTests(false); Controller.EmergencyStop();
            StartCoroutine(Run(Mathf.Clamp(blocks, 1, 20)));
        }
        public void TogglePause() { if (Active) Paused = !Paused; }
        public void Abort(string reason = "operator_stop")
        {
            if (!Busy && !Active) return;
            cancelled = true; Paused = false; stopReason = reason; Fixation = "";
            Prompt = "采集正在结束，已确认的事件与原始脑电会保留。";
        }
        sealed class Reply { public string body, failure; }
        static readonly HttpClient LocalClient = new HttpClient(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(3) };
        static async Task<Reply> SendLocal(string path, string json)
        {
            try
            {
                if (QuestNetwork.Ready) return new Reply { body = await QuestNetwork.Send(path.Replace("/api/", "/v1/"), json == null ? null : System.Text.Encoding.UTF8.GetBytes(json)).ConfigureAwait(false) };
                if (Application.platform == RuntimePlatform.Android) return new Reply { failure = "请先配置头显与电脑中枢的配对信息" };
                using (var message = new HttpRequestMessage(json == null ? HttpMethod.Get : HttpMethod.Post, Base + path))
                {
                    if (json != null) { message.Content = new StringContent(json, System.Text.Encoding.UTF8, "application/json"); message.Headers.Add("X-EEGHub", "1"); }
                    using (var result = await LocalClient.SendAsync(message).ConfigureAwait(false))
                    {
                        string body = await result.Content.ReadAsStringAsync().ConfigureAwait(false);
                        if (body.Length > 65536) return new Reply { failure = "中枢响应超出限制" };
                        return result.IsSuccessStatusCode ? new Reply { body = body } : new Reply { failure = "中枢请求未确认：" + (int)result.StatusCode };
                    }
                }
            }
            catch (Exception) { return new Reply { failure = "无法连接中枢；请检查配对、网络和g.Nautilus数据接收" }; }
        }
        IEnumerator Request(string path, string json = null)
        {
            error = null; response = null;
            var pending = SendLocal(path, json);
            while (!pending.IsCompleted) yield return null;
            if (pending.IsFaulted || pending.IsCanceled) error = "本机中枢请求未完成";
            else { error = pending.Result.failure; response = pending.Result.body; }
        }
        IEnumerator Mark(Trial trial, string phase)
        {
            yield return new WaitForEndOfFrame();
            var camera = Camera.main;
            if (XRSettings.isDeviceActive)
                foreach (var candidate in FindObjectsByType<Camera>(FindObjectsSortMode.None))
                    if (candidate.enabled && candidate.name == "XR camera") { camera = candidate; break; }
            var position = camera ? camera.transform.position : Vector3.zero;
            var rotation = camera ? camera.transform.rotation : Quaternion.identity;
            var marker = new Marker { session_id = sessionId, seq = ++sequence, trial = trial.trial, label = trial.label, phase = phase,
                sender_monotonic_ms = Now, sender_frame = Time.frameCount, clock_offset_ms = offset, sync_rtt_ms = rtt,
                xr_active = XRSettings.isDeviceActive,
                head_pose = new[] { position.x, position.y, position.z, rotation.x, rotation.y, rotation.z, rotation.w } };
            string payload = JsonUtility.ToJson(marker);
            for (int attempt = 0; attempt < 3; attempt++)
            {
                yield return Request("/api/acquisition/event", payload);
                if (error == null)
                {
                    var ack = JsonUtility.FromJson<Ack>(response);
                    if (ack.ok && ack.ack_seq == marker.seq) { Saved = ack.ack_seq; yield break; }
                    error = "中枢事件序号未确认";
                }
                if (attempt < 2) yield return new WaitForSecondsRealtime(.15f);
            }
            Status = error; cancelled = true; stopReason = "transport_error";
        }
        IEnumerator Hold(Trial trial, int milliseconds)
        {
            double remaining = milliseconds, before = Now, heartbeat=Now;PhaseRemainingSeconds=milliseconds/1000f;
            while (remaining > 0 && !cancelled)
            {
                if (Paused)
                {
                    string previousPrompt=Prompt;Prompt = "已暂停"; Fixation = "";
                    yield return Mark(trial, "pause");
                    heartbeat = Now;
                    while (Paused && !cancelled)
                    {
                        if (Now - heartbeat >= 2000) { yield return Mark(trial, "heartbeat"); heartbeat = Now; }
                        yield return null;
                    }
                    if (cancelled) yield break;
                    yield return Mark(trial, "resume");
                    Prompt = previousPrompt; before = heartbeat = Now;
                }
                yield return null; double now = Now;double delta=Math.Min(remaining,Math.Max(0,now-before));remaining-=delta;heldMilliseconds+=delta;before=now;
                PhaseRemainingSeconds=(float)Math.Max(0,remaining)/1000f;Progress=plannedMilliseconds>0?Mathf.Clamp01((float)(heldMilliseconds/plannedMilliseconds)):0;
                // Long instructed holds must renew the sender lease too. Without
                // this, a 12s task can exceed the Hub's 10s sender timeout.
                if(!cancelled&&now-heartbeat>=2000){yield return Mark(trial,"heartbeat");before=heartbeat=Now;}
            }
        }
        IEnumerator Phase(Trial trial, string phase, int milliseconds, string prompt, string fixation)
        {
            Prompt = prompt; Fixation = "";PhaseName=phase;PhaseRemainingSeconds=milliseconds/1000f;
            yield return Mark(trial, phase);
            if (!cancelled) yield return Hold(trial, milliseconds);
        }
        static string Label(string label)
        {
            switch (label) { case "FORWARD": return "想象向前移动"; case "BACKWARD": return "想象向后移动"; case "LEFT": return "想象向左转"; case "RIGHT": return "想象向右转";case "FORWARD_LEFT": return "想象前进并左转";case "FORWARD_RIGHT": return "想象前进并右转";case "BACKWARD_LEFT": return "想象后退并左转";case "BACKWARD_RIGHT": return "想象后退并右转"; default: return "保持静止"; }
        }
        static string Symbol(string label)
        { switch (label) { case "FORWARD": return "↑"; case "BACKWARD": return "↓"; case "LEFT": return "←"; case "RIGHT": return "→"; default: return "+"; } }
        IEnumerator Run(int blocks)
        {
            Busy = true; Prompt="准备";PhaseName="sync";Status = "同步中枢时钟…"; rtt = double.PositiveInfinity;
            for (int i = 0; i < 5 && !cancelled; i++)
            {
                double start = Now; yield return Request("/api/acquisition/sync"); double end = Now;
                if (error != null) { Status = error; Busy = false; yield break; }
                var sync = JsonUtility.FromJson<Sync>(response);
                if (end - start < rtt) { rtt = end - start; offset = sync.hub_monotonic_ms - (start + end) / 2; }
            }
            if (cancelled) { Busy = false; yield break; }
            Status = "请求原始脑电会话…";
            yield return Request("/api/acquisition/start", JsonUtility.ToJson(new StartRequest { build_id = Controller.Health.build_id, blocks = blocks }));
            if (error != null) { Status = error; Busy = false; yield break; }
            var session = JsonUtility.FromJson<Session>(response); sessionId = session.session_id; Recording = session.recording;
            if (!session.active || String.IsNullOrEmpty(sessionId) || session.trials == null || session.trials.Length < 9)
            { Status = "中枢任务表无效"; Busy = false; yield break; }
            Active = true;
            TrialCount=session.trials.Length;plannedMilliseconds=0;
            foreach(var item in session.trials)plannedMilliseconds+=item.prepare_ms+item.cue_ms+item.task_ms+item.rest_ms;
            foreach (var trial in session.trials)
            {
                if (cancelled) break;
                Status = "试次 " + (trial.trial + 1) + " / " + session.trials.Length + " · 已保存事件 " + Saved;
                CurrentTrial=trial.trial+1;
                string instruction=string.IsNullOrEmpty(trial.instruction)?Label(trial.label):trial.instruction;
                yield return Phase(trial, "prepare", trial.prepare_ms, "准备："+instruction, "");
                if (cancelled) break;
                yield return Phase(trial, "cue", trial.cue_ms, instruction, "");
                if (cancelled) break;
                yield return Phase(trial, "task", trial.task_ms, trial.neutral_task_display?"":instruction, "");
                if (cancelled) break;
                yield return Phase(trial, "rest", trial.rest_ms, "放松休息", "");
                Progress = (trial.trial + 1f) / session.trials.Length;
            }
            string reason = cancelled ? stopReason : "completed";
            yield return Request("/api/acquisition/stop", JsonUtility.ToJson(new StopRequest { session_id = sessionId, reason = reason }));
            Active = Busy = Paused = false; Fixation = "";PhaseName="idle";PhaseRemainingSeconds=0;
            Status = error != null ? "结束请求未确认；中枢会在发送超时后关闭，已确认事件已保存。" : reason == "completed" ? "本区组采集完成 · " + Saved + " 个事件已确认保存" : "采集已中止 · " + Saved + " 个事件已保存";
            Prompt = "文件：" + Recording + "\n提示标签不是解码结果；未核验数据不能直接用于正式训练。";
        }
        void OnApplicationPause(bool paused) { if (paused) Abort("page_exit"); }
        void OnApplicationQuit() { Abort("page_exit"); } // Hub sender timeout closes a session if process exits before HTTP completion.
    }
}
