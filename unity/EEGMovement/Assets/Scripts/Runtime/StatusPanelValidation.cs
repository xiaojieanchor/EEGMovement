using System;using System.Collections;using System.IO;using UnityEngine;using UnityEngine.UI;
namespace EEGMovement.Unity
{
    // CLI-only protocol/text validation; never spawned in ordinary usage.
    public sealed class StatusPanelValidation:MonoBehaviour
    {
        string output;
        [Serializable]sealed class Result{public bool success,live_values,combo_value,expired_scores_hidden,transport_failure_hidden,new_model_values,start_before_arm,rejected_start_safe;public string build_id,scope;}
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]static void Enable()
        {var args=Environment.GetCommandLineArgs();for(int i=0;i+1<args.Length;i++)if(args[i]=="--eeg-validate-status"){var runner=new GameObject("Explicit read-only panel validation").AddComponent<StatusPanelValidation>();runner.output=Path.GetFullPath(args[i+1]);return;}}
        static string Texts(QuestStatusOverlay panel){string s="";foreach(var t in panel.GetComponentsInChildren<Text>(true))s+=t.text+"\n";return s;}
        IEnumerator Start()
        {
            yield return null;yield return null;var controller=FindFirstObjectByType<EEGAvatarController>();var panel=new GameObject("Panel validation").AddComponent<QuestStatusOverlay>();panel.Controller=controller;panel.Initialize(Camera.main.transform);panel.SetVisible(true);
            var r=new Result{build_id=controller.Health.build_id,scope="Actual Unity read-only status text and stale handling against isolated fixture; no physical B key or headset rendering claim"};
            float until=Time.realtimeSinceStartup+10;
            while(Time.realtimeSinceStartup<until){string s=Texts(panel);if(s.Contains("82.0%")&&s.Contains("46.0%")&&s.Contains("4.0%")){r.live_values=true;r.combo_value=s.Contains("前进 + 左转");break;}yield return null;}
            r.new_model_values=Texts(panel).Contains("70.09M")&&Texts(panel).Contains("96.1%")&&Texts(panel).Contains("15.1%")&&Texts(panel).Contains("CH6");
            controller.ToggleModelMotion();yield return new WaitForSecondsRealtime(.5f);r.start_before_arm=controller.ModelStatus.Contains("已启用");
            controller.ToggleModelMotion();yield return new WaitForSecondsRealtime(.5f);
            controller.ToggleModelMotion();yield return new WaitForSecondsRealtime(.5f);r.rejected_start_safe=controller.ModelStatus.Contains("未确认");
            until=Time.realtimeSinceStartup+10;
            while(Time.realtimeSinceStartup<until){string s=Texts(panel);if(s.Contains("等待有效通道及实时脑电")&&!s.Contains("82.0%")){r.expired_scores_hidden=true;break;}yield return null;}
            until=Time.realtimeSinceStartup+10;
            while(Time.realtimeSinceStartup<until){string s=Texts(panel);if(s.Contains("旧数据已失效")&&!s.Contains("82.0%")){r.transport_failure_hidden=true;break;}yield return null;}
            panel.SetVisible(false);r.success=r.live_values&&r.combo_value&&r.expired_scores_hidden&&r.transport_failure_hidden&&r.new_model_values&&r.start_before_arm&&r.rejected_start_safe;Directory.CreateDirectory(Path.GetDirectoryName(output));File.WriteAllText(output,JsonUtility.ToJson(r,true));Application.Quit(r.success?0:1);
        }
    }
}
