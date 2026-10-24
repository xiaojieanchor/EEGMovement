using System;
using System.Collections;
using System.Collections.Generic;
using System.Net.Http;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.XR;
using UnityEngine.XR.Interaction.Toolkit.UI;
namespace EEGMovement.Unity
{
    // Held buttons mark instructions only. They never supply movement commands.
    public sealed class SelfPacedAcquisition : MonoBehaviour
    {
        [Serializable] sealed class StartRequest {public string build_id,paradigm="self_paced_motor_imagery",presentation_protocol="self_paced_axes_v2",task_guidance_version="gaze_navigation_v2";}
        [Serializable] sealed class TaskInstruction {public string label,instruction,gaze_instruction;}
        [Serializable] sealed class Session {public bool active;public string session_id,recording,experiment_mode,task_binding_version;public string[] labels;public TaskInstruction[] task_instructions_list;}
        [Serializable] sealed class Sync {public double hub_monotonic_ms;}
        [Serializable] sealed class StopRequest {public string session_id,reason="completed";}
        [Serializable] sealed class Ack {public bool ok;public int ack_seq;}
        [Serializable] sealed class Marker {public string session_id,label,phase,pointer_source;public int seq,sender_frame;public double sender_monotonic_ms,clock_offset_ms,sync_rtt_ms;public float[] head_pose,controller_pose;public bool xr_active;}
        [Serializable] sealed class Control {public bool enabled;}
        public EEGAvatarController Controller;
        public Canvas InputCanvas { get { return surface?surface.GetComponent<Canvas>():null; } }
        public bool Active{get;private set;} public bool Busy{get;private set;} public string Status{get;private set;}="按住一个方向或中央静止键，松开即结束这一段。";
        public string Recording{get;private set;} public int Saved{get;private set;} public string Held{get;private set;}
        string sessionId,error,response;int sequence,pointer;bool stop,failed;double offset,rtt,nextHeartbeat;float began;
        Session currentSession;GameObject surface;Text statusText,guidanceText;readonly Queue<Marker> queue=new Queue<Marker>();readonly Dictionary<Camera,int> masks=new Dictionary<Camera,int>();
        static readonly HttpClient client=new HttpClient(new HttpClientHandler{UseProxy=false}){Timeout=TimeSpan.FromSeconds(2)};
        static bool ValidationMode{get{return Array.IndexOf(Environment.GetCommandLineArgs(),"--eeg-validate-selfpaced")>=0||Array.IndexOf(Environment.GetCommandLineArgs(),"--eeg-validate-status")>=0;}}
        static int ApiPort()
        {
            if(ValidationMode){var args=Environment.GetCommandLineArgs();for(int i=0;i+1<args.Length;i++)if(args[i]=="--eeg-validation-api-port"){int p;if(Int32.TryParse(args[i+1],out p)&&p>=1024&&p<=65535)return p;throw new InvalidOperationException("Invalid validation API port");}}
            return 8765;
        }
        public static async Task<string> Send(string path,string json=null)
        {
            if(QuestNetwork.Ready)return await QuestNetwork.Send(path.Replace("/api/","/v1/"),json==null?null:System.Text.Encoding.UTF8.GetBytes(json)).ConfigureAwait(false);
            if(Application.platform==RuntimePlatform.Android)throw new InvalidOperationException("请先配置头显与电脑中枢的配对信息");
            using(var request=new HttpRequestMessage(json==null?HttpMethod.Get:HttpMethod.Post,"http://127.0.0.1:"+ApiPort()+path))
            {if(json!=null){request.Content=new StringContent(json,System.Text.Encoding.UTF8,"application/json");request.Headers.Add("X-EEGHub","1");}
                using(var result=await client.SendAsync(request).ConfigureAwait(false)){string body=await result.Content.ReadAsStringAsync().ConfigureAwait(false);if(!result.IsSuccessStatusCode)throw new InvalidOperationException("中枢拒绝请求 "+(int)result.StatusCode);return body;}}
        }
        IEnumerator Request(string path,string json=null)
        {error=null;response=null;var pending=Send(path,json);while(!pending.IsCompleted)yield return null;if(pending.IsFaulted||pending.IsCanceled)error="中枢连接未确认，请检查配对、网络和g.Nautilus数据接收";else response=pending.Result;}
        public void Begin()
        {
            if(Busy||Active||!Controller||Controller.Acquisition&&(Controller.Acquisition.Busy||Controller.Acquisition.Active))return;
            Controller.AllowTests(false);Controller.EmergencyStop();stop=failed=false;Saved=sequence=0;Held=sessionId=null;queue.Clear();StartCoroutine(Run());
        }
        public void Finish(){ReleaseHeld();stop=true;}
        public void Abort(){ReleaseHeld();failed=true;stop=true;}
        public bool Press(string label,int pointerId)
        {
            if(!Active||stop||Held!=null||!Allowed(label))return false;Held=label;pointer=pointerId;Enqueue("hold_start",label);nextHeartbeat=Time.realtimeSinceStartupAsDouble+.4;
            var device=InputDevices.GetDeviceAtXRNode(XRNode.RightHand);if(device.isValid)device.SendHapticImpulse(0,.12f,.025f);
            return true;
        }
        public void Release(string label,int pointerId){if(Held==label&&pointer==pointerId)ReleaseHeld();}
        void ReleaseHeld(){if(Held==null)return;string label=Held;Held=null;if(Active)Enqueue("hold_end",label);}
        Camera Head(){var rig=GetComponent<SpatialWorkspaceRig>();return rig&&rig.Active&&rig.Head?rig.Head.GetComponent<Camera>():Camera.main;}
        void Enqueue(string phase,string label)
        {
            if(queue.Count>=24){failed=stop=true;Status="事件队列过载；结束并保留已确认数据";return;}
            var camera=Head();Vector3 p=camera?camera.transform.position:Vector3.zero;Quaternion q=camera?camera.transform.rotation:Quaternion.identity;
            var poses=new float[14];for(int i=0;i<2;i++){var d=InputDevices.GetDeviceAtXRNode(i==0?XRNode.LeftHand:XRNode.RightHand);Vector3 v;Quaternion rot;d.TryGetFeatureValue(CommonUsages.devicePosition,out v);d.TryGetFeatureValue(CommonUsages.deviceRotation,out rot);int a=i*7;poses[a]=v.x;poses[a+1]=v.y;poses[a+2]=v.z;poses[a+3]=rot.x;poses[a+4]=rot.y;poses[a+5]=rot.z;poses[a+6]=rot.w;}
            queue.Enqueue(new Marker{session_id=sessionId,seq=++sequence,label=label,phase=phase,sender_frame=Time.frameCount,sender_monotonic_ms=Time.realtimeSinceStartupAsDouble*1000,clock_offset_ms=offset,sync_rtt_ms=rtt,head_pose=new[]{p.x,p.y,p.z,q.x,q.y,q.z,q.w},controller_pose=poses,xr_active=XRSettings.isDeviceActive,pointer_source=XRSettings.isDeviceActive?"xr_ray":"desktop_pointer"});
        }
        IEnumerator Run()
        {
            Busy=true;Status="同步并创建自主采集会话…";
            double t=Time.realtimeSinceStartupAsDouble*1000;yield return Request("/api/acquisition/sync");double end=Time.realtimeSinceStartupAsDouble*1000;
            if(error!=null){Status=error;Busy=false;yield break;}
            rtt=end-t;offset=JsonUtility.FromJson<Sync>(response).hub_monotonic_ms-(t+end)/2;
            yield return Request("/api/acquisition/start",JsonUtility.ToJson(new StartRequest{build_id=Controller.Health.build_id}));
            if(error!=null){Status=error;Busy=false;yield break;}var session=JsonUtility.FromJson<Session>(response);sessionId=session.session_id;Recording=session.recording;
            if(!session.active||String.IsNullOrEmpty(sessionId)){Status="中枢会话无效";Busy=false;yield break;}
            if(session.experiment_mode=="gaze_only"&&(session.task_binding_version!="gaze_navigation_v2"||session.labels==null||session.task_instructions_list==null))
            {yield return Request("/api/acquisition/stop",JsonUtility.ToJson(new StopRequest{session_id=sessionId,reason="client_guidance_missing"}));Status="眼动任务提示缺失，已停止；请更新中枢与客户端";Busy=false;yield break;}
            currentSession=session;
            Active=true;began=Time.unscaledTime;nextHeartbeat=Time.realtimeSinceStartupAsDouble+.4;BuildSurface();
            while(!stop||queue.Count>0)
            {
                if(!stop&&Time.realtimeSinceStartupAsDouble>=nextHeartbeat){Enqueue("heartbeat",Held);nextHeartbeat=Time.realtimeSinceStartupAsDouble+.4;}
                if(queue.Count==0){yield return null;continue;}var marker=queue.Dequeue();string payload=JsonUtility.ToJson(marker);bool confirmed=false;
                for(int attempt=0;attempt<2;attempt++)
                {yield return Request("/api/acquisition/event",payload);if(error==null){var ack=JsonUtility.FromJson<Ack>(response);if(ack.ok&&ack.ack_seq==marker.seq){Saved=ack.ack_seq;confirmed=true;break;}}}
                if(!confirmed){failed=stop=true;queue.Clear();Status="打标未确认，停止采集；只保留已确认的标签";}
                yield return null;
            }
            yield return Request("/api/acquisition/stop",JsonUtility.ToJson(new StopRequest{session_id=sessionId,reason=failed?"transport_error":"completed"}));
            Active=Busy=false;Held=null;HideSurface();Status=error!=null?"结束尚未确认，中枢会自动关闭并保存":"已保存自主采集："+Recording+" · "+Saved+" 个已确认事件";
        }
        Text Text(Transform parent,string value,int size,Vector2 position,Vector2 dimensions)
        {var go=new GameObject("Collection text",typeof(RectTransform),typeof(Text));go.layer=30;go.transform.SetParent(parent,false);var r=go.GetComponent<RectTransform>();r.anchoredPosition=position;r.sizeDelta=dimensions;var t=go.GetComponent<Text>();t.font=Resources.Load<Font>("Fonts/NotoSansCJKsc-Regular");t.fontSize=size;t.color=new Color(.08f,.12f,.17f);t.text=value;t.alignment=TextAnchor.MiddleCenter;t.raycastTarget=false;return t;}
        bool Allowed(string label){return currentSession==null||currentSession.labels==null||Array.IndexOf(currentSession.labels,label)>=0;}
        bool GazeOnly{get{return currentSession!=null&&currentSession.experiment_mode=="gaze_only";}}
        string TaskTitle(string label,string fallback)
        {
            if(!GazeOnly)return fallback;
            switch(label){case "FORWARD":return "向上看\n前进";case "BACKWARD":return "向下看\n后退";case "LEFT":return "向左看\n左转";case "RIGHT":return "向右看\n右转";case "REST":return "中央注视\n无导航";default:return fallback;}
        }
        string TaskHelp()
        {
            if(Held!=null&&currentSession!=null&&currentSession.task_instructions_list!=null)
                foreach(var item in currentSession.task_instructions_list)if(item.label==Held)return item.instruction;
            return GazeOnly?"无需肢体想象，可正常操作手柄；按键只打标，不驱动运动。":"按当前任务说明操作；按键只打标，松开为未标注。";
        }
        void BuildSurface()
        {
            if(surface){Destroy(surface);surface=null;}
            surface=new GameObject("Self-paced white collection",typeof(RectTransform),typeof(Canvas),typeof(CanvasScaler),typeof(GraphicRaycaster),typeof(TrackedDeviceGraphicRaycaster));surface.layer=30;
            var canvas=surface.GetComponent<Canvas>();canvas.sortingOrder=32750;canvas.renderMode=RenderMode.WorldSpace;var rect=surface.GetComponent<RectTransform>();rect.sizeDelta=new Vector2(1100,750);
            var background=new GameObject("White field",typeof(RectTransform),typeof(Image));background.layer=30;background.transform.SetParent(surface.transform,false);var bg=background.GetComponent<RectTransform>();bg.sizeDelta=new Vector2(10000,10000);background.GetComponent<Image>().color=Color.white;background.GetComponent<Image>().raycastTarget=true;
            Text(surface.transform,GazeOnly?"按住标注眼动 · 松开不标注":"按住标注任务 · 松开不标注",30,new Vector2(0,330),new Vector2(1000,45));
            guidanceText=Text(surface.transform,TaskHelp(),23,new Vector2(0,260),new Vector2(1000,75));
            string[] labels={"FORWARD","BACKWARD","LEFT","RIGHT","REST","FORWARD_LEFT","FORWARD_RIGHT","BACKWARD_LEFT","BACKWARD_RIGHT"};string[] titles={"↑  向前","↓  向后","←  向左","→  向右","静止","↖  前进左转","↗  前进右转","↙  后退左转","↘  后退右转"};Vector2[] positions={new Vector2(0,155),new Vector2(0,-155),new Vector2(-260,0),new Vector2(260,0),Vector2.zero,new Vector2(-260,155),new Vector2(260,155),new Vector2(-260,-155),new Vector2(260,-155)};
            for(int i=0;i<labels.Length;i++)
            {if(!Allowed(labels[i]))continue;var button=new GameObject("Hold "+labels[i],typeof(RectTransform),typeof(Image),typeof(Button),typeof(HoldLabelPress));button.layer=30;button.transform.SetParent(surface.transform,false);var r=button.GetComponent<RectTransform>();r.anchoredPosition=positions[i];r.sizeDelta=new Vector2(230,130);var hold=button.GetComponent<HoldLabelPress>();hold.Owner=this;hold.Label=labels[i];button.GetComponent<Button>().transition=Selectable.Transition.None;Text(button.transform,TaskTitle(labels[i],titles[i]),GazeOnly?32:i<5?42:32,Vector2.zero,new Vector2(225,120));}
            statusText=Text(surface.transform,"",25,new Vector2(0,-280),new Vector2(1000,60));
            var finish=new GameObject("Save and finish",typeof(RectTransform),typeof(Image),typeof(Button));finish.layer=30;finish.transform.SetParent(surface.transform,false);finish.GetComponent<Image>().color=new Color(.9f,.93f,.97f);var fr=finish.GetComponent<RectTransform>();fr.anchoredPosition=new Vector2(0,-355);fr.sizeDelta=new Vector2(300,62);finish.GetComponent<Button>().onClick.AddListener(Finish);Text(finish.transform,"结束并保存",30,Vector2.zero,new Vector2(295,58));
        }
        void HideSurface(){if(surface)surface.SetActive(false);foreach(var pair in masks)if(pair.Key)pair.Key.cullingMask=pair.Value;masks.Clear();}
        public void SynchronizeInputSurface()
        {
            if(!Active||!surface)return;var camera=Head();if(!camera)return;
            if(!masks.ContainsKey(camera))masks.Add(camera,camera.cullingMask);camera.cullingMask=1<<30;
            var canvas=surface.GetComponent<Canvas>();
            surface.GetComponent<GraphicRaycaster>().enabled=!XRSettings.isDeviceActive;
            if(XRSettings.isDeviceActive){canvas.renderMode=RenderMode.WorldSpace;surface.transform.SetPositionAndRotation(camera.transform.position+camera.transform.forward*1.6f,camera.transform.rotation);surface.transform.localScale=Vector3.one*.001f;canvas.worldCamera=camera;}
            else{canvas.renderMode=RenderMode.ScreenSpaceOverlay;surface.GetComponent<CanvasScaler>().uiScaleMode=CanvasScaler.ScaleMode.ScaleWithScreenSize;surface.GetComponent<CanvasScaler>().referenceResolution=new Vector2(1100,800);}
        }
        void LateUpdate(){if(statusText&&Active)statusText.text=(Held==null?"松开 · 未标注":"正在标注："+Held)+"   "+(Time.unscaledTime-began).ToString("F0")+"秒   已确认 "+Saved;if(guidanceText&&Active)guidanceText.text=TaskHelp();}
        void Update(){var rig=GetComponent<SpatialWorkspaceRig>();if(!rig||!rig.Active)SynchronizeInputSurface();if(Active&&(Controller.Health.paused||(!Controller.Health.focused&&!ValidationMode))){ReleaseHeld();Abort();}}
        void OnApplicationPause(bool value){if(value)Abort();}void OnApplicationFocus(bool value){if(!value&&Application.platform==RuntimePlatform.Android)Abort();}
        void OnDisable(){Abort();HideSurface();}void OnDestroy(){HideSurface();if(surface)Destroy(surface);}
    }
}
