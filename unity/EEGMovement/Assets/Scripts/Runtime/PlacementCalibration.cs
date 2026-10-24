using System;
using System.Collections;
using System.Text;
using UnityEngine;

namespace EEGMovement.Unity
{
    // One shared hub task. An authenticated display heartbeat owns presentation;
    // another headset/page cannot silently continue a missing user's task.
    public sealed class PlacementCalibration : MonoBehaviour
    {
        [Serializable] public class ResultMetrics {public float coverage,accuracy_including_rejection;public int event_correct,event_count,rest_false_starts,frames;public bool full_direction_support;}
        [Serializable] public class ResultData {public string check_id,recording,mode,state;public bool completed;public ResultMetrics metrics;}
        [Serializable] public class StatusData { public bool active,valid,motion_qualified,gates_runtime;public string state,reason,calibration_id,presentation_token,prompt,surface,label,stage,mode;public string[] failure_reasons;public int cursor,phase_count;public float accepted_seconds,elapsed_seconds,required_seconds,phase_elapsed,phase_seconds,wall_seconds,settle_seconds;public ResultData last_result; }
        [Serializable] class StartRequest {public string mode;public int repetitions=1;}
        [Serializable] class Presentation { public string calibration_id,presentation_token; public int seq,cursor; }
        public StatusData State {get;private set;}
        public bool BootRegistered {get;private set;}
        public string Message {get;private set;}="模型校验可选，不限制实时推理或动作开关";
        string token,id;int sequence;bool commandBusy,cancelPending;
        AcquisitionPresentation presentation;
        void Start(){presentation=gameObject.AddComponent<AcquisitionPresentation>();StartCoroutine(RegisterApplicationBoot());}
        IEnumerator RegisterApplicationBoot()
        {
            // Register this app's wear session for data attribution. This is a
            // transport/boot handshake, not permission based on a check score.
            while(!BootRegistered)
            {
                yield return Run("/v1/calibration/new-placement");
                if(!BootRegistered)yield return new WaitForSecondsRealtime(1);
            }
            StartCoroutine(Poll());
        }
        public void Begin(){Command("/v1/calibration/start",JsonUtility.ToJson(new StartRequest{mode="signal"}));}
        public void BeginDirections(){Command("/v1/calibration/start",JsonUtility.ToJson(new StartRequest{mode="directions"}));}
        public void Cancel()
        {
            // Stop presentation/lease renewal immediately, even if another
            // network command is still pending. The cancellation is not lost.
            token=null;if(presentation)presentation.Hide();cancelPending=true;TryCancel();
        }
        void TryCancel(){if(!commandBusy&&cancelPending){token=null;if(presentation)presentation.Hide();cancelPending=false;Command("/v1/calibration/cancel");}}
        public void NewPlacement(){Command("/v1/calibration/new-placement");}
        public void StartNormal(){Command("/v1/intent/start");}
        void Command(string path,string body="{}")
        {
            if(!BootRegistered&&!path.EndsWith("/new-placement")){Message="正在确认本次应用启动，请稍候";return;}
            if(!commandBusy)StartCoroutine(Run(path,body));
        }
        IEnumerator Run(string path,string body="{}")
        {
            commandBusy=true;var task=SelfPacedAcquisition.Send(path.Replace("/v1/","/api/"),body);
            while(!task.IsCompleted)yield return null;
            if(task.IsFaulted||task.IsCanceled)Message="请求未完成，请查看电脑中枢";
            else if(path.EndsWith("/start")&&path.Contains("calibration"))
            {try{var s=JsonUtility.FromJson<StatusData>(task.Result);token=s.presentation_token;id=s.calibration_id;sequence=0;State=s;}catch(Exception){Message="校准响应无效";}}
            else if(path.Contains("calibration")){token=null;if(presentation)presentation.Hide();if(path.EndsWith("/new-placement"))BootRegistered=true;}
            commandBusy=false;TryCancel();
        }
        IEnumerator Poll()
        {
            while(true)
            {
                var task=SelfPacedAcquisition.Send("/api/calibration/status");while(!task.IsCompleted)yield return null;
                if(task.IsFaulted||task.IsCanceled){Message="校准连接异常，计时暂停";if(presentation&&token!=null)presentation.Show(Camera.main,"计时已暂停","等待连接恢复","",Message,"右手 A 取消校准");}
                else
                {
                    StatusData s=null;try{s=JsonUtility.FromJson<StatusData>(task.Result);}catch(Exception){}
                    if(s!=null)
                    {
                        State=s;float required=s.required_seconds>0?s.required_seconds:60;
                        Message=s.active?s.reason+" · "+s.elapsed_seconds.ToString("F0")+" / "+required.ToString("F0")+"秒":"模型校验可选，结果不影响推理与动作开关";
                        if(!s.active&&s.last_result!=null&&s.last_result.metrics!=null){var m=s.last_result.metrics;Message=m.frames==0?"上次校验尚无可计分数据；部分记录已保存":"上次"+(s.last_result.mode=="signal"?"静息":"方向")+"校验：指令一致 "+(m.accuracy_including_rejection*100).ToString("F1")+"% · 覆盖 "+(m.coverage*100).ToString("F1")+"%\n事件 "+m.event_correct+"/"+m.event_count+" · 静止误启 "+m.rest_false_starts+"次 · 已保存至电脑";}
                        bool own=s.active&&s.surface=="vr"&&token!=null&&id==s.calibration_id&&Application.isFocused;
                        if(own)
                        {
                            var rig=FindFirstObjectByType<SpatialWorkspaceRig>();var camera=rig&&rig.Active&&rig.Head?rig.Head.GetComponent<Camera>():Camera.main;
                            if(camera)
                            {
                                string cue=string.IsNullOrEmpty(s.prompt)?Cue(s.label):s.prompt;
                                if(string.IsNullOrEmpty(cue)){presentation.Show(camera,"等待有效指令","计时暂停");yield return new WaitForSecondsRealtime(.15f);continue;}
                                float duration=s.phase_seconds>0?s.phase_seconds:(s.stage=="baseline"?20:5);
                                string stage=s.mode=="signal"?"静息模型检查":"方向性能校验 · 固定当前模型";
                                stage+=" · "+(s.cursor+1)+" / "+(s.phase_count>0?s.phase_count:21);
                                string detail=s.reason+"\n流程剩余 "+GuidedAcquisition.Clock(required-s.elapsed_seconds)+" · 合格信号 "+s.accepted_seconds.ToString("F1")+"秒";
                                if(s.settle_seconds>s.phase_elapsed)detail="准备动作 · "+Mathf.CeilToInt(s.settle_seconds-s.phase_elapsed)+" 秒后开始检查\n"+detail;
                                presentation.Show(camera,cue,stage,"本条剩余 "+GuidedAcquisition.Clock(duration-s.phase_elapsed),detail,"右手 A 结束并保存已采数据",s.elapsed_seconds/required);yield return new WaitForEndOfFrame();
                                if(token==null||cancelPending)continue;
                                var body=JsonUtility.ToJson(new Presentation{calibration_id=id,presentation_token=token,seq=++sequence,cursor=s.cursor});
                                var ack=SelfPacedAcquisition.Send("/api/calibration/present",body);while(!ack.IsCompleted)yield return null;
                            }
                        }
                        else if(presentation)presentation.Hide();
                        if(!s.active)token=null;
                    }
                }
                yield return new WaitForSecondsRealtime(.15f);
            }
        }
        static string Cue(string label){switch(label){case "REST":return "保持静止";case "FORWARD":return "想象向前移动";case "BACKWARD":return "想象向后移动";case "LEFT":return "想象向左转";case "RIGHT":return "想象向右转";default:return "";}}
        void OnApplicationPause(bool paused){if(paused&&token!=null)Cancel();}
        void OnDisable(){if(presentation)presentation.Hide();if(token!=null)Cancel();}
    }
}
